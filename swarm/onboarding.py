"""Hardware-specific setup plans and checksum-verified, owner-requested downloads."""
import hashlib
import http.client
import ipaddress
from pathlib import Path
import platform
import shutil
import socket
import ssl
import time
from urllib.parse import urlparse,urljoin
from .hardware import hardware
from .providers import RouteError
from .store import uid


def download_verified(root,url,expected,name,max_bytes,progress=lambda n:None,redirects=0):
    parsed=urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname or parsed.port not in {None,443} or parsed.username or parsed.password or parsed.fragment: raise RouteError("Downloads require public HTTPS on port 443")
    addresses={a[4][0] for a in socket.getaddrinfo(parsed.hostname,443,type=socket.SOCK_STREAM)}
    if not addresses or any(not ipaddress.ip_address(a).is_global for a in addresses): raise RouteError("Download destination is not public")
    target=Path(root)/"downloads";target.mkdir(parents=True,exist_ok=True)
    if shutil.disk_usage(target).free<max_bytes+256000000: raise RouteError("Insufficient disk space for the chosen download ceiling")
    connection=http.client.HTTPSConnection(parsed.hostname,timeout=30,context=ssl.create_default_context())
    connection._create_connection=lambda address,timeout,source_address=None:socket.create_connection((sorted(addresses)[0],443),timeout,source_address)
    path=target/(uid("verified_")+"-"+name);partial=path.with_suffix(path.suffix+".partial")
    try:
        connection.request("GET",(parsed.path or "/")+("?"+parsed.query if parsed.query else ""),headers={"User-Agent":"SwarmWorkbench/0.5"})
        response=connection.getresponse()
        if response.status in {301,302,303,307,308}:
            if redirects>=4 or not response.getheader("Location"): raise RouteError("Download redirect limit reached")
            return download_verified(root,urljoin(url,response.getheader("Location")),expected,name,max_bytes,progress,redirects+1)
        if response.status!=200: raise RouteError("Download rejected ("+str(response.status)+")")
        length=response.getheader("Content-Length")
        if length and int(length)>max_bytes: raise RouteError("Download exceeds the chosen ceiling")
        digest=hashlib.sha256();total=0
        with partial.open("xb") as stream:
            while chunk:=response.read(262144):
                total+=len(chunk)
                if total>max_bytes: raise RouteError("Download exceeds the chosen ceiling")
                digest.update(chunk);stream.write(chunk);progress(total)
        if digest.hexdigest()!=expected.lower(): raise RouteError("SHA-256 mismatch; downloaded bytes were discarded")
        if length and total!=int(length): raise RouteError("Download ended before its declared length")
        partial.replace(path)
        return dict(path=str(path),sha256=digest.hexdigest(),size=total,note="Inspect the upstream license; executable trust remains a separate owner choice")
    finally:
        connection.close()
        if partial.exists(): partial.unlink()


class Onboarding:
    def __init__(self,store,providers,intelligence,root): self.store,self.providers,self.intelligence,self.root=store,providers,intelligence,Path(root)

    def plan(self):
        hw=hardware();budgets=self.store.get("settings","budgets")
        capacity=min(hw["ram_available_gb"],budgets["ram_gb"])
        models=[m for m in self.intelligence.catalog() if m["source"]=="local" and "embedding" not in m.get("capabilities",[]) and m.get("estimated_ram_gb",1e10)<=capacity]
        models.sort(key=lambda m:m.get("parameters_b",0),reverse=True)
        devices=self.store.all("device")
        return dict(platform=platform.system(),machine=platform.machine(),hardware=hw,budgets=budgets,recommended_model=models[0] if models else None,
            prerequisites=dict(ollama=shutil.which("ollama"),llama_server=shutil.which("llama-server"),docker=shutil.which("docker")),
            provider_count=len(self.providers.public()),paired_devices=len([d for d in devices if d.get("approved") and not d.get("revoked")]),
            progress=self.store.get("settings","setup",dict(completed=[])),downloads=self.store.all("setup_download")[-20:],
            steps=[dict(id="allocation",title="Set RAM, VRAM and context allocations",ready=True),dict(id="model",title="Select an installed model or link a provider",ready=bool(self.providers.public() or self.store.all("runtime"))),
                dict(id="project",title="Choose project files and tool grants",ready=bool(self.store.all("project"))),dict(id="device",title="Optionally pair a contributing device",ready=any(d.get("approved") and not d.get("revoked") for d in devices)),dict(id="demo",title="Run and inspect the offline demo",ready=any(r.get("mode")=="demo" and r["status"]=="completed" for r in self.store.all("run")))])

    def mark(self,id,done):
        if id not in {"allocation","model","project","device","demo"}: raise RouteError("Unknown setup step")
        completed=set(self.store.get("settings","setup",dict(completed=[]))["completed"])
        if done: completed.add(id)
        else: completed.discard(id)
        return self.store.put("settings","setup",dict(completed=sorted(completed)))
