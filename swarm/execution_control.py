"""Durable branch gates and tree inspection backed by LangGraph interrupts."""
import copy
import time
from fastapi import APIRouter, HTTPException
from langgraph.types import interrupt
from pydantic import BaseModel, Field
from .schema import Architecture, AgentSpec
from .providers import RouteError
from .dataflow import bounded_json, render
from .composition import resolve


def gate(engine,run,agent,spec,phase,reason,inputs):
    control=engine.store.get('agent_control',agent['id'],{})
    key=agent['id']+':'+str(run.get('round',0))+':'+phase+':'+str(control.get('generation',0))
    row=engine.store.get('execution_gate',key)
    if row and row['status']=='approved': return
    bounded_json(inputs,128000)
    if not row:
        row=dict(id=key,run_id=run['id'],agent_id=agent['id'],phase=phase,reason=reason,inputs=inputs,status='waiting',created_at=time.time())
        engine.store.put('execution_gate',key,row)
        engine.store.event(run['id'],'branch_waiting',agent_id=agent['id'],message=agent['name']+': '+reason)
    engine.store.patch('agent',agent['id'],dict(status='held'))
    interrupt(dict(kind='branch_gate',gate_id=key,agent_id=agent['id'],reason=reason))
    # Approval is written before the checkpoint resumes. The node may restart.
    engine.store.patch('agent',agent['id'],dict(status='waiting'))


def checkpoint_gates(engine,checkpoint):
    return [item for task in checkpoint.tasks for item in task.interrupts]


def resume_values(engine,items):
    values={}
    for item in items:
        value=item.value
        if value.get('kind')=='branch_gate':
            row=engine.store.get('execution_gate',value['gate_id'],{})
            control=engine.store.get('agent_control',value['agent_id'],{})
            if row.get('status')=='approved' or control.get('skip'): values[item.id]=True
        elif value.get('kind')=='child_wait':
            control=engine.store.get('agent_control',value['agent_id'],{})
            ids=value.get('child_run_ids',[])
            if control.get('skip') or any(engine.store.get('run',id,{}).get('pruned') or engine.store.get('run',id,{}).get('status')=='completed' or (id in engine.tasks and not engine.tasks[id].done()) for id in ids): values[item.id]=True
    return values


def family_start(engine,id):
    seen=set()
    while id and id not in seen:
        seen.add(id);run=engine.store.get('run',id)
        if not run or run.get('pruned'): break
        if run['status'] in {'waiting','paused','interrupted','ready','queued','budget_exceeded'}: engine.start(id)
        elif id in engine.tasks and not engine.tasks[id].done():
            def retry(done,run_id=id):
                if not done.cancelled() and engine.store.get('run',run_id,{}).get('status')=='waiting': engine.start(run_id)
            engine.tasks[id].add_done_callback(retry)
        id=run.get('parent_run_id') if run.get('delegated') else None


def tree(engine,id):
    run=engine.store.get('run',id)
    if not run: raise HTTPException(404,'Mission not found')
    root_id=run.get('root_run_id',id)
    members=[r for r in engine.store.all('run') if r['id']==root_id or r.get('root_run_id')==root_id]
    records=[]
    all_reservations=engine.store.all('reservation')
    for member in members:
        agents=engine.agents(member['id']);reservations=[r for r in engine.store.all('reservation') if r['run_id']==member['id']]
        records.append(dict(id=member['id'],parent_run_id=member.get('parent_run_id'),via_agent=member.get('composition_agent'),depth=member.get('depth',0),
            task=member.get('composition_task',member['task']),name=member['architecture']['name'],status=member['status'],
            progress=dict(done=sum(a['status'] in {'done','skipped'} for a in agents),total=len(agents)),
            requests=sum(r.get('units',1) for r in reservations),tokens=sum(r['tokens'] for r in reservations),
            models=sorted({a['actual_route'] for a in agents if a.get('actual_route')}),
            agents=[dict(id=a['id'],node_id=a['node_id'],name=a['name'],status=a['status'],request_budget={**engine.store.get('branch_budget',a['id'],{}),
                'used_requests':sum(r.get('units',1) for r in all_reservations if a['id'] in r.get('scopes',[])),
                'used_tokens':sum(r['tokens'] for r in all_reservations if a['id'] in r.get('scopes',[]))}) for a in agents]))
    return dict(root_id=root_id,runs=records,gates=[g for g in engine.store.all('execution_gate') if g['run_id'] in {r['id'] for r in members} and g['status']=='waiting'])


class ControlInput(BaseModel):
    action:str=Field(pattern='^(hold|approve|skip)$')
class SurgeryInput(BaseModel):
    spec:AgentSpec


async def prune_children(engine,agent_id):
    for child in engine.store.all('run'):
        if agent_id in child.get('budget_scopes',[]) and child['status']!='completed':
            await engine.pause(child['id']);engine.store.patch('run',child['id'],dict(pruned=True,status='cancelled'))
            for row in engine.store.all('execution_gate'):
                if row['run_id']==child['id'] and row['status']=='waiting':engine.store.patch('execution_gate',row['id'],dict(status='cancelled'))


def install(app):
    router=APIRouter(prefix='/api/execution-control')
    @router.get('/tree/{id}')
    async def inspect(id:str): return tree(app.state.engine,id)
    @router.post('/agents/{id}')
    async def control(id:str,form:ControlInput):
        engine=app.state.engine;agent=engine.store.get('agent',id)
        if not agent: raise HTTPException(404,'Agent not found')
        run=engine.store.get('run',agent['run_id']);engine.require_live(run)
        if agent['status'] in {'done','skipped'}: raise HTTPException(409,'Completed work is preserved; branch the mission to change it')
        old=engine.store.get('agent_control',id,{})
        if form.action=='hold':
            engine.store.put('agent_control',id,dict(id=id,hold=True,skip=False,generation=old.get('generation',0)+1))
            engine.store.event(run['id'],'branch_hold_requested',agent_id=id,message='Pause this branch at its next safe boundary')
        else:
            with engine.store.transaction():
                engine.store.put('agent_control',id,dict(id=id,hold=False,skip=form.action=='skip',generation=old.get('generation',0)))
                for row in engine.store.all('execution_gate'):
                    if row['agent_id']==id and row['status']=='waiting': engine.store.patch('execution_gate',row['id'],dict(status='approved',decision=form.action,decided_at=time.time()))
            if form.action=='skip':
                await prune_children(engine,id)
            family_start(engine,run['id'])
        return dict(ok=True,action=form.action)
    @router.put('/agents/{id}')
    async def surgery(id:str,form:SurgeryInput):
        engine=app.state.engine;agent=engine.store.get('agent',id)
        if not agent: raise HTTPException(404,'Agent not found')
        run=engine.store.get('run',agent['run_id']);engine.require_live(run)
        if agent['status']!='held' and run['status'] not in {'paused','waiting','interrupted'}:
            raise HTTPException(409,'Pause this branch before editing it')
        if agent['status'] in {'done','skipped'}: raise HTTPException(409,'Completed work is preserved')
        if agent['status'] in {'running','chatting'}: raise HTTPException(409,'Wait until the active request reaches its hold before editing')
        if form.spec.id!=agent['node_id']: raise RouteError('Branch editing preserves the node ID and dependencies')
        if form.spec.tools is not None:
            allowed=engine.workspace.permissions(run,agent) if engine.workspace else []
            if set(form.spec.tools)-set(allowed): raise RouteError('Branch editing cannot add tools beyond current grants')
        allowed=engine.workspace.permissions(run,agent) if engine.workspace else []
        restricted=form.spec.model_dump()
        restricted['tools']=allowed if form.spec.tools is None else form.spec.tools
        original=run['architecture']
        raw=Architecture.model_validate({**original,'nodes':[restricted if n['id']==agent['node_id'] else n for n in original['nodes']]}).model_dump()
        frozen=resolve(engine.store,raw)
        updated=next(n for n in frozen['nodes'] if n['id']==agent['node_id'])
        changed={**original,'nodes':[updated if n['id']==agent['node_id'] else n for n in original['nodes']]}
        resolve(engine.store,changed)
        instructions=render(updated['instructions'],run.get('parameters',{}))
        await prune_children(engine,id)
        with engine.store.transaction():
            engine.store.patch('run',run['id'],dict(architecture=changed))
            patch=dict(name=updated['name'],role=updated['role'],instructions=instructions,
                tools=updated.get('tools'),requires=list(set(updated.get('requires',[])+(['vision'] if 'vision' in agent.get('requires',[]) else []))),output_schema=updated.get('output_schema',{}),validators=updated.get('validators',[]),pending_result=None,
                messages=[m for m in agent['messages'] if m.get('source')!='task'])
            if updated.get('preferred_route'): patch['route']=updated['preferred_route']
            engine.store.patch('agent',id,patch)
            engine.store.put('branch_budget',id,dict(id=id,requests=updated.get('request_budget'),tokens=updated.get('token_budget')))
            engine.store.event(run['id'],'branch_edited',agent_id=id,message='Paused branch updated; completed work and tool receipts preserved')
        return dict(updated=True,spec=updated)
    app.include_router(router)
