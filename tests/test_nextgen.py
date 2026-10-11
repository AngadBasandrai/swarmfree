import asyncio
import base64
import io
import json
import os
from pathlib import Path
import struct
import time
import zipfile
import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.app import create_app
from swarm.browser_agents import BrowserAgents,origin_of,addresses
from swarm.clarifications import Clarifications
from swarm.engine import Engine
from swarm.full_backup import create_full_backup,preview_full,restore_full,unpack
from swarm.intelligence import Intelligence,Evaluations,check_output,route_key
from swarm.mission import MissionBudget
from swarm.network import PrivatePool
from swarm.protocol import argument_schema,decode_calls,native_history,usage_counts,validate_schema
from swarm.providers import Providers,RouteError,ollama_stream
from swarm.remote_execution import RemoteExecution,checked_files,snapshot,execute_container
from swarm.runtimes import Runtimes,inspect_gguf
from swarm.schema import Architecture,RunInput,ProfileInput,JoinInput
from swarm.workspace import Workspace


async def until(check):
    async with asyncio.timeout(6):
        while not check(): await asyncio.sleep(.01)


def setup(store,vault,tmp_path):
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    providers=Providers(store,vault,'http://127.0.0.1:1')
    workspace=Workspace(store,tmp_path)
    return providers,workspace,Intelligence(store,providers,workspace)


def test_schema_tools_and_complete_native_handoff_groups():
    schema=argument_schema({'path':'file'})
    mapping={'workspace_read':('workspace.read',schema)}
    request=decode_calls([dict(id='one',function=dict(name='workspace_read',arguments='{"path":"README.md"}'))],mapping)[0]
    assert request['tool']=='workspace.read'
    with pytest.raises(RouteError): decode_calls([dict(function=dict(name='workspace_read',arguments='{"path":3}'))],mapping)
    with pytest.raises(RouteError): decode_calls([dict(function=dict(name='ungranted',arguments='{}'))],mapping)
    with pytest.raises(RouteError): validate_schema({'$ref':'https://example.com/schema'})
    with pytest.raises(RouteError): validate_schema({'type':'string','pattern':'(a+)+$'})
    calls=[dict(id='one',function=dict(name='workspace_read',arguments={'path':'README.md'}))]
    history=[dict(role='assistant',content='Native request',native_calls=calls),dict(role='user',content='Tool result',tool_call_id='one',tool_name='workspace.read')]
    assert native_history(history)[1]['role']=='tool'
    assert native_history(history,ollama=True)[0]['tool_calls'][0]['function']['arguments']=={'path':'README.md'}
    assert native_history(history[1:])[0]['role']=='user'


def test_reported_usage_replaces_token_estimates_and_preserves_uncertain_reservations(store,vault,tmp_path):
    setup(store,vault,tmp_path)
    counts=usage_counts({'usage':{'prompt_tokens':120,'completion_tokens':10,'prompt_tokens_details':{'cached_tokens':60}}})
    assert counts['cache_tokens']==60 and counts['reported']
    assert usage_counts({'usage':{'prompt_tokens':True,'completion_tokens':8}})=={}
    run=dict(id='r',limits={},mode='live',free_only=True);store.put('run','r',run)
    budget=MissionBudget(store);budget.reserve(run,'one','local','model',[dict(role='user',content='hello')]);budget.finish('one','hi',usage=counts)
    assert budget.usage(run)['tokens']==130 and budget.usage(run)['reported_usage']['cache_tokens']==60
    reserved=budget.reserve(run,'two','local','model',[dict(role='user',content='hello')]);budget.finish('two','partial',usage=counts,uncertain=True)
    assert store.get('reservation','two')['tokens']==reserved['tokens']


async def test_fragmented_native_calls_and_provider_usage_are_collected(store,vault,tmp_path,monkeypatch):
    providers,_,_=setup(store,vault,tmp_path)
    p=providers.add(ProfileInput(provider='compatible',label='Fixture',model='fixture',requests_per_minute=600))
    chunks=[dict(choices=[dict(index=0,delta=dict(tool_calls=[dict(index=0,id='call1',function=dict(name='workspace_',arguments='{"pa'))]))]),
        dict(choices=[dict(index=0,delta=dict(tool_calls=[dict(index=0,function=dict(name='read',arguments='th":"README.md"}'))]),finish_reason='tool_calls')]),
        dict(choices=[],usage=dict(prompt_tokens=42,completion_tokens=6)), '[DONE]']
    wire=''.join('data: '+(c if isinstance(c,str) else json.dumps(c))+'\n\n' for c in chunks)
    real=httpx.AsyncClient
    def handle(request):
        body=json.loads(request.content);assert body['stream_options']['include_usage'] and body['tools']
        return httpx.Response(200,text=wire)
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**kw:real(transport=httpx.MockTransport(handle)))
    usage,calls={},[]
    output=''.join([c async for c in providers.compatible(dict(p,_usage=usage,_native_calls=calls,_tools=[dict(type='function',function=dict(name='workspace_read',parameters={'type':'object'}))]),[dict(role='user',content='read')])])
    assert output=='' and calls[0]['function']['name']=='workspace_read'
    assert json.loads(calls[0]['function']['arguments'])=={'path':'README.md'} and usage['input_tokens']==42


async def test_ollama_native_calls_and_counts(store,vault,tmp_path,monkeypatch):
    real=httpx.AsyncClient
    wire=json.dumps(dict(message=dict(content='',tool_calls=[dict(function=dict(name='user_ask',arguments={'question':'Which format?'}))]),done=False))+'\n'+json.dumps(dict(done=True,prompt_eval_count=20,eval_count=7,prompt_eval_cached_count=4))+'\n'
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,text=wire))))
    usage,calls={},[]
    result=[c async for c in ollama_stream('http://127.0.0.1:1','fixture',[],dict(context=4096,_usage=usage,_native_calls=calls),'cpu')]
    assert not result and usage['output_tokens']==7 and calls[0]['function']['name']=='user_ask'


async def test_validation_escalates_and_only_validated_products_are_handed_off(store,vault,tmp_path,monkeypatch):
    providers,workspace,intelligence=setup(store,vault,tmp_path)
    architecture=Architecture(id='typed',name='Typed handoff',nodes=[dict(id='writer',name='Writer',role='Write',output_schema={'type':'object','required':['summary'],'properties':{'summary':{'type':'string'}}}),dict(id='reader',name='Reader',role='Read')],edges=[dict(source='writer',target='reader')]).model_dump()
    calls=[]
    async def options(run,agent): return [('local','small:1b'),('local','big:8b')]
    async def local(model,messages,budgets):
        calls.append((model,messages))
        budgets['_usage'].update(input_tokens=20,output_tokens=5,cache_tokens=0,reported=True,source='fixture')
        yield '{}' if model=='small:1b' else '{"summary":"Verified result"}'
    monkeypatch.setattr(providers,'local',local)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver,workspace);engine.intelligence=intelligence
        monkeypatch.setattr(engine,'candidates',options)
        run=engine.create(RunInput(task='Produce typed work',architecture_id='typed'),architecture)
        await engine.tasks[run['id']]
        saved=store.get('run',run['id']);assert saved['status']=='completed'
        writer=store.get('agent',run['id']+':writer');assert writer['result']['structured']=={'summary':'Verified result'}
        assert [m for m,_ in calls[:2]]==['small:1b','big:8b']
        assert 'Verified result' in json.dumps(calls[-1][1])
        assert [o['passed'] for o in store.all('outcome')][:2]==[False,True]
        assert engine.budget.usage(saved)['reported_requests']==3
        await engine.stop()


def test_task_ranking_uses_measured_quality_and_queue_pressure(store,vault,tmp_path):
    _,_,intel=setup(store,vault,tmp_path)
    agent=dict(id='a',tools=[],output_schema={'type':'object'});store.put('agent','a',agent)
    run=dict(id='r',preset='coding',routing_policy='quality')
    intel.outcome(run,agent,'local','small:1b',dict(passed=False,failures=['bad']),30)
    intel.outcome(run,agent,'local','big:8b',dict(passed=True,failures=[]),40)
    assert intel.rank(run,agent,[('local','small:1b'),('local','big:8b')])[0][1]=='big:8b'
    store.put('device','d',dict(id='d',hardware=dict(ram_available_gb=4),budgets=dict(ram_gb=4),models=[]))
    store.put('job','j',dict(device_id='d',status='queued',deadline=time.time()+10))
    assert intel.rank(dict(run,routing_policy='smallest'),agent,[('device',dict(device_id='d',model='tiny:0.6b')),('local','small:1b')])[0][0]=='local'


async def test_semantic_search_surfaces_synonyms_and_staleness_without_cross_project_leaks(store,vault,tmp_path,monkeypatch):
    _,workspace,intel=setup(store,vault,tmp_path)
    project_root=tmp_path/'project';project_root.mkdir();(project_root/'notes.txt').write_text('Electric vehicles use rechargeable batteries.',encoding='utf-8');(project_root/'.env').write_text('do not index')
    store.put('project','p',dict(id='p',root=str(project_root),name='Cars'));store.put('project','other',dict(id='other',root=str(tmp_path/'other'),name='Private'))
    workspace.remember('other','Private note','A hidden answer')
    async def embeddings(texts,model): return [[1.,0.] if ('vehicles' in t or 'transport' in t) else [0.,1.] for t in texts]
    monkeypatch.setattr(intel,'embed',embeddings)
    await intel.index('p','fixture');result=await intel.search('p','battery powered transport')
    assert result['matches'][0]['path']=='notes.txt' and result['matches'][0]['line']==1 and not result['matches'][0]['stale']
    assert 'hidden' not in json.dumps(result) and '.env' not in json.dumps(result)
    (project_root/'notes.txt').write_text('Changed contents',encoding='utf-8')
    assert (await intel.search('p','transport'))['matches'][0]['stale']
    with pytest.raises(RouteError): await intel.search('other','transport')


async def test_durable_question_suspends_only_requesting_agent_and_resumes_after_answer(store,vault,tmp_path):
    setup(store,vault,tmp_path);service=Clarifications(store)
    run=dict(id='r');agent=dict(id='a',status='running');store.put('agent','a',agent)
    task=asyncio.create_task(service.ask(run,agent,dict(question='Which format?',options=['JSON','Text'])))
    await until(lambda:bool(store.all('question')))
    assert store.get('agent','a')['status']=='awaiting_input'
    question=store.all('question')[0]
    with pytest.raises(RouteError): service.answer(question['id'],42)
    other_completed=False
    async def other():
        nonlocal other_completed
        await asyncio.sleep(.01);other_completed=True
    await other();assert other_completed and not task.done()
    task.cancel();await asyncio.gather(task,return_exceptions=True)
    again=asyncio.create_task(Clarifications(store).ask(run,agent,dict(question='Which format?',options=['JSON','Text'])))
    service.answer(question['id'],'JSON')
    assert (await again)['answer']=='JSON' and len(store.all('question'))==1


def test_gguf_inspection_checks_real_header_and_runtime_file_changes(store,vault,tmp_path):
    providers,_,_=setup(store,vault,tmp_path)
    file=tmp_path/'fixture.gguf';file.write_bytes(b'GGUF'+struct.pack('<IQQ',3,1,1)+b'fixture')
    executable=tmp_path/'llama-server';executable.write_text('fixture executable')
    assert inspect_gguf(file)['gguf_version']==3
    manager=Runtimes(store,providers,tmp_path)
    with pytest.raises(RouteError): manager.add(dict(path=str(file),server_path=str(executable),trusted=False))
    row=manager.add(dict(path=str(file),server_path=str(executable),trusted=True))
    assert manager.public()[0]['fits'] and row['status']=='stopped'
    file.write_bytes(b'bad')
    with pytest.raises(RouteError): inspect_gguf(file)


async def test_remote_execution_requires_two_grants_fences_leases_and_stages_results(store,vault,tmp_path):
    _,workspace,_=setup(store,vault,tmp_path);pool=PrivatePool(store);remote=RemoteExecution(store,pool,workspace)
    root=tmp_path/'project';root.mkdir();(root/'work.txt').write_text('original')
    project=dict(id='p',root=str(root),name='Project',tools=['remote.execute'],checks={'build':['python','build.py']},execution_devices=['d'])
    store.put('project','p',project)
    device=dict(id='d',approved=True,revoked=False,last_seen=time.time(),execution=dict(enabled=True),execution_approved=False)
    store.put('device','d',device);run=dict(id='r',project_id='p',tools_enabled=True);agent=dict(id='r:a',tools=None)
    with pytest.raises(RouteError): await remote.execute(project,run,agent,'d','build')
    device=store.patch('device','d',dict(execution_approved=True))
    task=asyncio.create_task(remote.execute(project,run,agent,'d','build'));await until(lambda:bool(store.all('job')))
    lease=pool.lease(device);assert lease['kind']=='execution' and lease['payload']['files']=={'work.txt':'original'}
    with pytest.raises(ValueError): pool.result(device,lease['id'],'stale',dict(done=True,execution_result=dict(exit_code=0,files={},output='')))
    pool.result(device,lease['id'],lease['lease'],dict(done=True,execution_result=dict(exit_code=0,files={'work.txt':'reviewable change'},output='build passed')))
    result=await task;assert result['exit_code']==0 and len(result['artifacts'])==1
    assert (root/'work.txt').read_text()=='original'
    workspace.apply(result['artifacts'][0]);assert (root/'work.txt').read_text()=='reviewable change'


@pytest.mark.parametrize('name',['../escape','C:/escape','/absolute','nested/.env','nested\\escape','.git/config'])
def test_remote_transfer_rejects_private_and_escaping_paths(name):
    with pytest.raises(RouteError): checked_files({name:'content'})


async def test_remote_worker_builds_only_container_argv_and_cleans_its_container(tmp_path,monkeypatch):
    captured=[]
    monkeypatch.setattr('swarm.isolation.shutil.which',lambda _: 'docker')
    async def check(self,id,project,argv):
        captured.append(argv);Path(project['root'],'result.txt').write_text('built');return dict(exit_code=0,output='ok')
    async def cleanup(argv,**kw): captured.append(argv);return ''
    monkeypatch.setattr(Workspace,'check',check);monkeypatch.setattr('swarm.isolation.command',cleanup)
    result=await execute_container(dict(argv=['python','build.py'],files={'build.py':'print("build")'}),'owner-image:1')
    assert result['files']['result.txt']=='built'
    assert captured[0][:3]==['docker','run','--rm'] and '--pull=never' in captured[0] and 'none' in captured[0]
    assert '--read-only' in captured[0] and captured[-1][:3]==['docker','rm','-f']


def test_encrypted_backup_recovers_attachments_worktree_and_remapped_project_without_overwrite(store,vault,tmp_path):
    _,workspace,_=setup(store,vault,tmp_path)
    root=tmp_path/'project';root.mkdir();(root/'main.py').write_text('original');(root/'.env').write_text('private secret')
    tree=tmp_path/'tree';tree.mkdir();(tree/'main.py').write_text('unfinished work');(tree/'.git').write_text('machine-specific pointer')
    attachments=tmp_path/'attachments';attachments.mkdir();(attachments/'owner.txt').write_text('owner attachment')
    store.put('project','p',dict(id='p',root=str(root),name='Project',tools=['workspace.read']))
    store.put('worktree','r:a',dict(id='r:a',root=str(tree),project_id='p',agent_id='r:a'))
    store.put('attachment','att',dict(id='att',path=str(attachments/'owner.txt')))
    store.put('extension','unsafe',dict(id='unsafe',enabled=True))
    backup=create_full_backup(tmp_path,store,'strong test passphrase',['p'])
    assert b'private secret' not in Path(backup['path']).read_bytes()
    with pytest.raises(RouteError): unpack(backup['path'],'wrong passphrase')
    target=tmp_path/'recovered';mapped=tmp_path/'remapped-project';mapping={'p':str(mapped)}
    preview=preview_full(backup['path'],'strong test passphrase',target,mapping)
    with pytest.raises(RouteError): restore_full(backup['path'],'strong test passphrase',target,mapping,'0'*64)
    result=restore_full(backup['path'],'strong test passphrase',target,mapping,preview['sha256'])
    assert result['restored'] and (mapped/'main.py').read_text()=='original' and not (mapped/'.env').exists()
    assert (target/'attachments'/'owner.txt').read_text()=='owner attachment'
    from swarm.store import Store
    restored=Store(target/'workbench.sqlite')
    assert restored.get('project','p')['root']==str(mapped)
    recovered_tree=restored.get('worktree','r:a');assert Path(recovered_tree['root'],'main.py').read_text()=='unfinished work'
    assert not Path(recovered_tree['root'],'.git').exists() and not restored.get('extension','unsafe')['enabled']
    restored.close()
    with pytest.raises(RouteError): preview_full(backup['path'],'strong test passphrase',target,mapping)


def test_nextgen_owner_boundary_and_reviewed_inputs(tmp_path,vault):
    app=create_app(tmp_path/'state',token='owner',vault=vault)
    with TestClient(app) as client:
        assert client.get('/api/next').status_code==401
        client.headers['Authorization']='Bearer owner'
        overview=client.get('/api/next');assert overview.status_code==200 and len(overview.json()['catalog'])>=6
        assert client.post('/api/next/runtimes',json=dict(path='missing.gguf',server_path='missing.exe',trusted=True)).status_code==400
        assert client.post('/api/next/evaluations',json=dict(name='Invalid',tasks=[dict(task='task',checks=[dict(kind='shell',value='command')])],variants=[dict(name='one',architecture_id='baseline')])).status_code==422
        assert client.post('/api/next/restore/preview',json=dict(path='backup',password='strong passphrase',target=str(tmp_path/'state'))).status_code==400
        async def hold_download():
            app.state.download_tasks['fixture']=asyncio.create_task(asyncio.Event().wait())
        async def release_download():
            task=app.state.download_tasks.pop('fixture');task.cancel()
            await asyncio.gather(task,return_exceptions=True)
        client.portal.call(hold_download)
        try:
            blocked=client.post('/api/next/backup',json=dict(password='strong backup passphrase',project_ids=[]))
            assert blocked.status_code==400 and 'background work' in blocked.text
            assert not list((tmp_path/'state'/'backups').glob('*.swarmbak'))
        finally: client.portal.call(release_download)


def test_evaluation_suite_recommends_smallest_passing_team(tmp_path,vault):
    app=create_app(tmp_path/'state',token='owner',vault=vault)
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer owner'
        result=client.post('/api/next/evaluations',json=dict(name='Compare teams',tasks=[dict(task='Include verification',checks=[dict(kind='contains',value='verification')])],variants=[dict(name='One',architecture_id='baseline'),dict(name='Team',architecture_id='pipeline')],mission=dict(mode='demo')))
        assert result.status_code==200
        end=time.monotonic()+12
        while time.monotonic()<end:
            job=client.get('/api/next').json()['evaluations'][0]
            if job['status']!='running': break
            time.sleep(.05)
        assert job['status']=='completed' and job['recommendation']['name']=='One' and job['recommendation']['team_size']==1


def test_browser_origins_and_local_networks_are_rejected():
    assert origin_of('https://EXAMPLE.com/path')=='https://example.com'
    for url in ['http://example.com','https://user:pass@example.com','https://example.com:8000']:
        with pytest.raises(RouteError): origin_of(url)


async def test_browser_dns_rejects_loopback():
    with pytest.raises(RouteError): await addresses('https://127.0.0.1')


async def test_engine_native_tools_keep_wire_history_and_validate_before_effects(store,vault,tmp_path,monkeypatch):
    providers,workspace,intel=setup(store,vault,tmp_path)
    project_root=tmp_path/'project';project_root.mkdir();(project_root/'README.md').write_text('Actual project evidence')
    store.put('project','p',dict(id='p',root=str(project_root),name='Project',tools=['workspace.read']))
    observed=[]
    async def local(model,messages,budgets):
        observed.append(native_history(messages,ollama=True))
        if len(observed)==1:
            assert budgets['_tools'][0]['function']['name']=='workspace_read'
            budgets['_native_calls'].append(dict(function=dict(name='workspace_read',arguments={'path':'README.md'})))
        else: yield 'Read the actual evidence and completed the work'
    monkeypatch.setattr(providers,'local',local)
    architecture=Architecture(id='native',name='Native',nodes=[dict(id='reader',name='Reader',role='Read the file')]).model_dump()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver,workspace);engine.intelligence=intel
        run=engine.create(RunInput(task='Read evidence',architecture_id='native',project_id='p',tools_enabled=True,native_tools=True,route=dict(kind='local',model='fixture')),architecture)
        await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed'
        assert any(m['role']=='tool' and 'Actual project evidence' in m['content'] for m in observed[-1])
        assert len(store.all('operation'))==1 and store.all('operation')[0]['status']=='completed'
        assert store.all('request')[0]['tools']
        await engine.stop()


async def test_gguf_managed_lifecycle_uses_loopback_auth_and_reloads_changed_context(store,vault,tmp_path,monkeypatch):
    providers,_,_=setup(store,vault,tmp_path)
    file=tmp_path/'fixture.gguf';file.write_bytes(b'GGUF'+struct.pack('<IQQ',3,1,1)+b'fixture')
    executable=tmp_path/'llama-server';executable.write_text('trusted protocol fixture')
    manager=Runtimes(store,providers,tmp_path);argvs=[]
    class Process:
        returncode=None
        def terminate(self): self.returncode=0
        def kill(self): self.returncode=-1
        async def wait(self): return self.returncode
    async def spawn(*argv,**kw): argvs.append(argv);return Process()
    async def inv(_): return dict(models=[],loaded=[])
    real=httpx.AsyncClient
    monkeypatch.setattr('swarm.runtimes.inventory',inv)
    monkeypatch.setattr(asyncio,'create_subprocess_exec',spawn)
    monkeypatch.setattr(httpx,'AsyncClient',lambda *a,**kw:real(transport=httpx.MockTransport(lambda r:httpx.Response(200,json=dict(status='ok')))))
    row=manager.add(dict(path=str(file),server_path=str(executable),trusted=True))
    ready=await manager.start(row['id'])
    assert ready['status']=='ready' and '--no-agent' in argvs[0] and argvs[0][argvs[0].index('--host')+1]=='127.0.0.1'
    key=providers.vault.get('runtime_'+row['id']);assert key and key not in str(argvs)
    assert not list((tmp_path/'runtime-secrets').iterdir())
    store.patch('settings','budgets',dict(context=8192))
    assert (await manager.start(row['id']))['context']==8192 and len(argvs)==2
    await manager.close();assert not manager.processes and not providers.vault.get('runtime_'+row['id'])


def test_evaluations_execute_granted_named_checks_and_fail_when_permissions_are_missing(tmp_path,vault):
    import sys
    directory=tmp_path/'project';directory.mkdir()
    app=create_app(tmp_path/'state',token='owner',vault=vault)
    with TestClient(app) as client:
        client.headers['Authorization']='Bearer owner'
        project=client.post('/api/projects',json=dict(name='Checks',root=str(directory),tools=['checks.run'],checks={'verify':[sys.executable,'-c','print("passing named check")']})).json()
        for enabled in (True,False):
            response=client.post('/api/next/evaluations',json=dict(name='Named checks',tasks=[dict(task='Complete the task',checks=[dict(kind='named_check',value='verify')])],variants=[dict(name='One',architecture_id='baseline')],mission=dict(mode='demo',project_id=project['id'],tools_enabled=enabled)))
            assert response.status_code==200
            id=response.json()['id'];end=time.monotonic()+8
            while time.monotonic()<end:
                job=next(j for j in client.get('/api/next').json()['evaluations'] if j['id']==id)
                if job['status']!='running': break
                time.sleep(.05)
            assert job['status']=='completed' and job['rows'][0]['passed']==enabled
            if enabled: assert 'passing named check' in job['rows'][0]['check_results'][0]['result']['output']
