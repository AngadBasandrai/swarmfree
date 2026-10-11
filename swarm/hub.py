"""Self-hosted volunteer inference broker. Workers connect outbound using leases."""
import argparse
import asyncio
import json
import os
import secrets
import time
from collections import deque
from fastapi import HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
import uvicorn
from .app import create_app
from .network import digest
from .providers import RouteError
from .store import uid


class ClientInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    rpm: int = Field(default=6, ge=1, le=60)
    daily_requests: int = Field(default=100, ge=1, le=10000)


class Message(BaseModel):
    role: str = Field(pattern="^(system|user|assistant)$")
    content: str = Field(max_length=24000)


class CompletionInput(BaseModel):
    model: str = Field(min_length=1, max_length=200)
    messages: list[Message] = Field(min_length=1, max_length=100)
    stream: bool = True
    max_tokens: int = Field(default=1536, ge=1, le=4096)
    duplicate_check: bool=False


def create_hub(data_dir=None, token=None):
    app = create_app(data_dir, token, desktop=True)
    active = set()
    waiting=deque()
    reserved_workers=set()

    async def allocate(model,count):
        ticket=object()
        waiting.append(ticket)
        try:
            async with asyncio.timeout(30):
                while True:
                    jobs=app.state.store.all("job")
                    busy={j["device_id"] for j in jobs if j["status"] in {"queued","leased"} and j["deadline"]>time.time()}
                    candidates=[d for d in app.state.pool.public() if d["approved"] and d["online"] and not d["revoked"] and d["id"] not in busy|reserved_workers and any(m["name"]==model for m in d["models"])]
                    candidates.sort(key=lambda d:(-(d["health"].get("completion_rate") or 0),d["health"].get("mean_elapsed_ms") or 999999,d.get("completed_jobs",0)))
                    if waiting[0] is ticket and len(candidates)>=count:
                        selected=candidates[:count]
                        reserved_workers.update(d["id"] for d in selected)
                        return selected
                    await asyncio.sleep(.1)
        except TimeoutError as error:
            raise RouteError("Fair contribution queue timed out; retry when workers are available") from error
        finally:
            waiting.remove(ticket)

    def authenticate(request):
        key = request.headers.get("authorization", "").removeprefix("Bearer ")
        client = app.state.store.get("hub_client", digest(key))
        if not client or client.get("revoked"): raise HTTPException(401, "Hub client key is invalid")
        return client

    def models():
        output = {}
        for device in app.state.pool.public():
            if device["approved"] and device["online"] and not device["revoked"]:
                for model in device["models"]:
                    output[model["name"]] = dict(id=model["name"], object="model", owned_by="volunteers")
        return list(output.values())

    @app.post("/api/hub/clients", status_code=201)
    async def add_client(form: ClientInput):
        key = secrets.token_urlsafe(32)
        id = digest(key)
        app.state.store.put("hub_client", id, dict(id=id, **form.model_dump(), revoked=False, requests=[], day="", day_count=0))
        return dict(id=id, api_key=key, note="Store this key now; the hub keeps only its hash")

    @app.delete("/api/hub/clients/{id}")
    async def revoke_client(id: str):
        if not app.state.store.get("hub_client", id): raise HTTPException(404, "Client not found")
        app.state.store.patch("hub_client", id, dict(revoked=True))
        return dict(revoked=True)

    @app.get("/v1/models")
    async def list_models(request: Request):
        authenticate(request)
        return dict(object="list", data=models())

    @app.post("/v1/chat/completions")
    async def completion(request: Request, form: CompletionInput):
        if sum(len(m.content) for m in form.messages) > 96000: raise HTTPException(413, "Prompt limit exceeded")
        client = authenticate(request)
        with app.state.store.lock:
            now = time.time()
            client = app.state.store.get("hub_client", client["id"])
            recent = [t for t in client["requests"] if t > now-60]
            day = time.strftime("%Y-%m-%d", time.gmtime(now))
            count = client["day_count"] if client["day"] == day else 0
            units=2 if form.duplicate_check else 1
            if client["id"] in active or len(active) >= 64 or len(recent)+units > client["rpm"] or count+units > client["daily_requests"]:
                raise HTTPException(429, "Contribution queue/client budget reached", headers={"Retry-After": "60"})
            devices = [d for d in app.state.pool.public() if d["approved"] and d["online"] and not d["revoked"] and any(m["name"] == form.model for m in d["models"])]
            if not devices: raise HTTPException(429, "No volunteer is serving this model", headers={"Retry-After": "30"})
            if form.duplicate_check and len(devices)<2: raise HTTPException(429,"Duplicate checks need two distinct online workers",headers={"Retry-After":"30"})
            active.add(client["id"])
            app.state.store.patch("hub_client", client["id"], dict(requests=recent+[now]*units, day=day, day_count=count+units))
        id = uid("completion_")
        import re
        match=re.fullmatch(r"00-([a-f0-9]{32})-([a-f0-9]{16})-[a-f0-9]{2}",request.headers.get("traceparent",""))
        trace=dict(trace_id=match[1],parent_span_id=match[2]) if match else {}
        async def output():
            selected=[]
            try:
                selected=await allocate(form.model,2 if form.duplicate_check else 1)
                if form.duplicate_check:
                    async def collect(device):
                        text=""
                        async for chunk in app.state.pool.infer(device["id"],form.model,[m.model_dump() for m in form.messages],form.max_tokens,client_id=client["id"],**trace): text+=chunk
                        return text
                    outputs=await asyncio.gather(*(collect(d) for d in selected),return_exceptions=True)
                    if any(isinstance(value,BaseException) for value in outputs): raise RouteError("A duplicate-check worker failed; no comparison is available")
                    agree=" ".join(outputs[0].casefold().split())==" ".join(outputs[1].casefold().split())
                    check_id=uid("duplicate_")
                    app.state.store.put("duplicate_check",check_id,dict(id=check_id,client_id=client["id"],model=form.model,devices=[d["id"] for d in selected],
                        agreement=agree,outputs=outputs,created_at=time.time(),note="Normalized text agreement only; not a correctness judgment"))
                    yield outputs[0]+("\n\n[Two-worker check: normalized outputs agree; correctness is unverified.]" if agree else "\n\n[Two-worker check: outputs differ; review the disagreement.]")
                else:
                    async for text in app.state.pool.infer(selected[0]["id"], form.model, [m.model_dump() for m in form.messages], form.max_tokens,allow_reassign=True,client_id=client["id"],**trace): yield text
            finally:
                reserved_workers.difference_update(d["id"] for d in selected)
                active.discard(client["id"])
        if not form.stream:
            text = ""
            try:
                async for chunk in output(): text += chunk
            except RouteError as error: raise HTTPException(429,str(error),headers={"Retry-After":"30"}) from error
            return dict(id=id, choices=[dict(message=dict(role="assistant", content=text), finish_reason="stop")])
        async def events():
            try:
                async for text in output():
                    yield "data: " + json.dumps(dict(id=id, choices=[dict(delta=dict(content=text), finish_reason=None)])) + "\n\n"
                yield "data: " + json.dumps(dict(id=id, choices=[dict(delta={}, finish_reason="stop")])) + "\n\n"
                yield "data: [DONE]\n\n"
            except RouteError:
                yield 'data: {"error":{"message":"Volunteer inference was interrupted"}}\n\n'
            finally:
                active.discard(client["id"])
        return StreamingResponse(events(), media_type="text/event-stream")
    return app


def main():
    parser = argparse.ArgumentParser(description="Self-host a volunteer inference hub/relay; no central service is required")
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8443)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    parser.add_argument("--public-origin")
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost", "::1"} and not (args.tls_cert and args.tls_key and args.public_origin and args.public_origin.startswith("https://")):
        parser.error("Remote hubs require TLS and an HTTPS public origin")
    if args.public_origin: os.environ["SWARM_PUBLIC_ORIGIN"] = args.public_origin
    token = os.getenv("SWARM_OWNER_TOKEN") or secrets.token_urlsafe(32)
    print("Hub operator token: " + token, flush=True)
    print("Use the authenticated /api/hub/clients and device invitation/approval APIs. Share only client keys and invitations, never the operator token.", flush=True)
    uvicorn.run(create_hub(args.data_dir, token), host=args.host, port=args.port, ssl_certfile=args.tls_cert, ssl_keyfile=args.tls_key, access_log=False)
