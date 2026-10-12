"""Opt-in pure response caching, scoped to project, policy, revision and inputs."""
import os
from pathlib import Path
import time
from .privacy import digest
from .workspace import BLOCKED,scoped_path
from .providers import RouteError


def project_revision(project):
    if not project: return "no-project"
    entries=[];total=0
    for directory,dirs,files in os.walk(project["root"],followlinks=False):
        dirs[:]=[d for d in dirs if d.casefold() not in BLOCKED and not d.casefold().startswith(".env") and not (Path(directory)/d).is_symlink()]
        for name in sorted(files):
            relative=(Path(directory)/name).relative_to(project["root"]).as_posix()
            try:
                path=scoped_path(project,relative)
                if path.is_symlink(): return None
                size=path.stat().st_size;total+=size
                if len(entries)>=1000 or total>32000000: return None
                import hashlib
                entries.append((relative,hashlib.sha256(path.read_bytes()).hexdigest()))
            except (OSError,RouteError): return None
    return digest(sorted(entries))


class ResultCache:
    def __init__(self,store,workspace,privacy): self.store,self.workspace,self.privacy=store,workspace,privacy

    def key(self,run,agent,messages,kind,data):
        if not run.get("reuse_cache") or run.get("mode", "live")!="live" or run.get("attachments") or self.workspace.permissions(run,agent): return None
        revision=(data.get("model_revision") or run.get("model_revision")) if kind=="profile" else run.get("model_revision")
        if kind=="runtime": revision=digest({k:data.get(k) for k in ("identity","modified_ns","size")})
        if kind not in {"profile","local","runtime","device"} or not revision: return None
        project=self.store.get("project",run.get("project_id",""));source=project_revision(project)
        if source is None: return None
        route={k:data.get(k) for k in ("id","device_id","provider","base_url","model","name")} if isinstance(data,dict) else data
        return digest(dict(project=run.get("project_id",""),source=source,policy=self.privacy.policy(run.get("project_id","")),labels=agent.get("data_labels",[]),
            messages=messages,route=route,revision=revision,schema=agent.get("output_schema"),validators=agent.get("validators"),requires=agent.get("requires")))

    def get(self,key):
        row=self.store.get("response_cache",key) if key else None
        if row and row["expires_at"]>time.time():
            self.store.patch("response_cache",key,dict(hits=row.get("hits",0)+1,last_used=time.time()));return row

    def put(self,key,run,content):
        if not key or len(content)>100000: return
        settings=self.store.get("settings","response_cache",dict(ttl_seconds=3600,max_entries=200))
        rows=sorted(self.store.all("response_cache"),key=lambda r:r.get("last_used",r["created_at"]))
        for row in rows[:max(0,len(rows)-settings["max_entries"]+1)]: self.store.delete("response_cache",row["id"])
        self.store.put("response_cache",key,dict(id=key,project_id=run.get("project_id",""),content=content,created_at=time.time(),expires_at=time.time()+settings["ttl_seconds"],hits=0))
