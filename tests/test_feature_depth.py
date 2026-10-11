import asyncio
import io
import json
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pypdf import PdfWriter
from pypdf.generic import DictionaryObject,NameObject,DecodedStreamObject
from swarm.engine import Engine
from swarm.network import PrivatePool
from swarm.providers import Providers, RouteError
from swarm.research import pdf_text
from swarm.schema import Architecture,RunInput,ProfileInput
from swarm.workspace import Workspace


def test_pdf_extracts_actual_page_text_and_rejects_encryption():
    writer=PdfWriter()
    page=writer.add_blank_page(width=400,height=400)
    font=DictionaryObject({NameObject('/Type'):NameObject('/Font'),NameObject('/Subtype'):NameObject('/Type1'),NameObject('/BaseFont'):NameObject('/Helvetica')})
    page[NameObject('/Resources')]=DictionaryObject({NameObject('/Font'):DictionaryObject({NameObject('/F1'):writer._add_object(font)})})
    stream=DecodedStreamObject()
    stream.set_data(b'BT /F1 12 Tf 20 20 Td (Evidence from a real PDF) Tj ET')
    page[NameObject('/Contents')]=writer._add_object(stream)
    output=io.BytesIO()
    writer.write(output)
    extracted=pdf_text(output.getvalue())
    assert '[Page 1]' in extracted and 'Evidence from a real PDF' in extracted
    writer.encrypt('private')
    encrypted=io.BytesIO()
    writer.write(encrypted)
    with pytest.raises(RouteError):pdf_text(encrypted.getvalue())


async def test_doctor_filters_required_json_and_does_not_reuse_probe_for_another_model(store,vault,tmp_path,monkeypatch):
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    providers=Providers(store,vault,'http://127.0.0.1:1')
    profile=providers.add(ProfileInput(provider='google',label='Fixture',api_key='fake',model='json-model',free_eligible=True))
    async def stream(*args):yield '{"swarm_probe":true,"value":7}'
    monkeypatch.setattr(providers,'compatible',stream)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver)
        agent=dict(route={'kind':'profile','profile_id':profile['id'],'model':''},requires=['json'])
        with pytest.raises(RouteError):await engine.candidates(dict(free_only=True),agent)
        probe=await providers.doctor('profile',profile['id'])
        assert probe['json'] and probe['streaming'] and probe['vision_status']=='not tested'
        assert await engine.candidates(dict(free_only=True),agent)
        agent['route']['model']='another-model'
        with pytest.raises(RouteError):await engine.candidates(dict(free_only=True),agent)
        await engine.stop()


async def test_delegation_executes_child_work_and_enforces_root_budget(store,vault,tmp_path,monkeypatch):
    store.put('settings','budgets',dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    store.put('project','p',dict(id='p',name='Project',root=str(tmp_path),tools=['agent.delegate'],checks={}))
    providers=Providers(store,vault,'http://127.0.0.1:1')
    profile=providers.add(ProfileInput(provider='google',label='Fixture',api_key='fake',model='fake',free_eligible=True))
    async def stream(profile,messages):
        text=json.dumps(messages)
        if 'You are Specialist' in text:yield 'Completed specialist evidence'
        elif 'Tool result' in text:yield 'Parent integrates completed specialist evidence'
        else:yield '{"tool":"agent.delegate","arguments":{"task":"Verify evidence","role":"Verifier"}}'
    monkeypatch.setattr(providers,'compatible',stream)
    workspace=Workspace(store,tmp_path)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,providers,PrivatePool(store),saver,workspace)
        workspace.delegate=engine.delegate
        architecture=Architecture(id='single',name='Hierarchy',nodes=[dict(id='parent',name='Parent',role='Coordinate')]).model_dump()
        run=engine.create(RunInput(task='Delegate verification',architecture_id='single',project_id='p',tools_enabled=True,max_subtasks=1,route={'kind':'profile','profile_id':profile['id']}),architecture)
        async with asyncio.timeout(10):
            while store.get('run',run['id'])['status'] not in {'completed','interrupted'}:await asyncio.sleep(.01)
        final=store.get('run',run['id'])
        assert final['status']=='completed'
        children=[r for r in store.all('run') if r.get('delegated')]
        assert len(children)==1 and children[0]['status']=='completed'
        assert 'specialist evidence' in final['results'][0]['content']
        agent=store.get('agent',run['id']+':parent')
        with pytest.raises(RouteError):await engine.delegate(final,agent,dict(task='Another',role='Verifier'))
        await engine.stop()
