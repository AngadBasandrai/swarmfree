"""Authenticated desktop controls for the 0.4 execution and recovery services."""
import asyncio
import base64
import json
from pathlib import Path
import time
from typing import Literal
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field,model_validator
from .schema import Route,RunInput,AgentSpec
from .providers import RouteError
from .protocol import validate_schema
from .store import uid


class GGUFInput(BaseModel):
    name:str=Field(default="",max_length=160)
    path:str=Field(min_length=1,max_length=1500)
    server_path:str=Field(min_length=1,max_length=1500)
    gpu_layers:int=Field(default=0,ge=0,le=999)
    license:str=Field(default="Check model license",max_length=200)
    trusted:bool=False

class BrowserSite(BaseModel):
    origin:str=Field(max_length=500)
    interact:bool=False

class ProjectAccess(BaseModel):
    browser_sites:list[BrowserSite]=Field(default_factory=list,max_length=12)
    execution_devices:list[str]=Field(default_factory=list,max_length=12)

class BrowserSettings(BaseModel):
    executable:str=Field(default="",max_length=1500)
    trusted:bool=False

class BrowserAction(BaseModel):
    action:Literal["open","read","click","type","screenshot"]
    arguments:dict=Field(default_factory=dict)

class QuestionAnswer(BaseModel):
    answer:object=None
    cancel:bool=False

class IndexInput(BaseModel):
    project_id:str=Field(max_length=80)
    model:str=Field(default="qwen3-embedding:0.6b",max_length=200)

class SearchInput(BaseModel):
    project_id:str=Field(max_length=80)
    query:str=Field(min_length=1,max_length=3000)

class EvalTask(BaseModel):
    task:str=Field(min_length=1,max_length=24000)
    output_schema:dict=Field(default_factory=dict,alias="schema")
    checks:list[dict]=Field(min_length=1,max_length=10)
    @model_validator(mode="after")
    def rules(self):
        ordinary=[c for c in self.checks if c.get("kind")!="named_check"]
        for rule in self.checks:
            if rule.get("kind")=="named_check" and (not isinstance(rule.get("value"),str) or not 1<=len(rule["value"])<=80): raise ValueError("Named evaluation check needs an owner-configured check name")
        AgentSpec(id="check",name="Check",role="Check",output_schema=self.output_schema,validators=ordinary)
        return self

class EvalVariant(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    architecture_id:str=Field(max_length=80)
    route:Route=Field(default_factory=Route)
    instructions:str=Field(default="",max_length=4000)

class EvalInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    tasks:list[EvalTask]=Field(min_length=1,max_length=12)
    variants:list[EvalVariant]=Field(min_length=1,max_length=6)
    mission:dict=Field(default_factory=dict)
    @model_validator(mode="after")
    def unique_names(self):
        if len({v.name for v in self.variants})!=len(self.variants): raise ValueError("Variant names must be unique")
        RunInput.model_validate({**self.mission,"task":self.tasks[0].task,"architecture_id":self.variants[0].architecture_id})
        return self

class FullBackupInput(BaseModel):
    password:str=Field(min_length=12,max_length=1024)
    project_ids:list[str]=Field(default_factory=list,max_length=20)

class RestoreInput(BaseModel):
    path:str=Field(min_length=1,max_length=1500)
    password:str=Field(min_length=12,max_length=1024)
    target:str=Field(min_length=1,max_length=1500)
    mapping:dict[str,str]=Field(default_factory=dict)
    reviewed_sha256:str=Field(default="",max_length=64)

class RemoteInput(BaseModel):
    agent_id:str=Field(max_length=150)
    device_id:str=Field(max_length=80)
    name:str=Field(max_length=80)


def install_nextgen(app,root):
    router=APIRouter(prefix="/api/next")
    def job(kind,runner,*args):
        id=uid(kind+"_")
        app.state.store.put(kind,id,dict(id=id,status="running",created_at=time.time()))
        async def execute():
            try:
                result=await runner(*args)
                app.state.store.patch(kind,id,dict(status="completed",result=result,finished_at=time.time()))
            except asyncio.CancelledError:
                app.state.store.patch(kind,id,dict(status="cancelled"));raise
            except Exception as error:
                app.state.store.patch(kind,id,dict(status="failed",error=str(error) if isinstance(error,RouteError) else "Operation failed; inspect runtime and configuration"))
        app.state.feature_tasks[id]=asyncio.create_task(execute())
        return dict(id=id,status="running")

    @router.get("")
    async def overview():
        store=app.state.store
        return dict(runtimes=app.state.runtimes.public(),catalog=app.state.intelligence.catalog(),questions=store.all("question")[-100:],
            browser_sessions=store.all("browser_session")[-100:],browser_actions=store.all("browser_action")[-200:],browser_settings=store.get("settings","browser",{}),
            outcomes=store.all("outcome")[-100:],evaluations=store.all("evaluation_job")[-30:],
            index_jobs=store.all("index_job")[-20:],browser_jobs=store.all("browser_job")[-10:],remote_jobs=[{k:v for k,v in j.items() if k not in {"payload","messages","lease_hash","execution_result"}} for j in store.all("job") if j.get("kind")=="execution"][-50:],
            indices=[dict(project_id=i["id"],model=i["model"],chunks=len(i["rows"]),indexed_at=i["indexed_at"]) for i in store.all("semantic_index")],
            scheduler=store.get("settings","scheduler",{}),backups=store.all("full_backup")[-20:])

    @router.get("/catalog")
    async def catalog(query:str=""): return app.state.intelligence.catalog(query[:200])

    @router.post("/catalog/refresh")
    async def refresh_catalog(): return await app.state.intelligence.refresh_catalog()

    @router.post("/runtimes")
    async def add_runtime(form:GGUFInput):
        try: return app.state.runtimes.add(form.model_dump())
        except (OSError,ValueError) as error: raise RouteError("Cannot import model or executable path") from error

    @router.post("/runtimes/{id}/start")
    async def start_runtime(id:str):
        async with app.state.providers.local_lock: return await app.state.runtimes.start(id)

    @router.post("/runtimes/{id}/probe")
    async def probe_runtime(id:str):
        started=time.monotonic();text=""
        stream=app.state.runtimes.infer(id,[dict(role="user",content='Reply only with JSON {"ok":true}.')],100,{})
        try:
            async for chunk in stream: text+=chunk
        finally: await stream.aclose()
        try: passed=json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())=={"ok":True}
        except ValueError: passed=False
        key="runtime:"+id
        return app.state.store.put("capability",key,dict(id=key,model=id,json=passed,streaming=True,latency_ms=round((time.monotonic()-started)*1000),context_tokens=app.state.store.get("settings","budgets")["context"],context_source="Owner allocation",checked_at=time.time()))

    @router.delete("/runtimes/{id}")
    async def remove_runtime(id:str):
        async with app.state.providers.local_lock: await app.state.runtimes.stop(id)
        app.state.store.delete("runtime",id);return dict(deleted=True)

    @router.post("/runtimes/{id}/stop")
    async def stop_runtime(id:str):
        async with app.state.providers.local_lock: await app.state.runtimes.stop(id)
        return dict(stopped=True)

    @router.put("/projects/{id}/access")
    async def project_access(id:str,form:ProjectAccess):
        from .browser_agents import origin_of
        if not app.state.store.get("project",id): raise HTTPException(404,"Project not found")
        sites=[dict(origin=origin_of(g.origin),interact=g.interact) for g in form.browser_sites]
        for device in form.execution_devices:
            if not app.state.store.get("device",device): raise RouteError("Selected execution worker no longer exists")
        return app.state.store.patch("project",id,dict(browser_sites=sites,execution_devices=form.execution_devices))

    @router.put("/devices/{id}/execution")
    async def approve_execution(id:str,body:dict):
        if not isinstance(body.get("approved"),bool): raise RouteError("Supply an explicit execution approval")
        device=app.state.store.get("device",id)
        if not device or not device.get("approved") or device.get("revoked"): raise RouteError("Approve this private worker before granting execution")
        return app.state.store.patch("device",id,dict(execution_approved=body["approved"]))

    @router.put("/browser/settings")
    async def browser_settings(form:BrowserSettings):
        if form.executable and (not form.trusted or not Path(form.executable).is_file()): raise RouteError("Explicitly trust an existing headless Chromium executable")
        await app.state.browser_agents.stop()
        return app.state.store.put("settings","browser",form.model_dump())

    @router.post("/browser/install")
    async def install_browser(): return job("browser_job",app.state.browser_agents.install)

    @router.get("/browser/status")
    async def browser_status():
        from playwright._impl._driver import compute_driver_executable
        executable,cli=compute_driver_executable()
        return dict(driver_available=Path(executable).is_file() and Path(cli).is_file(),driver_path=executable)

    @router.post("/browser/{agent_id}/action")
    async def browser_action(agent_id:str,form:BrowserAction):
        agent=app.state.store.get("agent",agent_id)
        if not agent: raise HTTPException(404,"Agent not found")
        run=app.state.store.get("run",agent["run_id"]);project=app.state.store.get("project",run.get("project_id",""))
        if not project: raise RouteError("Select a project for this agent")
        return await app.state.browser_agents.action(project,run,agent,form.action,form.arguments)

    @router.post("/browser/{agent_id}/close")
    async def close_browser(agent_id:str): await app.state.browser_agents.close(agent_id);return dict(closed=True)

    @router.get("/browser/screenshots/{id}")
    async def screenshot(id:str):
        row=app.state.store.get("browser_action",id)
        if not row or not row.get("screenshot"): raise HTTPException(404,"Screenshot not found")
        path=Path(row["screenshot"]).resolve()
        if not path.is_relative_to(Path(root).resolve()/"browser-shots"): raise RouteError("Invalid screenshot path")
        return dict(image="data:image/png;base64,"+base64.b64encode(path.read_bytes()).decode())

    @router.post("/questions/{id}/answer")
    async def answer_question(id:str,form:QuestionAnswer): return app.state.clarifications.answer(id,form.answer,form.cancel)

    @router.post("/index")
    async def build_index(form:IndexInput): return job("index_job",app.state.intelligence.index,form.project_id,form.model)

    @router.post("/search")
    async def semantic_search(form:SearchInput): return await app.state.intelligence.search(form.project_id,form.query)

    @router.post("/evaluations")
    async def evaluate(form:EvalInput):
        if any(not app.state.store.get("architecture",v.architecture_id) for v in form.variants): raise RouteError("Evaluation architecture not found")
        id=uid("eval_");record=dict(id=id,status="running",form=form.model_dump(by_alias=True),rows=[],created_at=time.time())
        app.state.store.put("evaluation_job",id,record)
        app.state.feature_tasks[id]=asyncio.create_task(app.state.evaluations.run(id,form.model_dump(by_alias=True)))
        return record

    @router.post("/jobs/{id}/cancel")
    async def cancel_job(id:str):
        task=app.state.feature_tasks.get(id)
        if task: task.cancel();await asyncio.gather(task,return_exceptions=True)
        return dict(cancelled=bool(task))

    @router.post("/remote")
    async def remote_execute(form:RemoteInput):
        agent=app.state.store.get("agent",form.agent_id)
        if not agent: raise HTTPException(404,"Agent not found")
        run=app.state.store.get("run",agent["run_id"])
        if "remote.execute" not in app.state.workspace.permissions(run,agent): raise RouteError("Grant this agent the remote execution tool first")
        return job("remote_job",app.state.workspace.execute,run,agent,dict(tool="remote.execute",arguments=dict(device_id=form.device_id,name=form.name)))

    @router.post("/backup")
    async def full_backup(form:FullBackupInput):
        from .full_backup import create_full_backup
        active=[*app.state.engine.tasks.values(),*app.state.engine.chat_tasks.values(),*app.state.feature_tasks.values(),*app.state.conversion_tasks.values(),*app.state.download_tasks.values()]
        if any(not task.done() for task in active) or app.state.voice_lock.locked() or any(j["status"] in {"queued","leased"} for j in app.state.store.all("job")):
            raise RouteError("Pause active missions, chats and background work before taking a consistent full backup")
        app.state.backup_active=True
        previous_automation=app.state.automation.enabled;app.state.automation.enabled=False
        task=asyncio.create_task(asyncio.to_thread(create_full_backup,root,app.state.store,form.password,list(dict.fromkeys(form.project_ids))))
        try:
            try: result=await asyncio.shield(task)
            except asyncio.CancelledError:
                await task;raise
        finally:
            app.state.backup_active=False;app.state.automation.enabled=previous_automation
        id=uid("backup_");return app.state.store.put("full_backup",id,dict(id=id,**result,created_at=time.time()))

    @router.get("/backup/{id}/download")
    async def download_backup(id:str):
        from fastapi.responses import FileResponse
        row=app.state.store.get("full_backup",id)
        if not row: raise HTTPException(404,"Backup not found")
        path=Path(row["path"]).resolve()
        if not path.is_relative_to(Path(root).resolve()/"backups"): raise RouteError("Invalid backup path")
        return FileResponse(path,filename=path.name,media_type="application/octet-stream")

    def check_restore(form):
        target=Path(form.target).resolve();active=Path(root).resolve()
        if target==active or active.is_relative_to(target) or target.is_relative_to(active): raise RouteError("Restore into a separate, empty data directory while this coordinator remains open")
        active_roots=[Path(p["root"]).resolve() for p in app.state.store.all("project")]
        if any(target==p or target.is_relative_to(p) or p.is_relative_to(target) for p in active_roots): raise RouteError("Restore directory cannot overlap an active project")
        for path in form.mapping.values():
            if not path: continue
            mapped=Path(path).resolve()
            if any(mapped==p or mapped.is_relative_to(p) or p.is_relative_to(mapped) for p in [active,*active_roots]): raise RouteError("Project remapping cannot overlap this active workspace")

    @router.post("/restore/preview")
    async def restore_preview(form:RestoreInput):
        from .full_backup import preview_full
        check_restore(form)
        return await asyncio.to_thread(preview_full,form.path,form.password,form.target,form.mapping)

    @router.post("/restore")
    async def restore(form:RestoreInput):
        from .full_backup import restore_full
        check_restore(form)
        if len(form.reviewed_sha256)!=64: raise RouteError("Preview and review the restore paths first")
        return await asyncio.to_thread(restore_full,form.path,form.password,form.target,form.mapping,form.reviewed_sha256)

    app.include_router(router)
