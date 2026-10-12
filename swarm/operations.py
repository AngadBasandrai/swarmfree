"""Owner-authenticated command center and 0.6 project/experiment controls."""
import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
import secrets
import time
from typing import Literal
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field, model_validator
from .schema import RunInput
from .store import uid
from .providers import RouteError
from .project_ops import CampaignInput, KnowledgeInput
from .adaptive_ops import FeedbackInput, EnergyInput
from .notebooks import NotebookInput
from .voice import VoiceConfig, VoiceChunk, transcribe, validate_voice_config
from .training import DatasetInput, TrainingInput
from .compatibility import CompatibilityInput, CASES
from .tool_apps import CSP
from .teamwork import MemberInput, token_hash


class PreflightInput(BaseModel):
    mission: RunInput
    architecture_ids: list[str] = Field(min_length=1, max_length=10)

class ReviewInput(BaseModel):
    accepted: bool
    note: str = Field(default="", max_length=2000)

class HashInput(BaseModel):
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

class PanelInput(BaseModel):
    project_id: str = Field(max_length=80)
    extension_id: str = Field(max_length=80)
    tool_name: str = Field(max_length=160)

class PanelCall(BaseModel):
    tool_name: str = Field(max_length=160)
    arguments: dict = Field(default_factory=dict)

class EnableInput(BaseModel):
    enabled: bool

class SendInput(BaseModel):
    agent_id: str = Field(max_length=160)
    text: str = Field(min_length=1, max_length=12000)

class PromoteInput(BaseModel):
    evaluation_id: str = Field(max_length=100)
    profile_id: str = Field(max_length=100)


def install_operations(app):
    router = APIRouter(prefix="/api/ops")

    def launch(kind, runner):
        s = app.state.store
        if sum(j.get("status") == "running" for j in s.all(kind)) >= 2: raise RouteError("Finish or cancel an active job first")
        id = uid(kind+"_"); s.put(kind, id, dict(id=id, status="running", created_at=time.time()))
        async def execute():
            try:
                result = await runner(id)
                s.patch(kind, id, dict(status="completed", result=result, finished_at=time.time()))
            except asyncio.CancelledError:
                s.patch(kind, id, dict(status="cancelled")); raise
            except Exception as e:
                s.patch(kind, id, dict(status="failed", error=str(e)[:2000] if isinstance(e, RouteError) else "Operation failed; inspect its prerequisites and trusted runner"))
        app.state.feature_tasks[id] = asyncio.create_task(execute())
        return s.get(kind, id)

    @router.get("")
    async def status():
        s = app.state.store
        runs = s.all("run")
        attention = [dict(id=r["id"], task=r["task"][:180], project_id=r.get("project_id"), status=r["status"], error=r.get("error", "")) for r in runs if r["status"] in {"interrupted", "paused", "queued", "budget_exceeded"}][-30:]
        return dict(running=[dict(id=r["id"], task=r["task"][:180], project_id=r.get("project_id"), status=r["status"], usage=app.state.engine.budget.usage(r)) for r in runs if r["status"] == "running"],
            attention=attention, questions=[q for q in s.all("question") if q["status"] == "pending"][-30:],
            reviews=[dict(id=a["id"], path=a["path"], project_id=a["project_id"], agent_id=a["agent_id"]) for a in s.all("artifact") if a["status"] == "staged"][-40:],
            campaigns=[app.state.project_ops.update(c["id"]) for c in s.all("campaign")],
            knowledge_projects=list({k["project_id"] for k in s.all("knowledge")}), notebooks=s.all("notebook"), notebook_jobs=s.all("notebook_job")[-20:],
            feedback=s.all("route_feedback"), outcomes=s.all("outcome")[-100:],
            voice=s.get("settings", "voice", dict(argv=[], trusted=False)),
            energy_policy=s.get("settings", "energy_policy", EnergyInput().model_dump()), energy=s.get("settings", "energy_observation", {}), energy_days=s.all("energy_day"),
            datasets=s.all("training_dataset"), training_jobs=s.all("training_job")[-20:], active_adapters=s.all("adapter_active"),
            compatibility=s.all("compatibility_job")[-20:], reports=s.all("compatibility_report")[-100:], compatibility_cases=CASES)

    @router.post("/campaigns")
    async def create_campaign(form:CampaignInput): return app.state.project_ops.create(form)

    @router.post("/campaigns/{id}/launch")
    async def launch_campaign(id:str): return app.state.project_ops.launch(id)

    @router.post("/campaigns/{id}/{action}")
    async def campaign_control(id:str, action:str): return await app.state.project_ops.control(id, action)

    @router.post("/campaigns/{id}/milestones/{milestone}/review")
    async def campaign_review(id:str, milestone:str, form:ReviewInput): return app.state.project_ops.review(id, milestone, form.accepted, form.note)

    @router.post("/preflight")
    async def preflight(form:PreflightInput):
        from .hardware import inventory
        local = await inventory(app.state.providers.ollama)
        local["observed_at"] = time.time()
        return await asyncio.to_thread(app.state.project_ops.preflight, form.mission, form.architecture_ids, local)

    @router.post("/feedback")
    async def feedback(form:FeedbackInput): return app.state.adaptive_ops.feedback(form)

    @router.get("/knowledge/{project_id}")
    async def knowledge(project_id:str): return app.state.project_ops.knowledge_list(project_id)

    @router.post("/knowledge")
    async def save_knowledge(form:KnowledgeInput): return app.state.project_ops.knowledge(form)

    @router.put("/knowledge/{id}")
    async def update_knowledge(id:str, form:KnowledgeInput): return app.state.project_ops.knowledge(form, id)

    @router.get("/knowledge/{id}/history")
    async def knowledge_history(id:str): return [r for r in app.state.store.all("knowledge_revision") if r["id"] == id]

    @router.put("/energy")
    async def energy(form:EnergyInput): return app.state.store.put("settings", "energy_policy", form.model_dump())

    @router.put("/voice")
    async def voice_config(form:VoiceConfig): return app.state.store.put("settings", "voice", validate_voice_config(form))

    @router.post("/voice/chunk")
    async def voice_chunk(form:VoiceChunk):
        async with app.state.voice_lock: return await transcribe(app.state.store, app.state.workspace.root, form)

    @router.post("/voice/send")
    async def voice_send(form:SendInput):
        if not app.state.store.get("agent", form.agent_id): raise HTTPException(404, "Agent not found")
        app.state.engine.steer(form.agent_id, form.text); return dict(sent=True)

    @router.put("/apps/{extension_id}")
    async def apps_enabled(extension_id:str, form:EnableInput):
        app.state.extensions.config(extension_id,"mcp")
        return app.state.store.patch("extension", extension_id, dict(apps_enabled=form.enabled))

    @router.post("/apps/inspect")
    async def apps_inspect(form:PanelInput): return await app.state.tool_apps.inspect(form.project_id, form.extension_id, form.tool_name)

    @router.get("/apps/view/{id}")
    async def apps_document(id:str):
        row = app.state.tool_apps.panel(id)
        return HTMLResponse(row["html"], headers={"Content-Security-Policy":CSP, "Cache-Control":"no-store", "Permissions-Policy":"camera=(), microphone=(), geolocation=(), clipboard-read=(), clipboard-write=()"})

    @router.post("/apps/{id}/call")
    async def apps_call(id:str, form:PanelCall): return await app.state.tool_apps.call(id, form.tool_name, form.arguments)

    @router.post("/notebooks")
    async def notebook_create(form:NotebookInput): return app.state.notebooks.save(form)

    @router.put("/notebooks/{id}")
    async def notebook_update(id:str, form:NotebookInput): return app.state.notebooks.save(form,id)

    @router.get("/notebooks/{id}/export")
    async def notebook_export(id:str): return app.state.notebooks.export(id)

    @router.post("/notebooks/{id}/run")
    async def notebook_run(id:str, form:HashInput): return launch("notebook_job",lambda job:app.state.notebooks.run(id,form.sha256))

    @router.post("/datasets")
    async def dataset_create(form:DatasetInput): return app.state.training.dataset(form)

    @router.post("/datasets/{id}/export")
    async def dataset_export(id:str, form:HashInput): return app.state.training.review(id,form.sha256)

    @router.post("/training")
    async def train(form:TrainingInput): return launch("training_job",lambda id:app.state.training.run(id,form))

    @router.post("/training/{id}/promote")
    async def promote(id:str, form:PromoteInput): return app.state.training.promote(id,form.evaluation_id,form.profile_id)

    @router.post("/training/rollback/{project_id}")
    async def rollback(project_id:str): return app.state.training.rollback(project_id)

    @router.post("/compatibility")
    async def compatibility(form:CompatibilityInput):
        if form.mission.route.kind == "auto" or form.mission.route.kind == "acp": raise RouteError("Select one explicit model route for a reproducible compatibility report")
        if form.mission.public_allowed: raise RouteError("Use local, private or direct-provider inference for acceptance fixtures")
        return launch("compatibility_job",lambda id:app.state.compatibility.run(id,form))

    @router.post("/compatibility/import")
    async def report_import(form:dict): return app.state.compatibility.import_report(form)

    @router.post("/jobs/{id}/cancel")
    async def cancel(id:str):
        task = app.state.feature_tasks.get(id)
        if task: task.cancel(); await asyncio.gather(task, return_exceptions=True)
        return dict(cancelled=bool(task))

    @router.post("/companion/pair")
    async def companion_pair(form:MemberInput):
        from .private_listener import load_settings
        config = load_settings(app.state.workspace.root)
        if not config.enabled: raise RouteError("Enable the private TLS listener with a reachable HTTPS origin first")
        cert = Path(config.cert_file)
        if cert.stat().st_size > 16000: raise RouteError("Certificate is too large for a pairing bundle")
        import re
        from cryptography import x509
        from cryptography.hazmat.primitives import serialization
        blocks = re.findall(r"-----BEGIN CERTIFICATE-----.*?-----END CERTIFICATE-----",cert.read_text(encoding="utf-8"),re.S)
        try:
            pem = "".join(x509.load_pem_x509_certificate(block.encode()).public_bytes(serialization.Encoding.PEM).decode() for block in blocks)
        except ValueError as error: raise RouteError("Use a valid PEM public certificate chain") from error
        if not pem: raise RouteError("Use a PEM public certificate chain")
        member = app.state.team.add(form)
        invite = secrets.token_urlsafe(32); id = uid("phone_")
        app.state.vault.set(id,member["token"],True)
        app.state.store.put("companion_invite",id,dict(id=id,member_id=member["member"]["id"],invite_hash=token_hash(invite),expires_at=time.time()+600,redeemed=False))
        bundle = dict(format="swarm-companion-v1",origin=config.public_origin,invite_id=id,invite=invite,certificate=pem,expires_at=time.time()+600)
        import qrcode
        from qrcode.exceptions import DataOverflowError
        encoded = ""; note = ""
        try:
            image = qrcode.make(json.dumps(bundle,separators=(",",":")),error_correction=qrcode.constants.ERROR_CORRECT_L)
            buffer=io.BytesIO();image.save(buffer,format="PNG");encoded=base64.b64encode(buffer.getvalue()).decode()
        except (DataOverflowError, ValueError):
            note = "This certificate bundle is too large for a QR code. Export the pairing JSON and import or paste it on the phone."
        return dict(bundle=bundle,qr_png=encoded,note=note,member=member["member"],certificate_sha256=hashlib.sha256(pem.encode()).hexdigest())

    app.include_router(router)
