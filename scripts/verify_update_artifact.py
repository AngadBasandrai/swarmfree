"""Verify Tauri's prehashed Minisign payload and authenticated comment without GUI."""
import base64
import hashlib
import json
from pathlib import Path
import sys
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

def verify(artifact,public_key):
    artifact=Path(artifact)
    armor=base64.b64decode(public_key,validate=True).decode().splitlines()
    key=base64.b64decode(armor[1],validate=True)
    lines=base64.b64decode(Path(str(artifact)+'.sig').read_text().strip(),validate=True).decode().splitlines()
    if len(lines)!=4 or not lines[2].startswith('trusted comment: '): raise ValueError('Invalid signature armor')
    signature=base64.b64decode(lines[1],validate=True);comment_signature=base64.b64decode(lines[3],validate=True)
    if len(key)!=42 or len(signature)!=74 or key[:2]!=b'Ed' or signature[:2]!=b'ED' or key[2:10]!=signature[2:10]: raise ValueError('Signature algorithm or publisher identity mismatch')
    public=Ed25519PublicKey.from_public_bytes(key[10:])
    with artifact.open('rb') as stream: hashed=hashlib.file_digest(stream,'blake2b').digest()
    public.verify(signature[10:],hashed)
    public.verify(comment_signature,signature[10:]+lines[2][17:].encode())
    return lines[2][17:]

if __name__=='__main__':
    root=Path(__file__).resolve().parent.parent
    config=json.loads((root/'src-tauri/tauri.conf.json').read_text())
    verify(sys.argv[1],config['plugins']['updater']['pubkey'])
    print('Artifact and trusted-comment signatures match the shipped updater key')
