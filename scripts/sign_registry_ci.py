"""Refresh an already public registry index using the CI publisher secret."""
import base64
import json
import os
from pathlib import Path
import time
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from swarm.privacy import canonical
from swarm.registry import verify
from swarm.registry_defaults import PUBLISHER_KEYS
def main():
    root=Path(__file__).resolve().parent.parent
    key=Ed25519PrivateKey.from_private_bytes(base64.b64decode(os.environ['SWARM_REGISTRY_PRIVATE_KEY']))
    path=root/'public/registry/index.json';envelope=json.loads(path.read_text())
    payload=verify(envelope,PUBLISHER_KEYS);now=int(time.time());payload.update(serial=max(now,payload['serial']+1),expires_at=now+365*86400)
    envelope.update(payload=payload,signature=base64.b64encode(key.sign(canonical(payload).encode())).decode())
    verify(envelope,PUBLISHER_KEYS);path.write_text(json.dumps(envelope,indent=2)+'\n')
if __name__=='__main__':main()
