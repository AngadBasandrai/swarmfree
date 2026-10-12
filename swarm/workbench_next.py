"""Owner-authenticated 0.5 desktop controls; public team routes live separately."""
import asyncio
import json
import threading
import time
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from .schema import Architecture,RunInput
from .store import uid
from .providers import RouteError
from .teamwork import MemberInput,ConnectInput
from .architecture_copilot import design,preview,DESIGN_SCHEMA
from .onboarding import download_verified
from .fault_lab import SCENARIOS

class DesignInput(BaseModel):
    description:str=Field(min_length=5,max_length=4000)
    topology:Literal["auto","pipeline","parallel","supervisor"]="auto"
class DesignMission(DesignInput):
    mission:RunInput
class PreviewInput(BaseModel):
    architecture:Architecture
class Approval(BaseModel):
    sha256:str=Field(pattern=r"^[0-9a-f]{64}$")
class DownloadInput(BaseModel):
    url:str=Field(max_length=2000)
    sha256:str=Field(pattern=r"^[0-9a-fA-F]{64}$")
    name:str=Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]{0,120}$")
    max_bytes:int=Field(default=2000000000,ge=1,le=32000000000)
class StepInput(BaseModel):
    done:bool=True
class PolicyRule(BaseModel):
    pattern:str=Field(min_length=1,max_length=200)
    destinations:list[Literal["local","private","cloud","public"]]=Field(min_length=1,max_length=4)
class PolicyInput(BaseModel):
    destinations:list[Literal["local","private","cloud","public"]]=Field(min_length=1,max_length=4)
    profile_ids:list[str]=Field(default_factory=list,max_length=100)
    device_ids:list[str]=Field(default_factory=list,max_length=100)
    file_rules:list[PolicyRule]=Field(default_factory=list,max_length=30)
    scan_secrets:bool=True
class ReviewInput(BaseModel):
    decision:Literal["approved","redact","blocked"]
class CacheInput(BaseModel):
    ttl_seconds:int=Field(default=3600,ge=60,le=604800)
    max_entries:int=Field(default=200,ge=1,le=1000)
class PeerInput(BaseModel):
    agent_id:str=Field(max_length=160)
    channel:str=Field(max_length=48)
    target:str=Field(max_length=48)
    content:str=Field(min_length=1,max_length=4000)
class FaultInput(BaseModel):
    scenarios:list[str]=Field(default_factory=lambda:list(SCENARIOS),min_length=1,max_length=8)
class A2AInput(BaseModel):
    label:str=Field(min_length=1,max_length=80)
    origin:str=Field(max_length=500)
    token:str=Field(default="",max_length=4096)
    ephemeral:bool=True
    free_eligible:bool=False
class AccessInput(BaseModel):
    team_tools_allowed:bool=False
    team_architectures:list[str]=Field(default_factory=list,max_length=30)
    a2a_endpoints:list[str]=Field(default_factory=list,max_length=30)
class RemoteInput(BaseModel):
    path:str=Field(max_length=500)
    method:Literal["GET","POST"]="GET"
    body:dict|None=None
class RegistryInput(BaseModel):
    url:str=Field(max_length=2000)
    keys:dict[str,str]
class PlacementInput(BaseModel):
    model:str=Field(min_length=1,max_length=200)
    sharding:Literal["Pipeline","Tensor"]="Pipeline"
    min_nodes:int=Field(default=2,ge=1,le=16)

def install_workbench(app):
    router=APIRouter(prefix="/api/workbench")
    def launch(kind,runner):
        store=app.state.store
        if sum(j.get("status")=="running" for j in store.all(kind))>=2: raise RouteError("Finish or cancel an active job first")
        id=uid(kind+"_");store.put(kind,id,dict(id=id,status="running",created_at=time.time()))
        async def execute():
            try:
                result=await runner(id)
                store.patch(kind,id,dict(status="completed",result=result,finished_at=time.time()))
            except asyncio.CancelledError:
                store.patch(kind,id,dict(status="cancelled"));raise
            except Exception as error:
                store.patch(kind,id,dict(status="failed",error=str(error)[:1000] if isinstance(error,RouteError) else "Operation failed; check configuration"))
        app.state.feature_tasks[id]=asyncio.create_task(execute());return store.get(kind,id)
    @router.get("")
    async def status():
        s=app.state.store
        return dict(setup=app.state.onboarding.plan(),members=app.state.team.public(),audit=s.all("team_audit")[-100:],team_remote=s.get("settings","team_remote",{}),
            egress=s.all("egress_review")[-50:],cache=dict(entries=len(s.all("response_cache")),hits=sum(r.get("hits",0) for r in s.all("response_cache")),settings=s.get("settings","response_cache",dict(ttl_seconds=3600,max_entries=200))),
            faults=s.all("fault_job")[-20:],scenarios=SCENARIOS,a2a_endpoints=s.all("a2a_endpoint"),a2a_tasks=s.all("a2a_task")[-50:],
            registry=app.state.registry.settings(),registry_index=s.get("registry_index","current",{}),plugin_drafts=s.all("plugin_draft"),clusters=s.all("cluster_snapshot"),placements=s.all("cluster_preview"),jobs=s.all("a2a_stream")[-10:])
    @router.post("/setup/steps/{id}")
    async def setup_step(id:str,form:StepInput): return app.state.onboarding.mark(id,form.done)
    @router.post("/setup/download")
    async def download(form:DownloadInput):
        async def runner(id):
            stop=threading.Event()
            def progress(n):
                if stop.is_set(): raise RouteError("Download cancelled")
                app.state.store.patch("setup_download",id,dict(completed=n,max_bytes=form.max_bytes))
            task=asyncio.create_task(asyncio.to_thread(download_verified,app.state.workspace.root,form.url,form.sha256,form.name,form.max_bytes,progress))
            try: return await asyncio.shield(task)
            except asyncio.CancelledError:
                stop.set();await asyncio.gather(task,return_exceptions=True);raise
        return launch("setup_download",runner)
    @router.post("/jobs/{id}/cancel")
    async def cancel(id:str):
        task=app.state.feature_tasks.get(id)
        if task: task.cancel();await asyncio.gather(task,return_exceptions=True)
        return dict(cancelled=bool(task))
    @router.post("/architectures/design")
    async def designer(form:DesignInput): return design(form.description,form.topology)
    @router.post("/architectures/model-design")
    async def model_designer(form:DesignMission):
        architecture=Architecture(id="architect",name="Architecture copilot",nodes=[dict(id="designer",name="Designer",role="Design a valid swarm DAG",instructions="Return JSON with name, description, nodes and edges. Use only the requested roles. Assign x/y positions. No tools or executable commands.",tools=[],output_schema=DESIGN_SCHEMA,requires=["json"])]).model_dump()
        mission=form.mission.model_dump();mission.update(task=form.description,architecture_id="architect",tools_enabled=False,max_subtasks=0,inputs={})
        return app.state.engine.create(RunInput.model_validate(mission),architecture)
    @router.post("/architectures/preview")
    async def validate_design(form:PreviewInput): return preview(form.architecture.model_dump())
    @router.post("/architectures/save")
    async def save_design(form:PreviewInput):
        row=preview(form.architecture.model_dump())["architecture"];row["id"]=uid("custom_")
        from .composition import resolve
        resolve(app.state.store,row)
        return app.state.architectures.save(row)
    @router.get("/channels/{run_id}")
    async def channels(run_id:str):
        s=app.state.store;run=s.get("run",run_id)
        if not run: raise HTTPException(404,"Mission not found")
        return dict(channels=run["architecture"].get("channels",[]),messages=[m for m in s.all("peer_message") if m["run_id"]==run_id],claims=[m for m in s.all("task_claim") if m["run_id"]==run_id])
    @router.post("/channels/{run_id}")
    async def send(run_id:str,form:PeerInput):
        run=app.state.store.get("run",run_id);agent=app.state.store.get("agent",form.agent_id)
        if not run or not agent or agent["run_id"]!=run_id: raise RouteError("Choose a member of this mission")
        return app.state.mailboxes.send(run,agent,form.channel,form.target,form.content)
    @router.get("/policies/{id}")
    async def policy(id:str): return app.state.privacy.policy(id)
    @router.put("/policies/{id}")
    async def save_policy(id:str,form:PolicyInput): return app.state.privacy.save(id,form.model_dump())
    @router.post("/egress/{id}")
    async def decide(id:str,form:ReviewInput): return app.state.privacy.decide(id,form.decision)
    @router.put("/cache")
    async def cache(form:CacheInput): return app.state.store.put("settings","response_cache",form.model_dump())
    @router.delete("/cache")
    async def clear_cache():
        s=app.state.store
        for row in s.all("response_cache"): s.delete("response_cache",row["id"])
        return dict(cleared=True)
    @router.post("/members")
    async def add_member(form:MemberInput): return app.state.team.add(form)
    @router.post("/members/{id}/revoke")
    async def revoke(id:str): return await app.state.team.revoke(id)
    @router.put("/projects/{id}/access")
    async def access(id:str,form:AccessInput):
        s=app.state.store
        if not s.get("project",id): raise HTTPException(404,"Project not found")
        for kind,ids in (("architecture",form.team_architectures),("a2a_endpoint",form.a2a_endpoints)):
            if any(not s.get(kind,k) for k in ids): raise RouteError("Choose existing architectures and endpoints")
        app.state.team.audit("owner","project.access",id);return s.patch("project",id,form.model_dump())
    @router.post("/team/connect")
    async def connect(form:ConnectInput): return app.state.team.connect(form)
    @router.post("/team/disconnect")
    async def disconnect():
        app.state.vault.delete("team_remote");return app.state.store.put("settings","team_remote",dict(enabled=False))
    @router.post("/team/remote")
    async def remote(form:RemoteInput): return await app.state.team.remote(form.path,form.method,form.body)
    @router.post("/faults")
    async def faults(form:FaultInput):
        if any(n not in SCENARIOS for n in form.scenarios): raise RouteError("Unknown failure simulation")
        async def runner(id):
            await app.state.faults.run(id,form.scenarios)
            row=app.state.store.get("fault_job",id)
            return dict(rows=row.get("rows",[]),passed=row.get("passed",False))
        return launch("fault_job",runner)
    @router.post("/a2a")
    async def add_a2a(form:A2AInput): return app.state.a2a.add(form.model_dump())
    @router.post("/a2a/{id}/discover")
    async def discover(id:str): return await app.state.a2a.discover(id)
    @router.delete("/a2a/{id}")
    async def disable(id:str):
        app.state.vault.delete(id);return app.state.store.patch("a2a_endpoint",id,dict(enabled=False))
    @router.post("/a2a/tasks/{id}/subscribe")
    async def subscribe(id:str):
        row=app.state.store.get("a2a_task",id,{})
        run=app.state.store.get("run",row.get("run_id",""),{});project=app.state.store.get("project",run.get("project_id",""),{})
        if row.get("endpoint_id") not in project.get("a2a_endpoints",[]): raise RouteError("This project's grant was withdrawn")
        async def runner(job): return await app.state.a2a.subscribe(id)
        return launch("a2a_stream",runner)
    @router.put("/registry")
    async def registry_config(form:RegistryInput): return app.state.registry.configure(form.url,form.keys)
    @router.post("/registry/refresh")
    async def registry_refresh(): return await app.state.registry.refresh()
    @router.post("/registry/{id}/inspect")
    async def registry_inspect(id:str): return await app.state.registry.inspect(id)
    @router.post("/registry/{id}/import")
    async def registry_import(id:str,form:Approval): return app.state.registry.install(id,form.sha256)
    @router.post("/clusters/{id}/inspect")
    async def cluster_inspect(id:str): return await app.state.cluster.inspect(id)
    @router.post("/clusters/{id}/preview")
    async def placement(id:str,form:PlacementInput): return await app.state.cluster.preview(id,form.model,form.sharding,form.min_nodes)
    @router.post("/clusters/{id}/deploy")
    async def deploy(id:str,form:Approval): return await app.state.cluster.deploy(id,form.sha256)
    @router.delete("/clusters/{id}/instances/{instance}")
    async def remove_instance(id:str,instance:str): return await app.state.cluster.remove(id,instance)
    app.include_router(router)
