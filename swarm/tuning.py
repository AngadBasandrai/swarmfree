"""Explicit, bounded local benchmarks and conservative placement recommendations."""
import asyncio
import time
import httpx
from .hardware import hardware,inventory,fit_model
from .providers import RouteError
from .store import uid


async def tune(store,providers,models,contexts):
    inv=await inventory(providers.ollama)
    hw=hardware()
    budgets=store.get("settings","budgets")
    installed={m["name"]:m for m in inv["models"]}
    if not models or any(m not in installed for m in models): raise RouteError("Select installed Ollama models to benchmark")
    rows=[]
    async with providers.local_lock:
        async with httpx.AsyncClient(timeout=180,trust_env=False,follow_redirects=False) as client:
            for name in list(dict.fromkeys(models))[:3]:
                for context in sorted(set(contexts))[:3]:
                    active=await inventory(providers.ollama)
                    fit=fit_model(installed[name],hardware(),dict(budgets,context=context),active["loaded"])
                    row=dict(model=name,context=context,fit=fit,quantization=installed[name].get("details",{}).get("quantization_level","unknown"))
                    if fit["fits"]:
                        options=dict(num_ctx=context,num_predict=64)
                        if fit["placement"]=="cpu": options["num_gpu"]=0
                        started=time.monotonic()
                        try:
                            response=await client.post(providers.ollama+"/api/chat",json=dict(model=name,messages=[dict(role="user",content="Explain how a queue works in three concise sentences.")],stream=False,keep_alive=0,options=options))
                            response.raise_for_status()
                            data=response.json()
                            if not data.get("done") or data.get("error"): raise ValueError()
                            count,duration=data.get("eval_count",0),data.get("eval_duration",0)
                            row.update(status="measured",elapsed_ms=round((time.monotonic()-started)*1000),tokens_per_second=round(count/(duration/1e9),2) if duration else None,
                                generated_tokens=count,prompt_tokens=data.get("prompt_eval_count"),load_ms=round(data.get("load_duration",0)/1e6))
                        except (httpx.HTTPError,ValueError,TypeError): row.update(status="failed",error="Benchmark failed; inspect Ollama and available memory")
                    else: row["status"]="excluded"
                    rows.append(row)
    measured=[r for r in rows if r["status"]=="measured"]
    measured.sort(key=lambda r:(-(r.get("tokens_per_second") or 0),r["fit"]["estimate_gb"]))
    recommendation=dict(model=measured[0]["model"],context=measured[0]["context"],placement=measured[0]["fit"]["placement"],quantization=measured[0]["quantization"],concurrency=1) if measured else None
    id=uid("tuning_")
    return store.put("tuning",id,dict(id=id,created_at=time.time(),hardware=hw,rows=rows,recommendation=recommendation,
        note="Short performance sample, not a quality/context accuracy benchmark. Local inference stays serialized; linked devices can run in parallel"))
