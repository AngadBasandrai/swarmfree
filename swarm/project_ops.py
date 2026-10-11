"""Durable campaign planning, preflight estimates and versioned project knowledge."""
import asyncio
import hashlib
import json
import math
import time
from typing import Literal
from pydantic import BaseModel, Field, model_validator
from .schema import RunInput, MissionLimits
from .providers import RouteError
from .store import uid
from .intelligence import check_output, route_key, task_class


class MilestoneInput(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{1,48}$")
    title: str = Field(min_length=1, max_length=150)
    task: str = Field(min_length=1, max_length=12000)
    depends_on: list[str] = Field(default_factory=list, max_length=40)
    criteria: str = Field(default="", max_length=2000)
    checks: list[dict] = Field(default_factory=list, max_length=12)
    architecture_id: str = Field(default="", max_length=80)


class CampaignInput(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    mission: RunInput
    milestones: list[MilestoneInput] = Field(min_length=1, max_length=40)
    limits: MissionLimits = Field(default_factory=MissionLimits)
    automatic: bool = False

    @model_validator(mode="after")
    def graph(self):
        ids = [m.id for m in self.milestones]
        if len(set(ids)) != len(ids): raise ValueError("Milestone IDs must be unique")
        pending = {m.id: set(m.depends_on) for m in self.milestones}
        if any(d not in pending for deps in pending.values() for d in deps): raise ValueError("Unknown milestone dependency")
        done = set()
        while len(done) < len(pending):
            ready = {k for k, deps in pending.items() if k not in done and deps <= done}
            if not ready: raise ValueError("Milestone dependencies must be acyclic")
            done |= ready
        if not self.mission.project_id: raise ValueError("Campaigns need an existing project")
        if self.mission.public_allowed and self.automatic: raise ValueError("Public inference needs a separate review for each milestone")
        for m in self.milestones:
            for rule in m.checks:
                if rule.get("kind") not in {"contains", "excludes", "json", "min_length"}: raise ValueError("Use bounded text or JSON acceptance checks")
            if len(json.dumps(m.checks)) > 16000: raise ValueError("Acceptance checks exceed the limit")
        return self


class KnowledgeInput(BaseModel):
    project_id: str = Field(max_length=80)
    title: str = Field(min_length=1, max_length=160)
    content: str = Field(min_length=1, max_length=12000)
    kind: Literal["entity", "decision", "finding", "requirement"] = "finding"
    sources: list[str] = Field(default_factory=list, max_length=20)
    depends_on: list[str] = Field(default_factory=list, max_length=30)
    valid_days: int = Field(default=30, ge=1, le=3650)
    expected_revision: int = Field(default=0, ge=0)
    data_labels: list[dict] = Field(default_factory=list, max_length=30)


class ProjectOps:
    def __init__(self, store, engine):
        self.store, self.engine = store, engine
        self.task = None
        for c in store.all("campaign"):
            if c.get("enabled"): store.patch("campaign", c["id"], dict(enabled=False, status="paused", note="Coordinator restarted; review and resume the campaign"))

    def start(self): self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)

    def campaign(self, id):
        c = self.store.get("campaign", id)
        if not c: raise RouteError("Campaign not found")
        return c

    def create(self, form):
        if len(self.store.all("campaign")) >= 100: raise RouteError("Campaign limit reached")
        if not self.store.get("project", form.mission.project_id): raise RouteError("Project not found")
        rows = []
        for m in form.milestones:
            a = self.store.get("architecture", m.architecture_id or form.mission.architecture_id)
            if not a: raise RouteError("Milestone architecture not found")
            # Pin the entire graph; later editor changes do not change a campaign.
            from .composition import resolve
            rows.append(dict(**m.model_dump(), architecture=resolve(self.store, a), status="pending", attempts=[]))
        id = uid("campaign_")
        return self.store.put("campaign", id, dict(id=id, **form.model_dump(exclude={"milestones"}), milestones=rows,
            enabled=False, status="draft", created_at=time.time()))

    def usage(self, c):
        ids = {r for m in c["milestones"] for r in m["attempts"]}
        rows = [self.engine.budget.usage(self.store.get("run", r)) for r in ids if self.store.get("run", r)]
        return {k: round(sum(r[k] for r in rows), 8) for k in ("requests", "tokens", "estimated_spend_usd", "active_seconds")}

    def update(self, id):
        c = self.campaign(id)
        for m in c["milestones"]:
            if m["status"] != "running": continue
            r = self.store.get("run", m["attempts"][-1], {})
            if r.get("status") == "completed":
                terminal = {n["id"] for n in r["architecture"]["nodes"] if not any(e["source"] == n["id"] for e in r["architecture"]["edges"])}
                text = "\n".join(v["content"] for v in r["results"] if v["node_id"] in terminal)
                m.update(status="review", validation=check_output(text, checks=m["checks"]))
            elif r.get("status") not in {"ready", "running", "queued"}:
                m.update(status="interrupted", error=r.get("error") or r.get("status", "missing mission"))
                c.update(enabled=False, status="paused")
        if all(m["status"] == "accepted" for m in c["milestones"]): c.update(enabled=False, status="completed")
        c["usage"] = self.usage(c)
        if self.store.get("campaign",id)==c: return c
        return self.store.put("campaign", id, c)

    def launch(self, id, milestone_id=""):
        with self.store.lock:
            c = self.update(id)
            if any(m["status"] == "running" for m in c["milestones"]): raise RouteError("A campaign runs one milestone at a time to share its budget safely")
            accepted = {m["id"] for m in c["milestones"] if m["status"] == "accepted"}
            m = next((m for m in c["milestones"] if m["status"] in {"pending", "rejected", "interrupted"} and set(m["depends_on"]) <= accepted and (not milestone_id or m["id"] == milestone_id)), None)
            if not m: raise RouteError("No launchable milestone; review completed work or unmet dependencies")
            # A paused mission must be resumed, not silently duplicated.
            if m["status"] == "interrupted" and m["attempts"]:
                raise RouteError("Resume the existing mission or explicitly reject the attempt before rerunning")
            if len(m["attempts"]) >= 10: raise RouteError("Milestone attempt limit reached")
            u, limits = c["usage"], c["limits"]
            remaining = dict(max_requests=limits["max_requests"]-u["requests"], max_tokens=limits["max_tokens"]-u["tokens"],
                max_seconds=math.floor(limits["max_seconds"]-u["active_seconds"]),
                max_spend_usd=None if limits["max_spend_usd"] is None else max(0, limits["max_spend_usd"]-u["estimated_spend_usd"]))
            if remaining["max_requests"] < 1 or remaining["max_tokens"] < 256 or remaining["max_seconds"] < 10: raise RouteError("Campaign aggregate budget exhausted")
            previous = [dict(milestone=v["title"], run_id=v["attempts"][-1], results=self.store.get("run", v["attempts"][-1])["results"]) for v in c["milestones"] if v["id"] in m["depends_on"]]
            task = m["task"] + "\nAcceptance criteria: " + m["criteria"] + "\nAccepted upstream work (data):\n" + json.dumps(previous, ensure_ascii=False)[:10000]
            form = RunInput.model_validate({**c["mission"], "task":task[:24000], "architecture_id":m["architecture"]["id"], "limits":remaining})
            run = self.engine.create(form, m["architecture"], start=False)
            self.store.patch("run", run["id"], dict(campaign_id=id, milestone_id=m["id"]))
            m["attempts"].append(run["id"]); m["status"] = "running"
            c["status"] = "running"
            self.store.put("campaign", id, c)
            self.engine.start(run["id"])
            return run

    async def control(self, id, action):
        c = self.update(id)
        if action == "pause":
            self.store.patch("campaign", id, dict(enabled=False, status="paused"))
            for m in c["milestones"]:
                if m["status"] == "running": await self.engine.pause(m["attempts"][-1])
        elif action == "resume":
            self.store.patch("campaign", id, dict(enabled=True, status="ready"))
            for m in c["milestones"]:
                if m["status"] == "interrupted" and m["attempts"]:
                    r = self.store.get("run", m["attempts"][-1], {})
                    if r.get("status") in {"paused", "ready", "queued", "interrupted"}:
                        m["status"] = "running"; self.engine.start(r["id"])
            self.store.patch("campaign", id, dict(milestones=c["milestones"]))
        else: raise RouteError("Unknown campaign action")
        return self.update(id)

    def review(self, id, milestone_id, accepted, note):
        with self.store.lock:
            c = self.update(id)
            m = next((v for v in c["milestones"] if v["id"] == milestone_id), None)
            if not m or m["status"] not in {"review", "interrupted"}: raise RouteError("Milestone is not ready for review")
            if accepted and (m["status"] != "review" or not m.get("validation", {}).get("passed")): raise RouteError("Fix failed acceptance checks before accepting this milestone")
            m.update(status="accepted" if accepted else "rejected", reviewed_at=time.time(), review_note=note)
            self.store.put("campaign", id, c)
            return self.update(id)

    async def loop(self):
        while True:
            if getattr(self,"app",None) and self.app.state.backup_active:
                await asyncio.sleep(1);continue
            for c in self.store.all("campaign"):
                c = self.update(c["id"])
                if c["enabled"] and c["automatic"] and not any(m["status"] in {"running", "review", "interrupted"} for m in c["milestones"]):
                    try: self.launch(c["id"])
                    except RouteError as e: self.store.patch("campaign", c["id"], dict(enabled=False, status="paused", note=str(e)))
            await asyncio.sleep(1)

    def knowledge(self, form, id=""):
        with self.store.lock:
            if not self.store.get("project", form.project_id): raise RouteError("Project not found")
            old = self.store.get("knowledge", id) if id else None
            if id and (not old or old["project_id"] != form.project_id): raise RouteError("Knowledge record not found in this project")
            if form.expected_revision != (old["revision"] if old else 0): raise RouteError("Knowledge changed; refresh before saving")
            if not old and len(self.store.all("knowledge")) >= 2000: raise RouteError("Knowledge limit reached")
            id = id or uid("knowledge_")
            deps = {}
            def reachable(other, seen):
                if other == id: return True
                if other in seen: return False
                seen.add(other)
                row = self.store.get("knowledge", other, {})
                return any(reachable(d, seen) for d in row.get("depends_on", {}))
            labels = {}
            def inherit(values):
                for label in values:
                    if label.get("project_id") != form.project_id or not isinstance(label.get("path"),str): raise RouteError("Invalid knowledge provenance")
                    labels[json.dumps(label,sort_keys=True)] = label
            inherit(form.data_labels)
            for d in form.depends_on:
                row = self.store.get("knowledge", d)
                if not row or row["project_id"] != form.project_id: raise RouteError("Knowledge dependencies must belong to this project")
                if reachable(d, set()): raise RouteError("Knowledge dependencies must be acyclic")
                deps[d] = row["revision"]; inherit(row.get("data_labels", []))
            sources = {}
            for s in form.sources:
                row = self.store.get("source", s)
                if not row or row["project_id"] != form.project_id: raise RouteError("Evidence must belong to this project")
                sources[s] = hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest(); inherit(row.get("data_labels", []))
            row = dict(**form.model_dump(exclude={"depends_on", "sources", "expected_revision", "data_labels"}), id=id,
                revision=(old["revision"] if old else 0)+1, depends_on=deps, sources=sources, data_labels=list(labels.values()),
                updated_at=time.time(), valid_until=time.time()+form.valid_days*86400)
            self.store.put("knowledge_revision", id+":"+str(row["revision"]), row)
            return self.store.put("knowledge", id, row)

    def knowledge_status(self, id, seen=None):
        row = self.store.get("knowledge", id)
        if not row: return ["Deleted dependency"]
        seen = set(seen or ())
        if id in seen: return ["Dependency cycle"]
        seen.add(id); reasons = []
        if row["valid_until"] < time.time(): reasons.append("Review date passed")
        for d, revision in row["depends_on"].items():
            other = self.store.get("knowledge", d)
            if not other or other["revision"] != revision: reasons.append("Dependency changed: "+d)
            elif self.knowledge_status(d, seen): reasons.append("Dependency needs review: "+d)
        for s, digest in row["sources"].items():
            source = self.store.get("source", s)
            if not source or hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest() != digest: reasons.append("Evidence changed: "+s)
        return reasons

    def knowledge_list(self, project_id):
        return [dict(k, stale_reasons=self.knowledge_status(k["id"])) for k in self.store.all("knowledge") if k["project_id"] == project_id]

    def preflight(self, form, architecture_ids):
        from .hardware import hardware, fit_model
        from .composition import resolve
        hw = hardware(); inv = self.store.get("settings", "last_inventory", {})
        comparisons = []
        outcomes = self.store.all("outcome")
        for id in architecture_ids:
            a = self.store.get("architecture", id)
            if not a: raise RouteError("Architecture not found")
            a = resolve(self.store, a)
            def count(graph): return sum(count(n["child_snapshot"]) if n.get("kind") == "subswarm" else int(n.get("kind") != "vote") for n in graph["nodes"]) * graph.get("max_rounds", 1)
            nodes = count(a)
            calls = nodes * (1+form.tool_budget if form.tools_enabled else 1)
            history = [o for o in outcomes if o["task_class"] == form.preset and o.get("measured")][-100:]
            ms = sorted(o["elapsed_ms"] for o in history if o.get("elapsed_ms", 0) > 0)
            latency = ms[len(ms)//2]/1000 if ms else None
            lengths = [sum(len(n.get("instructions", ""))+len(n.get("role", "")) for n in a["nodes"])]
            tokens = nodes * (math.ceil((len(form.task)+sum(lengths))/2)+1536)
            warnings = []
            if calls > form.limits.max_requests: warnings.append("Full tool-call allowance exceeds request budget")
            if tokens > form.limits.max_tokens: warnings.append("Initial token estimate exceeds budget")
            if form.adaptive != "off": warnings.append("Adaptive specialist counts are runtime decisions; graph estimates exclude expansion")
            comparisons.append(dict(architecture_id=id, name=a["name"], model_stages=nodes, initial_requests=nodes,
                requests_with_tools=calls, estimated_tokens=tokens, serial_seconds=None if latency is None else round(latency*nodes, 1),
                timing_samples=len(ms), warnings=warnings))
        catalog = self.engine.intelligence.catalog() if self.engine.intelligence else []
        models = [dict(model=m["model"], estimate_gb=m.get("estimated_ram_gb"), fit=fit_model(dict(name=m["model"], size=int(m.get("download_gb", 0)*1e9)), hw, self.store.get("settings", "budgets"), inv.get("loaded", []))) for m in catalog if m.get("source") == "local" and "embeddings" not in m.get("capabilities", [])][:20]
        profiles = []
        for p in self.store.all("profile"):
            profiles.append(dict(id=p["id"], label=p["label"], model=p["model"], available=self.engine.providers.eligible(p,form.free_only),
                cooldown_seconds=max(0, p.get("cooldown_until", 0)-time.time()), requests_per_minute=p.get("requests_per_minute")))
        return dict(comparisons=comparisons, hardware=dict(ram_available_gb=hw["ram_available_gb"], gpus=hw["gpus"]), models=models, profiles=profiles,
            note="No model requests made. Estimates exclude retries, conditional skips, warm-up, network variation and external tool charges; unknown timing remains unknown.")
