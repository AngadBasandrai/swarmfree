import asyncio
import hashlib
import json
import secrets
import time
from .providers import RouteError
from .store import uid


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


class PrivatePool:
    """Inference-only private workers. No provider keys or workspace tools leave the owner."""
    def __init__(self, store):
        self.store = store

    def invite(self):
        token = secrets.token_urlsafe(32)
        self.store.put("invite", digest(token), dict(expires=time.time()+300, used=False))
        return dict(token=token, expires_at=time.time()+300)

    def join(self, form):
        with self.store.lock:
            invite = self.store.get("invite", digest(form.invite))
            if not invite or invite["used"] or invite["expires"] < time.time():
                raise ValueError("Invitation expired or already used")
            self.store.patch("invite", digest(form.invite), {"used": True})
            token = secrets.token_urlsafe(32)
            id = uid("d_")
            self.store.put("device", id, dict(id=id, name=form.name, token_hash=digest(token),
                approved=False, revoked=False, last_seen=time.time(), hardware=form.hardware,
                models=form.models, budgets=form.budgets.model_dump(),execution=form.execution,execution_approved=False))
            return dict(device_id=id, token=token, status="pending-owner-approval")

    def authenticate(self, id, token):
        device = self.store.get("device", id)
        if not device or device["revoked"] or not secrets.compare_digest(device["token_hash"], digest(token)):
            raise ValueError("Worker credentials are invalid or revoked")
        return device

    def public(self):
        output=[]
        for d in self.store.all("device"):
            receipts=[r for r in self.store.all("receipt") if r["device_id"]==d["id"]][-20:]
            successful=[r for r in receipts if r["status"]=="done"]
            output.append(dict(**{k:v for k,v in d.items() if k!="token_hash"},online=time.time()-d["last_seen"]<35,
                health=dict(sample_jobs=len(receipts),completion_rate=round(len(successful)/len(receipts),3) if receipts else None,
                    mean_elapsed_ms=round(sum(r["elapsed_ms"] for r in successful)/len(successful)) if successful else None,
                    note="Observed completion/latency; answer correctness is not measured")))
        return output

    def receipt(self,job,status):
        if self.store.get("receipt",job["id"]): return
        self.store.put("receipt",job["id"],dict(id=job["id"],device_id=job["device_id"],model=job["model"],status=status,
            elapsed_ms=round(max(0,time.time()-job.get("created_at",time.time()))*1000),output_characters=len(job.get("text","")),
            estimated_output_tokens=round(len(job.get("text",""))/2),client_id=job.get("client_id",""),created_at=time.time(),trace_id=job.get("trace_id",""),parent_span_id=job.get("parent_span_id","")))

    def heartbeat(self, device, data):
        changes = dict(last_seen=time.time())
        for key in ("hardware", "models", "budgets", "execution"):
            if key in data:
                changes[key] = data[key]
        self.store.patch("device", device["id"], changes)
        # Heartbeats cannot approve or restore a revoked device.
        return dict(approved=device["approved"])

    def lease(self, device):
        with self.store.lock:
            if not device["approved"]:
                return None
            now = time.time()
            for job in self.store.all("job"):
                if job["device_id"] == device["id"] and job["status"] == "leased":
                    if job["lease_until"] > now and job["deadline"] > now:
                        return None
                    self.store.patch("job", job["id"], {"status": "expired"})
                    self.receipt(job,"expired")
            for job in self.store.all("job"):
                if job["device_id"] != device["id"] or job["status"] != "queued":
                    continue
                if job["deadline"] <= now:
                    self.store.patch("job", job["id"], {"status": "expired"})
                    self.receipt(job,"expired")
                    continue
                if job.get("kind")=="execution" and (not device.get("execution_approved") or not device.get("execution",{}).get("enabled")):
                    self.store.patch("job",job["id"],dict(status="failed",error="Execution permission was withdrawn"));continue
                token = secrets.token_urlsafe(24)
                self.store.patch("job", job["id"], dict(status="leased", lease_hash=digest(token), lease_until=now+30))
                return dict(id=job["id"], model=job["model"], messages=job["messages"], lease=token,
                    deadline=job["deadline"], max_tokens=job.get("max_tokens", 1536),kind=job.get("kind","inference"),payload=job.get("payload"),native_tools=job.get("native_tools",[]))
        return None

    def result(self, device, job_id, lease, data):
        with self.store.lock:
            job = self.store.get("job", job_id)
            if not job or job["device_id"] != device["id"] or job["status"] != "leased" or not device["approved"]:
                raise ValueError("Job is cancelled, completed, or not assigned to this device")
            now = time.time()
            if job["deadline"] < now or job["lease_until"] < now or not secrets.compare_digest(job["lease_hash"], digest(lease)):
                raise ValueError("Stale or expired job lease")
            text = data.get("text", "")
            if not isinstance(text, str) or len(text) > 100000:
                raise ValueError("Invalid inference output")
            if not text.startswith(job.get("text", "")):
                raise ValueError("Streaming results must append to the existing output")
            changes = dict(text=text, lease_until=min(now+30, job["deadline"]))
            if job.get("kind")=="execution":
                if not device.get("execution_approved") or not device.get("execution",{}).get("enabled"): raise ValueError("Execution approval was withdrawn")
                if data.get("done") and not data.get("error"):
                    from .remote_execution import checked_files
                    result=data.get("execution_result")
                    if not isinstance(result,dict) or not isinstance(result.get("exit_code"),int) or not isinstance(result.get("output",""),str) or len(result.get("output",""))>64000: raise ValueError("Invalid remote execution result")
                    try: checked_files(result.get("files",{}))
                    except RouteError as e: raise ValueError(str(e)) from e
                    changes["execution_result"]=result
            if data.get("done"):
                usage=data.get("usage",{})
                if usage:
                    if not isinstance(usage,dict) or any(isinstance(usage.get(k),bool) or not isinstance(usage.get(k),int) or not 0<=usage[k]<=100000000 for k in ("input_tokens","output_tokens","cache_tokens")):
                        raise ValueError("Invalid worker token accounting")
                    usage={k:usage[k] for k in ("input_tokens","output_tokens","cache_tokens")};usage.update(reported=True,source="linked worker")
                changes["usage"]=usage
                calls=data.get("native_calls",[])
                if not isinstance(calls,list) or len(calls)>8 or len(json.dumps(calls))>120000: raise ValueError("Invalid worker native calls")
                changes["native_calls"]=calls
            if data.get("error"):
                changes.update(status="failed", error="Worker inference failed; inspect the worker terminal")
            elif data.get("done"):
                changes["status"] = "done"
            if changes.get("status") in {"done", "failed"}:
                field = "completed_jobs" if changes["status"] == "done" else "failed_jobs"
                self.store.patch("device", device["id"], {field: device.get(field, 0)+1})
                self.receipt(dict(job,**changes),changes["status"])
            return self.store.patch("job", job_id, changes)

    async def infer(self, device_id, model, messages, max_tokens=1536, *, allow_reassign=False,client_id="",trace_id="",parent_span_id="",usage=None,native_tools=None,native_calls=None):
        device = self.store.get("device", device_id)
        if not device or not device["approved"] or device["revoked"] or time.time()-device["last_seen"] > 35:
            raise RouteError("The selected device is not approved or online")
        job_id = uid("j_")
        self.store.put("job", job_id, dict(id=job_id, device_id=device_id, model=model, messages=messages,
            status="queued", text="", native_tools=native_tools or [], max_tokens=max(1, min(int(max_tokens), 4096)), deadline=time.time()+180,created_at=time.time(),client_id=client_id,trace_id=trace_id,parent_span_id=parent_span_id))
        cursor = 0
        try:
            while True:
                job = self.store.get("job", job_id)
                if len(job["text"]) > cursor:
                    yield job["text"][cursor:]
                    cursor = len(job["text"])
                if job["status"] == "done":
                    if usage is not None: usage.update(job.get("usage",{}))
                    if native_calls is not None: native_calls.extend(job.get("native_calls",[]))
                    return
                if job["status"] in {"failed", "cancelled", "expired"}:
                    self.receipt(job,job["status"])
                    if allow_reassign and not cursor and job["status"]!="cancelled":
                        busy={j["device_id"] for j in self.store.all("job") if j["status"] in {"queued","leased"} and j["deadline"]>time.time()}
                        alternatives=[d for d in self.public() if d["id"]!=device_id and d["id"] not in busy and d["approved"] and not d["revoked"] and d["online"] and any(m["name"]==model for m in d["models"])]
                        if alternatives:
                            async for chunk in self.infer(alternatives[0]["id"],model,messages,max_tokens,client_id=client_id,trace_id=trace_id,parent_span_id=parent_span_id,usage=usage,native_tools=native_tools,native_calls=native_calls): yield chunk
                            return
                    raise RouteError(job.get("error", "Worker job was interrupted"))
                if time.time() > job["deadline"] or (job["status"] == "leased" and time.time() > job["lease_until"]):
                    self.store.patch("job", job_id, {"status": "expired"})
                    continue
                await asyncio.sleep(.15)
        finally:
            job = self.store.get("job", job_id)
            if job["status"] in {"queued", "leased"}:
                self.store.patch("job", job_id, {"status": "cancelled"})
                self.receipt(job,"cancelled")
