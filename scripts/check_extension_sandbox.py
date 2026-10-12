"""Disposable Linux CI only: exercise real Docker isolation through MCP stdio."""
import asyncio
import json
import os
from pathlib import Path
import tempfile
from swarm.extensions import Extensions
from swarm.store import Store
from swarm.isolation import command

SERVER=r'''
import json,os,socket
for line in __import__('sys').stdin:
    req=json.loads(line)
    if 'id' not in req:continue
    method=req['method']
    if method=='initialize':result={'protocolVersion':'2025-11-25'}
    elif method=='tools/list':result={'tools':[{'name':'probe','inputSchema':{'type':'object'}}]}
    else:
        result={'input':open('/workspace/input.txt').read(),'secret':open(os.environ['TOKEN_FILE']).read()}
        try:open('/workspace/forbidden.txt','w').write('bad');result['readonly']=False
        except OSError:result['readonly']=True
        try:socket.create_connection(('8.8.8.8',53),1).close();result['network_blocked']=False
        except OSError:result['network_blocked']=True
        result['private_file_visible']=os.path.exists('/workspace/.env')
    print(json.dumps({'jsonrpc':'2.0','id':req['id'],'result':result}),flush=True)
'''


async def main():
    if os.environ.get('SWARM_DISPOSABLE_DOCKER_TEST')!='1':raise SystemExit('This check runs only on an explicitly opted-in disposable Docker runner')
    await command(['docker','pull','python:3.13-alpine'],timeout=120,limit=64000)
    info=json.loads(await command(['docker','image','inspect','python:3.13-alpine']))[0]
    image=info['RepoDigests'][0]
    class Vault:
        def get(self,id):return 'sandbox-test-secret-value' if id=='extension_fixture' else None
    with tempfile.TemporaryDirectory(prefix='swarm-docker-acceptance-') as folder:
        root=Path(folder);(root/'server.py').write_text(SERVER);(root/'input.txt').write_text('allowed input');(root/'.env').write_text('PRIVATE=unavailable')
        store=Store(root/'fixture.sqlite')
        config=dict(id='fixture',kind='mcp',name='CI fixture',enabled=True,transport='stdio',argv=['python','/workspace/server.py'],isolation=dict(enabled=True,image=image,files=['server.py','input.txt'],secrets={'TOKEN':'extension_fixture'}))
        store.put('extension','fixture',config);extension=Extensions(store,Vault());project=dict(root=str(root))
        try:
            await extension.discover('fixture',project)
            result=await extension.call_tool('mcp.fixture.probe',{},project)
            assert result['input']=='allowed input' and result['readonly'] and result['network_blocked']
            assert result['secret']=='[sandbox secret redacted]' and not result['private_file_visible']
            assert not (root/'forbidden.txt').exists()
            assert not (await command(['docker','ps','-aq','--filter','name=swarm-extension-'])).strip()
            print('Actual Docker/MCP isolation passed: selected files, read-only workspace, denied network, secret redaction, container cleanup')
        finally:store.close()

if __name__=='__main__':asyncio.run(main())
