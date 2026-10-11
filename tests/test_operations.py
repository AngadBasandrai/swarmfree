import asyncio
import base64
import io
import json
import re
import time
import wave
from types import SimpleNamespace
import pytest
from fastapi.testclient import TestClient
from swarm.app import create_app
from swarm.providers import RouteError
from swarm.project_ops import KnowledgeInput, CampaignInput
from swarm.adaptive_ops import AdaptiveOps
from swarm.schema import RunInput
from swarm.notebooks import NotebookInput, Notebooks, digest
from swarm.training import Training, DatasetInput, TrainingInput


@pytest.fixture
def client(tmp_path,vault,monkeypatch):
    monkeypatch.setenv('SWARM_OLLAMA_URL','http://127.0.0.1:1')
    hw=lambda:dict(name='fixture',system='Windows',machine='x64',cpu='fixture',cores=4,threads=8,ram_total_gb=16,ram_available_gb=12,gpus=[],unified_memory=False)
    monkeypatch.setattr('swarm.app.hardware',hw)
    monkeypatch.setattr('swarm.hardware.hardware',hw)
    with TestClient(create_app(tmp_path/'state',token='owner',vault=vault)) as c:
        c.post('/api/session',json={'token':'owner'})
        project=tmp_path/'project';project.mkdir();(project/'file.txt').write_text('ordinary text')
        p=c.post('/api/projects',json=dict(name='Project',root=str(project),tools=[])).json()
        c.project=p;c.state=c.app.state
        yield c


def poll(client,kind,id):
    deadline=time.monotonic()+20
    while time.monotonic()<deadline:
        row=client.state.store.get(kind,id)
        if row['status'] not in {'ready','running','queued'}: return row
        time.sleep(.03)
    raise AssertionError('Job did not finish')


def test_campaign_dependencies_reviews_budget_and_restart(client):
    form=dict(name='Release',mission=dict(task='Project',architecture_id='baseline',project_id=client.project['id'],mode='demo'),
        milestones=[dict(id='plan',title='Plan',task='Plan project',criteria='A reviewable plan'),dict(id='build',title='Build',task='Deliver project',depends_on=['plan'])],
        limits=dict(max_requests=2,max_tokens=20000,max_seconds=60,max_spend_usd=0))
    row=client.post('/api/ops/campaigns',json=form);assert row.status_code==200,row.text
    id=row.json()['id'];r=client.post('/api/ops/campaigns/'+id+'/launch').json();assert poll(client,'run',r['id'])['status']=='completed'
    c=client.get('/api/ops').json()['campaigns'][0];assert c['milestones'][0]['status']=='review'
    assert client.post('/api/ops/campaigns/'+id+'/launch').status_code==400
    assert client.post('/api/ops/campaigns/'+id+'/milestones/plan/review',json={'accepted':True}).status_code==200
    r=client.post('/api/ops/campaigns/'+id+'/launch').json();assert poll(client,'run',r['id'])['status']=='completed'
    assert client.get('/api/ops').json()['campaigns'][0]['usage']['requests']==2
    assert client.post('/api/ops/campaigns/'+id+'/milestones/build/review',json={'accepted':False}).status_code==200
    assert 'exhausted' in client.post('/api/ops/campaigns/'+id+'/launch').text
    cyclic={**form,'milestones':[dict(id='a',title='A',task='work',depends_on=['a'])]}
    assert client.post('/api/ops/campaigns',json=cyclic).status_code==422


def test_stop_all_disables_automatic_campaigns(client):
    row=client.post('/api/ops/campaigns',json=dict(name='Campaign',automatic=True,mission=dict(task='x',architecture_id='baseline',project_id=client.project['id'],mode='demo'),milestones=[dict(id='one',title='One',task='work')])).json()
    client.state.store.patch('campaign',row['id'],dict(enabled=True))
    assert client.post('/api/stop').status_code==200
    assert not client.state.store.get('campaign',row['id'])['enabled']


def test_knowledge_dependency_versions_cycles_scope_and_provenance(client):
    p=client.project['id']
    a=client.post('/api/ops/knowledge',json=dict(project_id=p,title='API',content='Version one')).json()
    b=client.post('/api/ops/knowledge',json=dict(project_id=p,title='Client',content='Depends on API',depends_on=[a['id']])).json()
    assert not client.get('/api/ops/knowledge/'+p).json()[1]['stale_reasons']
    assert client.put('/api/ops/knowledge/'+a['id'],json=dict(project_id=p,title='API',content='Wrong revision')).status_code==400
    assert client.put('/api/ops/knowledge/'+a['id'],json=dict(project_id=p,title='API',content='Version two',expected_revision=1)).status_code==200
    assert client.get('/api/ops/knowledge/'+p).json()[1]['stale_reasons']
    assert client.put('/api/ops/knowledge/'+a['id'],json=dict(project_id=p,title='API',content='Cycle',expected_revision=2,depends_on=[b['id']])).status_code==400
    assert len(client.get('/api/ops/knowledge/'+a['id']+'/history').json())==2
    assert client.post('/api/ops/knowledge',json=dict(project_id=p,title='Bad',content='bad',sources=['outside'])).status_code==400
    client.state.store.put('source','source',dict(id='source',project_id=p,content='file data',data_labels=[dict(project_id=p,path='private.txt')]))
    k=client.post('/api/ops/knowledge',json=dict(project_id=p,title='Sourced',content='Finding',sources=['source'])).json()
    assert k['data_labels']==[dict(project_id=p,path='private.txt')]


def test_feedback_version_isolation_and_no_duplicate_votes(client):
    s=client.state.store
    s.put('run','r',dict(id='r',mode='live'))
    for i in range(3): s.put('outcome',str(i),dict(id=str(i),run_id='r',route='local:model',task_class='coding',passed=True,measured=True,model_revision='sha:one',elapsed_ms=10))
    for _ in range(2): assert client.post('/api/ops/feedback',json=dict(outcome_id='0',score=4)).status_code==200
    assert len(s.all('route_feedback'))==1
    ops=client.state.adaptive_ops
    assert ops.evidence('local:model','sha:one','coding')['supported']
    assert not ops.evidence('local:model','sha:two','coding')['supported']
    assert not ops.evidence('local:model','','coding')['supported']


def test_preflight_does_not_make_requests_and_handles_unknown_measurements(client):
    before=len(client.state.store.all('request'))
    result=client.post('/api/ops/preflight',json=dict(mission=dict(task='Plan a project',architecture_id='baseline'),architecture_ids=['baseline','supervisor']))
    assert result.status_code==200,result.text
    rows=result.json()['comparisons'];assert rows[0]['initial_requests']==1
    assert all(r['serial_seconds'] is None for r in rows)
    assert len(client.state.store.all('request'))==before


def test_preflight_reports_shared_quota_without_claiming_requests(client):
    for name,limit in [('first',10),('second',3)]:
        result=client.post('/api/profiles',json=dict(provider='google',label=name,api_key='fake-fixture-key-'+name,ephemeral=True,model='fixture',quota_scope='one-project',requests_per_minute=limit,free_eligible=True))
        assert result.status_code==201,result.text
    scope=client.state.store.all('profile')[0]['quota_group'];now=time.time()
    client.state.store.put('quota',scope,dict(requests=[now]*3,cooldown_until=now+20))
    before=client.state.store.get('quota',scope)
    result=client.post('/api/ops/preflight',json=dict(mission=dict(task='Check',architecture_id='baseline'),architecture_ids=['baseline'])).json()
    group=result['quota_groups'][0]
    assert group['requests_per_minute']==3 and group['remaining_requests']==0 and group['cooldown_seconds']>0
    assert not any(p['available_now'] for p in result['profiles'])
    assert client.state.store.get('quota',scope)==before


def test_training_receipt_promotion_exact_profile_and_tampering(client,tmp_path,monkeypatch):
    import hashlib
    import sys
    s=client.state.store;p=client.project['id'];training=client.state.training
    s.put('run','r',dict(id='r',mode='live',task='Ordinary question',project_id=p))
    s.put('agent','a',dict(id='a',run_id='r',status='done',result={'content':'Ordinary answer'},data_labels=[]))
    dataset=training.dataset(DatasetInput(project_id=p,name='Reviewed',agent_ids=['a']))
    base=tmp_path/'weights';base.mkdir();(base/'config.json').write_text('{}');(base/'model.safetensors').write_bytes(b'fixture weights')
    monkeypatch.setattr('swarm.hardware.hardware',lambda:dict(gpus=[dict(id='0',backend='cuda',free_gb=8)]))
    s.put('settings','budgets',dict(vram_gb=8))
    form=TrainingInput(dataset_id=dataset['id'],reviewed_sha256=dataset['sha256'],python_executable=sys.executable,base_directory=str(base),trusted=True)
    async def command(argv,**kwargs):
        config=json.loads(__import__('pathlib').Path(argv[-1]).read_text());output=__import__('pathlib').Path(config['output_directory']);output.mkdir()
        (output/'adapter_config.json').write_text('{}');(output/'adapter_model.safetensors').write_bytes(b'fixture adapter')
        assert argv[1:3]==['-m','swarm.training_runner'] and config['dataset_path']
        return ''
    monkeypatch.setattr('swarm.training.command',command)
    s.put('training_job','job',dict(id='job',status='running'))
    result=asyncio.run(training.run('job',form));s.patch('training_job','job',dict(status='completed',result=result))
    assert result['files']['adapter_model.safetensors']==hashlib.sha256(b'fixture adapter').hexdigest()
    s.put('profile','serving',dict(id='serving',model_revision=result['revision']))
    s.put('run','evalrun',dict(id='evalrun',project_id=p,mode='live'))
    s.put('evaluation_job','eval',dict(id='eval',status='completed',rows=[dict(run_id='evalrun',passed=True)]))
    s.put('request','call',dict(run_id='evalrun',profile_id='other',model_revision=result['revision']))
    with pytest.raises(RouteError,match='exclusively'):training.promote('job','eval','serving')
    s.patch('request','call',dict(profile_id='serving'))
    assert training.promote('job','eval','serving')['revision']==result['revision']
    assert training.rollback(p)['rolled_back']
    (__import__('pathlib').Path(result['adapter_directory'])/'adapter_model.safetensors').write_bytes(b'changed')
    with pytest.raises(RouteError,match='changed'):training.promote('job','eval','serving')


def test_energy_uses_observed_samples_and_blocks_unknown_telemetry(store):
    ops=AdaptiveOps(store);now=time.time()
    ops.sample(dict(observed_at=now,watts=100,temperature_c=40))
    ops.sample(dict(observed_at=now+10,watts=200,temperature_c=50))
    assert abs(store.all('energy_day')[0]['wh']-150*10/3600)<1e-8
    ops.sample(dict(observed_at=now+30,watts=None,temperature_c=None))
    assert store.all('energy_day')[0]['sampled_seconds']==10
    store.put('settings','energy_policy',dict(enabled=True,require_telemetry=True))
    assert 'required' in ops.admission('local','model',now=now+31)
    assert not ops.admission('profile',{},now=now+31)
    store.put('settings','energy_observation',dict(observed_at=now,watts=100,temperature_c=95))
    store.put('settings','energy_policy',dict(enabled=True,max_temperature_c=85))
    assert 'temperature' in ops.admission('local','model',now=now)


def test_notebook_review_hash_changes_on_inputs_and_code(client,monkeypatch):
    body=dict(project_id=client.project['id'],title='Calculation',image='python:3.13-slim',cells=[dict(kind='python',content='print(2+2)')],inputs={'data.csv':'a\n1'})
    result=client.post('/api/ops/notebooks',json=body);assert result.status_code==200,result.text
    row=result.json()
    changed=client.put('/api/ops/notebooks/'+row['id'],json={**body,'inputs':{'data.csv':'a\n2'},'expected_revision':1}).json()
    assert changed['review_sha256']!=row['review_sha256']
    async def run():
        with pytest.raises(RouteError,match='Review'): await client.state.notebooks.run(row['id'],row['review_sha256'])
    asyncio.run(run())
    exported=client.get('/api/ops/notebooks/'+row['id']+'/export').json();assert exported['nbformat']==4
    assert client.post('/api/ops/notebooks',json={**body,'inputs':{'../escape.txt':'x'}}).status_code==422


def test_notebook_isolated_execution_and_capture(store,tmp_path,monkeypatch):
    s=store;s.put('project','p',dict(id='p'));book=Notebooks(s,tmp_path)
    row=book.save(NotebookInput(project_id='p',title='Notebook',image='python:3.13',cells=[dict(kind='python',content='print(4)')]))
    calls=[]
    async def command(argv,**kwargs):
        calls.append(argv)
        if argv[1:3]==['image','inspect']: return 'sha256:'+'a'*64
        if argv[1]=='run': return json.dumps(dict(cells=[dict(stdout='4\n',error='')],files=[dict(name='answer.txt',data=base64.b64encode(b'4').decode())]))
        return ''
    monkeypatch.setattr('swarm.notebooks.command',command);monkeypatch.setattr('swarm.notebooks.shutil.which',lambda _: 'docker')
    result=asyncio.run(book.run(row['id'],row['review_sha256']))
    run=next(a for a in calls if a[1]=='run');assert '--read-only' in run and 'none' in run and any('readonly' in a for a in run)
    assert result['files'][0]['sha256']==__import__('hashlib').sha256(b'4').hexdigest()
    assert calls[-1][1:3]==['rm','-f']


def test_voice_validates_pcm_and_uses_local_command(client,monkeypatch):
    buffer=io.BytesIO()
    with wave.open(buffer,'wb') as f:f.setnchannels(1);f.setsampwidth(2);f.setframerate(16000);f.writeframes(b'\0\0'*1600)
    client.state.store.put('settings','voice',dict(trusted=True,argv=['local-transcriber','{input}']))
    async def command(argv,**kwargs):return 'Review the project'
    monkeypatch.setattr('swarm.voice.command',command)
    assert client.post('/api/ops/voice/chunk',json={'wav':base64.b64encode(buffer.getvalue()).decode()}).json()['text']=='Review the project'
    assert client.post('/api/ops/voice/chunk',json={'wav':'invalid'}).status_code==400
    assert not list(client.state.workspace.root.glob('voice-*'))


def test_mcp_app_grants_expiration_and_isolated_document(client,monkeypatch):
    s=client.state.store;p=client.project['id'];uri='ui://test/view'
    s.put('extension','x',dict(id='x',kind='mcp',enabled=True,apps_enabled=True,tools=[dict(name='show',inputSchema={'type':'object'},_meta={'ui':{'resourceUri':uri}})]))
    s.patch('project',p,dict(tools=['mcp.x.show']))
    async def mcp(config,method,params,project):return dict(contents=[dict(uri=uri,mimeType='text/html;profile=mcp-app',text='<html><body>Panel</body></html>')])
    monkeypatch.setattr(client.state.extensions,'mcp',mcp)
    panel=client.post('/api/ops/apps/inspect',json=dict(project_id=p,extension_id='x',tool_name='show')).json()
    client.cookies.clear();document=client.get('/api/ops/apps/view/'+panel['id'])
    assert document.status_code==200 and "connect-src 'none'" in document.headers['content-security-policy']
    assert client.get('/api/ops').status_code==401
    client.post('/api/session',json={'token':'owner'})
    assert client.post('/api/ops/apps/'+panel['id']+'/call',json={'tool_name':'ungranted','arguments':{}}).status_code==400
    s.patch('tool_panel',panel['id'],dict(expires_at=0));assert client.get('/api/ops/apps/view/'+panel['id']).status_code==400


def test_training_dataset_excludes_sensitive_demo_failed_and_incomplete_work(client):
    s=client.state.store;p=client.project['id']
    for id,mode,answer,labels,status in [('good','live','An ordinary answer',[],'done'),('demo','demo','Scripted',[],'done'),('secret','live','api_key=abcdefghijklmnop',[],'done'),('file','live','File-derived',[{'project_id':p,'path':'private'}],'done'),('partial','live','partial',[],'running')]:
        s.put('run',id,dict(id=id,mode=mode,task='Question',project_id=p));s.put('agent',id,dict(id=id,run_id=id,status=status,result={'content':answer},data_labels=labels))
    result=client.post('/api/ops/datasets',json=dict(project_id=p,name='Reviewed',agent_ids=['good','demo','secret','file','partial']))
    assert result.status_code==200,result.text
    row=result.json();assert len(row['examples'])==1 and len(row['excluded'])==4
    assert client.post('/api/ops/datasets/'+row['id']+'/export',json={'sha256':'0'*64}).status_code==400


def test_companion_invitation_one_use_and_questions_project_roles(client,vault):
    s=client.state.store;p=client.project['id']
    member=client.post('/api/workbench/members',json=dict(name='Phone',role='operator',project_ids=[p])).json()
    from swarm.teamwork import token_hash
    s.put('companion_invite','invite',dict(id='invite',member_id=member['member']['id'],invite_hash=token_hash('oneuseinvitation1234567890'),expires_at=time.time()+60,redeemed=False))
    vault.set('invite',member['token'])
    client.cookies.clear();form=dict(invite_id='invite',invite='oneuseinvitation1234567890')
    assert client.post('/api/team/companion/redeem',json=form).status_code==200
    assert client.post('/api/team/companion/redeem',json=form).status_code==401
    assert vault.get('invite') is None
    s.put('run','r',dict(id='r',project_id=p,status='paused'))
    s.put('question','q',dict(id='q',run_id='r',agent_id='a',question='Clarify',schema={'type':'string'},status='pending'))
    h={'Authorization':'Bearer '+member['token']}
    assert len(client.get('/api/team/questions',headers=h).json())==1
    assert client.post('/api/team/questions/q/answer',json={'answer':'Use blue'},headers=h).status_code==200


def test_compatibility_runs_real_engine_workflows_with_scripted_provider_fixture(client,monkeypatch,vault):
    # This verifies harness behavior, not real model accuracy or provider compatibility.
    s=client.state.store
    profile=dict(id='model',label='Fixture',model='fixture-model',provider='gemini',enabled=True,storage='session',free_eligible=True,quota_group='fixture',model_revision='sha:fixture')
    s.put('profile','model',profile);vault.set('model','fixturekey')
    async def stream(profile,messages):
        await asyncio.sleep(.01)
        schema=profile.get('_output_schema',{})
        if schema: yield json.dumps({'nonce':schema['properties']['nonce']['const']});return
        text=json.dumps(messages)
        if 'Read clue.txt' in text and not any(m.get('role')=='user' and 'Tool result (data)' in m.get('content','') for m in messages):
            yield json.dumps({'tool':'workspace.read','arguments':{'path':'clue.txt'}});return
        matches=re.findall(r'\b[0-9a-f]{16}\b',text)
        yield matches[-1] if matches else 'No identifier'
    monkeypatch.setattr(client.state.providers,'compatible',stream)
    result=client.post('/api/ops/compatibility',json=dict(mission=dict(task='fixture',architecture_id='baseline',route={'kind':'profile','profile_id':'model'},limits={'max_seconds':60,'max_requests':12,'max_tokens':100000}),runtime_version='fixture-v1',model_revision='sha:fixture'))
    assert result.status_code==200,result.text
    row=poll(client,'compatibility_job',result.json()['id'])
    assert row['status']=='completed',row
    assert all(r['passed'] for r in row['result']['rows']),row
    assert row['result']['rows'][-1]['stage_requests']['source']==1
    report=row['result']['report'];assert 'messages' not in json.dumps(report) and 'fixturekey' not in json.dumps(report)
    assert client.post('/api/ops/compatibility/import',json=report).status_code==200
    assert client.post('/api/ops/compatibility/import',json={**report,'prompt':'private prompt'}).status_code==400
