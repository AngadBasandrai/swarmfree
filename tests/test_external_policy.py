import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.engine import Engine
from swarm.network import PrivatePool
from swarm.providers import Providers,RouteError
from swarm.workspace import Workspace


async def test_acp_permission_does_not_bypass_free_only_or_required_capabilities(store,vault,tmp_path):
    store.put('project','p',dict(id='p',name='Project',root=str(tmp_path),tools=['acp.fixture'],checks={}))
    workspace=Workspace(store,tmp_path)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'cp.sqlite')) as saver:
        engine=Engine(store,Providers(store,vault,'http://127.0.0.1:1'),PrivatePool(store),saver,workspace)
        run=dict(project_id='p',tools_enabled=True,free_only=True)
        agent=dict(route={'kind':'acp','extension_id':'fixture'},tools=None,requires=[])
        with pytest.raises(RouteError,match='billing'):await engine.candidates(run,agent)
        run['free_only']=False
        assert await engine.candidates(run,agent)==[('acp','fixture')]
        agent['requires']=['json']
        with pytest.raises(RouteError,match='capability'):await engine.candidates(run,agent)
        agent['requires']=[]
        agent['tools']=[]
        with pytest.raises(RouteError,match='Grant'):await engine.candidates(run,agent)
        await engine.stop()
