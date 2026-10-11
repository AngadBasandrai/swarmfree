"""Experimental Exo MLX cluster inspection and reviewed placement requests."""
import json
import time
from urllib.parse import urlparse,quote
import httpx
from .providers import RouteError
from .privacy import digest

class Cluster:
    def __init__(self,store,providers): self.store,self.providers=store,providers
    def profile(self,id):
        row=self.store.get("profile",id)
        if not row or not row["enabled"] or row["provider"]!="exo": raise RouteError("Choose an enabled Exo profile")
        parsed=urlparse(row["base_url"])
        return row,parsed.scheme+"://"+parsed.netloc
    async def request(self,id,path,method="GET",body=None,params=None):
        row,origin=self.profile(id)
        async with httpx.AsyncClient(timeout=30,follow_redirects=False,trust_env=False) as client:
            async with client.stream(method,origin+path,headers=self.providers.headers(row),json=body,params=params) as response:
                if response.status_code>=300: raise RouteError("Exo rejected the request ("+str(response.status_code)+")")
                raw=b""
                async for chunk in response.aiter_bytes():
                    raw+=chunk
                    if len(raw)>2000000: raise RouteError("Exo topology exceeds 2 MB")
                try: return json.loads(raw),len(raw)
                except ValueError as error: raise RouteError("This Exo version returned an unsupported response") from error
    async def inspect(self,id):
        started=time.monotonic();state,size=await self.request(id,"/state");elapsed=max(.001,time.monotonic()-started)
        models,_=await self.request(id,"/v1/models")
        return self.store.put("cluster_snapshot",id,dict(id=id,state=state,models=models,observed_at=time.time(),roundtrip_ms=round(elapsed*1000),response_bytes=size,
            application_kib_s=round(size/1024/elapsed,2),note="HTTP topology transfer measurement includes server overhead; it does not measure tensor interconnect bandwidth. Exo MLX deployments require supported Apple Silicon hosts."))
    async def preview(self,id,model,sharding,min_nodes):
        instance,_=await self.request(id,"/instance/placement",params=dict(model_id=model,sharding=sharding,instance_meta="MlxRing",min_nodes=min_nodes))
        return self.store.put("cluster_preview",id,dict(id=id,instance=instance,sha256=digest(instance),model=model,created_at=time.time()))
    async def deploy(self,id,reviewed_hash):
        preview=self.store.get("cluster_preview",id,{})
        if preview.get("sha256")!=reviewed_hash or time.time()-preview.get("created_at",0)>300: raise RouteError("Inspect a fresh placement before deployment")
        result,_=await self.request(id,"/instance","POST",dict(instance=preview["instance"]))
        return self.store.put("cluster_deployment",id,dict(id=id,result=result,instance=preview["instance"],created_at=time.time()))
    async def remove(self,id,instance):
        result,_=await self.request(id,"/instance/"+quote(instance,safe=""),"DELETE")
        return result
