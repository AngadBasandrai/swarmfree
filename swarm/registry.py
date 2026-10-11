"""Pinned Ed25519 registry envelopes. Import never starts programs or missions."""
import asyncio
import base64
import hashlib
import json
import re
import time
from urllib.parse import urljoin,urlparse
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.exceptions import InvalidSignature
from .privacy import canonical
from .research import public_fetch
from .providers import RouteError
from .schema import Architecture
from .store import uid

DEFAULT_URL="https://swarms.grafitelab.in/registry/index.json"
FALLBACK_URL="https://angadbasandrai.github.io/swarmfree/registry/index.json"

def verify(envelope,keys):
    try:
        if envelope.get("format")!="swarm-json-v1": raise ValueError()
        key=keys[envelope["key_id"]]
        payload=envelope["payload"]
        Ed25519PublicKey.from_public_bytes(base64.b64decode(key,validate=True)).verify(base64.b64decode(envelope["signature"],validate=True),canonical(payload).encode())
        return payload
    except (KeyError,ValueError,TypeError,InvalidSignature,AttributeError) as error:
        raise RouteError("Registry signature is invalid or its publisher key is not pinned") from error

class Registry:
    def __init__(self,store): self.store=store
    def settings(self):
        from .registry_defaults import PUBLISHER_KEYS
        return self.store.get("settings","registry",dict(url=DEFAULT_URL,keys=PUBLISHER_KEYS))
    def configure(self,url,keys):
        if urlparse(url).scheme!="https": raise RouteError("Registry requires HTTPS")
        if len(keys)>20: raise RouteError("Too many publisher keys")
        for key in keys.values():
            try: Ed25519PublicKey.from_public_bytes(base64.b64decode(key,validate=True))
            except ValueError as error: raise RouteError("Invalid Ed25519 public key") from error
        return self.store.put("settings","registry",dict(url=url,keys=keys))
    async def refresh(self):
        config=self.settings();url=config["url"]
        try: raw,_=await asyncio.to_thread(public_fetch,url)
        except (RouteError,OSError):
            if url!=DEFAULT_URL: raise
            url=FALLBACK_URL;raw,_=await asyncio.to_thread(public_fetch,url)
        payload=verify(json.loads(raw),config["keys"])
        if payload.get("kind")!="index" or not isinstance(payload.get("packages"),list) or len(payload["packages"])>200: raise RouteError("Invalid registry index")
        if payload.get("expires_at",0)<time.time(): raise RouteError("Registry index expired")
        previous=self.store.get("registry_index","current",{})
        if payload.get("serial",0)<previous.get("serial",0): raise RouteError("Registry rollback rejected")
        for entry in payload["packages"]:
            if not re.fullmatch(r"packages/[a-zA-Z0-9_.-]+\.json",entry.get("path","")) or not re.fullmatch(r"[0-9a-f]{64}",entry.get("sha256","")): raise RouteError("Invalid registry package path/hash")
        return self.store.put("registry_index","current",dict(payload,url=url,fetched_at=time.time()))
    async def inspect(self,id):
        index=self.store.get("registry_index","current",{});entry=next((p for p in index.get("packages",[]) if p["id"]==id),None)
        if not entry: raise RouteError("Refresh and select a listed package first")
        raw,_=await asyncio.to_thread(public_fetch,urljoin(index["url"],entry["path"]))
        if hashlib.sha256(raw).hexdigest()!=entry["sha256"]: raise RouteError("Package bytes do not match the signed index")
        payload=verify(json.loads(raw),self.settings()["keys"])
        if payload.get("id")!=id or payload.get("version")!=entry.get("version"): raise RouteError("Package identity mismatch")
        if payload.get("kind")=="architecture": Architecture.model_validate(payload["architecture"])
        elif payload.get("kind")!="plugin": raise RouteError("Unsupported registry package kind")
        return self.store.put("registry_preview",id,dict(payload,reviewed_hash=entry["sha256"]))
    def install(self,id,reviewed_hash):
        preview=self.store.get("registry_preview",id)
        entry=next((p for p in self.store.get("registry_index","current",{}).get("packages",[]) if p["id"]==id),{})
        if not preview or preview["reviewed_hash"]!=reviewed_hash or entry.get("sha256")!=reviewed_hash: raise RouteError("Inspect the current package and permissions first")
        if preview["kind"]=="architecture":
            value=Architecture.model_validate({**preview["architecture"],"id":uid("community_"),"builtin":False}).model_dump()
            self.store.put("architecture",value["id"],value);return dict(architecture_id=value["id"])
        id=uid("plugin_draft_")
        self.store.put("plugin_draft",id,dict(preview,id=id,enabled=False))
        return dict(draft_id=id,note="Review the declared executable and configure it in Extensions before activation")
