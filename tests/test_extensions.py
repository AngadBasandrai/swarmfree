import json
import sys
import pytest
import httpx
from swarm.extensions import Extensions


async def test_mcp_stdio_discovery_and_explicit_tool_execution(store,vault,tmp_path):
    server = tmp_path/"mcp.py"
    server.write_text('''import sys,json
for line in sys.stdin:
    message=json.loads(line)
    method=message['method']
    if 'id' not in message: continue
    if method=='initialize': result={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}
    elif method=='tools/list': result={'tools':[{'name':'echo','description':'echo data','inputSchema':{'type':'object'}}]}
    elif method=='tools/call': result={'content':[{'type':'text','text':message['params']['arguments']['text']}]}
    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
''',encoding="utf-8")
    store.put("extension","example",dict(id="example",name="Fixture",kind="mcp",transport="stdio",enabled=True,argv=[sys.executable,str(server)],url="",tools=[]))
    extensions = Extensions(store,vault)
    project = dict(root=str(tmp_path))
    assert (await extensions.discover("example",project))["tools"][0]["name"] == "echo"
    result = await extensions.call_tool("mcp.example.echo",{"text":"portable data"},project)
    assert result["content"][0]["text"] == "portable data"


async def test_mcp_http_sessions_and_secret_is_outside_record_database(store,vault,tmp_path,monkeypatch):
    requests = []
    def transport(request):
        data=json.loads(request.content) if request.method=='POST' else {}
        requests.append((request.method,data,dict(request.headers)))
        if request.method=='DELETE': return httpx.Response(204)
        if data['method']=='notifications/initialized': return httpx.Response(202)
        if data['method']=='initialize': return httpx.Response(200,json={'jsonrpc':'2.0','id':1,'result':{'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'fixture','version':'1'}}},headers={'Mcp-Session-Id':'fixture-session'})
        return httpx.Response(200,json={'jsonrpc':'2.0','id':data['id'],'result':{'tools':[{'name':'echo'}]}})
    original=httpx.AsyncClient
    monkeypatch.setattr("swarm.extensions.httpx.AsyncClient",lambda **kwargs:original(transport=httpx.MockTransport(transport),**kwargs))
    store.put("extension","remote",dict(id="remote",name="Remote",kind="mcp",transport="http",enabled=True,argv=[],url="https://example.com/mcp",tools=[]))
    vault.set("extension_remote","secret-extension-key",True)
    await Extensions(store,vault).discover("remote",dict(root=str(tmp_path)))
    assert requests[0][2]["authorization"] == "Bearer secret-extension-key"
    assert requests[2][2]["mcp-session-id"] == "fixture-session"
    assert requests[-1][0] == "DELETE"
    assert "secret-extension-key" not in json.dumps(store.all("extension"))


async def test_acp_initialization_prompt_and_client_permission_denial(store,vault,tmp_path):
    server=tmp_path/"acp.py"
    server.write_text('''import sys,json
for line in sys.stdin:
    message=json.loads(line)
    if message['method']=='initialize': result={'protocolVersion':1,'agentCapabilities':{}}
    elif message['method']=='session/new': result={'sessionId':'session-one'}
    elif message['method']=='session/prompt':
        print(json.dumps({'jsonrpc':'2.0','id':99,'method':'session/request_permission','params':{'sessionId':'session-one','options':[]}}),flush=True)
        reply=json.loads(sys.stdin.readline())
        assert reply['result']['outcome']['outcome']=='cancelled'
        print(json.dumps({'jsonrpc':'2.0','method':'session/update','params':{'sessionId':'session-one','update':{'sessionUpdate':'agent_message_chunk','content':{'type':'text','text':'Portable task result; client action denied'}}}}),flush=True)
        result={'stopReason':'end_turn'}
    print(json.dumps({'jsonrpc':'2.0','id':message['id'],'result':result}),flush=True)
''',encoding="utf-8")
    store.put("extension","agent",dict(id="agent",name="Agent",kind="acp",transport="stdio",enabled=True,argv=[sys.executable,str(server)],url="",tools=[]))
    text = "".join([part async for part in Extensions(store,vault).agent_stream("agent",dict(root=str(tmp_path)),[dict(role="user",content="Continue from these findings")])])
    assert text == "Portable task result; client action denied"
