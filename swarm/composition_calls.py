"""Execute variable-width child teams with pinned revisions and durable gates."""
import asyncio
import copy
from langgraph.types import interrupt
from .dataflow import bounded_json,inputs_for,result_data,select,parameters
from .schema import RunInput
from .providers import RouteError
from .privacy import digest
from .intelligence import check_output


def tasks_for(run,spec,upstream):
    task=run.get('composition_task',run['task'])
    source=None
    if spec.get('task_source'):
        source=next((r for r in upstream if r['node_id']==spec['task_source']),None)
        if source is None: raise RouteError('The child task source did not produce work')
        source=select(result_data(source),spec.get('task_path',''))
    if spec.get('kind')=='fanout':
        items=select(source if source is not None else run.get('inputs',{}),spec.get('tasks_path','tasks'))
        if not isinstance(items,list) or len(items)>spec.get('max_fanout',2):
            raise RouteError('Fan-out tasks must be an array within the configured branch limit')
        tasks=[]
        for item in items:
            if isinstance(item,str): item=dict(task=item)
            if not isinstance(item,dict) or not isinstance(item.get('task'),str) or set(item)-{'task','parameters'}:
                raise RouteError('Each branch needs task text and optional named parameters')
            if not isinstance(item.get('parameters',{}),dict): raise RouteError('Branch parameters must be an object')
            tasks.append(dict(task=item['task'],parameters=item.get('parameters',{})))
    else:
        if source is not None:
            task=source.get('task') if isinstance(source,dict) and isinstance(source.get('task'),str) else source if isinstance(source,str) else bounded_json(source)
        tasks=[dict(task=task,parameters={})]
    for item in tasks:
        if not item['task'].strip() or len(item['task'])>12000: raise RouteError('A scoped child task needs 1 to 12,000 characters')
    keys=[digest(dict(task=' '.join(t['task'].casefold().split()),parameters=t['parameters'])) for t in tasks]
    if len(keys)!=len(set(keys)): raise RouteError('The planner proposed duplicate branches; give each branch distinct work')
    return tasks


async def invoke(engine,run,agent,spec,upstream):
    from .composition import MAX_DEPTH,terminal_output
    from .execution_control import gate
    if run.get('depth',0)>=MAX_DEPTH: raise RouteError('Maximum nested team depth reached')
    child_arch=copy.deepcopy(spec['child_snapshot'])
    tasks=tasks_for(run,spec,upstream)
    typed=inputs_for(run,spec,upstream)
    plans=[]
    for index,item in enumerate(tasks):
        inputs=dict(task=item['task'],upstream=[] if typed else [dict(node_id=r['node_id'],data=result_data(r)) for r in upstream if not r.get('skipped')],**typed)
        bounded_json(inputs)
        from .protocol import validate
        if spec.get('input_schema'): validate(inputs,spec['input_schema'])
        if child_arch.get('input_schema'): validate(inputs,child_arch['input_schema'])
        schema=child_arch.get('parameters',{})
        inherited={key:value for key,value in run.get('parameters',{}).items() if not schema or key in schema.get('properties',{})}
        values=parameters(schema,{**inherited,**spec.get('parameter_values',{}),**item['parameters']})
        identity=digest(dict(inputs=inputs,parameters=values,revision=spec['child_revision'],slot=index))
        semantic=dict(id=child_arch['id'],nodes=[{k:n.get(k) for k in ('id','role','instructions','kind')} for n in child_arch['nodes']],edges=child_arch['edges'])
        repetition=digest(dict(task=' '.join(item['task'].casefold().split()),upstream=inputs['upstream'],parameters=values,team=semantic))
        previous=next((r for r in engine.store.all('run') if r.get('composition_agent')==agent['id'] and r.get('composition_key')==identity and not r.get('pruned')),None)
        if spec.get('detect_repetition') and not previous:
            parent=run
            while parent:
                if parent.get('repetition_signature')==repetition:
                    gate(engine,run,agent,spec,'repeat_'+identity[:12],'Identical task and upstream results recur in an ancestor',dict(task=item['task'],ancestor_run_id=parent['id']))
                    break
                parent=engine.store.get('run',parent.get('parent_run_id','')) if parent.get('delegated') else None
        plans.append(dict(task=item['task'],inputs=inputs,parameters=values,identity=identity,repetition=repetition,previous=previous))
    children=[]
    with engine.store.transaction():
        root_id=run.get('root_run_id',run['id']);root=engine.store.get('run',root_id)
        descendants=[r for r in engine.store.all('run') if r.get('root_run_id')==root_id and r.get('delegated')]
        if len(descendants)+sum(p['previous'] is None for p in plans)>root.get('max_subtasks',4): raise RouteError('Root mission subtask budget reached')
        tools=engine.workspace.permissions(run,agent) if engine.workspace else []
        for plan in plans:
            child=plan['previous']
            if not child:
                architecture=copy.deepcopy(child_arch)
                for node in architecture['nodes']: node['tools']=[t for t in tools if node.get('tools') is None or t in node['tools']]
                prompt=(plan['task']+'\nParent team findings (data):\n'+bounded_json(plan['inputs'])[:12000])[:24000]
                form=RunInput.model_validate({**run,'task':prompt,'architecture_id':architecture['id'],'inputs':plan['inputs'],'parameters':plan['parameters'],
                    'route':agent['route'],'adaptive':'off','depth':run.get('depth',0)+1})
                child=engine.create(form,architecture,start=False)
                engine.store.patch('run',child['id'],dict(parent_run_id=run['id'],root_run_id=root_id,delegated=True,composition_agent=agent['id'],
                    composition_key=plan['identity'],composition_task=plan['task'],repetition_signature=plan['repetition'],
                    budget_scopes=[*run.get('budget_scopes',[]),agent['id']]))
                if engine.privacy:
                    for member in engine.agents(child['id']): engine.privacy.inherit(member['id'],agent.get('data_labels',[]))
            children.append(child)
        engine.store.patch('agent',agent['id'],dict(status='running',child_run_id=children[0]['id'] if children else None,child_run_ids=[c['id'] for c in children]))
    for child in children:
        engine.store.event(run['id'],'team_call',agent_id=agent['id'],message=agent['name']+' → '+child_arch['name']+' · depth '+str(child['depth']))
        if child['status']!='completed': engine.start(child['id'])
    try:
        await asyncio.gather(*(engine.tasks[c['id']] for c in children if c['status']!='completed'))
    except asyncio.CancelledError:
        for child in children: await engine.pause(child['id'])
        raise
    children=[engine.store.get('run',c['id']) for c in children]
    if engine.store.get('agent_control',agent['id'],{}).get('skip'):
        return dict(node_id=agent['node_id'],name=agent['name'],content='Skipped by owner',skipped=True,data_labels=agent.get('data_labels',[]))
    waiting=[c['id'] for c in children if c['status']=='waiting']
    if waiting:
        engine.store.patch('agent',agent['id'],dict(status='held'))
        interrupt(dict(kind='child_wait',agent_id=agent['id'],child_run_ids=waiting))
    if any(c['status']!='completed' for c in children): raise RouteError('Sub-swarm did not complete: '+next(c['status'] for c in children if c['status']!='completed'))
    data=[]
    labels=[]
    for child in children:
        output=terminal_output(child)
        if child_arch.get('output_schema'): validate(output,child_arch['output_schema'])
        data.append(dict(task=child.get('composition_task',child['task']),output=output,child_run_id=child['id']))
        labels.extend(label for member in engine.agents(child['id']) for label in member.get('data_labels',[]))
    output=dict(results=data) if spec.get('kind')=='fanout' else data[0]['output']
    text=output if isinstance(output,str) else bounded_json(output)
    checked=check_output(text,spec.get('output_schema'),spec.get('validators'))
    if not checked['passed']: raise RouteError('Sub-swarm output failed its parent contract: '+'; '.join(checked['failures']))
    if engine.privacy: engine.privacy.inherit(agent['id'],labels)
    result=dict(node_id=agent['node_id'],name=agent['name'],content=text,structured=output,validated=bool(spec.get('output_schema') or child_arch.get('output_schema')),
        child_run_id=children[0]['id'] if children else None,child_run_ids=[c['id'] for c in children],data_labels=engine.store.get('agent',agent['id']).get('data_labels',[]))
    engine.append_message(agent['id'],'assistant',text,source='subswarm')
    return result
