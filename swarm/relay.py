"""Optional HTTPS mailbox relay. Only endpoint devices hold the inference encryption key.

The relay sees timing, channel identities and ciphertext sizes. It is a transport,
not an audited anonymity service. Each channel is owner-provisioned and revocable.
"""
import argparse
import asyncio
import base64
from contextlib import asynccontextmanager
import json
import os
from pathlib import Path
import secrets
import time
from urllib.parse import urlparse
import httpx
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag
from fastapi import FastAPI,HTTPException,Request
from pydantic import BaseModel,Field
from .locking import WorkspaceLock
from .network import digest
from .providers import RouteError,validate_base_url
from .schema import JoinInput
from .store import Store,uid


class Envelope(BaseModel):
    id: str=Field(pattern=r"^[a-f0-9]{32}$")
    sent_at: float=Field(allow_inf_nan=False)
    nonce: str=Field(min_length=16,max_length=16)
    ciphertext: str=Field(min_length=24,max_length=1500000)


class RPCRequest(BaseModel):
    id: str=Field(pattern=r"^[a-f0-9]{32}$")
    path: str=Field(min_length=1,max_length=200)
    body: dict=Field(default_factory=dict)
    token: str=Field(default="",max_length=256)


def aad(channel,sender,id,sent_at):
    return json.dumps(["swarm-relay-1",channel,sender,id,sent_at],separators=(",",":")).encode()


def seal(key,channel,sender,value):
    id=secrets.token_hex(16)
    now=time.time()
    nonce=os.urandom(12)
    data=json.dumps(value,separators=(",",":")).encode()
    if len(data)>1_000_000: raise RouteError("Relay message exceeds 1 MB")
    encrypted=AESGCM(base64.urlsafe_b64decode(key)).encrypt(nonce,data,aad(channel,sender,id,now))
    return dict(id=id,sent_at=now,nonce=base64.b64encode(nonce).decode(),ciphertext=base64.b64encode(encrypted).decode())


def unseal(key,channel,sender,envelope):
    envelope=Envelope.model_validate(envelope).model_dump()
    if abs(time.time()-envelope["sent_at"])>120: raise RouteError("Relay message expired; synchronize endpoint clocks")
    try:
        nonce=base64.b64decode(envelope["nonce"],validate=True)
        data=base64.b64decode(envelope["ciphertext"],validate=True)
        value=AESGCM(base64.urlsafe_b64decode(key)).decrypt(nonce,data,aad(channel,sender,envelope["id"],envelope["sent_at"]))
        return json.loads(value)
    except (ValueError,InvalidTag) as error: raise RouteError("Relay message failed endpoint authentication") from error


def create_relay(data_dir,operator_token):
    root=Path(data_dir)
    @asynccontextmanager
    async def lifespan(app):
        with WorkspaceLock(root):
            app.state.store=Store(root/"relay.sqlite")
            try: yield
            finally: app.state.store.close()
    app=FastAPI(title="Swarm encrypted device relay",lifespan=lifespan)
    @app.middleware("http")
    async def bounded_body(request,call_next):
        from fastapi.responses import JSONResponse
        try: declared=int(request.headers.get("content-length","0"))
        except ValueError: return JSONResponse(dict(detail="Invalid content length"),status_code=400)
        if declared>2_000_000: return JSONResponse(dict(detail="Relay body exceeds 2 MB"),status_code=413)
        chunks,length=[],0
        async for chunk in request.stream():
            length+=len(chunk)
            if length>2_000_000: return JSONResponse(dict(detail="Relay body exceeds 2 MB"),status_code=413)
            chunks.append(chunk)
        request._body=b"".join(chunks)
        response=await call_next(request)
        response.headers["Cache-Control"]="no-store"
        return response

    def operator(request):
        if not secrets.compare_digest(request.headers.get("authorization","").removeprefix("Bearer "),operator_token):
            raise HTTPException(401,"Relay operator credential required")

    def authenticate(channel,request):
        record=app.state.store.get("relay_channel",channel)
        token=request.headers.get("authorization","").removeprefix("Bearer ")
        if not record or record.get("revoked") or record["expires_at"]<time.time(): raise HTTPException(401,"Relay channel expired or revoked")
        for role in ("owner","worker"):
            if secrets.compare_digest(record[role+"_hash"],digest(token)): return record,role
        raise HTTPException(401,"Invalid channel credential")

    def purge():
        store=app.state.store
        for message in store.all("relay_message"):
            if message["expires_at"]<time.time(): store.delete("relay_message",message["id"])

    @app.post("/v1/channels",status_code=201)
    async def create_channel(request:Request):
        operator(request)
        store=app.state.store
        if sum(not c.get("revoked") and c["expires_at"]>time.time() for c in store.all("relay_channel"))>=64:
            raise HTTPException(429,"Relay channel capacity reached")
        owner,worker=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        id=uid("channel_")
        expires=time.time()+30*86400
        store.put("relay_channel",id,dict(id=id,owner_hash=digest(owner),worker_hash=digest(worker),expires_at=expires,revoked=False))
        return dict(channel=id,owner_token=owner,worker_token=worker,expires_at=expires)

    @app.delete("/v1/channels/{channel}")
    async def revoke(channel:str,request:Request):
        record,role=authenticate(channel,request)
        if role!="owner": raise HTTPException(403,"Only the owner can revoke a channel")
        app.state.store.patch("relay_channel",channel,dict(revoked=True))
        for message in app.state.store.all("relay_message"):
            if message["channel"]==channel: app.state.store.delete("relay_message",message["id"])
        return dict(revoked=True)

    @app.post("/v1/channels/{channel}/send",status_code=202)
    async def send(channel:str,request:Request,form:Envelope):
        record,role=authenticate(channel,request)
        if abs(time.time()-form.sent_at)>120: raise HTTPException(400,"Envelope expired")
        purge()
        store=app.state.store
        with store.lock:
            messages=store.all("relay_message")
            if sum(m["channel"]==channel for m in messages)>=16 or sum(len(m["envelope"]["ciphertext"]) for m in messages)+len(form.ciphertext)>64_000_000:
                raise HTTPException(429,"Relay mailbox is full")
            id=channel+":"+form.id
            if not store.get("relay_message",id):
                store.put("relay_message",id,dict(id=id,channel=channel,recipient="worker" if role=="owner" else "owner",envelope=form.model_dump(),expires_at=time.time()+120))
        return dict(queued=True)

    @app.get("/v1/channels/{channel}/receive")
    async def receive(channel:str,request:Request):
        record,role=authenticate(channel,request)
        purge()
        messages=[m["envelope"] for m in app.state.store.all("relay_message") if m["channel"]==channel and m["recipient"]==role][:8]
        return dict(messages=messages)

    @app.delete("/v1/channels/{channel}/messages/{id}")
    async def acknowledge(channel:str,id:str,request:Request):
        record,role=authenticate(channel,request)
        store=app.state.store
        message=store.get("relay_message",channel+":"+id)
        if message and message["recipient"]!=role: raise HTTPException(403,"Cannot acknowledge another endpoint's message")
        store.delete("relay_message",channel+":"+id)
        return dict(acknowledged=True)
    return app


class RelayEndpoint:
    def __init__(self,settings,role,client=None):
        self.settings,self.role=settings,role
        self.client=client or httpx.AsyncClient(timeout=15,trust_env=False,follow_redirects=False)
        self.base=validate_base_url(settings["url"])+"/v1/channels/"+settings["channel"]
        self.headers={"Authorization":"Bearer "+settings[role+"_token"]}

    async def send(self,value):
        envelope=seal(self.settings["key"],self.settings["channel"],self.role,value)
        response=await self.client.post(self.base+"/send",headers=self.headers,json=envelope)
        response.raise_for_status()

    async def receive(self):
        response=await self.client.get(self.base+"/receive",headers=self.headers)
        response.raise_for_status()
        output=[]
        for envelope in response.json().get("messages",[])[:8]:
            try: value=unseal(self.settings["key"],self.settings["channel"],"worker" if self.role=="owner" else "owner",envelope)
            except (RouteError,ValueError): value=None
            output.append((envelope["id"],value))
        return output

    async def ack(self,id):
        response=await self.client.delete(self.base+"/messages/"+id,headers=self.headers)
        response.raise_for_status()

    async def close(self): await self.client.aclose()


class RelayWorkerClient:
    def __init__(self,settings,base):
        self.endpoint=RelayEndpoint(settings,"worker")
        self.headers={}
        self.base=base
        self.lock=asyncio.Lock()

    async def post(self,url,json=None):
        async with self.lock:
            id=secrets.token_hex(16)
            path=url.removeprefix(self.base)
            request=dict(id=id,path=path,body=json or {},token=self.headers.get("Authorization","").removeprefix("Bearer "))
            await self.endpoint.send(request)
            deadline=time.monotonic()+25
            while time.monotonic()<deadline:
                for message_id,value in await self.endpoint.receive():
                    await self.endpoint.ack(message_id)
                    if value and value.get("id")==id:
                        return httpx.Response(value["status"],json=value["body"],request=httpx.Request("POST",url))
                await asyncio.sleep(.2)
            raise httpx.RequestError("Relay coordinator did not respond",request=httpx.Request("POST",url))

    async def __aenter__(self): return self
    async def __aexit__(self,*args): await self.endpoint.close()


class RelayLinks:
    def __init__(self,store,vault,pool):
        self.store,self.vault,self.pool=store,vault,pool
        self.tasks={}
        for link in store.all("relay_link"):
            if link.get("enabled"): self.start(link["id"])

    def start(self,id):
        task=self.tasks.get(id)
        if not task or task.done(): self.tasks[id]=asyncio.create_task(self.bridge(id))

    async def add(self,url,operator_key,ephemeral):
        url=validate_base_url(url)
        async with httpx.AsyncClient(timeout=15,trust_env=False,follow_redirects=False) as client:
            response=await client.post(url+"/v1/channels",headers={"Authorization":"Bearer "+operator_key})
            response.raise_for_status()
            issued=response.json()
        if not issued.get("channel","").startswith("channel_"): raise RouteError("Relay returned an invalid channel")
        settings=dict(url=url,channel=issued["channel"],key=base64.urlsafe_b64encode(AESGCM.generate_key(256)).decode(),
            owner_token=issued["owner_token"],worker_token=issued["worker_token"])
        id=issued["channel"]
        self.vault.set("relay_"+id,json.dumps(settings),ephemeral)
        self.store.put("relay_link",id,dict(id=id,url=url,enabled=True,status="connecting",expires_at=issued["expires_at"],last_seen=0))
        invitation=self.pool.invite()
        bundle=dict(version=2,origin=url,relay={k:v for k,v in settings.items() if k!="owner_token"},invite=invitation["token"],expires_at=invitation["expires_at"])
        self.start(id)
        return dict(bundle=base64.urlsafe_b64encode(json.dumps(bundle).encode()).decode(),expires_at=bundle["expires_at"],channel=id,origin=url)

    def dispatch(self,value):
        value=RPCRequest.model_validate(value).model_dump()
        path=value.get("path","")
        body=value.get("body",{})
        if path=="/api/worker/join": return self.pool.join(JoinInput.model_validate(body))
        parts=path.split("/")
        if len(parts)<5 or parts[:3]!=["","api","worker"]: raise ValueError("Relay accepts worker inference protocol only")
        device=self.pool.authenticate(parts[3],value.get("token",""))
        if len(parts)==5 and parts[4]=="heartbeat":
            from .app import Heartbeat
            return self.pool.heartbeat(device,Heartbeat.model_validate(body).model_dump())
        if len(parts)==5 and parts[4]=="lease": return dict(job=self.pool.lease(device))
        if len(parts)==6 and parts[4]=="jobs":
            from .app import WorkerResult
            form=WorkerResult.model_validate(body)
            return dict(status=self.pool.result(device,parts[5],form.lease,form.model_dump())["status"])
        raise ValueError("Relay route is not allowed")

    async def bridge(self,id):
        secret=self.vault.get("relay_"+id)
        if not secret:
            self.store.patch("relay_link",id,dict(status="credential unavailable"))
            return
        endpoint=RelayEndpoint(json.loads(secret),"owner")
        try:
            while self.store.get("relay_link",id,{}).get("enabled"):
                try:
                    for envelope_id,value in await endpoint.receive():
                        import re
                        if value and isinstance(value,dict) and isinstance(value.get("id"),str) and re.fullmatch(r"[a-f0-9]{32}",value["id"]):
                            key=id+":"+value["id"]
                            cached=self.store.get("relay_response",key)
                            if cached: reply=unseal(endpoint.settings["key"],id,"owner",cached["encrypted"])
                            else:
                                try: reply=dict(id=value["id"],status=200,body=self.dispatch(value))
                                except (ValueError,KeyError): reply=dict(id=value["id"],status=409,body=dict(detail="Worker request rejected; inspect credentials/lease"))
                                encrypted=seal(endpoint.settings["key"],id,"owner",reply)
                                self.store.put("relay_response",key,dict(id=key,encrypted=encrypted,created_at=time.time()))
                            await endpoint.send(reply)
                        await endpoint.ack(envelope_id)
                    self.store.patch("relay_link",id,dict(status="connected",last_seen=time.time()))
                    for cached in self.store.all("relay_response"):
                        if cached["created_at"]<time.time()-120: self.store.delete("relay_response",cached["id"])
                    await asyncio.sleep(.2)
                except (httpx.HTTPError,RouteError,ValueError):
                    self.store.patch("relay_link",id,dict(status="retrying; check relay reachability/expiry"))
                    await asyncio.sleep(5)
        finally: await endpoint.close()

    async def remove(self,id):
        self.store.patch("relay_link",id,dict(enabled=False,status="revoked"))
        task=self.tasks.pop(id,None)
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        secret=self.vault.get("relay_"+id)
        if secret:
            endpoint=RelayEndpoint(json.loads(secret),"owner")
            try: await endpoint.client.delete(endpoint.base,headers=endpoint.headers)
            except httpx.HTTPError: pass
            finally: await endpoint.close()
        self.vault.delete("relay_"+id)

    async def stop(self):
        for task in self.tasks.values(): task.cancel()
        await asyncio.gather(*self.tasks.values(),return_exceptions=True)


def main():
    import uvicorn
    parser=argparse.ArgumentParser(description="Self-host an encrypted outbound device mailbox relay")
    parser.add_argument("--data-dir",required=True)
    parser.add_argument("--host",default="127.0.0.1")
    parser.add_argument("--port",type=int,default=9443)
    parser.add_argument("--tls-cert")
    parser.add_argument("--tls-key")
    args=parser.parse_args()
    if args.host not in {"127.0.0.1","localhost","::1"} and not (args.tls_cert and args.tls_key): parser.error("Internet relays require TLS")
    token=os.getenv("SWARM_RELAY_OPERATOR_TOKEN") or secrets.token_urlsafe(32)
    print("Relay operator token: "+token,flush=True)
    uvicorn.run(create_relay(args.data_dir,token),host=args.host,port=args.port,ssl_certfile=args.tls_cert,ssl_keyfile=args.tls_key,access_log=False)
