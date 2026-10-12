import asyncio
import copy
import io
import json
from pathlib import Path
import time
import zipfile
from contextlib import asynccontextmanager
from types import SimpleNamespace
import httpx
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.app import create_app
from swarm.architecture_workbench import Workshop
from swarm.schema import Architecture,RunInput,JoinInput
from swarm.engine import Engine
from swarm.providers import Providers,RouteError
from swarm.network import PrivatePool
from swarm.workspace import Workspace
from swarm.experiments import Experiments,fingerprint,require_gate,ablate
from swarm.reliability import lint,fix_lint,compose
from swarm import checkpoint_tools,workspace_sync
from swarm.runtime_install import RuntimeInstaller,unpack,sha
from swarm.private_cluster import private_ip,model_manifest,ClusterWorker
from swarm.extension_isolation import policy,IsolatedRPC
from swarm.privacy import digest


def team(nodes=('writer',),edges=(),**extra):
    return Architecture(id='quality_test',name='Quality test',nodes=[dict(id=n,name=n,role='Complete the deliverable') for n in nodes],edges=[dict(source=a,target=b) for a,b in edges],**extra).model_dump()


@asynccontextmanager
async def runner(store,vault,tmp_path):
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'quality-checkpoints.sqlite')) as saver:
        engine=Engine(store,Providers(store,vault,'http://127.0.0.1:1'),PrivatePool(store),saver,Workspace(store,tmp_path))
        try:yield engine
        finally:await engine.stop()


@pytest.mark.asyncio
async def test_brief_acceptance_failure_repair_and_outcome_join(store,vault,tmp_path,model_fixture):
    calls=[]
    async def response(name,role,task,prior,steering=''):
        calls.append((name,task))
        if name=='broken':raise RouteError('Provider is temporarily unavailable')
        yield 'approved deliverable'
    model_fixture(store,vault,response)
    arch=team(('broken','success','repair','finish'),(('broken','success'),('broken','repair'),('success','finish'),('repair','finish')))
    arch['edges'][1]['on']='failure'
    async with runner(store,vault,tmp_path) as engine:
        run=engine.create(RunInput(task='Produce a report',architecture_id=arch['id'],brief={'objective':'Report','deliverables':['A table'],'acceptance':[{'kind':'contains','value':'approved'}]}),arch)
        await engine.tasks[run['id']]
        saved=store.get('run',run['id'])
        assert saved['status']=='completed' and saved['acceptance']['passed']
        assert [n for n,_ in calls]==['broken','repair','finish']
        assert 'A table' in calls[0][1]
        assert store.get('agent',run['id']+':broken')['result']['failed']
        assert store.get('agent',run['id']+':success')['status']=='skipped'


@pytest.mark.asyncio
async def test_stage_timeout_routes_to_repair_but_budget_does_not(store,vault,tmp_path,model_fixture):
    async def response(name,*args):
        if name=='slow':await asyncio.sleep(2)
        yield 'done'
    model_fixture(store,vault,response)
    arch=team(('slow','repair'),(('slow','repair'),));arch['edges'][0]['on']='failure';arch['nodes'][0]['timeout_seconds']=1
    async with runner(store,vault,tmp_path) as engine:
        run=engine.create(RunInput(task='Do work',architecture_id=arch['id']),arch);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed'
        assert 'Stage timed out' in store.get('agent',run['id']+':slow')['result']['content']
        run=engine.create(RunInput(task='No budget',architecture_id=arch['id'],limits={'max_tokens':1000,'max_requests':1}),arch)
        # Force a hard global limit before the request. Recovery must not bypass it.
        from swarm.mission import BudgetExceeded
        async def exhausted(*args):raise BudgetExceeded('Mission request budget reached')
        engine.respond=exhausted
        await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='budget_exceeded'
        assert store.get('agent',run['id']+':repair')['status']=='waiting'


@pytest.mark.asyncio
async def test_acceptance_failed_is_saved_and_checkpoint_fork_reuses_completed_requests(store,vault,tmp_path,model_fixture):
    calls=[]
    async def response(name,*args):calls.append(name);yield 'ok'
    model_fixture(store,vault,response)
    arch=team(('first','last'),(('first','last'),))
    async with runner(store,vault,tmp_path) as engine:
        run=engine.create(RunInput(task='Work',architecture_id=arch['id'],brief={'acceptance':[{'kind':'contains','value':'missing'}]}),arch);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='acceptance_failed'
        points=await checkpoint_tools.history(engine,run['id']);point=next(p for p in points if len(p['results'])==1)
        fork=await checkpoint_tools.fork(engine,run['id'],point['id']);await engine.tasks[fork['id']]
        assert calls==['first','last','last']
        assert store.get('agent',fork['id']+':first')['messages'][0]['source']=='checkpoint'
        store.patch('run',run['id'],dict(architecture={**arch,'name':'Changed revision'}))
        with pytest.raises(RouteError,match='different graph revision'):await checkpoint_tools.fork(engine,run['id'],point['id'])


def test_graph_lint_and_fix_find_parameter_type_and_missing_port(store):
    for reserved in ('results','architecture_digest'):
        with pytest.raises(ValueError,match='reserved'):team((reserved,))
    arch=team(('source','target'),(('source','target'),))
    arch['nodes'][0].update(instructions='Work in {{language}}',output_schema={'type':'object','properties':{'count':{'type':'integer'}},'additionalProperties':False})
    arch['nodes'][1]['input_schema']={'type':'object','properties':{'count':{'type':'string'}},'required':['count']}
    arch['edges'][0]['bindings']=[{'source_path':'count','target_name':'count'}]
    result=lint(store,arch)
    assert {'missing_parameter','port_type'}<={i['code'] for i in result['issues']}
    arch=fix_lint(arch,next(i['fix'] for i in result['issues'] if i['code']=='missing_parameter'))
    assert 'language' in arch['parameters']['properties']
    arch['edges'][0]['bindings'][0]['source_path']='unknown'
    assert 'missing_port' in [i['code'] for i in lint(store,arch)['issues']]
    arch['edges'][0].update(on='failure',bindings=[dict(source_path='error',target_name='count')])
    assert 'missing_port' not in [i['code'] for i in lint(store,arch)['issues']]


def test_artifact_disagreements_three_contributors_and_provenance(store):
    store.put('run','r',dict(id='r'))
    for node in ('a','b','c'):
        store.put('agent','r:'+node,dict(id='r:'+node,node_id=node,status='done',result=dict(node_id=node,content='{"answer":"'+node+'"}',structured={'answer':node})))
    sections=[dict(name='answer',node_id=n,path='answer') for n in ('a','b','c')]
    unresolved=compose(store,'r',sections)
    assert not unresolved['ready'] and len(unresolved['conflicts'])==1
    result=compose(store,'r',sections,{'answer':'r:c'})
    assert result['ready'] and result['fields']=={'answer':'c'} and len(result['provenance']['answer'])==3
    with pytest.raises(RouteError):compose(store,'r',sections,{'answer':'foreign:agent'})


def test_table_assembly_merges_rows_and_requires_cell_resolution(store):
    store.put('run','r',dict(id='r'))
    for node,data in [('a',[dict(id='one',value=1)]),('b',[dict(id='one',value=2),dict(id='two',value=3)])]:
        store.put('agent','r:'+node,dict(id='r:'+node,node_id=node,status='done',result=dict(node_id=node,content=json.dumps(data),structured=data)))
    sections=[dict(name='table',node_id=n,path='',mode='table') for n in ('a','b')]
    result=compose(store,'r',sections)
    assert len(result['fields']['table'])==2 and not result['ready']
    result=compose(store,'r',sections,{'table.one.value':'r:b'})
    assert result['ready'] and result['fields']['table']==[dict(id='one',value=2),dict(id='two',value=3)]


@pytest.mark.asyncio
async def test_repeated_ablations_gate_revision_and_shared_budget(store,vault,tmp_path,model_fixture):
    async def response(*args):yield 'ok'
    model_fixture(store,vault,response)
    arch=team(('first','last'),(('first','last'),))
    async with runner(store,vault,tmp_path) as engine:
        service=Experiments(store,engine)
        policy=dict(tasks=[dict(task='Write ok',checks=[dict(kind='contains',value='ok')])],repeats=2,required_nodes=[],forbidden_tools=[])
        store.put('regression_gate','g',dict(id='g',policy=policy))
        form=dict(architecture=arch,**policy,gate_id='g',mission={'limits':{'max_requests':20,'max_tokens':64000}},ablations=[dict(name='Without first',remove_nodes=['first'])])
        row=service.prepare(form);await service.run(row['id']);job=store.get('experiment',row['id'])
        assert job['status']=='completed' and len(job['rows'])==4 and job['passed']
        assert job['summaries'][0]['requests']==4 and job['summaries'][1]['requests']==2
        approved={**arch,'regression_gate_id':'g'};require_gate(store,approved)
        saved=Workshop(store).save(approved)
        assert saved['regression_gate_id']=='g'
        changed=copy.deepcopy(saved);changed['nodes'][0]['instructions']='New behavior'
        with pytest.raises(RouteError,match='passing repeated evaluation'):Workshop(store).save(changed)
        with pytest.raises(RouteError,match='Clear'):Workshop(store).save({**saved,'regression_gate_id':''})
        limited=service.prepare({**form,'gate_id':'','mission':{'limits':{'max_requests':3,'max_tokens':64000}}});await service.run(limited['id'])
        limited=store.get('experiment',limited['id'])
        assert limited['status']=='failed' and 'budget' in limited['error']
        reservations=[r for r in store.all('reservation') if r['root_id']==limited['root_run_id']]
        assert sum(r.get('units',1) for r in reservations)<=3


@pytest.mark.asyncio
async def test_duplicate_review_reuses_only_approved_unchanged_source(store,vault,tmp_path,model_fixture):
    calls=[]
    async def response(name,*args):calls.append(name);yield 'ok'
    model_fixture(store,vault,response)
    arch=team(('first','second'),(('first','second'),))
    async with runner(store,vault,tmp_path) as engine:
        run=engine.create(RunInput(task='Same bounded task',architecture_id=arch['id'],deduplicate_tasks=True),arch);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='waiting' and calls==['first']
        gate=store.all('execution_gate')[0]
        store.patch('execution_gate',gate['id'],dict(status='approved',reuse_agent_id=run['id']+':first'))
        engine.start(run['id']);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed' and calls==['first']
        assert store.get('agent',run['id']+':second')['result']['reused_from']==run['id']+':first'


def test_encrypted_selective_sync_conflict_replay_and_no_credentials(store,tmp_path):
    from swarm.store import Store
    value=Workshop(store).save(team());password='a long test passphrase'
    selection=[dict(kind='architecture',id=value['id'])]
    envelope=workspace_sync.seal(store,selection,password)
    assert 'nodes' not in json.dumps(envelope)
    with StoreContext(tmp_path/'destination.sqlite') as target:
        preview=workspace_sync.preview(target,envelope,password)
        receipt=workspace_sync.apply(target,envelope,password,preview['review_digest'],{})
        assert receipt['items'][0]['kind']=='architecture'
        assert workspace_sync.preview(target,envelope,password)['already_applied']
        target.patch('architecture',value['id'],dict(name='Local change'))
        another=workspace_sync.seal(store,selection,password);review=workspace_sync.preview(target,another,password)
        assert review['items'][0]['conflict']
        target.patch('architecture',value['id'],dict(name='New change'))
        with pytest.raises(RouteError,match='changed since preview'):workspace_sync.apply(target,another,password,review['review_digest'],{})
    with pytest.raises(RouteError,match='decrypt'):workspace_sync.open_bundle(envelope,'incorrect password')
    store.patch('architecture',value['id'],dict(description='api_key=sk-'+('a'*30)))
    with pytest.raises(RouteError,match='credential'):workspace_sync.seal(store,selection,password)


class StoreContext:
    def __init__(self,path):from swarm.store import Store;self.store=Store(path)
    def __enter__(self):return self.store
    def __exit__(self,*args):self.store.close()


def test_sync_requires_current_knowledge_approval_and_archives_history(store):
    row=dict(id='k',project_id='p',title='Finding',content='Approved evidence',revision=1)
    store.put('knowledge','k',row)
    with pytest.raises(RouteError,match='Approve'):workspace_sync.seal(store,[dict(kind='knowledge',id='k')],'long test passphrase')
    store.put('knowledge_sync_approval','k',dict(digest=digest(row)))
    envelope=workspace_sync.seal(store,[dict(kind='knowledge',id='k')],'long test passphrase')
    preview=workspace_sync.preview(store,envelope,'long test passphrase');workspace_sync.apply(store,envelope,'long test passphrase',preview['review_digest'],{})
    assert store.get('synced_knowledge','k')['read_only']
    store.patch('knowledge','k',dict(content='Changed finding'))
    with pytest.raises(RouteError,match='Approve'):workspace_sync.seal(store,[dict(kind='knowledge',id='k')],'long test passphrase')


def test_runtime_archive_traversal_symlink_and_tamper(store,tmp_path):
    package=tmp_path/'package.zip'
    with zipfile.ZipFile(package,'w') as archive:archive.writestr('../escape','bad');archive.writestr('llama-server.exe',b'fixture')
    with pytest.raises(RouteError,match='Unsafe'):unpack(package,tmp_path/'extract')
    with zipfile.ZipFile(package,'w') as archive:archive.writestr('llama-server.exe',b'fixture');archive.writestr('lib.dll',b'dependency')
    folder=tmp_path/'managed-runtimes'/'installed';contents=unpack(package,folder)
    row=dict(folder=str(folder),**contents)
    installer=RuntimeInstaller(store,tmp_path,None)
    assert Path(installer.verify(row)).name=='llama-server.exe'
    (folder/'lib.dll').write_bytes(b'tampered')
    with pytest.raises(RouteError,match='changed'):installer.verify(row)
    (folder/'lib.dll').write_bytes(b'dependency');(folder/'unexpected.dll').write_bytes(b'extra')
    with pytest.raises(RouteError,match='contents differ'):installer.verify(row)


@pytest.mark.asyncio
async def test_runtime_activation_and_rollback_do_not_launch_binary(store,tmp_path):
    closed=[]
    async def close():closed.append(True)
    runtimes=SimpleNamespace(close=close,providers=SimpleNamespace(local_lock=asyncio.Lock()))
    installer=RuntimeInstaller(store,tmp_path,runtimes)
    for id in ('one','two'):
        folder=tmp_path/'managed-runtimes'/id;folder.mkdir(parents=True);(folder/'llama-server.exe').write_bytes(id.encode())
        store.put('runtime_install',id,dict(id=id,status='installed',folder=str(folder),server='llama-server.exe',files={'llama-server.exe':sha(folder/'llama-server.exe')}))
    await installer.activate('one');await installer.activate('two')
    assert store.get('settings','active_runtime')['previous_id']=='one'
    await installer.activate('one');assert len(closed)==3


@pytest.mark.parametrize('address',['8.8.8.8','127.0.0.1','169.254.1.2','::1','0.0.0.0'])
def test_private_cluster_rejects_public_loopback_and_linklocal(address):
    with pytest.raises(RouteError):private_ip(address)


def test_private_cluster_manifest_and_model_code_rejection(tmp_path):
    root=tmp_path/'models';folder=root/'model';folder.mkdir(parents=True);(folder/'config.json').write_text('{}');(folder/'model.safetensors').write_bytes(b'weights fixture')
    before=model_manifest(root,'model');(folder/'model.safetensors').write_bytes(b'changed weights fixture')
    assert model_manifest(root,'model')['digest']!=before['digest']
    (folder/'model.py').write_text('pass')
    with pytest.raises(RouteError,match='remote code'):model_manifest(root,'model')


def test_worker_cluster_lease_needs_separate_approval_and_revocation(store):
    pool=PrivatePool(store);invite=pool.invite()
    joined=pool.join(JoinInput(invite=invite['token'],name='Cluster worker',budgets={'ram_gb':4,'vram_gb':8},cluster={'enabled':True}))
    device=store.patch('device',joined['device_id'],dict(approved=True))
    def job(id):store.put('job',id,dict(id=id,device_id=device['id'],kind='cluster',model='control',messages=[],text='',status='queued',deadline=time.time()+60,payload={'action':'inspect'}))
    job('rejected');assert pool.lease(device) is None and store.get('job','rejected')['status']=='failed'
    device=store.patch('device',device['id'],dict(cluster_approved=True));job('allowed');lease=pool.lease(device)
    assert lease['kind']=='cluster'
    device=store.patch('device',device['id'],dict(cluster_approved=False))
    with pytest.raises(ValueError,match='withdrawn'):pool.result(device,'allowed',lease['lease'],dict(done=True,cluster_result={'healthy':True}))


def test_extension_isolation_digest_resource_and_secret_reference_bounds():
    image='registry/fixture@sha256:'+('a'*64)
    result=policy(dict(image=image,files=['src/main.py'],secrets={'TOKEN':'extension_fixture'}))
    assert result['network']=='none'
    for changed in (dict(image='latest'),dict(memory_mb=0),dict(cpus=99),dict(secrets={'TOKEN':'provider_key'})):
        with pytest.raises(RouteError):policy({**result,**changed})


def test_extension_snapshot_does_not_follow_in_project_symlink_to_private_file(tmp_path):
    from swarm.full_backup import eligible_files
    (tmp_path/'.env').write_text('private data')
    try:(tmp_path/'ordinary.txt').symlink_to(tmp_path/'.env')
    except OSError:pytest.skip('Symlink creation requires platform permission')
    assert not list(eligible_files(tmp_path))


@pytest.mark.asyncio
async def test_ablations_preserve_frozen_child_revisions(store,vault,tmp_path,model_fixture):
    calls=[]
    (tmp_path/'data.txt').write_text('fixture evidence')
    store.put('project','p',dict(id='p',name='Project',root=str(tmp_path),tools=['workspace.read'],checks={}))
    async def response(name,*args):
        calls.append(name)
        if name in {'child','New child'}:
            agent=next(a for a in store.all('agent') if a['name']==name and a['status']=='running')
            await engine.workspace.execute(store.get('run',agent['run_id']),agent,dict(tool='workspace.read',arguments=dict(path='data.txt')))
        yield 'ok'
    model_fixture(store,vault,response)
    child={**team(('child',)),'id':'child_team'};store.put('architecture',child['id'],child)
    parent=team(('nested','extra'),())
    parent['nodes'][0].update(kind='subswarm',subswarm_id='child_team')
    async with runner(store,vault,tmp_path) as engine:
        service=Experiments(store,engine)
        form=dict(architecture=parent,tasks=[dict(task='Work',checks=[dict(kind='contains',value='ok')])],repeats=1,ablations=[dict(name='Without extra',remove_nodes=['extra'])],mission={'project_id':'p','tools_enabled':True,'max_subtasks':8,'limits':{'max_requests':20,'max_tokens':64000}})
        row=service.prepare(form)
        changed=copy.deepcopy(child);changed['nodes'][0]['name']='New child';store.put('architecture',child['id'],changed)
        await service.run(row['id'])
        assert store.get('experiment',row['id'])['status']=='completed'
        assert calls.count('child')==2 and 'New child' not in calls
        assert [r['requests'] for r in store.get('experiment',row['id'])['rows']]==[2,1]
        assert row['architecture_digest']!=fingerprint(store,parent)
        policy=store.get('experiment',row['id'])['policy']
        store.put('regression_gate','frozen_gate',dict(id='frozen_gate',policy=policy,proof_id=row['id']))
        with pytest.raises(RouteError,match='passing repeated evaluation'):
            engine.create(RunInput(task='New mission',architecture_id=parent['id']),{**parent,'regression_gate_id':'frozen_gate'})
        restricted=service.prepare({**form,'forbidden_tools':['workspace.read']});await service.run(restricted['id'])
        restricted=store.get('experiment',restricted['id'])
        assert restricted['status']=='completed' and not restricted['passed']
        assert all('Forbidden tool: workspace.read' in r['failures'] for r in restricted['rows'])


@pytest.mark.asyncio
async def test_sandbox_redacts_acp_notifications_as_well_as_results(vault):
    received=[]
    class RPC:
        async def send(self,*args,**kwargs):
            kwargs['on_notification']({'content':'secret fixture value'})
            return {'result':'secret fixture value'}
    isolated=IsolatedRPC({}, {}, vault);isolated.rpc=RPC();isolated.redactions=['secret fixture value']
    result=await isolated.send('session/prompt',{},on_notification=received.append)
    assert result['result']=='[sandbox secret redacted]' and received[0]['content']=='[sandbox secret redacted]'


@pytest.mark.asyncio
async def test_quality_owner_api_sync_preview_and_scope(tmp_path,vault):
    app=create_app(tmp_path,token='owner',vault=vault)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://127.0.0.1:8000') as client:
            assert (await client.get('/api/systems')).status_code==401
            client.headers['Authorization']='Bearer owner'
            response=await client.post('/api/quality/lint',json={'value':{'architecture':team()}})
            assert response.status_code==200 and response.json()['errors']==0
            assert (await client.get('/api/systems')).status_code==200
            arch=app.state.architectures.save(team());password='long test sync passphrase'
            exported=await client.post('/api/systems/sync/export',json={'value':{'selection':[{'kind':'architecture','id':arch['id']}],'password':password}})
            assert exported.status_code==200
            preview=await client.post('/api/systems/sync/preview',json={'value':{'envelope':exported.json(),'password':password}})
            assert preview.status_code==200
            assert (await client.post('/api/team/sync',json={'value':{'envelope':exported.json()}})).status_code==401
