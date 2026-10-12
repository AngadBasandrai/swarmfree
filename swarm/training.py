"""Reviewed local datasets, optional LoRA jobs and evaluation-gated promotion."""
import hashlib
import json
from pathlib import Path
import time
from pydantic import BaseModel, Field
from .providers import RouteError
from .privacy import scan, canonical
from .store import uid
from .isolation import command


def adapter_manifest(output):
    output = Path(output)
    if not (output/"adapter_config.json").is_file() or not (output/"adapter_model.safetensors").is_file():
        raise RouteError("Training did not produce a complete LoRA adapter")
    files = list(output.iterdir())
    if any(p.is_symlink() or not p.is_file() for p in files) or sum(p.stat().st_size for p in files)>512*1024**2:
        raise RouteError("Adapter outputs must be ordinary files totaling at most 512 MiB")
    manifest = {}
    for p in files:
        h = hashlib.sha256()
        with p.open('rb') as f:
            while chunk:=f.read(1024*1024): h.update(chunk)
        manifest[p.name] = h.hexdigest()
    return manifest


class DatasetInput(BaseModel):
    project_id: str = Field(max_length=80)
    name: str = Field(min_length=1, max_length=120)
    agent_ids: list[str] = Field(min_length=1, max_length=100)


class TrainingInput(BaseModel):
    dataset_id: str = Field(max_length=100)
    reviewed_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    base_directory: str = Field(min_length=1, max_length=4000)
    python_executable: str = Field(min_length=1, max_length=4000)
    max_steps: int = Field(default=30, ge=1, le=1000)
    rank: int = Field(default=8, ge=1, le=64)
    sequence_length: int = Field(default=256, ge=64, le=2048)
    quantized: bool = True
    timeout_seconds: int = Field(default=1800, ge=30, le=14400)
    trusted: bool = False


class Training:
    def __init__(self, store, root):
        self.store, self.root = store, Path(root)/"training"
        self.root.mkdir(exist_ok=True)

    def dataset(self, form):
        if not self.store.get("project", form.project_id): raise RouteError("Project not found")
        if len(self.store.all("training_dataset")) >= 100: raise RouteError("Dataset limit reached")
        examples = []; excluded = []
        for id in dict.fromkeys(form.agent_ids):
            a = self.store.get("agent", id); r = self.store.get("run", a["run_id"]) if a else None
            if not r or r.get("project_id") != form.project_id: raise RouteError("Select agents from this project")
            if r.get("mode", "live") != "live" or a.get("status") != "done": excluded.append(dict(agent_id=id, reason="Only completed live work can become training data")); continue
            if a.get("data_labels"): excluded.append(dict(agent_id=id, reason="File-derived provenance requires a separately curated dataset")); continue
            answer = a.get("result", {}).get("content", "")
            prompt = r["task"]
            if not answer or a.get("validation", {}).get("passed") is False: excluded.append(dict(agent_id=id, reason="Missing or failed final output")); continue
            example = dict(messages=[dict(role="user", content=prompt[:12000]), dict(role="assistant", content=answer[:12000])])
            if scan(example): excluded.append(dict(agent_id=id, reason="Potential credential content detected")); continue
            examples.append(dict(example=example, agent_id=id, run_id=r["id"], model_revision=r.get("model_revision", "")))
        if not examples: raise RouteError("No eligible examples; review completed live agents without sensitive provenance")
        id = uid("dataset_"); digest = hashlib.sha256(canonical(examples).encode()).hexdigest()
        return self.store.put("training_dataset", id, dict(id=id, project_id=form.project_id, name=form.name, examples=examples,
            excluded=excluded, sha256=digest, created_at=time.time(), note="Heuristic screening is incomplete; review every prompt and answer before training or exporting"))

    def review(self, id, digest):
        row = self.store.get("training_dataset", id)
        if not row or row["sha256"] != digest: raise RouteError("Review the current dataset first")
        if hashlib.sha256(canonical(row["examples"]).encode()).hexdigest() != digest: raise RouteError("Dataset content changed")
        return row

    def prepare(self, form, id):
        if not form.trusted: raise RouteError("Trust the selected Python environment and review the training job before starting")
        row = self.review(form.dataset_id, form.reviewed_sha256)
        base = Path(form.base_directory).resolve(); python = Path(form.python_executable)
        if not python.is_absolute() or not python.is_file(): raise RouteError("Select an existing Python executable in your training environment")
        if not base.is_dir() or not (base/"config.json").is_file() or not list(base.glob("*.safetensors")): raise RouteError("Select a downloaded Transformers model with config.json and safetensors weights")
        if any(p.is_symlink() for p in base.glob("*.safetensors")): raise RouteError("Model weights must be ordinary local files")
        from .hardware import hardware
        hw = hardware(); weights = sum(p.stat().st_size for p in base.glob("*.safetensors"))/1024**3
        selected = next((g for g in hw.get("gpus",[]) if g.get("backend")=="cuda" and g.get("id")=="0"),None)
        if not selected: raise RouteError("The built-in training backend requires a supported CUDA GPU at device 0; install its dependencies separately")
        free = selected.get("free_gb")
        allocation = self.store.get("settings", "budgets", {}).get("vram_gb", 0)
        estimate = max(2, weights*(.6 if form.quantized else 2)+form.sequence_length/256)
        if free is None or min(free, allocation) < estimate: raise RouteError("Training estimate exceeds observed free VRAM on CUDA device 0 or the selected allocation")
        if self.store.get("settings", "energy_policy", {}).get("enabled"):
            from .adaptive_ops import AdaptiveOps
            reason = AdaptiveOps(self.store).admission("runtime", {})
            if reason: raise RouteError(reason)
        folder = self.root/id; folder.mkdir()
        data = folder/"examples.jsonl"
        data.write_text("\n".join(json.dumps(v["example"], ensure_ascii=False) for v in row["examples"]), encoding="utf-8")
        config = dict(**form.model_dump(exclude={"python_executable", "trusted", "base_directory"}), dataset_path=str(data), output_directory=str(folder/"adapter"), base_directory=str(base))
        path = folder/"job.json"; path.write_text(json.dumps(config), encoding="utf-8")
        return path, dict(dataset_id=row["id"], project_id=row["project_id"], estimated_vram_gb=round(estimate, 2), base_directory=str(base), config=config)

    async def run(self, id, form):
        path, details = self.prepare(form, id)
        self.store.patch("training_job", id, details)
        # The selected environment must have installed Swarm Workbench's optional training extra.
        await command([form.python_executable, "-m", "swarm.training_runner", str(path)], cwd=self.root/id,
            timeout=form.timeout_seconds, limit=200000)
        output = self.root/id/"adapter"
        manifest = adapter_manifest(output)
        return dict(adapter_directory=str(output), files=manifest, revision="adapter:"+hashlib.sha256(canonical(manifest).encode()).hexdigest(),dataset_sha256=form.reviewed_sha256,
            base_directory=str(Path(form.base_directory).resolve()), status="candidate", note="Load this adapter in a compatible external runtime and run a live evaluation before promotion")

    def promote(self, id, evaluation_id, profile_id):
        job = self.store.get("training_job", id); evaluation = self.store.get("evaluation_job", evaluation_id)
        profile = self.store.get("profile", profile_id)
        if not job or job.get("status") != "completed" or not profile: raise RouteError("Select a completed adapter job and a configured serving profile")
        manifest = job["result"]["files"]
        if adapter_manifest(self.root/id/"adapter") != manifest: raise RouteError("Adapter files changed after training; create and evaluate a new candidate")
        revision = "adapter:"+hashlib.sha256(canonical(manifest).encode()).hexdigest()
        if profile.get("model_revision") != revision: raise RouteError("Pin the serving profile to the displayed adapter revision before evaluation")
        if not evaluation or evaluation.get("status") != "completed" or not evaluation.get("rows"): raise RouteError("Complete a live evaluation of the adapter first")
        for row in evaluation["rows"]:
            run = self.store.get("run", row["run_id"], {})
            if not row["passed"] or run.get("mode") != "live" or run.get("project_id") != job["project_id"]: raise RouteError("All adapter evaluation cases must pass live in the same project")
            calls = [c for c in self.store.all("request") if c.get("run_id") == run["id"]]
            if not calls or any(c.get("model_revision") != revision or c.get("profile_id")!=profile_id for c in calls): raise RouteError("Evaluation did not exclusively use this serving profile and adapter revision")
        old = self.store.get("adapter_active", job["project_id"])
        cursor = old
        for _ in range(19):
            if not cursor: break
            cursor = cursor.get("previous")
        if cursor: cursor["previous"] = None
        activation = dict(id=job["project_id"], job_id=id, profile_id=profile_id, revision=revision, previous=old, evaluation_id=evaluation_id, promoted_at=time.time())
        return self.store.put("adapter_active", activation["id"], activation)

    def rollback(self, project_id):
        row = self.store.get("adapter_active", project_id)
        if not row: raise RouteError("No promoted adapter")
        if row.get("previous"): return self.store.put("adapter_active", project_id, row["previous"])
        self.store.delete("adapter_active", project_id); return dict(rolled_back=True)
