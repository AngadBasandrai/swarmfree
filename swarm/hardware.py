import asyncio
import os
import platform
import psutil
import httpx
from .gpu import extra_gpus

GIB = 1024**3


def hardware():
    mem = psutil.virtual_memory()
    gpus = []
    try:
        import pynvml
        pynvml.nvmlInit()
        try:
            for i in range(pynvml.nvmlDeviceGetCount()):
                h = pynvml.nvmlDeviceGetHandleByIndex(i)
                m = pynvml.nvmlDeviceGetMemoryInfo(h)
                name = pynvml.nvmlDeviceGetName(h)
                gpus.append(dict(id=str(i), name=name.decode() if isinstance(name, bytes) else name,
                    total_gb=round(m.total/GIB, 2), free_gb=round(m.free/GIB, 2), backend="cuda"))
        finally:
            pynvml.nvmlShutdown()
    except Exception:
        pass
    known = {g["name"] for g in gpus}
    for gpu in extra_gpus():
        if gpu["name"] not in known and not (gpu.get("vendor") == 0x10de and gpus): gpus.append(gpu)
    if platform.system() == "Darwin" and platform.machine() == "arm64":
        gpus.append(dict(id="metal", name="Apple Metal (unified memory)", total_gb=round(mem.total/GIB, 2),
            free_gb=round(mem.available/GIB, 2), backend="metal", free_source="shared RAM"))
    return dict(name=platform.node(), system=platform.system(), machine=platform.machine(),
        cpu=platform.processor() or platform.machine(), cores=psutil.cpu_count(logical=False),
        threads=psutil.cpu_count(), ram_total_gb=round(mem.total/GIB, 2), ram_available_gb=round(mem.available/GIB, 2),
        gpus=gpus, unified_memory=platform.system() == "Darwin" and platform.machine() == "arm64",
        gpu_detection="platform adapters" if gpus else "unavailable", note="GPU capacity and measured free memory are separate. Driver/runtime support varies. Apple RAM and GPU memory share one pool.")


def default_budgets(hw):
    return dict(ram_gb=round(hw["ram_total_gb"] * .5, 1),
        vram_gb=0 if hw.get("unified_memory") else round(max([g["total_gb"] for g in hw["gpus"]], default=0) * .7, 1),
        context=4096, allow_cpu=True)


def fit_model(model, hw, budgets, loaded=None):
    """Conservative admission estimate, not a hard process-level allocator."""
    weights = model.get("size", 0) / GIB
    if weights <= 0:
        return dict(fits=False, placement="unknown", estimate_gb=0, reason="Model size is unknown; refresh inventory")
    # KV cache varies by architecture and quantization. Explicitly label this estimate.
    estimate = round(weights * 1.15 + .5 + budgets["context"] / 4096 * .35, 2)
    resident = next((m for m in (loaded or []) if m.get("name") == model["name"]), None)
    if resident and resident.get("context_length", 0) >= budgets["context"]:
        estimate = max(estimate, round(resident.get("size", 0)/GIB, 2))
    other_vram = sum(m.get("size_vram", 0)/GIB for m in (loaded or []) if m.get("name") != model["name"])
    resident_vram = sum(m.get("size_vram", 0)/GIB for m in (loaded or []) if m.get("name") == model["name"])
    gpu_free = max([g.get("free_gb") or 0 for g in hw["gpus"]], default=0)
    if not hw["unified_memory"] and hw["gpus"] and estimate <= min(budgets["vram_gb"]-other_vram, gpu_free+resident_vram):
        return dict(fits=True, placement="gpu", estimate_gb=estimate, reason="Fits the measured free VRAM and configured budget")
    ram_free = hw["ram_available_gb"]
    def ram_size(m):
        return m.get("size", 0) if hw["unified_memory"] else max(0, m.get("size", 0)-m.get("size_vram", 0))
    other_ram = sum(ram_size(m)/GIB for m in (loaded or []) if m.get("name") != model["name"])
    resident_ram = sum(ram_size(m)/GIB for m in (loaded or []) if m.get("name") == model["name"])
    if hw["unified_memory"] and any(g.get("backend") == "metal" for g in hw["gpus"]) and estimate <= min(budgets["ram_gb"]-other_ram, ram_free+resident_ram):
        return dict(fits=True, placement="gpu", estimate_gb=estimate, reason="Fits the shared RAM budget; Ollama can use Metal on supported models")
    if budgets["allow_cpu"] and estimate <= min(budgets["ram_gb"]-other_ram, ram_free+resident_ram):
        return dict(fits=True, placement="cpu", estimate_gb=estimate, reason="Fits RAM; CPU placement is supported but may be slower")
    return dict(fits=False, placement="none", estimate_gb=estimate, reason="Exceeds available memory or the configured budget")


async def inventory(url):
    async with httpx.AsyncClient(timeout=3, follow_redirects=False, trust_env=False) as c:
        try:
            tags, running = await asyncio.gather(c.get(url + "/api/tags"), c.get(url + "/api/ps"))
            tags.raise_for_status()
            running.raise_for_status()
            return dict(connected=True, models=tags.json().get("models", []), loaded=running.json().get("models", []))
        except (httpx.HTTPError, ValueError):
            return dict(connected=False, models=[], loaded=[], error="Ollama is not reachable. Start Ollama to use local models.")


def ollama_url():
    from urllib.parse import urlparse
    url = os.getenv("SWARM_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    parsed = urlparse(url)
    if parsed.hostname not in {"127.0.0.1", "localhost", "::1"} or parsed.scheme not in {"http", "https"} or parsed.username:
        raise ValueError("SWARM_OLLAMA_URL must be a loopback URL. Pair a worker for remote devices.")
    return url
