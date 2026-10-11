"""Auditable user feedback and measured device energy admission."""
import asyncio
import datetime as dt
import math
import time
from collections import defaultdict
from pydantic import BaseModel, Field
from .providers import RouteError
from .intelligence import route_key, task_class
from .store import uid


class FeedbackInput(BaseModel):
    outcome_id: str = Field(max_length=120)
    score: int = Field(ge=0, le=5)
    correction: str = Field(default="", max_length=3000)


class EnergyInput(BaseModel):
    enabled: bool = False
    max_temperature_c: float = Field(default=85, ge=35, le=110, allow_inf_nan=False)
    max_daily_wh: float | None = Field(default=None, gt=0, le=1000000, allow_inf_nan=False)
    quiet_start: int = Field(default=22, ge=0, le=23)
    quiet_end: int = Field(default=7, ge=0, le=23)
    quiet_enabled: bool = False
    utc_offset_minutes: int = Field(default=0, ge=-720, le=840)
    prefer_low_power: bool = False
    require_telemetry: bool = False


def telemetry():
    """Only measurements the driver actually supplies, never guessed CPU power."""
    gpus = []
    try:
        import pynvml as nv
        nv.nvmlInit()
        try:
            for i in range(min(16, nv.nvmlDeviceGetCount())):
                h = nv.nvmlDeviceGetHandleByIndex(i)
                row = dict(index=i, name=nv.nvmlDeviceGetName(h), watts=None, temperature_c=None)
                for field, fn, scale in (("watts", nv.nvmlDeviceGetPowerUsage, 1000), ("temperature_c", lambda handle: nv.nvmlDeviceGetTemperature(handle, nv.NVML_TEMPERATURE_GPU), 1)):
                    try:
                        value = float(fn(h))/scale
                        if math.isfinite(value) and value >= 0: row[field] = value
                    except nv.NVMLError: pass
                if isinstance(row["name"], bytes): row["name"] = row["name"].decode(errors="replace")
                gpus.append(row)
        finally: nv.nvmlShutdown()
    except Exception: pass
    return dict(observed_at=time.time(), gpus=gpus, watts=sum(g["watts"] for g in gpus) if gpus and all(g["watts"] is not None for g in gpus) else None,
        temperature_c=max((g["temperature_c"] for g in gpus if g["temperature_c"] is not None), default=None),
        scope="Observed NVIDIA GPU board power; CPU, wall power and other devices are excluded")


class AdaptiveOps:
    def __init__(self, store):
        self.store = store
        self.last = None
        self.task = None

    def start(self): self.task = asyncio.create_task(self.loop())

    async def stop(self):
        if self.task:
            self.task.cancel(); await asyncio.gather(self.task, return_exceptions=True)

    def feedback(self, form):
        outcome = self.store.get("outcome", form.outcome_id)
        if not outcome or not self.store.get("run", outcome["run_id"]): raise RouteError("Measured outcome not found")
        if self.store.get("run", outcome["run_id"]).get("mode") == "demo": raise RouteError("Scripted demo feedback cannot train live routing")
        # One editable vote per outcome; editing never multiplies its weight.
        return self.store.put("route_feedback", form.outcome_id, dict(id=form.outcome_id, **form.model_dump(), created_at=time.time()))

    def evidence(self, route, revision, category):
        outcomes = [o for o in self.store.all("outcome") if o["route"] == route and o["task_class"] == category and o.get("model_revision", "") == revision and revision][-100:]
        measured = [o for o in outcomes if o.get("measured")]
        feedback = {v["outcome_id"]:v for v in self.store.all("route_feedback")}
        votes = [feedback[o["id"]]["score"]/5 for o in outcomes if o["id"] in feedback]
        # Smoothed evidence, with owner feedback counted once per result.
        samples = len(measured)+len(votes)
        quality = (1+sum(o["passed"] for o in measured)+sum(votes))/(2+samples)
        latency = sorted(o["elapsed_ms"] for o in outcomes if o.get("elapsed_ms", 0) > 0)
        return dict(route=route, revision=revision, task_class=category, validation_samples=len(measured), feedback_samples=len(votes),
            quality=round(quality, 4), supported=samples >= 3, median_ms=latency[len(latency)//2] if latency else None,
            note="Task-specific checks and human scores; this is not a universal accuracy measure" if revision else "Pin a model revision before reusing evidence")

    def rank(self, run, agent, options):
        evidence = []
        for kind, data in options:
            revision = (data.get("model_revision", "") if isinstance(data, dict) else "") or run.get("model_revision", "")
            evidence.append(self.evidence(route_key(kind, data), revision, task_class(run, agent)))
        paired = list(zip(options, evidence))
        # Do not replace an admission/placement choice on sparse or unknown-version evidence.
        paired.sort(key=lambda pair: -pair[1]["quality"] if pair[1]["supported"] else -.5)
        if agent.get("id"): self.store.patch("agent", agent["id"], dict(learned_routing=[e for _, e in paired]))
        return [o for o, _ in paired]

    def sample(self, row):
        now = row["observed_at"]
        if self.last and self.last["watts"] is not None and row["watts"] is not None and 0 < now-self.last["observed_at"] <= 15:
            seconds = now-self.last["observed_at"]
            wh = (row["watts"]+self.last["watts"])/2*seconds/3600
            day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()
            daily = self.store.get("energy_day", day, dict(id=day, wh=0, sampled_seconds=0))
            self.store.put("energy_day", day, dict(daily, wh=daily["wh"]+wh, sampled_seconds=daily["sampled_seconds"]+seconds))
        self.last = row
        self.store.put("settings", "energy_observation", row)
        for old in self.store.all("energy_day")[:-90]: self.store.delete("energy_day", old["id"])

    def admission(self, kind, data, now=None):
        p = self.store.get("settings", "energy_policy", {})
        if not p.get("enabled") or kind not in {"local", "runtime", "device"}: return ""
        now = time.time() if now is None else now
        hour = dt.datetime.fromtimestamp(now, dt.timezone(dt.timedelta(minutes=p.get("utc_offset_minutes", 0)))).hour
        start, end = p.get("quiet_start", 22), p.get("quiet_end", 7)
        quiet = start <= hour < end if start < end else hour >= start or hour < end if start != end else False
        if p.get("quiet_enabled") and quiet: return "Device quiet hours are active"
        row = self.store.get("settings", "energy_observation", {})
        if kind == "device": row = self.store.get("device", data["device_id"], {}).get("hardware", {}).get("energy", {})
        fresh = 0 <= now-row.get("observed_at", 0) <= 15
        if p.get("require_telemetry") and (not fresh or row.get("temperature_c") is None or row.get("watts") is None): return "Fresh power and temperature measurements are required"
        if fresh and row.get("temperature_c") is not None and row["temperature_c"] >= p.get("max_temperature_c", 85): return "Observed GPU temperature exceeds the selected ceiling"
        if kind in {"local", "runtime"} and p.get("max_daily_wh"):
            day = dt.datetime.fromtimestamp(now, dt.timezone.utc).date().isoformat()
            if self.store.get("energy_day", day, {}).get("wh", 0) >= p["max_daily_wh"]: return "Measured local GPU energy budget exhausted for this UTC day"
        return ""

    def watts(self, kind, data):
        row = self.store.get("device", data["device_id"], {}).get("hardware", {}).get("energy", {}) if kind == "device" else self.store.get("settings", "energy_observation", {}) if kind in {"local", "runtime"} else {}
        if time.time()-row.get("observed_at", 0) > 15: return float("inf")
        return row.get("watts") if row.get("watts") is not None else float("inf")

    async def loop(self):
        while True:
            if self.store.get("settings", "energy_policy", {}).get("enabled") and not (getattr(self,"app",None) and self.app.state.backup_active):
                self.sample(await asyncio.to_thread(telemetry))
            else: self.last = None
            await asyncio.sleep(5)
