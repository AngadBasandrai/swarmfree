import asyncio
import copy
import threading
import time
from contextlib import asynccontextmanager
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.app import create_app
from swarm.architecture_workbench import Workshop
from swarm.schema import Architecture,RunInput
from swarm.providers import Providers,RouteError
from swarm.engine import Engine
from swarm.network import PrivatePool
from swarm.workspace import Workspace
from swarm.execution_control import family_start,tree
from swarm.templates import TEMPLATES


def team(nodes,edges=(),**extra):
    return Architecture(id='workshop',name='Workshop',nodes=[dict(id=n,name=n,role='Work',**extra.get('node_options',{})) for n in nodes],edges=[dict(source=s,target=t) for s,t in edges],**{k:v for k,v in extra.items() if k!='node_options'}).model_dump()


@asynccontextmanager
async def runner(store,vault,tmp_path):
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.sqlite')) as saver:
        engine=Engine(store,Providers(store,vault,'http://127.0.0.1:1'),PrivatePool(store),saver,Workspace(store,tmp_path))
        try: yield engine,saver
        finally: await engine.stop()


def test_architecture_revisions_cas_diff_and_restore(store):
    workshop=Workshop(store);value=team(['worker'])
    first=workshop.save(value);assert first['revision']==1
    second=workshop.save({**first,'name':'Changed'});assert second['revision']==2
    with pytest.raises(HTTPException) as e: workshop.save({**first,'name':'Stale edit'})
    assert e.value.status_code==409
    assert any(row['path']==['name'] for row in workshop.diff(value['id'],1,2))
    third=workshop.restore(value['id'],1,2);assert third['revision']==3 and third['name']=='Workshop'
    assert len(workshop.versions(value['id']))==3


def test_concurrent_shared_edits_merge_distinct_fields_and_reject_overlaps(store):
    store.put('project','p',dict(id='p'));workshop=Workshop(store)
    session=workshop.create_session('p',team(['one','two']));base=copy.deepcopy(session['draft'])
    one=copy.deepcopy(base);one['nodes'][0]['instructions']='first peer'
    first=workshop.edit(session['id'],0,one,'peer_a')
    two=copy.deepcopy(base);two['nodes'][1]['instructions']='second peer'
    second=workshop.edit(session['id'],0,two,'peer_b')
    assert [n['instructions'] for n in second['draft']['nodes']]==['first peer','second peer']
    conflicting=copy.deepcopy(base);conflicting['nodes'][0]['instructions']='conflict'
    with pytest.raises(HTTPException) as e: workshop.edit(session['id'],0,conflicting,'peer_c')
    assert e.value.status_code==409 and e.value.detail['paths']
    assert workshop.session(session['id'])['revision']==2
    saved=workshop.publish(session['id'],2);assert saved['revision']==1


def test_store_transaction_rolls_back_all_revision_records(store):
    with pytest.raises(RuntimeError):
        with store.transaction():
            store.put('architecture','one',dict(id='one'))
            store.put('architecture_revision','one:1',dict(id='one:1'))
            raise RuntimeError('simulated crash boundary')
    assert store.get('architecture','one') is None and store.get('architecture_revision','one:1') is None


@pytest.mark.asyncio
async def test_typed_connections_project_fields_and_validate_real_inputs(store,vault,tmp_path,model_fixture):
    async def response(name,role,task,prior,steering=''):
        yield '{"sources":[{"url":"https://example.test/evidence"}],"unselected":"private extra"}' if name=='researcher' else 'written result'
    model_fixture(store,vault,response)
    value=team(['researcher','writer'],[('researcher','writer')])
    value['edges'][0]['bindings']=[dict(source_path='sources',target_name='references')]
    value['nodes'][1]['input_schema']=dict(type='object',properties=dict(references=dict(type='array')),required=['references'])
    async with runner(store,vault,tmp_path) as (engine,_):
        run=engine.create(RunInput(task='Write the report',architecture_id=value['id']),value);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed'
        message=engine.agents(run['id'])[1]['messages'][0]['content']
        assert 'references' in message and 'https://example.test/evidence' in message and 'private extra' not in message


@pytest.mark.asyncio
async def test_named_parameters_expand_defaults_and_reject_missing_before_creation(store,vault,tmp_path,model_fixture):
    model_fixture(store,vault)
    value=team(['worker'],parameters=dict(type='object',properties=dict(topic=dict(type='string',default='test topic'))))
    value['nodes'][0]['instructions']='Investigate {{topic}}'
    async with runner(store,vault,tmp_path) as (engine,_):
        run=engine.create(RunInput(task='Investigate',architecture_id=value['id']),value,start=False)
        assert engine.agents(run['id'])[0]['instructions']=='Investigate test topic'
        value['nodes'][0]['instructions']='Investigate {{missing}}';before=len(store.all('run'))
        with pytest.raises(RouteError,match='Missing architecture parameter'): engine.create(RunInput(task='Investigate',architecture_id=value['id']),value)
        assert len(store.all('run'))==before


@pytest.mark.asyncio
@pytest.mark.parametrize('count',[0,1,2])
async def test_variable_fanout_executes_actual_child_tasks(store,vault,tmp_path,model_fixture,count):
    import json
    calls=[]
    async def response(name,role,task,prior,steering=''):
        calls.append((name,task))
        yield json.dumps(dict(tasks=[dict(task='Scoped task '+str(i)) for i in range(count)])) if name=='planner' else 'child work'
    model_fixture(store,vault,response)
    child=team(['worker']);child['id']='child';store.put('architecture','child',child)
    value=team(['planner','dispatch','join'],[('planner','dispatch'),('dispatch','join')])
    value['nodes'][1].update(kind='fanout',subswarm_id='child',task_source='planner',max_depth=2,max_fanout=2)
    value['nodes'][2]['kind']='join'
    async with runner(store,vault,tmp_path) as (engine,_):
        run=engine.create(RunInput(task='Plan independent tasks',architecture_id=value['id']),value);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed'
        children=[r for r in store.all('run') if r.get('delegated')]
        assert len(children)==count and len(calls)==count+1
        assert sorted(c['composition_task'] for c in children)==['Scoped task '+str(i) for i in range(count)]
        assert len(tree(engine,run['id'])['runs'])==count+1


@pytest.mark.asyncio
async def test_branch_allowance_applies_to_all_descendants_atomically(store,vault,tmp_path,model_fixture):
    async def response(name,role,task,prior,steering=''): yield '{"tasks":["one","two"]}' if name=='planner' else 'work'
    model_fixture(store,vault,response)
    child=team(['worker']);child['id']='child';store.put('architecture','child',child)
    value=team(['planner','dispatch'],[('planner','dispatch')]);value['nodes'][1].update(kind='fanout',subswarm_id='child',task_source='planner',request_budget=1)
    async with runner(store,vault,tmp_path) as (engine,_):
        run=engine.create(RunInput(task='Bound branches',architecture_id=value['id']),value);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='interrupted'
        charged=[r for r in store.all('reservation') if run['id']+':dispatch' in r.get('scopes',[])]
        assert len(charged)==1 and engine.budget.usage(run)['requests']==2


@pytest.mark.asyncio
async def test_breakpoint_preserves_parallel_work_and_resumes_after_process_restart(store,vault,tmp_path,model_fixture):
    calls=[]
    async def response(name,role,task,prior,steering=''): calls.append(name);yield 'work'
    model_fixture(store,vault,response)
    value=team(['held','independent']);value['nodes'][0]['break_before']=True
    async with runner(store,vault,tmp_path) as (engine,saver):
        run=engine.create(RunInput(task='Pause only one branch',architecture_id=value['id']),value);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='waiting' and calls==['independent']
        assert engine.budget.root(run).get('active_since') is None
        gate=store.all('execution_gate')[0];store.patch('execution_gate',gate['id'],dict(status='approved'))
        await engine.stop()
        restarted=Engine(store,engine.providers,engine.pool,saver,engine.workspace)
        try:
            restarted.start(run['id']);await restarted.tasks[run['id']]
            assert store.get('run',run['id'])['status']=='completed' and calls.count('independent')==1 and calls.count('held')==1
        finally: await restarted.stop()


@pytest.mark.asyncio
async def test_repetition_gate_stops_before_duplicate_recursive_child_creation(store,vault,tmp_path,model_fixture):
    async def response(name,role,task,prior,steering=''): yield 'same scoped task'
    model_fixture(store,vault,response)
    value=copy.deepcopy(next(t for t in TEMPLATES if t['id']=='recursive_decompose'));value['nodes'][1]['detect_repetition']=True
    async with runner(store,vault,tmp_path) as (engine,_):
        run=engine.create(RunInput(task='Detect repeated work',architecture_id=value['id']),value);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='waiting'
        assert len(store.all('run'))==2
        gate=store.all('execution_gate')[0];assert 'Identical task' in gate['reason']
        store.patch('execution_gate',gate['id'],dict(status='approved'));family_start(engine,gate['run_id'])
        await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed'


def test_shared_design_requires_project_scoped_architect_credentials(tmp_path,vault,monkeypatch):
    monkeypatch.setenv('SWARM_OLLAMA_URL','http://127.0.0.1:1')
    with TestClient(create_app(tmp_path/'state',token='owner',vault=vault)) as client:
        client.post('/api/session',json=dict(token='owner'))
        store=client.app.state.store;store.put('project','p',dict(id='p',name='Project',tools=[]));store.put('project','other',dict(id='other',name='Other',tools=[]))
        row=client.post('/api/architecture-workbench/sessions',json=dict(project_id='p',architecture=team(['worker']))).json()
        credentials={}
        for role,project in [('architect','p'),('viewer','p'),('architect','other')]:
            r=client.post('/api/workbench/members',json=dict(name=role+project,role=role,project_ids=[project])).json();credentials[role+project]={'Authorization':'Bearer '+r['token']}
        path='/api/team/architecture-sessions/'+row['id']
        assert client.get(path,headers=credentials['architectp']).status_code==200
        assert client.get(path,headers=credentials['viewerp']).status_code==403
        assert client.get(path,headers=credentials['architectother']).status_code==404
        draft=copy.deepcopy(row['draft']);draft['nodes'][0]['instructions']='Shared peer edit'
        result=client.post(path+'/edits',headers=credentials['architectp'],json=dict(base_revision=0,architecture=draft))
        assert result.status_code==200 and result.json()['revision']==1
        assert store.get('architecture','workshop') is None # Peer changes stay drafts until owner publication.


@pytest.mark.asyncio
async def test_extracted_component_preserves_typed_inputs_and_named_outputs(store,vault,tmp_path,model_fixture):
    async def response(name,role,task,prior,steering=''): yield '{"value":"evidence","unselected":"private-extra"}' if name=='source' else '{"summary":"component output"}'
    model_fixture(store,vault,response)
    value=team(['source','writer','consumer'],[('source','writer'),('writer','consumer')])
    value['edges'][0]['bindings']=[dict(source_path='value',target_name='evidence')]
    value['edges'][1]['bindings']=[dict(source_path='summary',target_name='summary')]
    extracted=Workshop(store).extract(value,['writer'],'Writing block')
    async with runner(store,vault,tmp_path) as (engine,_):
        run=engine.create(RunInput(task='Run a reusable block',architecture_id=value['id']),extracted['architecture']);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed'
        child=next(r for r in store.all('run') if r.get('delegated'))
        assert 'evidence' in engine.agents(child['id'])[0]['messages'][0]['content']
        assert 'private-extra' not in engine.agents(child['id'])[0]['messages'][0]['content']
        assert 'component output' in engine.agents(run['id'])[-1]['messages'][0]['content']


@pytest.mark.asyncio
async def test_hold_after_request_reuses_completed_response_on_approval(store,vault,tmp_path,model_fixture):
    entered=asyncio.Event();release=asyncio.Event();calls=[]
    async def response(name,role,task,prior,steering=''):
        calls.append(name);entered.set();await release.wait();yield 'Completed model response'
    model_fixture(store,vault,response)
    async with runner(store,vault,tmp_path) as (engine,_):
        value=team(['worker']);run=engine.create(RunInput(task='Hold current turn',architecture_id=value['id']),value)
        await asyncio.wait_for(entered.wait(),5)
        agent_id=run['id']+':worker';store.put('agent_control',agent_id,dict(hold=True,generation=1))
        release.set();await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='waiting'
        assert store.get('agent',agent_id)['pending_result']['content']=='Completed model response'
        gate=store.all('execution_gate')[0];assert gate['phase']=='after'
        store.patch('execution_gate',gate['id'],dict(status='approved'));store.put('agent_control',agent_id,dict(hold=False,generation=1))
        family_start(engine,run['id']);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']=='completed' and calls==['worker']


def wait_status(client,id,status):
    deadline=time.monotonic()+10
    while time.monotonic()<deadline:
        run=client.get('/api/runs/'+id).json()
        if run['status']==status:return run
        time.sleep(.015)
    raise AssertionError(run)


def test_paused_branch_edit_preserves_siblings_receipts_and_denies_new_tools(tmp_path,vault,monkeypatch,model_fixture):
    monkeypatch.setenv('SWARM_OLLAMA_URL','http://127.0.0.1:1')
    calls=[]
    async def response(name,role,task,prior,steering=''):calls.append(name);yield role
    with TestClient(create_app(tmp_path/'edit',token='owner',vault=vault)) as client:
        client.post('/api/session',json=dict(token='owner'));store=client.app.state.store
        model_fixture(store,vault,response)
        value=team(['held','sibling']);value['nodes'][0]['break_before']=True
        saved=client.post('/api/architectures',json=value).json()
        run=client.post('/api/runs',json=dict(task='Edit a branch',architecture_id=saved['id'])).json()
        held=wait_status(client,run['id'],'waiting');agent_id=run['id']+':held'
        receipt=dict(id='receipt',role='user',content='Existing tool receipt',complete=True,source='tool')
        agent=store.get('agent',agent_id);store.patch('agent',agent_id,dict(messages=[*agent['messages'],receipt]))
        spec={**held['architecture']['nodes'][0],'role':'Updated role','instructions':'New instructions'}
        denied=client.put('/api/execution-control/agents/'+agent_id,json=dict(spec={**spec,'tools':['workspace.write']}))
        assert denied.status_code==400
        assert client.put('/api/execution-control/agents/'+agent_id,json=dict(spec=spec)).status_code==200
        assert receipt in store.get('agent',agent_id)['messages']
        assert client.put('/api/execution-control/agents/'+run['id']+':sibling',json=dict(spec=held['architecture']['nodes'][1])).status_code==409
        assert client.post('/api/execution-control/agents/'+agent_id,json=dict(action='approve')).status_code==200
        done=wait_status(client,run['id'],'completed')
        assert calls.count('sibling')==1 and calls.count('held')==1
        assert next(a for a in done['agents'] if a['node_id']=='held')['result']['content']=='Updated role'


def test_skip_held_child_branch_prunes_only_unfinished_descendants(tmp_path,vault,monkeypatch,model_fixture):
    monkeypatch.setenv('SWARM_OLLAMA_URL','http://127.0.0.1:1')
    async def response(name,role,task,prior,steering=''):yield 'Real response'
    with TestClient(create_app(tmp_path/'skip',token='owner',vault=vault)) as client:
        client.post('/api/session',json=dict(token='owner'));store=client.app.state.store;model_fixture(store,vault,response)
        child=team(['worker'],node_options=dict(break_before=True));child['id']='child'
        client.post('/api/architectures',json=child)
        parent=team(['dispatch','sibling']);parent['nodes'][0].update(kind='subswarm',subswarm_id='child')
        client.post('/api/architectures',json=parent)
        run=client.post('/api/runs',json=dict(task='Prune unfinished child',architecture_id=parent['id'])).json()
        wait_status(client,run['id'],'waiting');children=[r for r in store.all('run') if r.get('delegated')];assert len(children)==1
        result=client.post('/api/execution-control/agents/'+run['id']+':dispatch',json=dict(action='skip'));assert result.status_code==200
        done=wait_status(client,run['id'],'completed')
        assert next(a for a in done['agents'] if a['node_id']=='dispatch')['status']=='skipped'
        assert next(a for a in done['agents'] if a['node_id']=='sibling')['status']=='done'
        assert store.get('run',children[0]['id'])['pruned'] and store.get('run',children[0]['id'])['status']=='cancelled'


@pytest.mark.asyncio
@pytest.mark.parametrize('tasks,limit', [(['duplicate','duplicate'],4),(['one','two','three'],4),(['one','two'],1)])
async def test_invalid_fanout_allocates_no_partial_children(store,vault,tmp_path,model_fixture,tasks,limit):
    async def response(name,role,task,prior,steering=''):yield __import__('json').dumps(dict(tasks=tasks))
    model_fixture(store,vault,response)
    child=team(['worker']);child['id']='child';store.put('architecture','child',child)
    value=team(['planner','dispatch'],[('planner','dispatch')]);value['nodes'][1].update(kind='fanout',subswarm_id='child',task_source='planner',max_fanout=2)
    async with runner(store,vault,tmp_path) as (engine,_):
        run=engine.create(RunInput(task='Reject invalid fanout',architecture_id=value['id'],max_subtasks=limit),value);await engine.tasks[run['id']]
        assert store.get('run',run['id'])['status']!='completed' and len(store.all('run'))==1


def test_missing_selector_and_unexpected_input_types_fail_before_consumer_calls():
    from swarm.dataflow import inputs_for
    from swarm.protocol import validate
    value=team(['source','target'],[('source','target')]);value['edges'][0]['bindings']=[dict(source_path='missing',target_name='evidence')]
    with pytest.raises(RouteError,match='missing'):inputs_for(dict(architecture=value),value['nodes'][1],[dict(node_id='source',content='{"actual":1}')])
    with pytest.raises(RouteError):validate(dict(evidence=12),dict(type='object',properties=dict(evidence=dict(type='string'))))


def test_editing_held_child_team_replaces_unfinished_children_with_new_revision(tmp_path,vault,monkeypatch,model_fixture):
    monkeypatch.setenv('SWARM_OLLAMA_URL','http://127.0.0.1:1')
    async def response(name,role,task,prior,steering=''):yield role
    with TestClient(create_app(tmp_path/'replace-child',token='owner',vault=vault)) as client:
        client.post('/api/session',json=dict(token='owner'));store=client.app.state.store;model_fixture(store,vault,response)
        child=team(['worker'],node_options=dict(break_before=True));child['id']='child'
        saved=client.post('/api/architectures',json=child).json()
        parent=team(['dispatch']);parent['nodes'][0].update(kind='subswarm',subswarm_id='child')
        client.post('/api/architectures',json=parent)
        run=client.post('/api/runs',json=dict(task='Replace held child',architecture_id=parent['id'])).json()
        held=wait_status(client,run['id'],'waiting');old=next(r for r in store.all('run') if r.get('delegated'))
        saved['nodes'][0].update(break_before=False,role='New child revision')
        assert client.post('/api/architectures',json=saved).status_code==201
        agent_id=run['id']+':dispatch'
        assert client.put('/api/execution-control/agents/'+agent_id,json=dict(spec=held['architecture']['nodes'][0])).status_code==200
        assert client.post('/api/execution-control/agents/'+agent_id,json=dict(action='approve')).status_code==200
        done=wait_status(client,run['id'],'completed')
        assert 'New child revision' in done['agents'][0]['result']['content']
        assert store.get('run',old['id'])['pruned']
        assert len([r for r in store.all('run') if r.get('delegated')])==2
        assert not [g for g in store.all('execution_gate') if g['status']=='waiting']


def test_blueprint_routes_reject_credentials_and_survive_saved_reuse(store):
    from pydantic import ValidationError
    from swarm.schema import AgentSpec
    with pytest.raises(ValidationError,match='never credentials'):AgentSpec(id='a',name='a',role='a',preferred_route=dict(kind='profile',api_key='not-allowed'))
    value=AgentSpec(id='a',name='a',role='a',preferred_route=dict(kind='profile',profile_id='p'),tools=['memory.search']).model_dump()
    store.put('agent_blueprint','saved',dict(id='saved',spec=value))
    assert store.get('agent_blueprint','saved')['spec']['preferred_route']['profile_id']=='p'


@pytest.mark.asyncio
async def test_individual_gate_approval_leaves_other_branches_waiting(store,vault,tmp_path,model_fixture):
    calls=[]
    async def response(name,role,task,prior,steering=''):calls.append(name);yield name
    model_fixture(store,vault,response)
    async with runner(store,vault,tmp_path) as (engine,_):
        value=team(['one','two'],node_options=dict(break_before=True));run=engine.create(RunInput(task='Review gates individually',architecture_id=value['id']),value)
        await engine.tasks[run['id']];assert calls==[]
        first=next(g for g in store.all('execution_gate') if g['agent_id'].endswith(':one'))
        store.patch('execution_gate',first['id'],dict(status='approved'));engine.start(run['id']);await engine.tasks[run['id']]
        assert calls==['one'] and store.get('run',run['id'])['status']=='waiting'
        other=next(g for g in store.all('execution_gate') if g['agent_id'].endswith(':two'))
        store.patch('execution_gate',other['id'],dict(status='approved'));engine.start(run['id']);await engine.tasks[run['id']]
        assert calls==['one','two'] and store.get('run',run['id'])['status']=='completed'
        assert len(store.get('run',run['id'])['results'])==2
