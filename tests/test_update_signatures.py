import base64
import hashlib
from pathlib import Path
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.exceptions import InvalidSignature
from scripts.verify_update_artifact import verify

def test_update_signatures_bind_artifact_bytes_and_trusted_comment(tmp_path):
    key=Ed25519PrivateKey.generate();id=b'12345678';payload=tmp_path/'update.bin';payload.write_bytes(b'owned artifact bytes')
    public=base64.b64encode(('untrusted comment: owned fixture\n'+base64.b64encode(b'Ed'+id+key.public_key().public_bytes_raw()).decode()).encode()).decode()
    signature=key.sign(hashlib.blake2b(payload.read_bytes()).digest());comment='timestamp:1\tfile:update.bin\tversion:0.5.0'
    lines=['untrusted comment: owned fixture',base64.b64encode(b'ED'+id+signature).decode(),'trusted comment: '+comment,base64.b64encode(key.sign(signature+comment.encode())).decode()]
    path=Path(str(payload)+'.sig');path.write_text(base64.b64encode('\n'.join(lines).encode()).decode())
    assert verify(payload,public)==comment
    payload.write_bytes(b'changed artifact')
    with pytest.raises(InvalidSignature): verify(payload,public)
    payload.write_bytes(b'owned artifact bytes');lines[2]+=' changed'
    path.write_text(base64.b64encode('\n'.join(lines).encode()).decode())
    with pytest.raises(InvalidSignature): verify(payload,public)
