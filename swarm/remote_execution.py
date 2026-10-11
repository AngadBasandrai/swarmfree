"""Fenced private execution leases; transferred files live in an ephemeral container mount."""
import asyncio
import json
import os
from pathlib import Path
import re
import tempfile
import time
from .providers import RouteError
from .store import uid
from .workspace import scoped_path,BLOCKED


def checked_files(files):
    if not isinstance(files,dict) or len(files)>100 or len(json.dumps(files))>512000: raise RouteError("Remote files exceed the bounded transfer limit")
    for name,text in files.items():
        # A POSIX name is portable across Windows/Linux; no drive, UNC or backslash aliases.
        if not isinstance(name,str) or not name or "\\" in name or ":" in name or name.startswith("/") or any(p in {"",".",".."} or p.casefold() in BLOCKED or p.casefold().startswith(".env") for p in name.split("/")):
            raise RouteError("Invalid remote file path")
        if not isinstance(text,str) or len(text.encode())>120000 or "\x00" in text: raise RouteError("Remote execution supports bounded UTF-8 text files")
    return files


def snapshot(project):
    files={};total=0;inspected=0
    for directory,dirs,names in os.walk(project["root"],followlinks=False):
        dirs[:]=[d for d in dirs if d.casefold() not in BLOCKED and not d.casefold().startswith(".env") and not (Path(directory)/d).is_symlink()]
        for name in names:
            inspected+=1
            if inspected>3000: raise RouteError("Project is too large for a remote execution snapshot; select a smaller project")
            try:
                rel=(Path(directory)/name).relative_to(project["root"]).as_posix();path=scoped_path(project,rel)
                if path.is_symlink() or path.stat().st_size>120000: continue
                text=path.read_text(encoding="utf-8")
                if "\x00" in text: continue
                total+=len(json.dumps({rel:text}))
                if total>512000 or len(files)>=100: raise RouteError("Project snapshot exceeds 100 text files / 512 KB; select a smaller project")
                files[rel]=text
            except (OSError,UnicodeError): continue
    return checked_files(files)


class RemoteExecution:
    def __init__(self,store,pool,workspace): self.store,self.pool,self.workspace=store,pool,workspace

    async def execute(self,project,run,agent,device_id,name):
        device=self.store.get("device",device_id)
        if not device or not device.get("approved") or device.get("revoked") or time.time()-device.get("last_seen",0)>35 or not device.get("execution_approved") or not device.get("execution",{}).get("enabled"):
            raise RouteError("Select an online, separately approved private execution worker")
        if device_id not in project.get("execution_devices",[]): raise RouteError("This project has not granted execution to this worker")
        argv=project.get("checks",{}).get(name)
        if not argv: raise RouteError("Remote execution requires an owner-configured named check")
        files=await asyncio.to_thread(snapshot,project)
        id=uid("exec_")
        self.store.put("job",id,dict(id=id,device_id=device_id,kind="execution",model="isolated execution",messages=[],text="",status="queued",deadline=time.time()+180,created_at=time.time(),
            run_id=run["id"],agent_id=agent["id"],project_id=project["id"],payload=dict(argv=argv,files=files,name=name)))
        try:
            while True:
                job=self.store.get("job",id)
                if job["status"]=="done":
                    result=job.get("execution_result",{})
                    returned=checked_files(result.get("files",{}));staged=[]
                    for path,content in returned.items():
                        if content!=files.get(path):
                            artifact=self.workspace.stage(project,run,agent,path,content,baseline_original=files.get(path))
                            staged.append(artifact["id"])
                    return dict(job_id=id,exit_code=result.get("exit_code"),output=result.get("output","")[:64000],artifacts=staged,deleted=[p for p in files if p not in returned],
                        note="Returned text changes are staged for review. Deletions, large and binary files require manual review.")
                if job["status"] in {"failed","expired","cancelled"}: raise RouteError(job.get("error") or "Remote execution was interrupted")
                if time.time()>job["deadline"] or (job["status"]=="leased" and time.time()>job["lease_until"]):
                    self.store.patch("job",id,dict(status="expired"));self.pool.receipt(job,"expired")
                await asyncio.sleep(.2)
        finally:
            job=self.store.get("job",id)
            if job["status"] in {"queued","leased"}: self.store.patch("job",id,dict(status="cancelled"))


async def execute_container(payload,image,ram_gb=1):
    if not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_@-]{0,240}",image): raise RouteError("Worker execution image is invalid")
    files=checked_files(payload.get("files",{}));argv=payload.get("argv",[])
    if not isinstance(argv,list) or not 1<=len(argv)<=30 or any(not isinstance(v,str) or not v or len(v)>2000 or "\x00" in v for v in argv): raise RouteError("Invalid remote command arguments")
    from .workspace import Workspace
    from .isolation import Isolation,command
    name="swarm-remote-"+uid()
    with tempfile.TemporaryDirectory(prefix="swarm-execution-") as folder:
        project=dict(root=folder,container_image=image)
        for relative,text in files.items():
            path=scoped_path(project,relative);path.parent.mkdir(parents=True,exist_ok=True);path.write_text(text,encoding="utf-8")
        args=Isolation(None,folder).docker_argv(project,argv,name)
        args[args.index("--memory")+1]=str(max(64,min(int(ram_gb*1024),8192)))+"m"
        workspace=Workspace(None,folder)
        try:
            result=await workspace.check(name,project,args)
            return dict(result,files=await asyncio.to_thread(snapshot,project))
        finally:
            try: await command(["docker","rm","-f",name],timeout=10)
            except (RouteError,OSError): pass
