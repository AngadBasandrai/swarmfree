"""Durable opt-in triggers while the coordinator is running; no OS input control."""
import asyncio
import fnmatch
import hashlib
import json
import os
from pathlib import Path
import time
from pydantic import BaseModel,Field,model_validator
from typing import Literal
from .schema import RunInput
from .store import uid
from .workspace import BLOCKED,scoped_path
from .providers import RouteError


class AutomationInput(BaseModel):
    name: str=Field(min_length=1,max_length=100)
    kind: Literal["interval","daily","files","git"]="interval"
    interval_seconds: int=Field(default=3600,ge=30,le=2678400)
    daily_hour_utc: int=Field(default=9,ge=0,le=23)
    pattern: str=Field(default="*.py",min_length=1,max_length=160)
    debounce_seconds: int=Field(default=10,ge=2,le=300)
    enabled: bool=False
    approval_required: bool=True
    mission: RunInput

    @model_validator(mode="after")
    def validate_trigger(self):
        if self.kind in {"files","git"} and not self.mission.project_id: raise ValueError("File/Git triggers need a project")
        if ".." in Path(self.pattern).parts or Path(self.pattern).is_absolute(): raise ValueError("Use a relative watch pattern")
        return self


class Automation:
    def __init__(self,store,engine):
        self.store,self.engine=store,engine
        self.task=asyncio.create_task(self.loop())
        self.enabled=True

    async def stop(self):
        self.task.cancel()
        await asyncio.gather(self.task,return_exceptions=True)

    def add(self,form):
        if len(self.store.all("automation"))>=40: raise RouteError("Limit of 40 automations reached")
        if not self.store.get("architecture",form.mission.architecture_id): raise RouteError("Unknown mission architecture")
        if form.mission.project_id and not self.store.get("project",form.mission.project_id): raise RouteError("Unknown mission project")
        if form.mission.public_allowed and not form.approval_required: raise RouteError("Public worker sharing needs approval on each trigger")
        id=uid("automation_")
        return self.store.put("automation",id,dict(id=id,**form.model_dump(),created_at=time.time(),next_at=time.time()+form.interval_seconds))

    async def fingerprint(self,automation):
        project=self.store.get("project",automation["mission"]["project_id"])
        if not project: raise RouteError("Automation project no longer exists")
        if automation["kind"]=="git":
            from .isolation import command
            text=await command(["git","rev-parse","HEAD"],project["root"])
            text+=await self.fingerprint(dict(automation,kind="files",pattern="*"))
            return hashlib.sha256(text.encode()).hexdigest()
        entries=[]
        inspected=0
        for directory,dirs,files in os.walk(project["root"],followlinks=False):
            dirs[:]=[d for d in dirs if d.casefold() not in BLOCKED and not d.casefold().startswith(".env") and not (Path(directory)/d).is_symlink()]
            for name in files:
                inspected+=1
                if inspected>2000: raise RouteError("Watch scope exceeds 2000 files; choose a smaller project")
                if inspected%40==0: await asyncio.sleep(0)
                relative=(Path(directory)/name).relative_to(project["root"]).as_posix()
                if fnmatch.fnmatch(relative,automation["pattern"]) or fnmatch.fnmatch(name,automation["pattern"]):
                    try:
                        target=scoped_path(project,relative)
                        stat=target.stat()
                        if stat.st_size>256000: continue
                        entries.append((relative,hashlib.sha256(target.read_bytes()).hexdigest()))
                    except (OSError,RouteError): continue
                    if len(entries)>500: raise RouteError("Watch pattern matches more than 500 files")
        return hashlib.sha256(json.dumps(sorted(entries)).encode()).hexdigest()

    def trigger(self,automation,reason):
        pending=[t for t in self.store.all("trigger") if t["automation_id"]==automation["id"] and t["status"] in {"pending","running"}]
        if pending: return
        id=uid("trigger_")
        trigger=self.store.put("trigger",id,dict(id=id,automation_id=automation["id"],name=automation["name"],reason=reason,mission=automation["mission"],
            status="pending",created_at=time.time()))
        self.notify("Automation ready",automation["name"]+": "+reason,trigger_id=id)
        if not automation["approval_required"]: self.launch(id)
        return trigger

    def launch(self,id):
        trigger=self.store.get("trigger",id)
        if not trigger or trigger["status"]!="pending": raise RouteError("Trigger is no longer pending")
        form=RunInput.model_validate(trigger["mission"])
        architecture=self.store.get("architecture",form.architecture_id)
        if not architecture: raise RouteError("Architecture no longer exists")
        if form.project_id and not self.store.get("project",form.project_id): raise RouteError("Project no longer exists")
        run=self.engine.create(form,architecture)
        self.store.patch("run",run["id"],dict(trigger_id=id))
        return self.store.patch("trigger",id,dict(status="running",run_id=run["id"]))

    def notify(self,title,body,**links):
        id=uid("notice_")
        self.store.put("notification",id,dict(id=id,title=title,body=body[:1000],read=False,created_at=time.time(),**links))
        notices=self.store.all("notification")
        for old in notices[:-200]: self.store.delete("notification",old["id"])

    async def tick(self):
        for trigger in self.store.all("trigger"):
            if trigger["status"]=="running":
                run=self.store.get("run",trigger["run_id"],{})
                if run.get("status") in {"completed","interrupted","budget_exceeded"}:
                    self.store.patch("trigger",trigger["id"],dict(status=run["status"]))
                    self.notify("Mission "+run["status"],trigger["name"],run_id=run["id"])
        if not self.enabled: return
        now=time.time()
        for automation in self.store.all("automation"):
            if not automation["enabled"]: continue
            try:
                kind=automation["kind"]
                if kind=="interval" and automation["next_at"]<=now:
                    self.trigger(automation,"Scheduled interval")
                    self.store.patch("automation",automation["id"],dict(next_at=now+automation["interval_seconds"]))
                elif kind=="daily":
                    utc=time.gmtime(now)
                    day=time.strftime("%Y-%m-%d",utc)
                    if utc.tm_hour>=automation["daily_hour_utc"] and automation.get("last_day")!=day:
                        self.trigger(automation,"Daily schedule (UTC)")
                        self.store.patch("automation",automation["id"],dict(last_day=day))
                elif kind in {"files","git"} and automation.get("poll_at",0)<=now:
                    fingerprint=await self.fingerprint(automation)
                    changes=dict(poll_at=now+max(2,min(automation["interval_seconds"],10)))
                    previous=automation.get("fingerprint")
                    if not previous: changes["fingerprint"]=fingerprint
                    elif fingerprint!=previous:
                        if automation.get("candidate")==fingerprint and now-automation.get("candidate_at",now)>=automation["debounce_seconds"]:
                            self.trigger(automation,"Watched "+kind+" changed")
                            changes.update(fingerprint=fingerprint,candidate=None)
                        elif automation.get("candidate")!=fingerprint: changes.update(candidate=fingerprint,candidate_at=now)
                    self.store.patch("automation",automation["id"],changes)
            except (RouteError,OSError,ValueError):
                self.store.patch("automation",automation["id"],dict(enabled=False,error="Trigger inspection failed; review its project and settings"))

    async def loop(self):
        while True:
            await asyncio.sleep(2)
            await self.tick()
