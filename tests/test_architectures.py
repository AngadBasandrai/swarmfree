import asyncio
import copy
import json
from contextlib import asynccontextmanager

import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import ValidationError
from swarm.composition import resolve, preview, base_case
from swarm.engine import Engine
from swarm.network import PrivatePool
from swarm.providers import Providers, RouteError
from swarm.schema import Architecture, RunInput
from swarm.templates import TEMPLATES
from swarm.workspace import Workspace
from fastapi.testclient import TestClient
from swarm.app import create_app


def preset(id):
    return copy.deepcopy(next(t for t in TEMPLATES if t['id'] == id))


@asynccontextmanager
async def runner(store, vault, tmp_path):
    store.put('settings', 'budgets', dict(ram_gb=4, vram_gb=0, context=4096, allow_cpu=True))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/'checkpoints.sqlite')) as saver:
        engine = Engine(store, Providers(store, vault, 'http://127.0.0.1:1'), PrivatePool(store), saver, Workspace(store, tmp_path))
        try:
            yield engine, saver
        finally:
            await engine.stop()


def test_every_preset_validates_and_expands(store):
    assert len(TEMPLATES) == 22
    assert len({t['id'] for t in TEMPLATES}) == 22
    for team in TEMPLATES:
        validated = Architecture.model_validate(team).model_dump()
        assert validated == team
        stats = preview(store, team)
        assert stats['expanded_nodes'] <= 128 and stats['max_depth'] <= 4
        assert stats['worst_model_calls'] >= stats['model_calls'] > 0


@pytest.mark.parametrize('depth', [0, 5])
def test_recursion_depth_validation(depth):
    team = preset('recursive_decompose'); team['nodes'][1]['max_depth'] = depth
    with pytest.raises(ValidationError): Architecture.model_validate(team)


def test_task_source_must_be_upstream():
    team = preset('recursive_decompose'); team['nodes'][1]['task_source'] = 'combine'
    with pytest.raises(ValidationError, match='upstream'): Architecture.model_validate(team)


def test_recursive_snapshots_are_finite_and_revision_pinned(store):
    team = preset('recursive_decompose'); team['id'] = 'my_team'; team['nodes'][1]['max_depth'] = 4
    frozen = resolve(store, team)
    current = frozen
    for depth in range(5):
        node = current['nodes'][1]
        assert current['id'] == 'my_team'
        if depth == 4: assert node['recursion_leaf'] and 'child_snapshot' not in node
        else: current = node['child_snapshot']
    json.dumps(frozen)  # No circular object references.
    store.put('architecture', 'my_team', {**team, 'name': 'Edited later'})
    assert resolve(store, frozen) == frozen
    assert preview(store, team)['child_calls'] == 4


def test_only_explicit_recursive_nodes_close_reference_cycles(store):
    one = Architecture(id='one', name='One', nodes=[dict(id='call', name='Call', role='work', kind='subswarm', subswarm_id='two')]).model_dump()
    two = Architecture(id='two', name='Two', nodes=[dict(id='call', name='Call', role='work', kind='subswarm', subswarm_id='one')]).model_dump()
    store.put('architecture', 'one', one); store.put('architecture', 'two', two)
    with pytest.raises(RouteError, match='recursive'): resolve(store, one)
    two['nodes'][0].update(kind='recursive', max_depth=2)
    store.put('architecture', 'two', two)
    # A second ordinary back-edge must also be marked recursive.
    with pytest.raises(RouteError, match='recursive'): resolve(store, one)
    one['nodes'][0].update(kind='recursive', max_depth=2)
    assert preview(store, one)['child_calls'] == 2


def test_exponential_trees_are_bounded_before_execution(store):
    tree = preset('recursive_fork')
    assert preview(store, tree)['child_calls'] == 2
    for node in tree['nodes']:
        if node['kind'] == 'recursive': node['max_depth'] = 4
    with pytest.raises(RouteError, match='128'): resolve(store, tree)


def test_revision_rounds_multiply_recursive_preview_bounds(store):
    team = preset('recursive_decompose'); team['max_rounds'] = 2
    stats = preview(store, team)
    assert stats['child_calls'] == 6 and stats['worst_model_calls'] == 36


def test_stop_condition_inspects_the_selected_task_source():
    node = dict(max_depth=4, task_source='scope', recursion_stop_contains='DONE')
    assert base_case(dict(depth=0), node, [dict(node_id='other', content='DONE'), dict(node_id='scope', content='keep going')]) == ''
    assert base_case(dict(depth=0), node, [dict(node_id='scope', content='Done: solved')]) == 'stop condition'
    assert base_case(dict(depth=4), node, []) == 'depth limit'


def test_owner_api_validates_recursive_copies_and_preflight_counts_children(tmp_path, vault, monkeypatch):
    monkeypatch.setenv('SWARM_OLLAMA_URL', 'http://127.0.0.1:1')
    with TestClient(create_app(tmp_path/'state', token='owner', vault=vault)) as client:
        client.post('/api/session', json=dict(token='owner'))
        team = preset('recursive_decompose'); team.update(id='custom_recursion', builtin=False)
        response = client.post('/api/architectures/preview', json=team)
        assert response.status_code == 200 and response.json()['model_calls'] == 7
        assert client.post('/api/architectures', json=team).status_code == 201
        preflight = client.post('/api/ops/preflight', json=dict(mission=dict(task='Plan', architecture_id=team['id'], max_subtasks=1), architecture_ids=[team['id']])).json()['comparisons'][0]
        assert preflight['initial_requests'] == 7 and preflight['child_calls'] == 2
        assert any('child-run limit' in text for text in preflight['warnings'])
        team['nodes'][1]['subswarm_id'] = 'missing_architecture'
        assert client.post('/api/architectures/preview', json=team).status_code == 400
        assert client.post('/api/architectures', json=team).status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize('id', [t['id'] for t in TEMPLATES])
async def test_all_presets_execute_against_owned_model_fixtures(store, vault, tmp_path, model_fixture, id):
    async def response(name, role, task, prior, steering=''):
        yield '{"route":"CODE"}' if name == 'Router' else '{"decision":"A","justification":"owned fixture"}' if name.startswith('Perspective') else 'owned fixture work'
    model_fixture(store, vault, response)
    team = preset(id)
    async with runner(store, vault, tmp_path) as (engine, _):
        run = engine.create(RunInput(task='Complete the bounded task', architecture_id=id), team)
        await engine.tasks[run['id']]
        assert store.get('run', run['id'])['status'] == 'completed'
        assert engine.budget.usage(run)['requests'] > 0


@pytest.mark.asyncio
async def test_live_recursive_handoffs_and_permission_intersection(store, vault, tmp_path, model_fixture):
    calls = []
    async def response(name, role, task, prior, steering=''):
        calls.append((name, task))
        yield 'a smaller scoped task' if name == 'Decomposer' else 'base case solved' if name == 'Recursive solver' else 'integrated child output'
    model_fixture(store, vault, response)
    team = preset('recursive_decompose'); team['nodes'][1]['tools'] = []
    async with runner(store, vault, tmp_path) as (engine, _):
        run = engine.create(RunInput(task='Solve the complete task', architecture_id=team['id']), team)
        await engine.tasks[run['id']]
        assert store.get('run', run['id'])['status'] == 'completed'
        runs = store.all('run'); assert sorted(r['depth'] for r in runs) == [0, 1, 2]
        children = [r for r in runs if r.get('root_run_id') == run['id']]
        assert all(r['inputs']['task'] == 'a smaller scoped task' for r in children)
        assert all(a['tools'] == [] for r in children for a in engine.agents(r['id']))
        assert len(calls) == 7 and engine.budget.usage(run)['requests'] == 7
        leaf = next(a for a in store.all('agent') if a.get('recursion'))
        assert leaf['recursion'] == dict(reason='depth limit', depth=2, max_depth=2)
        assert len([r for r in children if r['results'][1].get('child_run_id')]) == 1
        assert 'base case solved' in engine.agents(next(r['id'] for r in runs if r['depth'] == 2))[2]['messages'][0]['content']


@pytest.mark.asyncio
async def test_early_stop_solves_with_a_model_instead_of_creating_a_child(store, vault, tmp_path, model_fixture):
    async def response(name, role, task, prior, steering=''):
        yield 'BASE_CASE solve directly' if name == 'Decomposer' else 'solved'
    model_fixture(store, vault, response)
    team = preset('recursive_decompose')
    async with runner(store, vault, tmp_path) as (engine, _):
        run = engine.create(RunInput(task='Already small', architecture_id=team['id']), team)
        await engine.tasks[run['id']]
        assert store.get('run', run['id'])['status'] == 'completed'
        assert len(store.all('run')) == 1
        assert engine.budget.usage(run)['requests'] == 3
        assert engine.agents(run['id'])[1]['recursion']['reason'] == 'stop condition'


@pytest.mark.asyncio
@pytest.mark.parametrize('limit', ['children', 'requests'])
async def test_recursion_cannot_escape_root_budgets(store, vault, tmp_path, model_fixture, limit):
    async def response(name, role, task, prior, steering=''): yield 'continue with a smaller task'
    model_fixture(store, vault, response)
    team = preset('recursive_decompose')
    form = dict(task='Bounded mission', architecture_id=team['id'], max_subtasks=1) if limit == 'children' else dict(task='Bounded mission', architecture_id=team['id'], limits=dict(max_requests=3))
    async with runner(store, vault, tmp_path) as (engine, _):
        run = engine.create(RunInput(**form), team)
        await engine.tasks[run['id']]
        assert store.get('run', run['id'])['status'] == 'interrupted'
        if limit == 'children': assert len(store.all('run')) == 2
        else: assert engine.budget.usage(run)['requests'] == 3


@pytest.mark.asyncio
async def test_recursive_restart_reuses_children_and_completed_work(store, vault, tmp_path, model_fixture):
    gate = asyncio.Event(); calls = []
    async def response(name, role, task, prior, steering=''):
        calls.append(name)
        if name == 'Recursive solver': await gate.wait()
        yield 'smaller task' if name == 'Decomposer' else 'solved'
    model_fixture(store, vault, response)
    team = preset('recursive_decompose')
    async with runner(store, vault, tmp_path) as (engine, saver):
        run = engine.create(RunInput(task='Recover nested work', architecture_id=team['id']), team)
        async with asyncio.timeout(6):
            while 'Recursive solver' not in calls: await asyncio.sleep(.01)
        await engine.pause(run['id']); await engine.stop()
        before = {r['id'] for r in store.all('run')}
        gate.set()
        restarted = Engine(store, engine.providers, engine.pool, saver, engine.workspace)
        try:
            restarted.start(run['id']); await restarted.tasks[run['id']]
            assert store.get('run', run['id'])['status'] == 'completed'
            assert {r['id'] for r in store.all('run')} == before
            assert calls.count('Decomposer') == 3
        finally: await restarted.stop()


@pytest.mark.asyncio
async def test_router_runs_only_the_selected_specialist(store, vault, tmp_path, model_fixture):
    calls = []
    async def response(name, role, task, prior, steering=''):
        calls.append(name)
        yield '{"route":"CODE"}' if name == 'Router' else 'completed work'
    model_fixture(store, vault, response)
    team = preset('router')
    async with runner(store, vault, tmp_path) as (engine, _):
        run = engine.create(RunInput(task='Implement a function', architecture_id=team['id']), team)
        await engine.tasks[run['id']]
        assert store.get('run', run['id'])['status'] == 'completed'
        assert calls == ['Router', 'Coding specialist', 'Finisher']
        assert sum(a['status'] == 'skipped' for a in engine.agents(run['id'])) == 2
