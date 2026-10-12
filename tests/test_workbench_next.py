import asyncio
import base64
import json
import time
import pytest
import httpx
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from swarm.app import create_app
from swarm.schema import Architecture,RunInput
from swarm.privacy import Privacy,canonical
from swarm.result_cache import ResultCache
from swarm.workspace import Workspace
from swarm.registry import verify,Registry
from swarm.providers import RouteError
from swarm.composition import resolve
from swarm.fault_lab import scenario,SCENARIOS

def owner(tmp_path,vault):
    return TestClient(create_app(tmp_path/'data',token='owner',vault=vault))
def project(client,tmp_path):
    root=tmp_path/'project';root.mkdir(exist_ok=True);(root/'report.txt').write_text('public report')
    r=client.post('/api/projects',json=dict(name='Test project',root=str(root),tools=[]))
    assert r.status_code==201,r.text
    return r.json()

def test_setup_design_cache_and_bounded_inputs(tmp_path,vault,model_fixture):
    with owner(tmp_path,vault) as c:
        assert c.get('/api/workbench').status_code==401
        c.post('/api/session',json={'token':'owner'})
        r=c.get('/api/workbench');assert r.status_code==200,r.text
        assert r.json()['setup']['hardware']['ram_total_gb']>0
        assert c.post('/api/workbench/setup/steps/allocation',json={'done':True}).status_code==200
        assert c.post('/api/workbench/setup/steps/unknown',json={'done':True}).status_code==400
        draft=c.post('/api/workbench/architectures/design',json={'description':'planner researcher reviewer writer in parallel'}).json()
        assert len(draft['architecture']['nodes'])==4
        assert all(n['tools']==[] for n in draft['architecture']['nodes'])
        save=c.post('/api/workbench/architectures/save',json={'architecture':draft['architecture']})
        assert save.status_code==200 and save.json()['id']!=draft['architecture']['id']
        assert c.put('/api/workbench/cache',json={'ttl_seconds':1}).status_code==422
        assert c.delete('/api/workbench/cache').json()['cleared']
    with pytest.raises(ValueError): RunInput(task='x',architecture_id='baseline',inputs={'large':'a'*64000})

def test_team_project_scope_roles_expiration_revocation_and_audit(tmp_path,vault):
    with owner(tmp_path,vault) as c:
        c.post('/api/session',json={'token':'owner'});p=project(c,tmp_path)
        members={}
        for role in ('viewer','operator','reviewer'):
            row=c.post('/api/workbench/members',json=dict(name=role,role=role,project_ids=[p['id']])).json()
            members[role]=row
        c.cookies.clear()
        def h(role): return {'Authorization':'Bearer '+members[role]['token']}
        view=c.get('/api/team/snapshot',headers=h('viewer'))
        assert view.status_code==200 and 'root' not in view.json()['projects'][0]
        assert 'token_hash' not in view.text and 'owner' not in view.text
        body=dict(task='test',architecture_id='baseline',project_id=p['id'],mode='live')
        assert c.post('/api/team/runs',json=body,headers=h('viewer')).status_code==403
        assert c.post('/api/team/runs',json={**body,'project_id':'other'},headers=h('operator')).status_code==404
        run=c.post('/api/team/runs',json=body,headers=h('operator'))
        assert run.status_code==200,run.text
        assert c.post('/api/team/runs/'+run.json()['id']+'/pause',headers=h('reviewer')).status_code==403
        assert c.get('/api/team/snapshot').status_code==401
        c.post('/api/session',json={'token':'owner'})
        assert c.post('/api/workbench/members/'+members['viewer']['member']['id']+'/revoke').status_code==200
        assert c.get('/api/team/snapshot',headers=h('viewer')).status_code==401
        data=c.get('/api/workbench').json();assert len(data['audit'])>=5 and all(len(a['digest'])==64 for a in data['audit'])

def test_provenance_and_redacted_egress_decisions(store,tmp_path):
    store.put('project','p',dict(id='p'));privacy=Privacy(store)
    store.put('agent','a',dict(id='a',data_labels=[]));run=dict(id='r',project_id='p');agent=store.get('agent','a')
    privacy.save('p',dict(destinations=['local','cloud'],file_rules=[dict(pattern='private/*',destinations=['local'])],scan_secrets=True))
    privacy.mark('a','p',['private/key.txt'])
    with pytest.raises(RouteError): privacy.prepare([dict(role='user',content='summary')],'profile',dict(id='k'),run,agent)
    store.patch('agent','a',dict(data_labels=[]))
    messages=[dict(role='user',content='api_key=secretcredentialvalue123456')]
    with pytest.raises(RouteError): privacy.prepare(messages,'profile',dict(id='k'),run,agent)
    review=store.all('egress_review')[0]
    assert 'secretcredentialvalue' not in json.dumps(review)
    privacy.decide(review['id'],'redact')
    assert 'secretcredentialvalue' not in json.dumps(privacy.prepare(messages,'profile',dict(id='k'),run,agent))
    with pytest.raises(RouteError): privacy.decide(review['id'],'approved')

def test_cache_invalidates_files_rules_models_inputs_and_tool_grants(store,tmp_path):
    root=tmp_path/'files';root.mkdir();(root/'test.txt').write_text('one')
    project=dict(id='p',root=str(root),tools=[]);store.put('project','p',project)
    privacy=Privacy(store);workspace=Workspace(store,tmp_path);cache=ResultCache(store,workspace,privacy)
    run=dict(id='r',project_id='p',reuse_cache=True,tools_enabled=False,mode='live');agent=dict(id='a',tools=[],data_labels=[])
    route=dict(id='k',model='x',model_revision='sha:one');messages=[dict(role='user',content='hello')]
    key=cache.key(run,agent,messages,'profile',route);assert key
    cache.put(key,run,'answer');assert cache.get(key)['content']=='answer'
    (root/'test.txt').write_text('two');assert cache.key(run,agent,messages,'profile',route)!=key
    assert cache.key(run,agent,messages,'profile',{**route,'model_revision':''}) is None
    assert cache.key({**run,'attachments':['a']},agent,messages,'profile',route) is None
    assert cache.key(run,agent,[dict(role='user',content='changed')],'profile',route)!=key

def test_composition_snapshots_and_cycles(store):
    child=Architecture(id='child',name='Child',nodes=[dict(id='worker',name='Worker',role='work')]).model_dump()
    store.put('architecture','child',child)
    parent=Architecture(id='parent',name='Parent',nodes=[dict(id='team',name='Team',role='work',kind='subswarm',subswarm_id='child')]).model_dump()
    snap=resolve(store,parent);store.patch('architecture','child',dict(name='New'))
    assert snap['nodes'][0]['child_snapshot']['name']=='Child'
    child['nodes']=[dict(id='cycle',name='Cycle',role='work',kind='subswarm',subswarm_id='parent')]
    store.put('architecture','child',child);store.put('architecture','parent',parent)
    with pytest.raises(RouteError): resolve(store,parent)

def test_signed_envelopes_reject_tampering_and_unknown_publishers(store):
    private=Ed25519PrivateKey.generate();public=base64.b64encode(private.public_key().public_bytes_raw()).decode()
    payload=dict(kind='index',packages=[],expires_at=time.time()+100,serial=1)
    envelope=dict(format='swarm-json-v1',key_id='publisher',payload=payload,signature=base64.b64encode(private.sign(canonical(payload).encode())).decode())
    assert verify(envelope,{'publisher':public})==payload
    with pytest.raises(RouteError): verify(envelope,{})
    payload['serial']=2
    with pytest.raises(RouteError): verify(envelope,{'publisher':public})

@pytest.mark.asyncio
@pytest.mark.parametrize('name',list(SCENARIOS))
async def test_failure_containment_and_recovery(tmp_path,name):
    result=await scenario(name,tmp_path)
    assert result['passed'],result

@pytest.mark.asyncio
async def test_subswarm_handoffs_and_root_request_budget(store,vault,tmp_path):
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from swarm.engine import Engine
    from swarm.providers import Providers
    from swarm.network import PrivatePool
    store.put('settings','budgets',dict(ram_gb=2,vram_gb=0,context=4096,allow_cpu=True))
    provider=Providers(store,vault,'http://127.0.0.1:1')
    async def local(model,messages,budgets): yield 'verified output'
    provider.local=local
    workspace=Workspace(store,tmp_path)
    child=Architecture(id='child',name='Child',nodes=[dict(id='one',name='One',role='work',tools=[]),dict(id='two',name='Two',role='work',tools=[])],edges=[dict(source='one',target='two')]).model_dump()
    store.put('architecture','child',child)
    parent=Architecture(id='parent',name='Parent',nodes=[dict(id='team',name='Team',role='work',kind='subswarm',subswarm_id='child',tools=[])]).model_dump()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.sqlite')) as saver:
        engine=Engine(store,provider,PrivatePool(store),saver,workspace);engine.privacy=Privacy(store)
        run=engine.create(RunInput(task='Complete child',architecture_id='parent',route=dict(kind='local',model='fixture')),parent)
        await engine.tasks[run['id']]
        final=store.get('run',run['id']);assert final['status']=='completed',final
        assert final['results'][0]['child_run_id']
        childrun=store.get('run',final['results'][0]['child_run_id']);assert childrun['root_run_id']==run['id'] and childrun['inputs']['task']=='Complete child'
        run=engine.create(RunInput(task='Hit shared budget',architecture_id='parent',route=dict(kind='local',model='fixture'),limits=dict(max_requests=1)),parent)
        await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='interrupted'
        assert engine.budget.usage(store.get('run',run['id']))['requests']==1
        await engine.stop()

def test_mailbox_membership_limits_claims_and_provenance(store):
    from swarm.mailboxes import Mailboxes
    class Engine:
        privacy=Privacy(store)
        def append_message(self,id,role,content,**kwargs): return dict(role=role,content=content)
    run=dict(id='r',architecture=dict(nodes=[dict(id='one',message_budget=1),dict(id='two')],channels=[dict(id='team',members=['one','two'],max_messages=2)]))
    agent=dict(id='r:one',name='One',node_id='one',run_id='r',data_labels=[dict(project_id='p',path='restricted.txt')])
    store.put('agent',agent['id'],agent);store.put('agent','r:two',dict(id='r:two',node_id='two',run_id='r',data_labels=[]))
    box=Mailboxes(store,Engine());box.send(run,agent,'team','two','please review')
    assert store.get('agent','r:two')['data_labels']==agent['data_labels']
    with pytest.raises(RouteError): box.send(run,agent,'team','two','second')
    with pytest.raises(RouteError): box.send(run,agent,'team','outsider','hello')
    assert box.claim(run,agent,'team','review document')['owner']=='one'
    assert box.claim(run,store.get('agent','r:two'),'team','review document')['owner']=='one'

@pytest.mark.asyncio
async def test_a2a_versions_same_origin_grants_and_task_ownership(store,vault,monkeypatch):
    from swarm.a2a import A2A
    observed=[];binding='HTTP+JSON';version='1.0'
    def respond(request):
        observed.append(request)
        if request.url.path.endswith('agent-card.json'):
            return httpx.Response(200,json=dict(name='Owned agent',capabilities=dict(streaming=True),supportedInterfaces=[dict(url='http://127.0.0.1:9876/agent',protocolBinding=binding,protocolVersion=version)]))
        if request.url.path.endswith('message:send'):
            body=json.loads(request.content);assert body['message']['role']=='ROLE_USER'
            return httpx.Response(200,json=dict(task=dict(id='t1',status=dict(state='TASK_STATE_WORKING'))))
        if request.url.path=='/agent' and request.method=='POST':
            body=json.loads(request.content);assert body['method']=='message/send' and body['params']['message']['parts'][0]['kind']=='text'
            return httpx.Response(200,json=dict(jsonrpc='2.0',id=body['id'],result=dict(id='t2',status=dict(state='completed'))))
        return httpx.Response(200,json=dict(id='t1',status=dict(state='TASK_STATE_COMPLETED')))
    original=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    a2a=A2A(store,vault,Privacy(store));endpoint=a2a.add(dict(origin='http://127.0.0.1:9876',label='Owned fixture',free_eligible=True))
    await a2a.discover(endpoint['id']);project=dict(a2a_endpoints=[endpoint['id']]);run=dict(id='r',project_id='',free_only=True);agent=dict(id='a')
    task=await a2a.action(project,run,agent,'send',dict(endpoint_id=endpoint['id'],content='hello'))
    assert task['remote_id']=='t1'
    await a2a.action(project,run,agent,'poll',dict(endpoint_id=endpoint['id'],task_id=task['id']))
    with pytest.raises(RouteError): await a2a.action(project,dict(run,id='different'),agent,'poll',dict(endpoint_id=endpoint['id'],task_id=task['id']))
    with pytest.raises(RouteError): await a2a.action({},run,agent,'send',dict(endpoint_id=endpoint['id'],content='denied'))
    binding='JSONRPC';version='0.3';await a2a.discover(endpoint['id'])
    task=await a2a.action(project,run,agent,'send',dict(endpoint_id=endpoint['id'],content='older protocol'));assert task['remote_id']=='t2'

@pytest.mark.asyncio
async def test_exo_observation_preview_digest_and_deployment(store,vault,monkeypatch):
    from swarm.cluster import Cluster
    from swarm.providers import Providers
    from swarm.schema import ProfileInput
    provider=Providers(store,vault,'http://127.0.0.1:1');profile=provider.add(ProfileInput(provider='exo',label='Exo',model='fixture',base_url='http://127.0.0.1:9876/v1'))
    observed=[]
    def respond(request):
        observed.append(request)
        if request.url.path=='/state': return httpx.Response(200,json=dict(topology=dict(nodes=['mac1','mac2']),node_memory={'mac1':16,'mac2':16}))
        if request.url.path=='/v1/models': return httpx.Response(200,json=dict(data=[dict(id='fixture')]))
        if request.url.path=='/instance/placement': return httpx.Response(200,json=dict(instance_id='owned',shard_assignments=dict(model_id='fixture')))
        assert request.url.path=='/instance' and json.loads(request.content)['instance']['instance_id']=='owned'
        return httpx.Response(200,json=dict(command_id='request1'))
    original=httpx.AsyncClient;monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(respond),**kw))
    cluster=Cluster(store,provider);state=await cluster.inspect(profile['id']);assert len(state['state']['topology']['nodes'])==2
    preview=await cluster.preview(profile['id'],'fixture','Pipeline',2)
    with pytest.raises(RouteError): await cluster.deploy(profile['id'],'0'*64)
    result=await cluster.deploy(profile['id'],preview['sha256']);assert result['result']['command_id']=='request1'
