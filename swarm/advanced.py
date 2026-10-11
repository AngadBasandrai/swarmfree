"""Owner APIs for 0.3 context, budgets, debugging, automation and portable packages."""
import asyncio
import hashlib
import json
from pathlib import Path
import time
from fastapi import APIRouter,HTTPException
from pydantic import BaseModel,Field
from typing import Literal
from .automation import AutomationInput
from .schema import MissionLimits
from .providers import RouteError
from .store import uid


class HandoffInput(BaseModel):
    content: str=Field(min_length=1,max_length=6000)

class ReplayInput(BaseModel):
    node_id: str=Field(min_length=1,max_length=48)
    step: bool=True

class AttachmentInput(BaseModel):
    name: str=Field(min_length=1,max_length=160)
    mime: str=Field(max_length=80)
    base64: str=Field(min_length=4,max_length=23000000)

class ConversionSettings(BaseModel):
    ocr_argv: list[str]=Field(default_factory=list,max_length=30)
    transcribe_argv: list[str]=Field(default_factory=list,max_length=30)
    pdf_render_argv: list[str]=Field(default_factory=list,max_length=30)
    trusted: bool=False

class TuningInput(BaseModel):
    models: list[str]=Field(min_length=1,max_length=3)
    contexts: list[int]=Field(default_factory=lambda:[2048,4096],min_length=1,max_length=3)

class ResidencyInput(BaseModel):
    keep_alive_seconds: int=Field(default=300,ge=0,le=3600)

class ClaimInput(BaseModel):
    source_id: str=Field(default="",max_length=80)
    quote: str=Field(default="",max_length=4000)
    judgment: Literal["supported","contradicted","uncertain","unsupported"]="uncertain"
    note: str=Field(default="",max_length=2000)
    conflicts: list[str]=Field(default_factory=list,max_length=10)

class PackageImport(BaseModel):
    package: dict
    reviewed_sha256: str=Field(min_length=64,max_length=64)

class RelayInput(BaseModel):
    url: str=Field(min_length=1,max_length=500)
    operator_key: str=Field(min_length=1,max_length=4096)
    ephemeral: bool=False

class QRInput(BaseModel):
    bundle: str=Field(min_length=1,max_length=16000)


def install_advanced(app,root):
    router=APIRouter(prefix="/api")
    def record(kind,id):
        value=app.state.store.get(kind,id)
        if not value: raise HTTPException(404,kind.capitalize()+" not found")
        return value

    @router.get("/advanced")
    async def dashboard():
        store=app.state.store
        return dict(version="0.4.0",automations=store.all("automation"),automation_paused=not app.state.automation.enabled,
            triggers=store.all("trigger")[-80:],notifications=store.all("notification")[-100:],packages=store.all("package"),
            tuning=store.all("tuning")[-12:],tuning_jobs=store.all("tuning_job")[-10:],conversion_jobs=store.all("conversion_job")[-20:],claims=store.all("claim")[-200:],
            worktrees=store.all("worktree")[-60:],relay_links=store.all("relay_link"),receipts=store.all("receipt")[-100:],
            attachments=[{k:v for k,v in a.items() if k not in {"path","text"}} for a in store.all("attachment")[-100:]],
            residency=store.get("settings","residency",dict(keep_alive_seconds=300)),media_settings=store.get("settings","media",{}),
            quotas=[dict(scope=q["quota_group"],label=q["label"],local_remaining=max(0,q["requests_per_minute"]-q["recent_requests"]),
                cooldown_seconds=q["cooldown_seconds"],observed=q.get("observed_quota",{})) for q in app.state.providers.public()])

    @router.get("/agents/{id}/context")
    async def context(id:str):
        agent=record("agent",id)
        run=record("run",agent["run_id"])
        payload=app.state.engine.messages(run,agent)
        return dict(context=app.state.store.get("context",id),messages=payload,handoff=app.state.store.get("handoff",id))

    @router.put("/agents/{id}/handoff")
    async def handoff(id:str,form:HandoffInput):
        agent=record("agent",id)
        return app.state.store.put("handoff",id,dict(id=id,content=form.content,edited_by="owner",updated_at=time.time()))

    @router.get("/runs/{id}/budget")
    async def usage(id:str): return app.state.engine.budget.usage(record("run",id))

    @router.put("/runs/{id}/limits")
    async def limits(id:str,form:MissionLimits):
        run=record("run",id)
        root_run=app.state.engine.budget.root(run)
        if root_run["status"]=="running": raise HTTPException(409,"Pause the root mission before changing its limits")
        return app.state.store.patch("run",root_run["id"],dict(limits=form.model_dump()))

    @router.get("/runs/{id}/debugger")
    async def debugger(id:str):
        run=record("run",id)
        store=app.state.store
        family={id}|{r["id"] for r in store.all("run") if r.get("root_run_id")==id}
        requests=[{k:v for k,v in r.items() if k!="messages"} for r in store.all("request") if r["run_id"] in family]
        events=sorted([e for member in family for e in store.events(member,limit=1000)],key=lambda e:e["seq"])[-1000:]
        return dict(run_id=id,requests=requests,calls=[c for c in store.all("call") if c["run_id"] in family],
            operations=[o for o in store.all("operation") if o["run_id"] in family],events=events,budget=app.state.engine.budget.usage(run))

    @router.get("/requests/{id}")
    async def request_payload(id:str): return record("request",id)

    @router.get("/runs/{id}/trace")
    async def trace(id:str):
        run=record("run",id)
        root_id=run.get("root_run_id",id)
        trace_id=hashlib.sha256(root_id.encode()).hexdigest()[:32]
        family={id}|{r["id"] for r in app.state.store.all("run") if r.get("root_run_id")==id}
        spans=[]
        for call in app.state.store.all("call"):
            if call["run_id"] not in family: continue
            start=int(call.get("start_time",call["created_at"])*1e9)
            spans.append(dict(traceId=trace_id,spanId=hashlib.sha256(call.get("reservation_id",call["id"]).encode()).hexdigest()[:16],name="inference / "+call["kind"],kind=3,
                startTimeUnixNano=str(start),endTimeUnixNano=str(start+call["elapsed_ms"]*1_000_000),
                attributes=[dict(key="swarm.agent.id",value=dict(stringValue=call["agent_id"])),dict(key="swarm.route",value=dict(stringValue=call["route"]))],
                status=dict(code=1 if call["completed"] else 2)))
        for receipt in app.state.store.all("receipt"):
            if receipt.get("trace_id")!=trace_id: continue
            end=int(receipt["created_at"]*1e9)
            spans.append(dict(traceId=trace_id,spanId=hashlib.sha256(receipt["id"].encode()).hexdigest()[:16],parentSpanId=receipt.get("parent_span_id",""),name="worker inference",kind=2,
                startTimeUnixNano=str(end-receipt["elapsed_ms"]*1_000_000),endTimeUnixNano=str(end),
                attributes=[dict(key="swarm.device.id",value=dict(stringValue=receipt["device_id"]))],status=dict(code=1 if receipt["status"]=="done" else 2)))
        return dict(resourceSpans=[dict(resource=dict(attributes=[dict(key="service.name",value=dict(stringValue="swarm-workbench"))]),scopeSpans=[dict(scope=dict(name="swarm.engine",version="0.4.0"),spans=spans)])])

    @router.post("/runs/{id}/replay",status_code=201)
    async def replay(id:str,form:ReplayInput):
        run=record("run",id)
        if form.node_id not in {n["id"] for n in run["architecture"]["nodes"]}: raise HTTPException(400,"Unknown replay stage")
        await app.state.engine.pause(id)
        # Replaying creates new effect identities; it is an explicit owner action.
        return app.state.engine.branch(app.state.store.get("run",id),run["architecture"],run["route"],form.node_id,rerun=True,step=form.step)

    @router.post("/agents/{id}/worktree/collect")
    async def collect_worktree(id:str): return await app.state.isolation.collect(record("agent",id),app.state.workspace)

    @router.post("/attachments",status_code=201)
    async def attachment(form:AttachmentInput):
        value=await asyncio.to_thread(app.state.attachments.add,form.name,form.mime,form.base64)
        return {k:v for k,v in value.items() if k!="path"}

    @router.post("/attachments/{id}/{kind}",status_code=202)
    async def convert(id:str,kind:str):
        record("attachment",id)
        if kind not in {"ocr","transcribe"}: raise HTTPException(400,"Unknown conversion")
        if id in app.state.conversion_tasks and not app.state.conversion_tasks[id].done(): raise HTTPException(409,"This attachment already has an active conversion")
        job_id=uid("conversion_")
        job=app.state.store.put("conversion_job",job_id,dict(id=job_id,attachment_id=id,kind=kind,status="running",created_at=time.time()))
        async def execute():
            try:
                await app.state.attachments.convert(id,kind)
                app.state.store.patch("conversion_job",job_id,dict(status="completed"))
            except asyncio.CancelledError:
                app.state.store.patch("conversion_job",job_id,dict(status="cancelled"))
                raise
            except Exception as error:
                app.state.store.patch("conversion_job",job_id,dict(status="failed",error=str(error) if isinstance(error,RouteError) else "Conversion failed; check the configured command"))
        app.state.conversion_tasks[id]=asyncio.create_task(execute())
        return job

    @router.put("/media-settings")
    async def media_settings(form:ConversionSettings):
        if any(not arg or len(arg)>2000 for argv in [form.ocr_argv,form.transcribe_argv,form.pdf_render_argv] for arg in argv): raise HTTPException(400,"Invalid executable/argument array")
        return app.state.store.put("settings","media",form.model_dump())

    @router.post("/tuning",status_code=202)
    async def start_tuning(form:TuningInput):
        if any(c<512 or c>32768 for c in form.contexts): raise HTTPException(400,"Context must be between 512 and 32768")
        if any(not t.done() for t in app.state.feature_tasks.values()): raise HTTPException(409,"A benchmark is already running")
        from .tuning import tune
        id=uid("tuning_job_")
        job=app.state.store.put("tuning_job",id,dict(id=id,status="running",created_at=time.time()))
        async def execute():
            try:
                result=await tune(app.state.store,app.state.providers,form.models,form.contexts)
                app.state.store.patch("tuning_job",id,dict(status="completed",result_id=result["id"]))
            except asyncio.CancelledError:
                app.state.store.patch("tuning_job",id,dict(status="cancelled"))
                raise
            except Exception:
                app.state.store.patch("tuning_job",id,dict(status="failed",error="Benchmark failed; check installed models and Ollama"))
        app.state.feature_tasks[id]=asyncio.create_task(execute())
        return job

    @router.put("/residency")
    async def residency(form:ResidencyInput): return app.state.store.put("settings","residency",form.model_dump())

    @router.post("/runs/{id}/claims")
    async def claims(id:str): return dict(claims=app.state.evidence.extract(id))

    @router.get("/sources/{id}")
    async def source_text(id:str): return record("source",id)

    @router.put("/claims/{id}")
    async def review_claim(id:str,form:ClaimInput): return app.state.evidence.review(id,**form.model_dump())

    @router.post("/packages/preview")
    async def package_preview(value:dict):
        from .packages import preview
        return preview(value)

    @router.post("/packages/import",status_code=201)
    async def package_import(form:PackageImport):
        from .packages import preview,install
        if preview(form.package)["sha256"]!=form.reviewed_sha256: raise HTTPException(409,"Package changed after preview; review it again")
        return install(app.state.store,form.package)

    @router.post("/automations",status_code=201)
    async def add_automation(form:AutomationInput): return app.state.automation.add(form)

    @router.post("/automations/control/{action}")
    async def automation_control(action:str):
        if action not in {"pause","resume"}: raise HTTPException(404,"Unknown automation action")
        app.state.automation.enabled=action=="resume"
        app.state.store.put("settings","automation",dict(paused=action=="pause"))
        return dict(paused=action=="pause")

    @router.post("/automations/{id}/{action}")
    async def automation_action(id:str,action:str):
        value=record("automation",id)
        if action not in {"enable","disable","trigger"}: raise HTTPException(404,"Unknown automation action")
        if action=="trigger": return app.state.automation.trigger(value,"Manual trigger") or dict(already_pending=True)
        return app.state.store.patch("automation",id,dict(enabled=action=="enable"))

    @router.delete("/automations/{id}")
    async def delete_automation(id:str):
        record("automation",id)
        app.state.store.delete("automation",id)
        return dict(deleted=True)

    @router.post("/triggers/{id}/{action}")
    async def trigger_action(id:str,action:str):
        trigger=record("trigger",id)
        if trigger["status"]!="pending": raise HTTPException(409,"Trigger is no longer pending")
        if action=="approve": return app.state.automation.launch(id)
        if action=="dismiss": return app.state.store.patch("trigger",id,dict(status="dismissed"))
        raise HTTPException(404,"Unknown trigger action")

    @router.post("/notifications/{id}/read")
    async def read_notice(id:str):
        record("notification",id)
        return app.state.store.patch("notification",id,dict(read=True))

    @router.post("/relay-links",status_code=201)
    async def relay_link(form:RelayInput):
        try: return await app.state.relay_links.add(form.url,form.operator_key,form.ephemeral)
        except Exception as error:
            if isinstance(error,RouteError): raise
            raise HTTPException(400,"Cannot provision this relay; check URL and operator credential") from error

    @router.delete("/relay-links/{id}")
    async def revoke_relay(id:str):
        record("relay_link",id)
        await app.state.relay_links.remove(id)
        return dict(revoked=True)

    @router.post("/pairing/qr")
    async def qr(form:QRInput):
        from .pairing import qr_image
        return dict(image=qr_image(form.bundle))

    @router.get("/devices/{id}/diagnostics")
    async def diagnose(id:str):
        device=next((d for d in app.state.pool.public() if d["id"]==id),None)
        if not device: raise HTTPException(404,"Device not found")
        return dict(device=device,seconds_since_heartbeat=round(time.time()-device["last_seen"]),
            active_jobs=[{k:v for k,v in j.items() if k not in {"messages","lease_hash","text"}} for j in app.state.store.all("job") if j["device_id"]==id and j["status"] in {"queued","leased"}],
            checks=dict(approved=device["approved"],revoked=device["revoked"],recent_heartbeat=device["online"],advertised_models=len(device["models"])))
    app.include_router(router)
