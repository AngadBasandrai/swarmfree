"""Verify public examples and owned download fixtures, without external downloads."""
import hashlib
import json
from pathlib import Path
import socket
import pytest
from swarm.onboarding import download_verified
from swarm.providers import RouteError
from swarm.registry import verify
from swarm.registry_defaults import PUBLISHER_KEYS
from swarm.schema import Architecture

def test_shipped_index_signatures_bytes_permissions_and_graphs():
    root=Path(__file__).resolve().parents[1]/'public/registry'
    index=verify(json.loads((root/'index.json').read_text()),PUBLISHER_KEYS)
    assert len(index['packages'])>=5
    for entry in index['packages']:
        raw=(root/entry['path']).read_bytes();assert hashlib.sha256(raw).hexdigest()==entry['sha256']
        package=verify(json.loads(raw),PUBLISHER_KEYS);assert package['version']==entry['version']
        assert 'permissions' in package and 'examples' in package and 'evaluation' in package
        if package['kind']=='architecture': Architecture.model_validate(package['architecture'])
        else: assert package['extension']['enabled'] is False

def test_verified_download_discards_wrong_bytes_and_never_executes(tmp_path,monkeypatch):
    payload=b'owned bytes'
    class Response:
        status=200
        def getheader(self,k): return str(len(payload)) if k=='Content-Length' else None
        def read(self,n):
            if getattr(self,'read_once',False): return b''
            self.read_once=True;return payload
    class Connection:
        def __init__(self,*a,**kw): pass
        def request(self,*a,**kw): pass
        def getresponse(self): return Response()
        def close(self): pass
    monkeypatch.setattr('swarm.onboarding.socket.getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('8.8.8.8',443))])
    monkeypatch.setattr('swarm.onboarding.http.client.HTTPSConnection',Connection)
    result=download_verified(tmp_path,'https://owned.example/model.gguf',hashlib.sha256(payload).hexdigest(),'model.gguf',100)
    assert Path(result['path']).read_bytes()==payload
    with pytest.raises(RouteError): download_verified(tmp_path,'https://owned.example/model.gguf','0'*64,'wrong.gguf',100)
    assert not list(tmp_path.rglob('*.partial'))
    assert len(list((tmp_path/'downloads').iterdir()))==1
    monkeypatch.setattr('swarm.onboarding.socket.getaddrinfo',lambda *a,**kw:[(socket.AF_INET,socket.SOCK_STREAM,6,'',('127.0.0.1',443))])
    with pytest.raises(RouteError): download_verified(tmp_path,'https://owned.example/model.gguf','0'*64,'private.gguf',100)
