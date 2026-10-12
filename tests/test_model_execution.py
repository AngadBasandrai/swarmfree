import asyncio
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.app import create_app
from swarm.engine import Engine
from swarm.features import LabInput
from swarm.network import PrivatePool
from swarm.providers import Providers, RouteError
from swarm.schema import RunInput
from swarm.templates import TEMPLATES


def test_removed_execution_mode_is_rejected_by_all_mission_entry_points(tmp_path, vault):
    with TestClient(create_app(tmp_path/'state', token='owner', vault=vault)) as client:
        client.headers['Authorization']='Bearer owner'
        mission=dict(task='Old demo request',architecture_id='baseline',mode='demo')
        cases=[('/api/runs',mission),('/api/labs',dict(task='Compare',architecture_ids=['baseline'],mode='demo')),
               ('/api/automations',dict(name='Old automation',kind='interval',mission=mission)),
               ('/api/next/evaluations',dict(name='Old evaluation',tasks=[dict(task='Check',checks=[dict(kind='contains',value='ok')])],variants=[dict(name='One',architecture_id='baseline')],mission=mission)),
               ('/api/ops/campaigns',dict(name='Old campaign',mission=dict(mission,project_id='project'),milestones=[dict(id='one',title='One',task='Check')]))]
        for url,body in cases:
            response=client.post(url,json=body)
            assert response.status_code==422,(url,response.text)
            assert any('mode' in error['loc'] or "Input should be 'live'" in error['msg'] for error in response.json()['detail']),(url,response.text)
        assert not client.app.state.store.all('run')
        assert RunInput(task='Task',architecture_id='baseline').mode=='live'
        assert LabInput(task='Task',architecture_ids=['baseline']).mode=='live'


@pytest.mark.asyncio
async def test_missing_models_never_fall_back_to_generated_demo_text(store,vault,tmp_path,monkeypatch):
    async def no_models(url): return dict(models=[],loaded=[])
    monkeypatch.setattr('swarm.engine.inventory',no_models)
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,Providers(store,vault,'http://127.0.0.1:1'),PrivatePool(store),saver)
        try:
            run=engine.create(RunInput(task='Answer me',architecture_id='baseline'),next(t for t in TEMPLATES if t['id']=='baseline'))
            await engine.tasks[run['id']]
            final=store.get('run',run['id'])
            assert final['status']=='interrupted' and 'No eligible model' in final['error']
            assert final['results']==[] and not store.all('call')
            assert all(m['role']!='assistant' for a in engine.agents(run['id']) for m in a['messages'])
        finally: await engine.stop()


def test_saved_demonstration_stays_readable_but_cannot_resume_branch_or_chat(tmp_path,vault):
    with TestClient(create_app(tmp_path/'state',token='owner',vault=vault)) as client:
        client.headers['Authorization']='Bearer owner'
        engine=client.app.state.engine
        run=engine.create(RunInput(task='Archived task',architecture_id='baseline'),next(t for t in TEMPLATES if t['id']=='baseline'),start=False)
        client.app.state.store.patch('run',run['id'],dict(mode='demo',status='interrupted',results=[dict(node_id='agent',name='Agent',content='Historical output')]))
        agent=engine.agents(run['id'])[0]
        messages=agent['messages']
        assert client.get('/api/runs/'+run['id']).json()['results'][0]['content']=='Historical output'
        for url,body in [(f"/api/runs/{run['id']}/resume",{}),(f"/api/runs/{run['id']}/branch",{}),(f"/api/agents/{agent['id']}/chat",dict(content='Continue'))]:
            response=client.post(url,json=body)
            assert response.status_code==400 and 'archived run' in response.text,(url,response.text)
        assert engine.agents(run['id'])[0]['messages']==messages
        assert len(client.app.state.store.all('run'))==1 and not client.app.state.store.all('call')


@pytest.mark.asyncio
async def test_legacy_queue_is_interrupted_during_recovery(store,vault,tmp_path):
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    store.put('run','legacy',dict(id='legacy',mode='demo',status='queued',next_retry=0))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,Providers(store,vault,'http://127.0.0.1:1'),PrivatePool(store),saver)
        try:
            assert store.get('run','legacy')['status']=='interrupted'
            with pytest.raises(RouteError): engine.start('legacy')
            assert engine.tasks=={}
        finally: await engine.stop()
