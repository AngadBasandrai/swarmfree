import asyncio
import base64
import io
import json
from pathlib import Path
import secrets
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from PIL import Image
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from swarm.app import create_app
from swarm.automation import Automation,AutomationInput
from swarm.context import ContextManager
from swarm.engine import Engine
from swarm.evidence import Evidence
from swarm.isolation import Isolation,command
from swarm.media import Attachments,compatible_messages
from swarm.mission import MissionBudget,BudgetExceeded,token_estimate
from swarm.network import PrivatePool
from swarm.packages import preview,install
from swarm.pairing import qr_image
from swarm.providers import Providers,RouteError
from swarm.relay import create_relay,seal,unseal,RelayEndpoint,RelayWorkerClient
from swarm.schema import Architecture,RunInput,ProfileInput
from swarm.workspace import Workspace,sha


async def until(predicate,seconds=8):
    async with asyncio.timeout(seconds):
        while not predicate(): await asyncio.sleep(.01)


def settings(store,context=4096):
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=context,allow_cpu=True))


def image_bytes():
    buffer=io.BytesIO()
    Image.new('RGB',(24,24),(255,0,0)).save(buffer,format='PNG')
    return buffer.getvalue()


def test_context_compaction_retains_references_recent_direction_and_original_history(store):
    settings(store)
    messages=[dict(id=f'm{i}',role='assistant',content=('Archived details. '*200)+f'\nDecision {i}: preserve source evidence.',complete=True) for i in range(12)]
    messages.append(dict(id='latest',role='user',content='Newest direction: verify the final result.',complete=True))
    agent=dict(id='r:a',name='Agent',role='Review',messages=messages)
    store.put('agent',agent['id'],agent)
    store.put('artifact','patch',dict(id='patch',run_id='r',agent_id='r:a',path='code.py',status='staged'))
    store.put('source','source_one',dict(id='source_one',run_id='r',url='https://example.com'))
    original=json.dumps(messages)
    payload=ContextManager(store).build(dict(id='r',task='Complete a careful review'),agent,'Use evidence.',4096)
    record=store.get('context',agent['id'])
    assert record['compressed_messages']>0 and 'source_one' in record['handoff'] and 'patch' in record['handoff']
    assert 'Newest direction' in json.dumps(payload)
    assert token_estimate(payload)<=4096-record['reserved_output_tokens']-128
    assert json.dumps(store.get('agent',agent['id'])['messages'])==original


def test_shared_budget_counts_children_failed_calls_and_unknown_paid_prices(store):
    settings(store)
    root=dict(id='root',mode='live',free_only=True,limits=dict(max_requests=2,max_tokens=12000,max_seconds=60,max_spend_usd=0))
    child=dict(id='child',root_run_id='root',mode='live',free_only=True)
    store.put('run','root',root);store.put('run','child',child)
    budget=MissionBudget(store)
    messages=[dict(role='user',content='hello')]
    budget.reserve(root,'first','local','model',messages)
    budget.finish('first','partial',uncertain=True)
    budget.reserve(child,'second','device',{},messages)
    with pytest.raises(BudgetExceeded,match='request'): budget.reserve(child,'third','local','model',messages)
    assert budget.usage(child)['requests']==2
    assert store.get('reservation','first')['tokens']>1000
    store.patch('run','root',dict(limits=dict(max_requests=5,max_tokens=12000,max_seconds=60,max_spend_usd=1)))
    with pytest.raises(BudgetExceeded,match='prices'): budget.reserve(dict(root,free_only=False),'paid','profile',dict(provider='compatible'),messages)


def test_spending_reservations_admit_declared_costs_and_charge_duplicate_units(store):
    settings(store)
    run=dict(id='r',mode='live',free_only=False,limits=dict(max_requests=5,max_tokens=20000,max_seconds=60,max_spend_usd=.01))
    store.put('run','r',run)
    budget=MissionBudget(store)
    profile=dict(provider='google',input_usd_per_million=1,output_usd_per_million=1)
    record=budget.reserve(run,'priced','profile',profile,[dict(role='user',content='hi')])
    assert 0<record['cost']<.01
    with pytest.raises(BudgetExceeded,match='spending'): budget.reserve(run,'expensive','profile',dict(profile,output_usd_per_million=100),[dict(role='user',content='hi')])
    free=dict(run,free_only=True)
    duplicate=budget.reserve(free,'duplicate','profile',dict(provider='community',free_eligible=True,verify_duplicates=True),[dict(role='user',content='hi')])
    assert duplicate['units']==2 and budget.usage(run)['requests']==3


async def test_runtime_activity_does_not_double_count_parallel_children(store):
    settings(store)
    store.put('run','r',dict(id='r',limits=dict(max_seconds=60)))
    store.put('run','c',dict(id='c',root_run_id='r'))
    budget=MissionBudget(store)
    budget.begin('r');started=store.get('run','r')['active_since'];budget.begin('c')
    assert store.get('run','r')['active_since']==started
    budget.end('r');assert store.get('run','r')['active_since']==started
    budget.end('c');assert store.get('run','r')['active_since'] is None


async def test_engine_budget_is_enforced_before_second_node(store,vault,tmp_path,monkeypatch):
    settings(store)
    providers=Providers(store,vault,'http://127.0.0.1:1')
    profile=providers.add(ProfileInput(provider='google',label='Fixture',api_key='fake',model='fake',free_eligible=True))
    calls=[]
    async def stream(p,m):calls.append(m);yield 'Completed first stage'
    monkeypatch.setattr(providers,'compatible',stream)
    arch=Architecture(id='two',name='Two stages',nodes=[dict(id='a',name='First',role='Draft'),dict(id='b',name='Second',role='Review')],edges=[dict(source='a',target='b')]).model_dump()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver)
        run=engine.create(RunInput(task='Work',architecture_id='two',limits=dict(max_requests=1),route=dict(kind='profile',profile_id=profile['id'])),arch)
        await until(lambda:store.get('run',run['id'])['status']=='budget_exceeded')
        assert len(calls)==1 and store.get('agent',run['id']+':a')['status']=='done'
        request=store.all('request')[0]
        assert request['messages']==calls[0] and request['max_output_tokens']<=1536
        await engine.stop()


async def test_adaptive_team_deduplicates_specialists_and_shares_budget(store,vault,tmp_path,monkeypatch):
    settings(store)
    providers=Providers(store,vault,'http://127.0.0.1:1')
    profile=providers.add(ProfileInput(provider='google',label='Fixture',api_key='fake',model='fake',free_eligible=True))
    async def stream(p,m):
        identity=m[0]['content']
        if 'You are Planner' in identity: yield json.dumps(dict(answer='Initial plan',tasks=[dict(task='Check A',role='Verifier'),dict(task='Check A',role='Verifier'),dict(task='Check B',role='Reviewer')]))
        elif 'You are Specialist' in identity: yield 'Specialist completed a distinct check'
        else: yield 'Final answer based on actual specialist work'
    monkeypatch.setattr(providers,'compatible',stream)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver)
        arch=Architecture(id='single',name='Original',nodes=[dict(id='a',name='Agent',role='Help')]).model_dump()
        run=engine.create(RunInput(task='Plan a task',architecture_id='single',adaptive='speed',route=dict(kind='profile',profile_id=profile['id'])),arch)
        await until(lambda:store.get('run',run['id'])['status'] in {'completed','interrupted','budget_exceeded'})
        final=store.get('run',run['id'])
        assert final['status']=='completed',final['error']
        children=[r for r in store.all('run') if r.get('adaptive_child')]
        assert len(children)==2 and engine.budget.usage(final)['requests']==4
        assert 'Actual specialist work' in store.get('agent',run['id']+':planner')['result']['content']
        await engine.stop()


async def test_adaptive_complete_answer_avoids_unnecessary_synthesis(store,vault,tmp_path,monkeypatch):
    settings(store)
    providers=Providers(store,vault,'http://127.0.0.1:1')
    profile=providers.add(ProfileInput(provider='google',label='Fixture',api_key='fake',model='fake',free_eligible=True))
    async def stream(p,m):yield '{"answer":"Done alone","tasks":[]}'
    monkeypatch.setattr(providers,'compatible',stream)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver)
        arch=Architecture(id='single',name='Original',nodes=[dict(id='a',name='Agent',role='Help')]).model_dump()
        run=engine.create(RunInput(task='Simple task',architecture_id='single',adaptive='resources',route=dict(kind='profile',profile_id=profile['id'])),arch)
        await until(lambda:store.get('run',run['id'])['status']=='completed')
        assert engine.budget.usage(run)['requests']==1
        assert store.get('agent',run['id']+':synthesis')['result']['content']=='Done alone'
        await engine.stop()


def test_attachment_validation_and_real_multimodal_payload(store,tmp_path):
    media=Attachments(store,tmp_path,Workspace(store,tmp_path))
    item=media.add('red.png','image/png',base64.b64encode(image_bytes()).decode())
    messages=media.messages([item['id']])
    wire=compatible_messages(messages)
    assert wire[0]['content'][1]['image_url']['url'].startswith('data:image/png;base64,')
    assert wire[0]['content'][0]['type']=='text'
    with pytest.raises(RouteError):media.add('fake.png','image/png',base64.b64encode(b'not an image').decode())
    audio=media.add('voice.wav','audio/wav',base64.b64encode(b'RIFF fixture').decode())
    with pytest.raises(RouteError,match='Transcribe'):media.selected([audio['id']])


async def test_explicit_ocr_uses_a_trusted_command_and_keeps_images_local(store,tmp_path):
    import sys
    workspace=Workspace(store,tmp_path)
    media=Attachments(store,tmp_path,workspace)
    item=media.add('red.png','image/png',base64.b64encode(image_bytes()).decode())
    with pytest.raises(RouteError,match='trust'):await media.convert(item['id'],'ocr')
    store.put('settings','media',dict(trusted=True,ocr_argv=[sys.executable,'-c','print("Extracted fixture text")']))
    result=await media.convert(item['id'],'ocr')
    assert 'Extracted fixture text' in result['text'] and result['text_only']
    assert 'images' not in media.messages([item['id']])[0]


def test_evidence_requires_actual_source_passages_and_rejects_cross_mission_links(store):
    store.put('run','r',dict(id='r',architecture=dict(edges=[]),results=[dict(node_id='a',content='The queue has a bounded capacity [source_one]. Another statement has no citation.')]))
    store.put('source','source_one',dict(id='source_one',run_id='r',content='The queue has a bounded capacity of ten jobs.',content_hash=sha('text'),url='https://example.com',retrieved_at=time.time()))
    store.put('source','elsewhere',dict(id='elsewhere',run_id='other',content='Unrelated',content_hash='hash',retrieved_at=0))
    evidence=Evidence(store)
    claims=evidence.extract('r')
    assert claims[0]['status']=='unreviewed' and claims[1]['status']=='unsupported'
    with pytest.raises(RouteError,match='occur'):evidence.review(claims[0]['id'],'source_one','invented quotation','supported')
    with pytest.raises(RouteError,match='mission'):evidence.review(claims[0]['id'],'elsewhere','Unrelated','supported')
    record=evidence.review(claims[0]['id'],'source_one','bounded capacity of ten jobs','supported',conflicts=[claims[1]['id']])
    assert record['reviewed_by']=='owner' and record['evidence'][0]['content_hash']==sha('text')


def test_package_import_is_inert_and_does_not_grant_permissions(store):
    architecture=Architecture(id='example',name='Example',nodes=[dict(id='a',name='Agent',role='Review',tools=['checks.run'])]).model_dump()
    value=dict(format='swarm-workbench-package',format_version=1,name='Review',version='1.0.0',architecture=architecture,requested_permissions=['checks.run'],evaluations=[dict(task='Review a patch',rubric='Explain the evidence')])
    checked=preview(value)
    assert checked['permissions']==['checks.run']
    installed=install(store,value)
    assert installed['architecture']['id']!='example' and not store.all('project') and not store.all('run')
    with pytest.raises(RouteError):preview(dict(value,api_key='not allowed'))


async def test_schedule_requires_approval_debounces_files_and_survives_state(store,vault,tmp_path,model_fixture):
    settings(store)
    model_fixture(store,vault)
    store.put('architecture','single',Architecture(id='single',name='Single',nodes=[dict(id='a',name='A',role='Help')]).model_dump())
    repo=tmp_path/'files';repo.mkdir();(repo/'a.py').write_text('before')
    store.put('project','p',dict(id='p',root=str(repo),tools=[],checks={}))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,Providers(store,vault,'http://127.0.0.1:1'),PrivatePool(store),saver)
        automation=Automation(store,engine)
        created=automation.add(AutomationInput(name='Watch',kind='files',enabled=True,mission=dict(task='Review changes',architecture_id='single',project_id='p',mode='live')))
        await automation.tick()
        assert not store.all('trigger')
        (repo/'a.py').write_text('after')
        store.patch('automation',created['id'],dict(poll_at=0));await automation.tick()
        assert not store.all('trigger')
        store.patch('automation',created['id'],dict(poll_at=0,candidate_at=time.time()-20));await automation.tick()
        trigger=store.all('trigger')[0]
        assert trigger['status']=='pending' and not store.all('run')
        automation.trigger(store.get('automation',created['id']),'Duplicate')
        assert len(store.all('trigger'))==1
        result=automation.launch(trigger['id']);assert result['run_id']
        await until(lambda:store.get('run',result['run_id'])['status']=='completed')
        await automation.tick();assert store.get('trigger',trigger['id'])['status']=='completed'
        assert any(n.get('run_id') for n in store.all('notification'))
        await automation.stop();await engine.stop()


async def test_worktree_changes_are_reviewed_and_integrated_without_touching_other_agents(store,tmp_path):
    import shutil
    if not shutil.which('git'):pytest.skip('Git unavailable')
    repo=tmp_path/'repo';repo.mkdir()
    await command(['git','-c','init.templateDir=','init','-q'],repo)
    (repo/'code.py').write_text('original\n',encoding='utf-8')
    await command(['git','add','code.py'],repo)
    hooks=tmp_path/'empty-hooks';hooks.mkdir()
    await command(['git','-c','core.hooksPath='+str(hooks),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],repo)
    project=dict(id='p',root=str(repo),tools=['artifact.stage'],checks={})
    store.put('project','p',project)
    run=dict(id='r',project_id='p',isolated_worktrees=True);agent=dict(id='r:a',run_id='r')
    store.put('run','r',run)
    state=tmp_path/'state';state.mkdir()
    isolation=Isolation(store,state)
    tree=await isolation.prepare(run,agent)
    assert Path(tree['root'])!=repo
    workspace=Workspace(store,state);workspace.isolation=isolation
    artifact=workspace.stage(isolation.project(project,agent),run,agent,'code.py','agent change\n')
    workspace.apply(artifact['id'])
    assert (repo/'code.py').read_text()=='original\n'
    result=await isolation.collect(agent,workspace)
    integration=result['artifacts'][0]
    assert integration['integration']
    (repo/'code.py').write_text('owner changed it\n',encoding='utf-8')
    with pytest.raises(RouteError,match='changed'):workspace.apply(integration['id'])
    (repo/'code.py').write_text('original\n',encoding='utf-8')
    workspace.apply(integration['id'])
    assert (repo/'code.py').read_text()=='agent change\n'


def test_container_configuration_has_no_host_network_or_runtime_fallback(store,tmp_path,monkeypatch):
    isolation=Isolation(store,tmp_path)
    monkeypatch.setattr('swarm.isolation.shutil.which',lambda name:'/fixture/docker')
    argv=isolation.docker_argv(dict(root=str(tmp_path),container_image='python:3.13'),['python','check.py'],'swarm-test')
    assert '--pull=never' in argv and argv[argv.index('--network')+1]=='none' and '--read-only' in argv
    monkeypatch.setattr('swarm.isolation.shutil.which',lambda name:None)
    with pytest.raises(RouteError,match='Docker'):isolation.docker_argv(dict(root=str(tmp_path),container_image='python:3.13'),['python'],'swarm-test')


def test_relay_ciphertext_authenticates_channel_sender_and_tampering():
    key=base64.urlsafe_b64encode(AESGCM.generate_key(256)).decode()
    envelope=seal(key,'channel','worker',dict(prompt='private prompt'))
    assert 'private prompt' not in json.dumps(envelope)
    assert unseal(key,'channel','worker',envelope)['prompt']=='private prompt'
    with pytest.raises(RouteError):unseal(key,'different','worker',envelope)
    with pytest.raises(RouteError):unseal(key,'channel','owner',envelope)
    damaged=dict(envelope,ciphertext=base64.b64encode(b'changed ciphertext').decode())
    with pytest.raises(RouteError):unseal(key,'channel','worker',damaged)


async def test_relay_delivers_worker_protocol_and_never_stores_plaintext_credentials(tmp_path,vault,monkeypatch):
    relay=create_relay(tmp_path/'relay','operator')
    app=create_app(tmp_path/'coordinator',token='owner',vault=vault)
    async with relay.router.lifespan_context(relay),app.router.lifespan_context(app):
        def relay_client():return httpx.AsyncClient(transport=httpx.ASGITransport(app=relay),base_url='http://127.0.0.1:9443')
        async with relay_client() as client:
            issued=(await client.post('/v1/channels',headers={'Authorization':'Bearer operator'})).json()
        channel=issued['channel']
        configuration=dict(url='http://127.0.0.1:9443',channel=channel,key=base64.urlsafe_b64encode(AESGCM.generate_key(256)).decode(),owner_token=issued['owner_token'],worker_token=issued['worker_token'])
        original=RelayEndpoint.__init__
        def endpoint_init(self,settings,role,client=None):original(self,settings,role,relay_client())
        monkeypatch.setattr(RelayEndpoint,'__init__',endpoint_init)
        vault.set('relay_'+channel,json.dumps(configuration),True)
        app.state.store.put('relay_link',channel,dict(id=channel,url=configuration['url'],enabled=True,status='connecting'))
        app.state.relay_links.start(channel)
        invite=app.state.pool.invite()
        worker=RelayWorkerClient(configuration,'http://127.0.0.1:9443')
        async with worker:
            joined=(await worker.post(worker.base+'/api/worker/join',json=dict(invite=invite['token'],name='Relay worker',hardware={},models=[dict(name='fixture')],budgets=dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True)))).json()
            assert joined['device_id']
            app.state.store.patch('device',joined['device_id'],dict(approved=True))
            worker.headers['Authorization']='Bearer '+joined['token']
            async def collect():
                result=''
                async for text in app.state.pool.infer(joined['device_id'],'fixture',[dict(role='user',content='Private fixture mission')]):result+=text
                return result
            inference=asyncio.create_task(collect())
            await until(lambda:bool(app.state.store.all('job')))
            path=worker.base+'/api/worker/'+joined['device_id']
            job=(await worker.post(path+'/lease')).json()['job']
            assert job['messages'][0]['content']=='Private fixture mission'
            response=await worker.post(path+'/jobs/'+job['id'],json=dict(lease=job['lease'],text='Completed through encrypted relay',done=True))
            assert response.status_code==200 and await inference=='Completed through encrypted relay'
            caches=json.dumps(app.state.store.all('relay_response'))
            assert joined['token'] not in caches and 'Private fixture mission' not in caches
            assert app.state.store.all('receipt')[0]['status']=='done'


def test_qr_and_owner_api_preview_import_permissions_and_stop(tmp_path,vault):
    assert qr_image('trusted fixture').startswith('data:image/svg+xml;base64,')
    with TestClient(create_app(tmp_path/'state',token='owner',vault=vault)) as client:
        assert client.get('/api/advanced').status_code==401
        client.headers['Authorization']='Bearer owner'
        architecture=Architecture(id='example',name='Example',nodes=[dict(id='a',name='Agent',role='Help')]).model_dump()
        value=dict(name='Portable fixture',architecture=architecture)
        checked=client.post('/api/packages/preview',json=value).json()
        assert checked['sha256']
        assert client.post('/api/packages/import',json=dict(package=dict(value,name='changed'),reviewed_sha256=checked['sha256'])).status_code==409
        assert client.post('/api/packages/import',json=dict(package=value,reviewed_sha256=checked['sha256'])).status_code==201
        assert client.post('/api/stop').status_code==200
        assert client.get('/api/advanced').json()['automation_paused']


async def test_scanned_pdf_conversion_renders_then_ocr_labels_actual_fixture_pages(store,tmp_path):
    import sys
    from pypdf import PdfWriter
    writer=PdfWriter();writer.add_blank_page(width=100,height=100)
    data=io.BytesIO();writer.write(data)
    workspace=Workspace(store,tmp_path);media=Attachments(store,tmp_path,workspace)
    item=media.add('scan.pdf','application/pdf',base64.b64encode(data.getvalue()).decode())
    render='from PIL import Image; import sys; Image.new("RGB",(24,24),(255,255,255)).save(sys.argv[1]+"-1.png")'
    store.put('settings','media',dict(trusted=True,pdf_render_argv=[sys.executable,'-c',render,'{output}'],ocr_argv=[sys.executable,'-c','print("Scanned PDF fixture evidence")']))
    result=await media.convert(item['id'],'ocr')
    assert result['ocr_pages']==1 and '[OCR page 1]' in result['text'] and 'fixture evidence' in result['text']


async def test_vision_probe_sends_an_image_and_admits_only_matching_tested_model(store,vault,tmp_path,monkeypatch):
    settings(store,8192)
    providers=Providers(store,vault,'http://127.0.0.1:1')
    profile=providers.add(ProfileInput(provider='google',label='Vision fixture',api_key='fake',model='vision',free_eligible=True))
    inputs=[]
    async def stream(p,m):
        inputs.append(m)
        yield '{"color":"red"}' if m[0].get('images') else '{"swarm_probe":true,"value":7}'
    monkeypatch.setattr(providers,'compatible',stream)
    record=await providers.doctor('profile',profile['id'],vision=True)
    assert record['json'] and record['vision'] and len(inputs)==2 and inputs[1][0]['images']
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver)
        agent=dict(route=dict(kind='profile',profile_id=profile['id'],model=''),requires=['vision'])
        assert await engine.candidates(dict(free_only=True),agent)
        agent['route']['model']='untested'
        with pytest.raises(RouteError,match='vision'):await engine.candidates(dict(free_only=True),agent)
        await engine.stop()


async def test_hardware_tuning_uses_runtime_measurements_and_bounded_generation(store,vault,monkeypatch):
    from swarm.tuning import tune
    settings(store)
    providers=Providers(store,vault,'http://127.0.0.1:1')
    async def inv(url):return dict(models=[dict(name='fixture',size=1024,details=dict(quantization_level='Q4'))],loaded=[],connected=True)
    monkeypatch.setattr('swarm.tuning.inventory',inv)
    monkeypatch.setattr('swarm.tuning.hardware',lambda:dict(gpus=[],unified_memory=False,ram_available_gb=4))
    payloads=[]
    def response(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200,json=dict(done=True,eval_count=40,eval_duration=2_000_000_000,prompt_eval_count=10,load_duration=100_000_000))
    original=httpx.AsyncClient
    monkeypatch.setattr('swarm.tuning.httpx.AsyncClient',lambda *a,**kw:original(transport=httpx.MockTransport(response)))
    result=await tune(store,providers,['fixture'],[2048])
    assert result['rows'][0]['tokens_per_second']==20
    assert result['recommendation']['quantization']=='Q4' and result['recommendation']['concurrency']==1
    assert payloads[0]['options']['num_predict']==64 and payloads[0]['keep_alive']==0


async def test_volunteer_duplicate_check_uses_two_distinct_workers_and_receipts(tmp_path):
    from swarm.hub import create_hub
    app=create_hub(tmp_path/'hub',token='owner')
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            owner={'Authorization':'Bearer owner'}
            grant=(await client.post('/api/hub/clients',headers=owner,json=dict(name='Client',rpm=6))).json()
            devices=[]
            for index in range(2):
                invite=app.state.pool.invite()
                joined=app.state.pool.join(__import__('swarm.schema',fromlist=['JoinInput']).JoinInput(invite=invite['token'],name='Worker '+str(index),hardware={},models=[dict(name='fixture')],budgets=dict(ram_gb=4,vram_gb=0)))
                app.state.store.patch('device',joined['device_id'],dict(approved=True))
                devices.append(joined)
            pending=asyncio.create_task(client.post('/v1/chat/completions',headers={'Authorization':'Bearer '+grant['api_key']},json=dict(model='fixture',messages=[dict(role='user',content='Compare deterministic fixture outputs')],stream=False,duplicate_check=True)))
            await until(lambda:len(app.state.store.all('job'))==2)
            for joined in devices:
                device=app.state.pool.authenticate(joined['device_id'],joined['token'])
                job=app.state.pool.lease(device)
                app.state.pool.result(device,job['id'],job['lease'],dict(text='Same deterministic fixture output',done=True))
            response=await pending
            assert response.status_code==200 and 'normalized outputs agree' in response.json()['choices'][0]['message']['content']
            assert app.state.store.all('duplicate_check')[0]['agreement']
            receipts=app.state.store.all('receipt')
            assert len(receipts)==2 and len({r['device_id'] for r in receipts})==2
            assert app.state.store.get('hub_client',grant['id'])['day_count']==2


async def test_expired_worker_job_is_reassigned_before_output_and_old_lease_is_fenced(store):
    from swarm.schema import JoinInput
    pool=PrivatePool(store)
    devices=[]
    for index in range(2):
        invite=pool.invite()
        joined=pool.join(JoinInput(invite=invite['token'],name='Worker '+str(index),hardware={},models=[dict(name='fixture')],budgets=dict(ram_gb=4,vram_gb=0)))
        store.patch('device',joined['device_id'],dict(approved=True))
        devices.append(pool.authenticate(joined['device_id'],joined['token']))
    async def collect():
        text=''
        async for chunk in pool.infer(devices[0]['id'],'fixture',[dict(role='user',content='Work')],allow_reassign=True):text+=chunk
        return text
    task=asyncio.create_task(collect())
    await until(lambda:len(store.all('job'))==1)
    old=pool.lease(devices[0]);store.patch('job',old['id'],dict(lease_until=time.time()-1))
    await until(lambda:len(store.all('job'))==2)
    with pytest.raises(ValueError):pool.result(devices[0],old['id'],old['lease'],dict(text='stale',done=True))
    replacement=pool.lease(devices[1]);pool.result(devices[1],replacement['id'],replacement['lease'],dict(text='Replacement completed',done=True))
    assert await task=='Replacement completed'
    assert {r['status'] for r in store.all('receipt')}=={'expired','done'}


async def test_conversion_jobs_are_nonblocking_cancelled_by_stop_and_recovered_on_restart(tmp_path,vault,monkeypatch):
    from swarm.store import Store
    state=tmp_path/'state'
    stale=Store(state/'workbench.sqlite')
    stale.put('conversion_job','crashed',dict(id='crashed',status='running'))
    stale.put('tuning_job','benchmark',dict(id='benchmark',status='running'))
    stale.close()
    app=create_app(state,token='owner',vault=vault)
    started=asyncio.Event();cancelled=asyncio.Event()
    async def blocked(*args):
        started.set()
        try: await asyncio.Event().wait()
        except asyncio.CancelledError: cancelled.set();raise
    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.attachments,'convert',blocked)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver',headers={'Authorization':'Bearer owner'}) as client:
            dashboard=(await client.get('/api/advanced')).json()
            assert dashboard['conversion_jobs'][0]['status']=='interrupted' and dashboard['tuning_jobs'][0]['status']=='interrupted'
            item=(await client.post('/api/attachments',json=dict(name='scan.png',mime='image/png',base64=base64.b64encode(image_bytes()).decode()))).json()
            response=await client.post('/api/attachments/'+item['id']+'/ocr')
            assert response.status_code==202
            await asyncio.wait_for(started.wait(),2)
            assert (await client.post('/api/attachments/'+item['id']+'/ocr')).status_code==409
            assert (await client.post('/api/stop')).status_code==200 and cancelled.is_set()
            assert app.state.store.get('conversion_job',response.json()['id'])['status']=='cancelled'


async def test_relay_rejects_oversized_and_chunked_bodies_before_parsing(tmp_path):
    app=create_relay(tmp_path/'relay','operator')
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://testserver') as client:
            assert (await client.post('/v1/channels',headers={'Content-Length':'2000001'})).status_code==413
            async def chunks():
                yield b'x'*1_000_000
                yield b'x'*1_000_001
            assert (await client.post('/v1/channels',content=chunks())).status_code==413
            response=await client.post('/v1/channels',headers={'Authorization':'Bearer operator'})
            assert response.status_code==201 and response.headers['Cache-Control']=='no-store'
