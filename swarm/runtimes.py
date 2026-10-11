"""Owner-imported GGUF files and a managed, authenticated loopback llama-server."""
import asyncio
import hashlib
import os
from pathlib import Path
import secrets
import socket
import struct
import time
import httpx
from .hardware import hardware, inventory, fit_model
from .providers import RouteError
from .store import uid


def inspect_gguf(path):
    path=Path(path).resolve(strict=True)
    if not path.is_file() or path.suffix.lower()!=".gguf": raise RouteError("Select a GGUF model file")
    with path.open("rb") as source:
        header=source.read(24)
    if len(header)!=24 or header[:4]!=b"GGUF": raise RouteError("Invalid GGUF header")
    version,tensors,metadata=struct.unpack("<IQQ",header[4:])
    if version not in {2,3} or not 1<=tensors<=1000000 or metadata>1000000: raise RouteError("Unsupported or invalid GGUF model")
    return dict(path=str(path),size=path.stat().st_size,gguf_version=version,tensors=tensors,
        identity=hashlib.sha256(header+str(path.stat().st_size).encode()).hexdigest(),modified_ns=path.stat().st_mtime_ns)


class Runtimes:
    def __init__(self,store,providers,root):
        self.store,self.providers,self.root=store,providers,Path(root)
        self.processes={};self.keys={}
        for row in store.all("runtime"):
            if row.get("status") in {"ready","loading"}: store.patch("runtime",row["id"],dict(status="stopped",port=None))

    def add(self,form):
        info=inspect_gguf(form["path"])
        server=Path(form["server_path"]).resolve(strict=True)
        if not server.is_file() or not form.get("trusted"): raise RouteError("Explicitly trust the selected llama-server executable")
        id=uid("gguf_")
        return self.store.put("runtime",id,dict(id=id,name=form.get("name") or Path(info["path"]).stem,**info,
            server_path=str(server),server_modified_ns=server.stat().st_mtime_ns,server_size=server.stat().st_size,
            gpu_layers=form.get("gpu_layers",0),license=form.get("license","Owner must check model license"),enabled=True,status="stopped",created_at=time.time()))

    def public(self):
        budgets=self.store.get("settings","budgets")
        hw=hardware()
        rows=[]
        for row in self.store.all("runtime"):
            fit=self.fit(row,hw,budgets)
            process=self.processes.get(row["id"])
            if row.get("status")=="ready" and (not process or process.returncode is not None):
                row=self.store.patch("runtime",row["id"],dict(status="stopped",port=None))
            rows.append(dict(row,**fit))
        return rows

    def fit(self,row,hw,budgets):
        # Whole-file allocation plus context overhead. Partial GPU offload still needs RAM.
        estimate=round(row["size"]/1024**3*1.15+.5+budgets["context"]/4096*.3,3)
        ours=sum(r.get("estimated_ram_gb",0) for r in self.store.all("runtime") if r["id"] in self.processes and self.processes[r["id"]].returncode is None)
        ram_ok=estimate<=min(budgets["ram_gb"],hw["ram_available_gb"]+ours)
        own_gpu=estimate if row["id"] in self.processes and self.processes[row["id"]].returncode is None and row.get("gpu_layers") else 0
        gpu_ok=row.get("gpu_layers",0)==0 or any(estimate<=min(budgets["vram_gb"],g["total_gb"],(g.get("free_gb") or 0)+own_gpu) for g in hw["gpus"])
        return dict(fits=ram_ok and gpu_ok and (budgets["allow_cpu"] or row.get("gpu_layers",0)!=0),estimated_ram_gb=estimate,
                    reason="Conservative whole-model estimate; allocation is checked again before loading")

    async def stop(self,id):
        process=self.processes.pop(id,None)
        if process and process.returncode is None:
            process.terminate()
            try: await asyncio.wait_for(process.wait(),10)
            except TimeoutError: process.kill();await process.wait()
        self.providers.vault.delete("runtime_"+id);self.keys.pop(id,None)
        if self.store.get("runtime",id): self.store.patch("runtime",id,dict(status="stopped",port=None))

    async def release_others(self,keep=""):
        for id in list(self.processes):
            if id!=keep: await self.stop(id)

    async def start(self,id):
        row=self.store.get("runtime",id)
        if not row or not row.get("enabled"): raise RouteError("GGUF runtime is disabled or missing")
        budgets=self.store.get("settings","budgets")
        if id in self.processes and self.processes[id].returncode is None and row["status"]=="ready":
            if not self.fit(row,hardware(),budgets)["fits"]:
                await self.stop(id);raise RouteError("Loaded GGUF model exceeds the current memory allocation")
            if row.get("context")==budgets["context"]: return row
            await self.stop(id)
        await self.release_others(id)
        info=inspect_gguf(row["path"])
        server=Path(row["server_path"])
        if info["modified_ns"]!=row["modified_ns"] or info["identity"]!=row["identity"] or server.stat().st_mtime_ns!=row["server_modified_ns"] or server.stat().st_size!=row["server_size"]:
            raise RouteError("Model or runtime executable changed; import and review it again")
        budgets=self.store.get("settings","budgets")
        inv=await inventory(self.providers.ollama)
        # Unload only models this workbench loaded; do not interfere with unrelated clients.
        owned=self.store.get("settings","local_resident",{}).get("models",[])
        async with httpx.AsyncClient(timeout=10,trust_env=False) as client:
            for model in inv["loaded"]:
                if model["name"] in owned: await client.post(self.providers.ollama+"/api/generate",json=dict(model=model["name"],keep_alive=0))
        hw=hardware();fit=self.fit(row,hw,budgets)
        other=sum(m.get("size",0)/1024**3 for m in inv["loaded"] if m["name"] not in owned)
        if not fit["fits"] or fit["estimated_ram_gb"]+other>budgets["ram_gb"]: raise RouteError("GGUF model exceeds the current memory allocation or available memory")
        with socket.socket() as sock:
            sock.bind(("127.0.0.1",0));port=sock.getsockname()[1]
        key=secrets.token_urlsafe(32)
        folder=self.root/"runtime-secrets";folder.mkdir(exist_ok=True)
        keyfile=folder/(id+".key");keyfile.write_text(key,encoding="utf-8");keyfile.chmod(0o600)
        # Key-file avoids leaking the credential in the process command line.
        argv=[str(server),"-m",row["path"],"--host","127.0.0.1","--port",str(port),"-c",str(budgets["context"]),
              "-ngl",str(row["gpu_layers"]),"--parallel","1","--api-key-file",str(keyfile),"--no-webui","--no-agent"]
        options={"creationflags":0x08000000} if os.name=="nt" else {"start_new_session":True}
        self.store.patch("runtime",id,dict(status="loading",estimated_ram_gb=fit["estimated_ram_gb"]))
        try:
            process=await asyncio.create_subprocess_exec(*argv,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.DEVNULL,stderr=asyncio.subprocess.DEVNULL,**options)
            self.processes[id]=process;self.keys[id]=key;self.providers.vault.set("runtime_"+id,key,True)
            async with httpx.AsyncClient(timeout=2,trust_env=False,follow_redirects=False) as client:
                async with asyncio.timeout(120):
                    while True:
                        if process.returncode is not None: raise RouteError("llama-server exited while loading; check the executable and model")
                        try:
                            r=await client.get(f"http://127.0.0.1:{port}/health",headers={"Authorization":"Bearer "+key})
                            if r.status_code==200: break
                        except httpx.HTTPError: pass
                        await asyncio.sleep(.3)
            return self.store.patch("runtime",id,dict(status="ready",port=port,context=budgets["context"],loaded_at=time.time()))
        except (OSError,TimeoutError) as error:
            await self.stop(id);raise RouteError("Cannot start the trusted llama-server runtime") from error
        except BaseException:
            await self.stop(id);raise
        finally: keyfile.unlink(missing_ok=True)

    async def infer(self,id,messages,max_tokens,metadata):
        async with self.providers.local_lock:
            row=await self.start(id)
            profile_id="runtime_"+id
            # The internal adapter record is excluded from user profile lists and free-tier catalogs.
            self.store.put("profile",profile_id,dict(id=profile_id,internal=True,label=row["name"],provider="compatible",storage="session",model=row["name"],
                enabled=False,free_eligible=False,base_url=f"http://127.0.0.1:{row['port']}/v1",quota_group=profile_id,requests_per_minute=600,health="ready"))
            try:
                profile=self.store.get("profile",profile_id)
                async for chunk in self.providers.compatible(dict(profile,**metadata,_max_tokens=max_tokens),messages): yield chunk
            finally:
                self.store.delete("profile",profile_id)

    async def close(self):
        await self.release_others()
