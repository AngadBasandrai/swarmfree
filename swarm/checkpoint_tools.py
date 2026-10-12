"""Fork actual saved execution state without replaying completed external effects."""
import copy
from .schema import RunInput
from .privacy import digest
from .providers import RouteError
from .intelligence import check_output


async def history(engine,id):
    run=engine.store.get('run',id)
    if not run:raise RouteError('Mission not found')
    graph=engine.graph(run);rows=[]
    for round in range(run.get('round',0)+1):
        config={'configurable':{'thread_id':id if round==0 else f'{id}:round:{round}'}}
        async for state in graph.aget_state_history(config,limit=100):
            rows.append(dict(id=state.config['configurable']['checkpoint_id'],round=round,created_at=state.created_at,next=list(state.next),results=state.values.get('results',[]),architecture_digest=state.values.get('architecture_digest')))
    return rows


async def fork(engine,id,checkpoint_id,route=None):
    run=engine.store.get('run',id)
    point=next((r for r in await history(engine,id) if r['id']==checkpoint_id),None)
    if not point:raise RouteError('Checkpoint not found')
    if point['architecture_digest']!=digest(run['architecture']):raise RouteError('Checkpoint belongs to a different graph revision; fork a checkpoint from the current frozen graph')
    form=RunInput.model_validate({**run,'route':route or run['route'],'adaptive':'off'})
    created=engine.create(form,run['architecture'],start=False)
    with engine.store.transaction():
        engine.store.patch('run',created['id'],dict(parent_run_id=id,fork_checkpoint_id=checkpoint_id,carried_results=len(point['results'])))
        for result in point['results']:
            target=created['id']+':'+result['node_id'];agent=engine.store.get('agent',target)
            if not agent:raise RouteError('Saved checkpoint references an unavailable node')
            checked=check_output(result['content'],agent.get('output_schema'),agent.get('validators'))
            if result.get('skipped') or result.get('failed') or checked['passed']:
                engine.store.patch('agent',target,dict(status='done',result=copy.deepcopy(result),messages=[dict(id=target+':carried',role='assistant',content=result['content'],complete=True,source='checkpoint')],data_labels=result.get('data_labels',[])))
    engine.start(created['id'])
    return engine.store.get('run',created['id'])
