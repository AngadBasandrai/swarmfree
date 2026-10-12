import asyncio
import json
import os
from contextlib import asynccontextmanager
from pathlib import Path
import secrets
import time
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from platformdirs import user_data_dir
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware
from .engine import Engine
from .hardware import hardware, default_budgets, inventory, fit_model, ollama_url
from .network import PrivatePool
from .providers import Providers, PROVIDERS, RouteError
from .schema import Architecture, ProfileInput, RunInput, ChatInput, Route, BudgetInput, JoinInput
from .store import Store, uid
from .templates import TEMPLATES
from .vault import Vault
from .private_listener import PrivateNetwork, load_settings, save_settings
from .workspace import Workspace
from .extensions import Extensions
from .features import install_features
from .locking import WorkspaceLock
from .advanced import install_advanced
from .automation import Automation
from .isolation import Isolation
from .media import Attachments
from .evidence import Evidence
from .relay import RelayLinks
from .intelligence import Intelligence,Evaluations
from .runtimes import Runtimes
from .browser_agents import BrowserAgents
from .clarifications import Clarifications
from .remote_execution import RemoteExecution
from .nextgen import install_nextgen
from .privacy import Privacy
from .result_cache import ResultCache
from .mailboxes import Mailboxes
from .teamwork import Teamwork,install_team
from .onboarding import Onboarding
from .a2a import A2A
from .fault_lab import FaultLab
from .registry import Registry
from .cluster import Cluster
from .workbench_next import install_workbench
from .project_ops import ProjectOps
from .adaptive_ops import AdaptiveOps
from .notebooks import Notebooks
from .training import Training
from .tool_apps import ToolApps
from .compatibility import Compatibility
from .operations import install_operations


class SessionInput(BaseModel):
    token: str = ""


class ModelInput(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.:/-]*$")


class ProfileUpdate(BaseModel):
    model: str | None = Field(default=None, max_length=200)
    enabled: bool | None = None
    free_eligible: bool | None = None
    input_usd_per_million: float | None = Field(default=None,ge=0,le=10000,allow_inf_nan=False)
    output_usd_per_million: float | None = Field(default=None,ge=0,le=10000,allow_inf_nan=False)
    verify_duplicates: bool | None=None
    model_revision: str | None=Field(default=None,max_length=200)


class WorkerResult(BaseModel):
    lease: str = Field(max_length=256)
    text: str = Field(default="", max_length=100000)
    done: bool = False
    error: bool = False
    usage: dict = Field(default_factory=dict)
    native_calls: list[dict] = Field(default_factory=list,max_length=8)
    execution_result: dict | None = None


class Heartbeat(BaseModel):
    hardware: dict = Field(default_factory=dict)
    models: list[dict] = Field(default_factory=list, max_length=100)
    budgets: BudgetInput
    execution: dict = Field(default_factory=dict)


def create_app(data_dir=None, token=None, vault=None, *, desktop=False):
    root = Path(data_dir or os.getenv("SWARM_DATA_DIR") or user_data_dir("SwarmWorkbench", appauthor=False))
    owner_token = token or os.getenv("SWARM_OWNER_TOKEN") or secrets.token_urlsafe(32)
    public_origin = os.getenv("SWARM_PUBLIC_ORIGIN", "").rstrip("/")
    origins = {"http://localhost:5173", "http://127.0.0.1:5173", "http://localhost:8000", "http://127.0.0.1:8000"}
    if public_origin:
        origins.add(public_origin)

    @asynccontextmanager
    async def locked_lifespan(app):
        if (root/".restore-in-progress").exists(): raise ValueError("Recovery was interrupted; restore a fresh verified directory before starting this workspace")
        store = Store(root / "workbench.sqlite")
        app.state.store = store
        for kind in ("tuning_job", "conversion_job", "download", "evaluation_job", "index_job", "browser_job", "remote_job","setup_download","fault_job","a2a_stream","notebook_job","training_job","compatibility_job"):
            for job in store.all(kind):
                if job.get("status") in {"running", "starting"}:
                    store.patch(kind, job["id"], dict(status="interrupted"))
        for template in TEMPLATES:
            existing = store.get("architecture", template["id"])
            if not existing or existing.get("builtin"):
                store.put("architecture", template["id"], template)
        if not store.get("settings", "budgets"):
            store.put("settings", "budgets", default_budgets(hardware()))
        app.state.vault = vault or Vault()
        app.state.providers = Providers(store, app.state.vault, ollama_url())
        app.state.pool = PrivatePool(store)
        app.state.workspace = Workspace(store, root)
        app.state.isolation=Isolation(store,root)
        app.state.workspace.isolation=app.state.isolation
        app.state.attachments=Attachments(store,root,app.state.workspace)
        app.state.evidence=Evidence(store)
        app.state.feature_tasks={}
        app.state.conversion_tasks={}
        app.state.extensions = Extensions(store, app.state.vault)
        app.state.intelligence=Intelligence(store,app.state.providers,app.state.workspace)
        app.state.runtimes=Runtimes(store,app.state.providers,root)
        app.state.providers.runtimes=app.state.runtimes
        app.state.browser_agents=BrowserAgents(store,root)
        app.state.clarifications=Clarifications(store)
        app.state.remote_execution=RemoteExecution(store,app.state.pool,app.state.workspace)
        app.state.workspace.intelligence=app.state.intelligence
        app.state.workspace.browser=app.state.browser_agents
        app.state.workspace.clarifications=app.state.clarifications
        app.state.workspace.remote=app.state.remote_execution
        app.state.workspace.extensions = app.state.extensions
        app.state.inventory = dict(connected=False, models=[], loaded=[])
        app.state.inventory_at = 0
        app.state.download_tasks = {}
        async with AsyncSqliteSaver.from_conn_string(str(root / "checkpoints.sqlite")) as saver:
            app.state.engine = Engine(store, app.state.providers, app.state.pool, saver, app.state.workspace)
            app.state.workspace.delegate = app.state.engine.delegate
            app.state.engine.attachments=app.state.attachments
            app.state.engine.intelligence=app.state.intelligence
            app.state.engine.runtimes=app.state.runtimes
            app.state.privacy=Privacy(store)
            app.state.cache=ResultCache(store,app.state.workspace,app.state.privacy)
            app.state.mailboxes=Mailboxes(store,app.state.engine)
            app.state.engine.privacy=app.state.privacy
            app.state.engine.result_cache=app.state.cache
            app.state.workspace.privacy=app.state.privacy
            app.state.workspace.mailboxes=app.state.mailboxes
            app.state.team=Teamwork(store,app.state.vault,app.state.engine,app.state.workspace)
            from .architecture_workbench import Workshop
            app.state.architectures=Workshop(store)
            app.state.onboarding=Onboarding(store,app.state.providers,app.state.intelligence,root)
            app.state.a2a=A2A(store,app.state.vault,app.state.privacy)
            app.state.workspace.a2a=app.state.a2a
            app.state.faults=FaultLab(store,root)
            app.state.registry=Registry(store)
            app.state.cluster=Cluster(store,app.state.providers)
            app.state.evaluations=Evaluations(store,app.state.engine)
            app.state.automation=Automation(store,app.state.engine)
            app.state.automation.enabled=not store.get("settings","automation",{}).get("paused",False)
            app.state.project_ops=ProjectOps(store,app.state.engine)
            app.state.project_ops.app=app
            app.state.adaptive_ops=AdaptiveOps(store)
            app.state.adaptive_ops.app=app
            app.state.engine.project_ops=app.state.project_ops
            app.state.engine.adaptive_ops=app.state.adaptive_ops
            app.state.intelligence.adaptive_ops=app.state.adaptive_ops
            app.state.notebooks=Notebooks(store,root)
            app.state.training=Training(store,root)
            app.state.tool_apps=ToolApps(store,app.state.extensions)
            app.state.compatibility=Compatibility(app)
            app.state.voice_lock=asyncio.Lock()
            app.state.project_ops.start()
            app.state.adaptive_ops.start()
            app.state.relay_links=RelayLinks(store,app.state.vault,app.state.pool)
            try:
                yield
            finally:
                await app.state.project_ops.stop()
                await app.state.adaptive_ops.stop()
                await app.state.automation.stop()
                await app.state.relay_links.stop()
                await app.state.engine.stop()
                tasks = [*app.state.download_tasks.values(),*app.state.feature_tasks.values(),*app.state.conversion_tasks.values()]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await app.state.browser_agents.stop()
                await app.state.runtimes.close()
        store.close()

    @asynccontextmanager
    async def lifespan(app):
        with WorkspaceLock(root):
            async with locked_lifespan(app):
                yield

    app = FastAPI(title="Swarm Workbench", version="0.7.0", lifespan=lifespan)
    app.state.owner_token = owner_token
    app.state.backup_active = False
    hosts = ["127.0.0.1", "localhost", "[::1]", "testserver"]
    if public_origin:
        hosts.append(urlparse(public_origin).hostname)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=hosts)

    @app.middleware("http")
    async def authenticate(request, call_next):
        path = request.url.path
        if path.startswith("/api/") and not path.startswith(("/api/worker/","/api/team/","/api/ops/apps/view/")) and path not in {"/api/session", "/api/health"}:
            cookie = request.cookies.get("swarm_session", "")
            bearer = request.headers.get("authorization", "").removeprefix("Bearer ")
            if not secrets.compare_digest(cookie, owner_token) and not secrets.compare_digest(bearer, owner_token):
                return JSONResponse({"detail": "Sign in with the owner's access token"}, status_code=401)
        if path.startswith("/api/") and not path.startswith("/api/worker/"):
            origin = request.headers.get("origin")
            if origin and origin not in origins:
                return JSONResponse({"detail": "Untrusted browser origin"}, status_code=403)
            if app.state.backup_active and request.method not in {"GET","HEAD"}:
                return JSONResponse({"detail":"A full backup is in progress; try again after it completes"},status_code=409)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RouteError)
    async def provider_error(request, e):
        return JSONResponse({"detail": str(e)}, status_code=400)

    @app.get("/api/health")
    async def health():
        return dict(status="ok", version="0.7.0")

    @app.post("/api/session")
    async def session(form: SessionInput, request: Request):
        # Automatic bootstrap only for a direct loopback request with a loopback Host.
        local = request.client and request.client.host in {"127.0.0.1", "::1", "testclient"}
        local_host = request.url.hostname in {"127.0.0.1", "localhost", "::1", "testserver"}
        if not (not desktop and local and local_host and not form.token) and not secrets.compare_digest(form.token, owner_token):
            raise HTTPException(401, "Invalid owner access token")
        response = JSONResponse(dict(authenticated=True))
        response.set_cookie("swarm_session", owner_token, httponly=True, samesite="strict",
            secure=request.url.scheme == "https" or public_origin.startswith("https://"), path="/")
        return response

    async def local_inventory(force=False):
        if force or time.time()-app.state.inventory_at > 5:
            app.state.inventory = await inventory(app.state.providers.ollama)
            app.state.inventory_at = time.time()
        return app.state.inventory

    @app.get("/api/snapshot")
    async def snapshot():
        store = app.state.store
        hw = hardware()
        inv = await local_inventory()
        budgets = store.get("settings", "budgets")
        runs = store.all("run")
        # Conversations are fetched individually, not repeatedly returned in the global snapshot.
        run_summaries = [{k: v for k, v in r.items() if k not in {"results", "architecture"}} for r in reversed(runs[-60:])]
        models = [dict(**m, fit=fit_model(m, hw, budgets, inv["loaded"])) for m in inv["models"]]
        return dict(providers=PROVIDERS, profiles=app.state.providers.public(), architectures=store.all("architecture"),
            runs=run_summaries, hardware=hw, budgets=budgets, local={**inv, "models": models},
            devices=app.state.pool.public(), vault=app.state.vault.status(), downloads=store.all("download"),
            public_pool=dict(enabled=True, status="Self-hosted volunteer hub adapter; per-mission prompt-sharing consent required"),
            projects=store.all("project"), extensions=store.all("extension"), capabilities=store.all("capability"),
            private_network=getattr(app.state, "private_network", None),runtimes=app.state.runtimes.public())

    @app.post("/api/profiles", status_code=201)
    async def add_profile(form: ProfileInput):
        try:
            return app.state.providers.add(form)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.patch("/api/profiles/{id}")
    async def update_profile(id: str, form: ProfileUpdate):
        current=app.state.store.get("profile", id)
        if not current:
            raise HTTPException(404, "Profile not found")
        changes=form.model_dump(exclude_unset=True)
        if "model" in changes and changes["model"]!=current.get("model"):
            changes={"input_usd_per_million":None,"output_usd_per_million":None,**changes}
        return app.state.store.patch("profile", id, changes)

    @app.delete("/api/profiles/{id}")
    async def delete_profile(id: str):
        app.state.vault.delete(id)
        app.state.store.delete("profile", id)
        return dict(deleted=True)

    @app.post("/api/profiles/{id}/discover")
    async def discover(id: str):
        return dict(models=await app.state.providers.discover(id))

    @app.post("/api/architectures", status_code=201)
    async def save_architecture(form: Architecture):
        existing = app.state.store.get("architecture", form.id)
        if existing and existing["builtin"]:
            raise HTTPException(400, "Save a copy with a new ID to customize a built-in template")
        from .composition import resolve
        resolve(app.state.store, form.model_dump())
        return app.state.architectures.save(dict(form.model_dump(), builtin=False))

    @app.post("/api/architectures/preview")
    async def preview_architecture(form: Architecture):
        from .composition import preview
        return preview(app.state.store, form.model_dump())

    @app.delete("/api/architectures/{id}")
    async def delete_architecture(id: str):
        arch = app.state.store.get("architecture", id)
        if arch and arch["builtin"]:
            raise HTTPException(400, "Built-in templates cannot be deleted")
        app.state.store.delete("architecture", id)
        return dict(deleted=True)

    @app.post("/api/runs", status_code=201)
    async def create_run(form: RunInput):
        if form.project_id and not app.state.store.get("project", form.project_id):
            raise HTTPException(404, "Project not found")
        if form.tools_enabled and not form.project_id:
            raise HTTPException(400, "Select a project before granting tools")
        architecture = app.state.store.get("architecture", form.architecture_id)
        if not architecture:
            raise HTTPException(404, "Architecture not found")
        return app.state.engine.create(form, architecture)

    @app.get("/api/runs/{id}")
    async def read_run(id: str):
        run = app.state.store.get("run", id)
        if not run:
            raise HTTPException(404, "Run not found")
        children=[dict(**{k:r.get(k) for k in ("id","task","status","depth")}, name=r["architecture"]["name"], kind="team" if r.get("composition_agent") else "specialist") for r in app.state.store.all("run") if r.get("parent_run_id")==id and r.get("delegated")]
        return dict(**run, agents=app.state.engine.agents(id), events=app.state.store.events(id),usage=app.state.engine.budget.usage(run),children=children)

    @app.post("/api/runs/{id}/pause")
    async def pause_run(id: str):
        if not app.state.store.get("run", id):
            raise HTTPException(404, "Run not found")
        await app.state.engine.pause(id)
        return dict(status="paused")

    @app.post("/api/runs/{id}/resume")
    async def resume_run(id: str):
        run = app.state.store.get("run", id)
        if not run:
            raise HTTPException(404, "Run not found")
        if run["status"] not in {"paused", "interrupted", "queued","budget_exceeded","waiting"}:
            raise HTTPException(400, "Only paused or interrupted runs can resume")
        app.state.engine.start(id)
        return dict(status="running")

    @app.post("/api/agents/{id}/chat")
    async def agent_chat(id: str, form: ChatInput):
        agent = app.state.store.get("agent", id)
        if not agent:
            raise HTTPException(404, "Agent not found")
        app.state.engine.steer(id, form.content)
        return dict(queued=True)

    @app.put("/api/agents/{id}/route")
    async def agent_route(id: str, form: Route):
        agent = app.state.store.get("agent", id)
        if not agent:
            raise HTTPException(404, "Agent not found")
        was_running = app.state.store.get("run", agent["run_id"])["status"] == "running"
        if was_running:
            await app.state.engine.pause(agent["run_id"])
        chat_task = app.state.engine.chat_tasks.get(id)
        was_chatting = chat_task and not chat_task.done()
        if was_chatting:
            chat_task.cancel()
            await asyncio.gather(chat_task, return_exceptions=True)
        app.state.store.patch("agent", id, {"route": form.model_dump()})
        app.state.store.event(agent["run_id"], "route_changed", agent_id=id, message=f"{agent['name']}: model assignment updated; conversation preserved")
        if was_running:
            app.state.engine.start(agent["run_id"])
        if was_chatting:
            current_run = app.state.store.get("run", agent["run_id"])
            app.state.engine.chat_tasks[id] = asyncio.create_task(app.state.engine.chat(current_run, id))
        return dict(updated=True, resumed=was_running)

    @app.put("/api/budgets")
    async def budgets(form: BudgetInput):
        hw = hardware()
        if form.ram_gb > hw["ram_total_gb"]:
            raise HTTPException(400, "RAM budget cannot exceed this device's physical RAM")
        if hw["gpus"] and form.vram_gb > max(g["total_gb"] for g in hw["gpus"]):
            raise HTTPException(400, "VRAM budget cannot exceed an individual GPU's capacity")
        return app.state.store.put("settings", "budgets", form.model_dump())

    async def pull_model(id, model):
        store = app.state.store
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(600, connect=10), trust_env=False) as c:
                async with c.stream("POST", app.state.providers.ollama + "/api/pull", json=dict(name=model, stream=True)) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if not line:
                            continue
                        data = json.loads(line)
                        if "error" in data:
                            raise RouteError("Ollama could not download this model. Check its tag and available disk space.")
                        store.patch("download", id, {k: data[k] for k in ("status", "total", "completed") if k in data})
            store.patch("download", id, {"status": "complete"})
            app.state.inventory_at = 0
        except asyncio.CancelledError:
            store.patch("download", id, {"status": "interrupted"})
            raise
        except Exception:
            store.patch("download", id, dict(status="failed", error="Download failed; check Ollama and the model tag."))

    @app.post("/api/local/pull", status_code=202)
    async def pull(form: ModelInput):
        inv = await local_inventory(True)
        if not inv["connected"]:
            raise HTTPException(400, "Start Ollama before downloading a model")
        if any(not task.done() for task in app.state.download_tasks.values()):
            raise HTTPException(409, "A model download is already active")
        id = uid("download_")
        record = app.state.store.put("download", id, dict(id=id, model=form.model, status="starting", total=0, completed=0))
        app.state.download_tasks[id] = asyncio.create_task(pull_model(id, form.model))
        return record

    @app.post("/api/local/{action}")
    async def local_action(action: str, form: ModelInput):
        if action not in {"load", "unload"}:
            raise HTTPException(404, "Unknown model action")
        async with app.state.providers.local_lock:
            inv = await local_inventory(True)
            model = next((m for m in inv["models"] if m["name"] == form.model), None)
            if not model:
                raise HTTPException(400, "Model is not installed")
            budgets = app.state.store.get("settings", "budgets")
            fit = fit_model(model, hardware(), budgets, inv["loaded"])
            if action == "load":
                model,fit=await app.state.providers.prepare_local(form.model,budgets)
                owned=app.state.store.get("settings","local_resident",{}).get("models",[])
                app.state.store.put("settings","local_resident",dict(models=list(set(owned+[form.model]))[-100:]))
            options = dict(num_ctx=budgets["context"])
            if fit["placement"] == "cpu":
                options["num_gpu"] = 0
            try:
                async with httpx.AsyncClient(timeout=180, trust_env=False) as c:
                    r = await c.post(app.state.providers.ollama + "/api/generate", json=dict(model=form.model, prompt="",
                        stream=False, keep_alive=300 if action == "load" else 0, options=options))
                    r.raise_for_status()
            except httpx.HTTPError as e:
                raise HTTPException(400, "Ollama could not change the model state") from e
            app.state.inventory_at = 0
            return dict(status="loaded" if action == "load" else "unloaded")

    @app.post("/api/devices/invite")
    async def invite():
        return app.state.pool.invite()

    @app.post("/api/devices/{id}/{action}")
    async def device_action(id: str, action: str):
        device = app.state.store.get("device", id)
        if not device:
            raise HTTPException(404, "Device not found")
        if action not in {"approve", "revoke"}:
            raise HTTPException(404, "Unknown device action")
        if device["revoked"]:
            raise HTTPException(400, "Revoked devices must pair again")
        app.state.store.patch("device", id, dict(approved=action == "approve", revoked=action == "revoke"))
        if action == "revoke":
            for job in app.state.store.all("job"):
                if job["device_id"] == id and job["status"] in {"queued", "leased"}:
                    app.state.store.patch("job", job["id"], {"status": "cancelled"})
        return dict(updated=True)

    def worker_auth(id, request):
        token = request.headers.get("authorization", "").removeprefix("Bearer ")
        try:
            return app.state.pool.authenticate(id, token)
        except ValueError as e:
            raise HTTPException(401, str(e)) from e

    @app.post("/api/worker/join")
    async def worker_join(form: JoinInput):
        try:
            return app.state.pool.join(form)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e

    @app.post("/api/worker/{id}/heartbeat")
    async def worker_heartbeat(id: str, request: Request, form: Heartbeat):
        return app.state.pool.heartbeat(worker_auth(id, request), form.model_dump())

    @app.post("/api/worker/{id}/lease")
    async def worker_lease(id: str, request: Request):
        return dict(job=app.state.pool.lease(worker_auth(id, request)))

    @app.post("/api/worker/{id}/jobs/{job_id}")
    async def worker_result(id: str, job_id: str, request: Request, form: WorkerResult):
        try:
            result = app.state.pool.result(worker_auth(id, request), job_id, form.lease, form.model_dump())
            return dict(status=result["status"])
        except ValueError as e:
            raise HTTPException(409, str(e)) from e

    install_features(app, root)
    install_advanced(app, root)
    install_nextgen(app,root)
    install_team(app)
    from .architecture_workbench import install as install_architecture_workbench
    from .execution_control import install as install_execution_control
    install_architecture_workbench(app)
    install_execution_control(app)
    install_workbench(app)
    install_operations(app)

    # The desktop window embeds its UI. Its private engine exposes only authenticated APIs.
    if desktop:
        @app.get("/api/private-network")
        async def network_settings():
            try:
                return load_settings(root).model_dump()
            except (ValueError, OSError) as error:
                raise HTTPException(400, f"Cannot read private network settings: {error}") from error

        @app.put("/api/private-network")
        async def update_network_settings(form: PrivateNetwork):
            try:
                save_settings(root, form)
            except (ValueError, OSError) as error:
                raise HTTPException(400, f"Cannot use these TLS files: {error}") from error
            return dict(**form.model_dump(), restart_required=True)

        return app

    # Optional source-development/headless coordinator UI.
    package_dir = Path(__file__).resolve().parent
    default_web = package_dir.parent / "dist"
    if not (default_web / "index.html").exists():
        default_web = package_dir / "web_dist"
    dist = Path(os.getenv("SWARM_WEB_DIR", str(default_web)))
    if (dist / "assets").exists():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    async def frontend(path: str):
        if path.startswith("api/"):
            raise HTTPException(404, "API route not found")
        if (dist / "index.html").exists():
            return FileResponse(dist / "index.html")
        return JSONResponse(dict(message="Frontend is not built. Run npm install and npm run build, or npm run dev."))

    return app
