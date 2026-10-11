"""Isolated live-model acceptance suites; export contains measurements, never prompts."""
import asyncio
import hashlib
import json
from pathlib import Path
import platform
import secrets
import time
from pydantic import BaseModel, Field
from .providers import RouteError
from .schema import RunInput, Architecture
from .store import Store, uid
from .privacy import canonical, scan


class CompatibilityInput(BaseModel):
    mission: RunInput
    runtime_version: str = Field(min_length=1, max_length=100)
    model_revision: str = Field(min_length=1, max_length=200)
    cases: list[str] = Field(default_factory=lambda:["structured", "handoff", "tools", "recovery"], min_length=1, max_length=4)


CASES = ["structured", "handoff", "tools", "recovery"]


class Compatibility:
    def __init__(self, app): self.app = app

    async def run(self, id, form):
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
        from .engine import Engine
        from .workspace import Workspace
        from .intelligence import Intelligence
        from .privacy import Privacy
        root = self.app.state.workspace.root/"compatibility"/id
        root.mkdir(parents=True); fixture = root/"project"; fixture.mkdir()
        store = Store(root/"workbench.sqlite")
        rows = []; engine = None
        for kind in ("profile", "device", "capability", "runtime"):
            for r in self.app.state.store.all(kind): store.put(kind, r["id"], r)
        store.put("settings", "budgets", self.app.state.store.get("settings", "budgets"))
        store.put("project", "fixture", dict(id="fixture", name="Compatibility fixture", root=str(fixture), tools=["workspace.read"], checks={}))
        workspace = Workspace(store, root)
        base = form.mission.model_dump(); base.update(mode="live", project_id="fixture", attachments=[], inputs={},
            model_revision=form.model_revision, adaptive="off", max_subtasks=0, public_allowed=False, reuse_cache=False, isolated_worktrees=False)
        async def wait(run):
            await engine.tasks[run["id"]]
            return store.get("run", run["id"])
        try:
            for case in dict.fromkeys(form.cases):
                if case not in CASES: raise RouteError("Unknown compatibility case")
                nonce = secrets.token_hex(8); (fixture/"clue.txt").write_text(nonce)
                self.app.state.store.patch("compatibility_job", id, dict(case=case, rows=rows))
                started = time.monotonic()
                async with AsyncSqliteSaver.from_conn_string(str(root/"checkpoints.sqlite")) as saver:
                    engine = Engine(store, self.app.state.providers, self.app.state.pool, saver, workspace)
                    engine.intelligence = Intelligence(store, self.app.state.providers, workspace)
                    engine.runtimes = self.app.state.runtimes; engine.privacy = Privacy(store)
                    if case == "structured":
                        a = Architecture(id="structured", name="JSON acceptance", nodes=[dict(id="answer", name="Answer", role="Return only valid JSON",
                            instructions='Return {"nonce":"'+nonce+'"}', tools=[], output_schema=dict(type="object", properties=dict(nonce=dict(const=nonce)), required=["nonce"], additionalProperties=False))]).model_dump()
                    elif case == "tools":
                        a = Architecture(id="tools", name="File tool acceptance", nodes=[dict(id="reader", name="Reader", role="Read clue.txt with workspace.read and return its exact contents", tools=["workspace.read"], validators=[dict(kind="contains", value=nonce)])]).model_dump()
                    else:
                        a = Architecture(id="handoff", name="Handoff acceptance", nodes=[dict(id="source", name="Source", role="Return the exact identifier", tools=[], instructions="Identifier: "+nonce, validators=[dict(kind="contains", value=nonce)]),
                            dict(id="target", name="Target", role="Repeat the identifier from completed upstream work", tools=[], validators=[dict(kind="contains", value=nonce)])], edges=[dict(source="source", target="target")]).model_dump()
                    if case == "recovery":
                        # Finish one live stage, then actually rebuild Engine + SQLite saver.
                        partial = dict(a, nodes=[a["nodes"][0]], edges=[])
                        run = engine.create(RunInput.model_validate({**base, "task":"Produce the requested identifier", "architecture_id":a["id"], "tools_enabled":False}), partial)
                        first = await wait(run)
                        if first["status"] != "completed": raise RouteError("Initial recovery stage did not complete")
                        store.patch("run", run["id"], dict(architecture=a, status="paused"))
                        node = a["nodes"][1]; agent_id = run["id"]+":target"
                        store.put("agent", agent_id, dict(id=agent_id, run_id=run["id"], node_id="target", name=node["name"], role=node["role"],
                            instructions=node["instructions"], route=base["route"], status="waiting", messages=[], actual_route="", error="", tools=[], output_schema={}, validators=node["validators"], requires=[]))
                        await engine.stop()
                        saved_source = store.get("agent", run["id"]+":source")["result"]
                        graph = engine.graph(store.get("run",run["id"]))
                        await graph.aupdate_state({"configurable":{"thread_id":run["id"]}}, {"results":[]}, as_node="source")
                    else:
                        run = engine.create(RunInput.model_validate({**base, "task":"Read the fixture file and return its identifier" if case == "tools" else "Complete the identifier acceptance task", "architecture_id":a["id"], "tools_enabled":case == "tools"}), a)
                        final = await wait(run)
                        saved_source = None
                        await engine.stop()
                if case == "recovery":
                    async with AsyncSqliteSaver.from_conn_string(str(root/"checkpoints.sqlite")) as saver:
                        engine = Engine(store, self.app.state.providers, self.app.state.pool, saver, workspace)
                        engine.intelligence = Intelligence(store, self.app.state.providers, workspace); engine.runtimes = self.app.state.runtimes
                        engine.start(run["id"]); final = await wait(run)
                        retained = store.get("agent", run["id"]+":source")["result"] == saved_source
                        await engine.stop()
                else: retained = True
                ops = [o for o in store.all("operation") if o.get("run_id") == run["id"] and o.get("tool") == "workspace.read" and o.get("status") == "completed"]
                source_calls=len([r for r in store.all("request") if r["run_id"]==run["id"] and r["agent_id"]==run["id"]+":source"])
                passed = final["status"] == "completed" and retained and (case != "tools" or bool(ops)) and (case != "recovery" or source_calls == 1)
                rows.append(dict(case=case, passed=passed, elapsed_ms=round((time.monotonic()-started)*1000), usage=engine.budget.usage(final),
                    stage_requests={n["id"]:len([r for r in store.all("request") if r["run_id"]==run["id"] and r["agent_id"]==run["id"]+":"+n["id"]]) for n in final["architecture"]["nodes"]},
                    requests=len([r for r in store.all("request") if r["run_id"] == run["id"]]), note="Actual engine/checkpoint reconstruction" if case == "recovery" else "Live inference with bounded fixture checks"))
                self.app.state.store.patch("compatibility_job", id, dict(rows=rows))
            route = base["route"]
            model = route.get("model", "")
            if route["kind"] == "profile": model = self.app.state.store.get("profile", route["profile_id"], {}).get("model", model)
            if route["kind"] == "runtime": model = self.app.state.store.get("runtime", route["runtime_id"], {}).get("name", model)
            report = dict(format="swarm-compatibility-v1", app_version="0.6.0", model=model, model_revision=form.model_revision,
                runtime_version=form.runtime_version, route_kind=route["kind"], system=platform.system(), machine=platform.machine(),
                observed_at=time.time(), cases=[{k:v for k,v in r.items() if k not in {"usage","stage_requests"}} for r in rows],
                note="Owner-declared versions and one machine's workflow checks; no prompt content or credentials included")
            return dict(rows=rows, report=report, report_sha256=hashlib.sha256(canonical(report).encode()).hexdigest())
        finally:
            if engine: await engine.stop()
            store.close()

    def import_report(self, report):
        if len(canonical(report).encode()) > 32000 or scan(report): raise RouteError("Invalid compatibility report")
        if report.get("format") != "swarm-compatibility-v1": raise RouteError("Unsupported report format")
        fields = {"format", "app_version", "model", "model_revision", "runtime_version", "route_kind", "system", "machine", "observed_at", "cases", "note"}
        if set(report) != fields or not isinstance(report["cases"], list) or not 1 <= len(report["cases"]) <= 4: raise RouteError("Report contains unexpected fields")
        if any(set(c) != {"case", "passed", "elapsed_ms", "requests", "note"} or c["case"] not in CASES or not isinstance(c["passed"], bool) for c in report["cases"]): raise RouteError("Invalid workflow measurements")
        digest = hashlib.sha256(canonical(report).encode()).hexdigest()
        return self.app.state.store.put("compatibility_report", digest, dict(id=digest, report=report, trust="unverified owner report", imported_at=time.time()))
