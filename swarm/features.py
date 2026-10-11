"""Authenticated feature API; installed inside the coordinator's owner boundary."""
import asyncio
from pathlib import Path
import re
import time
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from .schema import RunInput, Route, Architecture
from .store import uid
from .workspace import TOOLS
from .providers import RouteError, validate_base_url
from .maintenance import create_backup


class ProjectInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    root: str = Field(min_length=1, max_length=1000)
    tools: list[str] = Field(default_factory=list, max_length=40)
    checks: dict[str, list[str]] = Field(default_factory=dict, max_length=12)
    search_url: str = Field(default="", max_length=500)
    container_image: str = Field(default="",max_length=240)


class NoteInput(BaseModel):
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=24000)
    tags: list[str] = Field(default_factory=list, max_length=10)


class BranchInput(BaseModel):
    architecture_id: str = ""
    route: Route | None = None
    through: str = Field(default="", max_length=48)


class LabInput(BaseModel):
    task: str = Field(min_length=1, max_length=24000)
    architecture_ids: list[str] = Field(min_length=1, max_length=4)
    mode: str = "demo"
    route: Route = Field(default_factory=Route)
    free_only: bool = True
    project_id: str = ""
    rubric: str = Field(default="", max_length=4000)


class ExtensionInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: str = Field(pattern="^(mcp|acp)$")
    transport: str = Field(default="stdio", pattern="^(stdio|http)$")
    argv: list[str] = Field(default_factory=list, max_length=30)
    url: str = Field(default="", max_length=500)
    token: str = Field(default="", max_length=4096)
    ephemeral: bool = False
    enabled: bool = False


class DoctorInput(BaseModel):
    kind: str = Field(pattern="^(local|profile)$")
    target: str = Field(min_length=1, max_length=240)
    vision: bool=False


class CertificateInput(BaseModel):
    hostname: str = Field(min_length=1, max_length=240, pattern=r"^[a-zA-Z0-9.:-]+$")


class ReleaseSettings(BaseModel):
    endpoint: str = Field(default="", max_length=500)
    public_key: str = Field(default="", max_length=2000)


class ProjectReference(BaseModel):
    project_id: str = Field(min_length=1, max_length=80)


class ScoreInput(BaseModel):
    score: float = Field(ge=0, le=5)


def install_features(app, root):
    router = APIRouter(prefix="/api")

    def checked_url(value):
        try: validate_base_url(value)
        except (ValueError, RouteError) as error: raise HTTPException(400, str(error)) from error

    def project(id):
        value = app.state.store.get("project", id)
        if not value: raise HTTPException(404, "Project not found")
        return value

    def validated_project(form):
        path = Path(form.root).expanduser().resolve()
        if not path.is_dir(): raise HTTPException(400, "Select an existing project directory")
        for name, argv in form.checks.items():
            if not re.fullmatch(r"[\w-]{1,48}", name) or not argv or len(argv) > 30 or any(not a or len(a) > 1000 for a in argv):
                raise HTTPException(400, "Checks need a short name and a bounded executable/argument list; no shell command strings")
        if form.search_url: checked_url(form.search_url)
        if form.container_image and not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9./:_@-]{0,240}",form.container_image):
            raise HTTPException(400,"Use an installed container image name or digest")
        allowed = set(TOOLS)
        for extension in app.state.store.all("extension"):
            if extension["kind"] == "acp": allowed.add("acp." + extension["id"])
            else: allowed.update("mcp." + extension["id"] + "." + t["name"] for t in extension.get("tools", []))
        if any(t not in allowed for t in form.tools): raise HTTPException(400, "Unknown tool; discover extension tools before granting them")
        return dict(form.model_dump(), root=str(path))

    @router.get("/features")
    async def features():
        store = app.state.store
        calls = store.all("call")
        labs = []
        for lab in store.all("lab"):
            runs = [r for r in store.all("run") if r.get("lab_id") == lab["id"]]
            rows = []
            for run in runs:
                run_calls = [c for c in calls if c["run_id"] == run["id"]]
                rows.append(dict(id=run["id"], architecture=run["architecture"]["name"], status=run["status"], mode=run["mode"],
                    elapsed_seconds=round(run.get("finished_at", time.time())-run["created_at"], 2), requests=len(run_calls),
                    estimated_tokens=sum(c["estimated_tokens"] for c in run_calls), score=run.get("evaluation_score"),
                    artifacts=sum(a["run_id"] == run["id"] for a in store.all("artifact"))))
            labs.append(dict(**lab, rows=rows))
        return dict(projects=store.all("project"), tools=TOOLS, extensions=store.all("extension"),
            capabilities=store.all("capability"), labs=labs[-20:], memory=store.all("memory")[-120:],
            artifacts=store.all("artifact")[-100:], operations=store.all("operation")[-100:],
            sources=[{k:v for k,v in s.items() if k != "content"} for s in store.all("source")[-100:]], calls=calls[-120:],
            backups=[dict(name=p.name, size=p.stat().st_size) for p in (Path(root)/"backups").glob("*.zip")])

    @router.post("/projects", status_code=201)
    async def add_project(form: ProjectInput):
        id = uid("project_")
        return app.state.store.put("project", id, dict(**validated_project(form), id=id, created_at=time.time()))

    @router.put("/projects/{id}")
    async def update_project(id: str, form: ProjectInput):
        existing = project(id)
        values = validated_project(form)
        if values["root"] != existing["root"] and any(r.get("project_id") == id and r["status"] in {"running", "queued"} for r in app.state.store.all("run")):
            raise HTTPException(409, "Pause this project's missions before changing its directory")
        return app.state.store.patch("project", id, values)

    @router.post("/projects/{id}/memory", status_code=201)
    async def add_memory(id: str, form: NoteInput):
        project(id)
        return app.state.workspace.remember(id, form.title, form.content, tags=form.tags)

    @router.get("/projects/{id}/memory")
    async def read_memory(id: str, query: str = ""):
        project(id)
        return dict(notes=app.state.workspace.memory(id, query, 40))

    @router.delete("/memory/{id}")
    async def delete_memory(id: str):
        app.state.store.delete("memory", id)
        return dict(deleted=True)

    @router.post("/artifacts/{id}/{action}")
    async def review_artifact(id: str, action: str):
        if action not in {"apply", "undo"}: raise HTTPException(404, "Unknown artifact action")
        return app.state.workspace.apply(id, undo=action == "undo")

    @router.post("/operations/{id}/retry")
    async def permit_retry(id: str):
        operation = app.state.store.get("operation", id)
        if not operation or operation["status"] != "interrupted":
            raise HTTPException(400, "Only an interrupted action needs explicit retry acknowledgement")
        agent = app.state.store.get("agent", operation["agent_id"], {})
        if agent.get("status") == "running": raise HTTPException(409, "Pause the agent before allowing a retry")
        return app.state.store.patch("operation", id, dict(status="failed", owner_allowed_retry=True))

    @router.post("/runs/{id}/branch", status_code=201)
    async def branch_run(id: str, form: BranchInput):
        run = app.state.store.get("run", id)
        if not run: raise HTTPException(404, "Run not found")
        if form.through and not any(n["id"] == form.through for n in run["architecture"]["nodes"]):
            raise HTTPException(400, "Branch point is not in this run")
        architecture = app.state.store.get("architecture", form.architecture_id) if form.architecture_id else run["architecture"]
        if not architecture: raise HTTPException(404, "Architecture not found")
        await app.state.engine.pause(id)
        return app.state.engine.branch(app.state.store.get("run", id), architecture, (form.route or Route.model_validate(run["route"])).model_dump(), form.through)

    @router.put("/agents/{id}/permissions")
    async def agent_permissions(id: str, tools: list[str]):
        agent = app.state.store.get("agent", id)
        if not agent: raise HTTPException(404, "Agent not found")
        run = app.state.store.get("run", agent["run_id"])
        available = project(run.get("project_id", ""))["tools"]
        if len(tools) > 40 or any(t not in available for t in tools): raise HTTPException(400, "Agent grants must be a subset of project permissions")
        return app.state.store.patch("agent", id, dict(tools=tools))

    @router.post("/stop")
    async def stop_all():
        app.state.automation.enabled=False
        app.state.store.put("settings","automation",dict(paused=True))
        await app.state.engine.stop()
        downloads = [*app.state.download_tasks.values(),*app.state.feature_tasks.values(),*app.state.conversion_tasks.values()]
        for task in downloads: task.cancel()
        await asyncio.gather(*downloads, return_exceptions=True)
        for run in app.state.store.all("run"):
            if run["status"] in {"running", "queued", "ready"}: app.state.store.patch("run", run["id"], dict(status="paused", error="Stopped by owner"))
        app.state.engine.queue_task = asyncio.create_task(app.state.engine.queue_loop())
        return dict(stopped=True)

    @router.post("/labs", status_code=201)
    async def create_lab(form: LabInput):
        id = uid("lab_")
        architectures = [app.state.store.get("architecture", a) for a in dict.fromkeys(["baseline", *form.architecture_ids])]
        if not all(architectures): raise HTTPException(400, "Unknown lab architecture")
        lab = app.state.store.put("lab", id, dict(id=id, task=form.task, rubric=form.rubric, created_at=time.time()))
        for architecture in architectures:
            run_form = RunInput(task=form.task, architecture_id=architecture["id"], route=form.route, mode=form.mode,
                project_id=form.project_id, free_only=form.free_only, lab_id=id, tools_enabled=False, low_quota=True)
            app.state.engine.create(run_form, architecture)
        return lab

    @router.put("/runs/{id}/score")
    async def score_run(id: str, form: ScoreInput):
        if not app.state.store.get("run", id): raise HTTPException(404, "Run not found")
        return app.state.store.patch("run", id, dict(evaluation_score=form.score))

    @router.post("/doctor")
    async def doctor(form: DoctorInput):
        return await app.state.providers.doctor(form.kind, form.target,vision=form.vision)

    @router.post("/extensions", status_code=201)
    async def add_extension(form: ExtensionInput):
        if form.kind == "acp" and form.transport != "stdio": raise HTTPException(400, "This ACP adapter supports stdio only")
        if form.transport == "stdio" and (not form.argv or any(len(a) > 1000 for a in form.argv)):
            raise HTTPException(400, "Provide an executable and argument array")
        if form.transport == "http": checked_url(form.url)
        id = uid("ext_")
        if form.token: app.state.vault.set("extension_" + id, form.token, form.ephemeral)
        return app.state.store.put("extension", id, dict(id=id, **form.model_dump(exclude={"token", "ephemeral"}), tools=[]))

    @router.delete("/extensions/{id}")
    async def remove_extension(id: str):
        app.state.vault.delete("extension_" + id)
        app.state.store.delete("extension", id)
        return dict(deleted=True)

    @router.post("/extensions/{id}/discover")
    async def discover_extension(id: str, form: ProjectReference):
        return await app.state.extensions.discover(id, project(form.project_id))

    @router.post("/backups", status_code=201)
    async def backup():
        await stop_all()
        with app.state.store.lock:
            return create_backup(root, app.state.store)

    @router.post("/pairing/certificate")
    async def certificate(form: CertificateInput):
        from .pairing import generate_certificate
        return generate_certificate(root, form.hostname)

    @router.get("/release-settings")
    async def release_settings():
        from .release_defaults import ENDPOINT,PUBLIC_KEY
        return app.state.store.get("settings", "release", dict(endpoint=ENDPOINT, public_key=PUBLIC_KEY))

    @router.put("/release-settings")
    async def save_release_settings(form: ReleaseSettings):
        if form.endpoint and not form.endpoint.startswith("https://"):
            raise HTTPException(400, "Update manifests require HTTPS")
        if form.endpoint: checked_url(form.endpoint)
        return app.state.store.put("settings", "release", form.model_dump())

    @router.post("/pairing/bundle")
    async def pairing_bundle():
        from .pairing import create_bundle
        return create_bundle(root, app.state.pool)

    @router.get("/pairing/discover")
    async def discover_lan():
        from .pairing import discover
        return dict(coordinators=await asyncio.to_thread(discover))

    app.include_router(router)
