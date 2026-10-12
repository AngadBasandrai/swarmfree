import asyncio
import hashlib
import ipaddress
import json
import time
from urllib.parse import urlparse
import httpx
from .hardware import hardware, inventory, fit_model
from .schema import ProfileInput
from .store import uid

PROVIDERS = [
    dict(id="google", name="Google Gemini", base_url="https://generativelanguage.googleapis.com/v1beta/openai", scope="Google Cloud project", docs="https://ai.google.dev/gemini-api/docs/rate-limits", hint="Add each account's key. Keys in the same project share quota."),
    dict(id="groq", name="Groq", base_url="https://api.groq.com/openai/v1", scope="Organization", docs="https://console.groq.com/docs/rate-limits", hint="Use the organization ID as the quota group."),
    dict(id="openrouter", name="OpenRouter", base_url="https://openrouter.ai/api/v1", scope="Account", docs="https://openrouter.ai/docs/guides/routing/routers/free-router", hint="Free-only runs use openrouter/free or models ending in :free."),
    dict(id="mistral", name="Mistral", base_url="https://api.mistral.ai/v1", scope="Workspace", docs="https://docs.mistral.ai/getting-started/quickstarts/studio/activate-and-generate-api-key", hint="Confirm your account's free eligibility and workspace quota."),
    dict(id="freellmpool", name="freellmpool proxy", base_url="http://127.0.0.1:8765/v1", scope="Gateway", docs="https://github.com/0xzr/freellmpool", hint="Connect a separately installed proxy. Its downstream routes are outside this app's quota tracking."),
    dict(id="compatible", name="Compatible endpoint", base_url="http://127.0.0.1:1234/v1", scope="Account / gateway", docs="", hint="LM Studio, llama.cpp, or another compatible endpoint. Supply the endpoint and model ID."),
    dict(id="community", name="Volunteer hub", base_url="https://YOUR-HUB/v1", scope="Hub client", docs="", hint="Connect a self-hosted volunteer hub. Missions must consent before sending prompts to it."),
    dict(id="exo", name="Exo distributed runtime", base_url="http://127.0.0.1:52415/v1", scope="Local cluster", docs="https://github.com/exo-explore/exo", hint="Experimental adapter to a separately installed Exo cluster; sharding is managed by Exo."),
]
CATALOG = {p["id"]: p for p in PROVIDERS}


class RouteError(Exception):
    def __init__(self, message, retry_after=None):
        super().__init__(message)
        self.retry_after = retry_after


def validate_base_url(url, loopback_only=False):
    p = urlparse(url)
    if not p.hostname or p.username or p.password or p.query or p.fragment:
        raise ValueError("Use a base URL without credentials, query, or fragment")
    loopback = p.hostname == "localhost"
    try:
        loopback = loopback or ipaddress.ip_address(p.hostname).is_loopback
    except ValueError:
        pass
    if loopback_only and not loopback:
        raise ValueError("The freellmpool proxy must run on loopback in this release")
    if p.scheme != "https" and not (p.scheme == "http" and loopback):
        raise ValueError("Remote endpoints require HTTPS; HTTP is allowed on loopback only")
    return url.rstrip("/")


class Providers:
    def __init__(self, store, vault, ollama):
        self.store, self.vault, self.ollama = store, vault, ollama
        self.scope_lock = asyncio.Lock()
        self.local_lock = asyncio.Lock()
        self.runtimes = None

    def add(self, form: ProfileInput):
        id = uid("p_")
        data = form.model_dump(exclude={"api_key", "ephemeral"})
        defaults = CATALOG[form.provider]
        base = form.base_url or defaults["base_url"]
        if form.provider in {"google", "groq", "openrouter", "mistral"} and base.rstrip("/") != defaults["base_url"]:
            raise ValueError("Use the official endpoint for this provider; select Compatible endpoint for custom servers")
        data["base_url"] = validate_base_url(base, form.provider in {"freellmpool", "exo"})
        if form.provider in {"google", "groq", "openrouter", "mistral"} and not form.api_key.strip():
            raise ValueError("This provider needs an API key")
        # Account labels are descriptive; they never create independent quota.
        scope = form.quota_scope.strip().casefold() or "unknown-shared"
        data["quota_group"] = f"{form.provider}:{scope}"
        data["scope_verified"] = False
        data["id"] = id
        data["created_at"] = time.time()
        data["fingerprint"] = hashlib.sha256(form.api_key.encode()).hexdigest()[:8] if form.api_key else "keyless"
        for profile in self.store.all("profile"):
            if form.api_key and profile["fingerprint"] == data["fingerprint"] and self.vault.get(profile["id"]) == form.api_key:
                raise ValueError("This key is already linked. Edit its profile instead of duplicating it.")
        data["storage"] = self.vault.set(id, form.api_key.strip(), form.ephemeral)
        data["health"] = "untested"
        return self.store.put("profile", id, data)

    def public(self):
        now = time.time()
        profiles = []
        for p in self.store.all("profile"):
            if p.get("internal"): continue
            quota = self.store.get("quota", p["quota_group"], {})
            key_required = p["storage"] != "none"
            profiles.append(dict(**p, key_available=bool(self.vault.get(p["id"])) if key_required else True,
                cooldown_seconds=max(0, round(quota.get("cooldown_until", 0)-now)),
                recent_requests=len([t for t in quota.get("requests", []) if t > now-60]),
                observed_quota=quota.get("observed",{})))
        return profiles

    async def discover(self, id):
        p = self.store.get("profile", id)
        if not p:
            raise RouteError("Profile not found")
        async with httpx.AsyncClient(timeout=15, follow_redirects=False) as c:
            try:
                r = await c.get(p["base_url"] + "/models", headers=self.headers(p))
                r.raise_for_status()
                models = [m["id"] for m in r.json().get("data", []) if isinstance(m.get("id"), str)]
                self.store.patch("profile", id, {"health": "ready", "models": models[:500], "last_checked": time.time()})
                return models[:500]
            except (httpx.HTTPError, ValueError) as e:
                status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
                self.store.patch("profile", id, {"health": "error"})
                raise RouteError(f"Model discovery failed{f' (HTTP {status})' if status else ''}. Check endpoint and key.") from e

    def headers(self, p):
        key = self.vault.get(p["id"])
        if p["storage"] != "none" and not key:
            raise RouteError(f"{p['label']}: key unavailable; reconnect this profile")
        return {"Authorization": "Bearer " + key} if key else {}

    def eligible(self, p, free_only):
        if not p["enabled"] or not p.get("model") or p.get("health") == "unauthorized":
            return False
        if p["storage"] != "none" and not self.vault.get(p["id"]):
            return False
        if free_only:
            if not p["free_eligible"] or p["provider"] in {"freellmpool", "compatible"}:
                return False
            if p["provider"] == "openrouter" and p["model"] != "openrouter/free" and not p["model"].endswith(":free"):
                return False
        return True

    async def claim(self, p):
        async with self.scope_lock:
            now = time.time()
            scope = p["quota_group"]
            q = self.store.get("quota", scope, {})
            if q.get("cooldown_until", 0) > now:
                raise RouteError(f"{p['label']}: quota group is cooling down", retry_after=q["cooldown_until"]-now)
            recent = [t for t in q.get("requests", []) if t > now-60]
            # Use the smallest configured limit across profiles that share a scope.
            limits = [other["requests_per_minute"] for other in self.store.all("profile") if other["quota_group"] == scope]
            if len(recent) >= min(limits, default=p["requests_per_minute"]):
                raise RouteError(f"{p['label']}: local request budget reached for this quota group", retry_after=min(recent)+60-now)
            q["requests"] = recent + [now]
            self.store.put("quota", scope, q)

    async def cooldown(self, p, seconds=60):
        async with self.scope_lock:
            q = self.store.get("quota", p["quota_group"], {})
            q["cooldown_until"] = time.time() + min(max(seconds, 1), 86400)
            self.store.put("quota", p["quota_group"], q)

    def retry_delay(self):
        now, delays = time.time(), []
        for profile in self.public():
            quota = self.store.get("quota", profile["quota_group"], {})
            recent = [t for t in quota.get("requests", []) if t > now-60]
            if profile["cooldown_seconds"]: delays.append(profile["cooldown_seconds"])
            elif len(recent) >= profile["requests_per_minute"]: delays.append(min(recent)+60-now)
        return min(delays) if delays else None

    async def doctor(self, kind, target, *, vision=False):
        started = time.monotonic()
        messages = [{"role": "user", "content": 'Reply ONLY with the JSON object {"swarm_probe":true,"value":7}.'}]
        if kind == "profile":
            profile = self.store.get("profile", target)
            if not profile or not profile.get("model"):
                raise RouteError("Select a model first")
            stream = self.compatible(profile, messages)
            key, model = profile["id"] + ":" + profile["model"], profile["model"]
        else:
            model, key = target, "local:" + target
            stream = self.local(target, messages, self.store.get("settings", "budgets"))
        text = ""
        try:
            async for chunk in stream:
                text += chunk
                if len(text) > 4000: break
        finally:
            await stream.aclose()
        try:
            data = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
            structured = data == {"swarm_probe": True, "value": 7}
        except ValueError:
            structured = False
        record = dict(id=key, model=model, json=structured, vision=False, vision_status="not tested",
            streaming=bool(text), latency_ms=round((time.monotonic()-started)*1000), checked_at=time.time(),
            context_tokens=self.store.get("settings", "budgets")["context"], context_source="configured ceiling, not a measured maximum")
        if vision:
            import base64,io
            from PIL import Image
            buffer=io.BytesIO()
            Image.new("RGB",(24,24),(255,0,0)).save(buffer,format="PNG")
            probe=[dict(role="user",content='What is the dominant color? Reply ONLY with JSON {"color":"NAME"}.',images=[base64.b64encode(buffer.getvalue()).decode()],mime="image/png")]
            stream=self.compatible(profile,probe) if kind=="profile" else self.local(target,probe,self.store.get("settings","budgets"))
            answer=""
            try:
                async for chunk in stream:
                    answer+=chunk
                    if len(answer)>4000: break
            finally: await stream.aclose()
            try: record["vision"]=json.loads(answer.strip().removeprefix("```json").removesuffix("```").strip()).get("color","").casefold()=="red"
            except (ValueError,AttributeError): record["vision"]=False
            record["vision_status"]="passed red-image probe" if record["vision"] else "failed red-image probe"
        if kind == "local":
            inv = await inventory(self.ollama)
            loaded = next((m for m in inv["loaded"] if m["name"] == model), None)
            record["observed_runtime"] = loaded
        return self.store.put("capability", key, record)

    async def compatible(self, p, messages):
        from .protocol import usage_counts
        from .media import compatible_messages
        messages=compatible_messages(messages)
        headers = self.headers(p)
        if p.get("_traceparent"): headers["traceparent"]=p["_traceparent"]
        await self.claim(p)
        async with httpx.AsyncClient(timeout=httpx.Timeout(90, connect=10), follow_redirects=False,trust_env=False) as c:
            try:
                body=dict(model=p["model"], messages=messages, stream=True, max_tokens=p.get("_max_tokens",1536))
                if p.get("_usage") is not None: body["stream_options"] = dict(include_usage=True)
                if p.get("_tools"): body["tools"] = p["_tools"]
                if p.get("_output_schema"): body["response_format"] = dict(type="json_schema", json_schema=dict(name="handoff", strict=True, schema=p["_output_schema"]))
                if p["provider"]=="community": body["duplicate_check"]=p.get("verify_duplicates",False)
                async with c.stream("POST", p["base_url"] + "/chat/completions", headers=headers,json=body) as r:
                    self.observe_quota(p,r.headers)
                    if r.status_code == 429:
                        try:
                            retry = float(r.headers.get("retry-after", "60"))
                        except ValueError:
                            retry = 60
                        await self.cooldown(p, retry)
                        raise RouteError(f"{p['label']}: provider rate limit; the entire quota group is cooling down")
                    if r.status_code in {401, 403}:
                        self.store.patch("profile", p["id"], {"health": "unauthorized"})
                        raise RouteError(f"{p['label']}: provider rejected the credentials or permissions")
                    r.raise_for_status()
                    finished = False
                    calls = {}
                    async for line in r.aiter_lines():
                        if not line.startswith("data:"):
                            continue
                        payload = line[5:].strip()
                        if payload == "[DONE]":
                            finished = True
                            break
                        data = json.loads(payload)
                        if p.get("_usage") is not None: p["_usage"].update(usage_counts(data))
                        if "error" in data:
                            raise RouteError(f"{p['label']}: provider reported a streaming error")
                        for choice in data.get("choices", []):
                            if choice.get("index", 0) != 0: continue
                            if choice.get("finish_reason"):
                                finished = True
                            content = choice.get("delta", {}).get("content")
                            for part in choice.get("delta", {}).get("tool_calls", []):
                                index = part.get("index", 0)
                                if not isinstance(index, int) or not 0 <= index < 8: raise RouteError("Too many native tool calls")
                                call = calls.setdefault(index, dict(id="", type="function", function=dict(name="", arguments="")))
                                if part.get("id"): call["id"] = part["id"]
                                for key in ("name", "arguments"):
                                    call["function"][key] += part.get("function", {}).get(key, "")
                                    if len(call["function"][key]) > 120000: raise RouteError("Native arguments exceed the limit")
                            if isinstance(content, str):
                                yield content
                    if not finished:
                        raise RouteError(f"{p['label']}: incomplete stream; work was kept as an interrupted draft")
                    if p.get("_native_calls") is not None: p["_native_calls"].extend(calls[i] for i in sorted(calls))
                    self.store.patch("profile", p["id"], {"health": "ready"})
            except httpx.HTTPError as e:
                status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) else None
                raise RouteError(f"{p['label']}: request failed{f' (HTTP {status})' if status else ''}") from e
            except (ValueError, KeyError, TypeError) as e:
                raise RouteError(f"{p['label']}: unsupported streaming response") from e

    def observe_quota(self,p,headers):
        observed={}
        for name in ("x-ratelimit-remaining-requests","x-ratelimit-remaining-tokens","x-ratelimit-reset-requests","x-ratelimit-reset-tokens","retry-after"):
            value=headers.get(name)
            if value and len(value)<100: observed[name]=value
        if observed:
            with self.store.lock:
                quota=self.store.get("quota",p["quota_group"],{})
                quota["observed"]=dict(headers=observed,observed_at=time.time(),source="provider response; scope/units may vary")
                self.store.put("quota",p["quota_group"],quota)

    async def local(self, model, messages, budgets):
        async with self.local_lock:
            selected,fit=await self.prepare_local(model,budgets)
            settings=self.store.get("settings","residency",{})
            owned=self.store.get("settings","local_resident",{}).get("models",[])
            self.store.put("settings","local_resident",dict(models=list(set(owned+[model]))[-100:]))
            async for text in ollama_stream(self.ollama, model, messages, dict(budgets,keep_alive=settings.get("keep_alive_seconds",300)), fit["placement"]):
                yield text

    def prospective_fit(self,model,hw,budgets,loaded):
        owned=self.store.get("settings","local_resident",{}).get("models",[])
        removed=[m for m in loaded if m["name"] in owned and m["name"]!=model["name"]]
        credit=sum(max(0,m.get("size",0)-m.get("size_vram",0))/1024**3 for m in removed)
        gpu_credit=sum(m.get("size_vram",0)/1024**3 for m in removed)
        if self.runtimes:
            runtime_rows=[r for r in self.store.all("runtime") if r["id"] in self.runtimes.processes and self.runtimes.processes[r["id"]].returncode is None]
            credit+=sum(r.get("estimated_ram_gb",0) for r in runtime_rows)
            gpu_credit+=sum(r.get("estimated_ram_gb",0) for r in runtime_rows if r.get("gpu_layers"))
        estimate_hw=dict(hw,ram_available_gb=min(hw.get("ram_total_gb",hw["ram_available_gb"]),hw["ram_available_gb"]+credit),gpus=[dict(g,free_gb=min(g["total_gb"],(g.get("free_gb") or 0)+gpu_credit)) for g in hw["gpus"]])
        return fit_model(model,estimate_hw,budgets,[m for m in loaded if m not in removed])

    async def prepare_local(self,model,budgets):
        if self.runtimes: await self.runtimes.release_others()
        inv=await inventory(self.ollama)
        selected=next((m for m in inv["models"] if m["name"]==model),None)
        if not selected: raise RouteError("Local model is not installed or Ollama is unavailable")
        fit=fit_model(selected,hardware(),budgets,inv["loaded"])
        if not fit["fits"]:
            owned=self.store.get("settings","local_resident",{}).get("models",[])
            async with httpx.AsyncClient(timeout=15,trust_env=False) as client:
                for resident in inv["loaded"]:
                    if resident["name"]!=model and resident["name"] in owned:
                        r=await client.post(self.ollama+"/api/generate",json=dict(model=resident["name"],keep_alive=0))
                        r.raise_for_status()
            inv=await inventory(self.ollama);fit=fit_model(selected,hardware(),budgets,inv["loaded"])
        if not fit["fits"]: raise RouteError(f"{model}: {fit['reason']} (estimated {fit['estimate_gb']} GiB)")
        return selected,fit


async def ollama_stream(url, model, messages, budgets, placement):
    from .protocol import usage_counts
    options = dict(num_ctx=budgets["context"], num_predict=min(budgets.get("max_tokens",1536), budgets["context"] // 3, 4096))
    if placement == "cpu":
        options["num_gpu"] = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10), follow_redirects=False, trust_env=False) as c:
        try:
            from .protocol import native_history
            clean=[{k:v for k,v in m.items() if k in {"role","content","images","tool_calls","tool_name"}} for m in native_history(messages,ollama=True)]
            body=dict(model=model, messages=clean, stream=True, keep_alive=budgets.get("keep_alive",300), options=options)
            if budgets.get("_tools"): body["tools"] = budgets["_tools"]
            if budgets.get("_output_schema"): body["format"] = budgets["_output_schema"]
            async with c.stream("POST", url + "/api/chat", json=body) as r:
                r.raise_for_status()
                done = False
                async for line in r.aiter_lines():
                    if not line:
                        continue
                    data = json.loads(line)
                    if data.get("error"):
                        raise RouteError("Ollama reported an inference error")
                    text = data.get("message", {}).get("content", "")
                    if budgets.get("_native_calls") is not None:
                        budgets["_native_calls"].extend(data.get("message", {}).get("tool_calls", []))
                    if text:
                        yield text
                    if data.get("done"):
                        done = True
                        if budgets.get("_usage") is not None: budgets["_usage"].update(usage_counts(data, ollama=True))
                if not done:
                    raise RouteError("Ollama stream ended before completion")
        except httpx.HTTPError as e:
            raise RouteError("Ollama request failed. Check the runtime and available memory.") from e
