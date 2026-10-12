"""Owner-reviewed reuse, scoped to a mission family. Similarity is a hint only."""
import copy
import difflib
from .privacy import digest
from .intelligence import check_output
from .providers import RouteError


def reuse_proposal(engine,run,agent,spec):
    if not run.get('deduplicate_tasks'): return None
    from .execution_control import gate
    key=agent['id']+':'+str(run.get('round',0))+':duplicate:'+str(engine.store.get('agent_control',agent['id'],{}).get('generation',0))
    existing=engine.store.get('execution_gate',key)
    root=run.get('root_run_id',run['id'])
    target_digest=digest(dict(task=run['task'],context=[m['content'] for m in agent['messages'] if m.get('source')=='task'],instructions=agent['instructions'],parameters=run.get('parameters'),inputs=run.get('inputs'),brief=run.get('brief'),route=agent.get('route'),tools=agent.get('tools'),labels=agent.get('data_labels',[])))
    if existing and existing['inputs'].get('target_digest')!=target_digest:
        if existing.get('reuse_agent_id'):raise RouteError('Task changed since duplicate review; choose independent execution')
        return None
    def compatible(other):
        mission=engine.store.get('run',other['run_id'],{})
        return (other['id']!=agent['id'] and other.get('status')=='done' and other.get('result') and not other['result'].get('failed')
            and mission.get('root_run_id',mission.get('id'))==root and mission.get('project_id')==run.get('project_id')
            and digest(other.get('data_labels',[]))==digest(agent.get('data_labels',[]))
            and all(other.get(k)==agent.get(k) for k in ('tools','output_schema','validators','route','requires'))
            and mission.get('parameters',{})==run.get('parameters',{}) and mission.get('inputs',{})==run.get('inputs',{}))
    if not existing:
        task=run.get('composition_task',run['task'])+'\n'+agent['instructions']
        candidates=[]
        for other in engine.store.all('agent'):
            if not compatible(other):continue
            mission=engine.store.get('run',other['run_id'])
            text=mission.get('composition_task',mission['task'])+'\n'+other['instructions']
            score=difflib.SequenceMatcher(None,task.casefold(),text.casefold(),autojunk=False).ratio()
            if score>=.9:candidates.append(dict(agent_id=other['id'],score=round(score,4),digest=digest(other['result']),preview=other['result']['content'][:2000]))
        if not candidates:return None
        candidates=sorted(candidates,key=lambda c:-c['score'])[:5]
        gate(engine,run,agent,spec,'duplicate','Similar completed work found; review before reusing',dict(candidates=candidates,target_digest=target_digest,method='Lexical similarity; equivalence is not established',choices='Choose a contributor or run this task independently'))
        existing=engine.store.get('execution_gate',key)
    elif existing['status']!='approved':
        gate(engine,run,agent,spec,'duplicate',existing['reason'],existing['inputs'])
        existing=engine.store.get('execution_gate',key)
    choice=existing.get('reuse_agent_id')
    if not choice:return None
    proposal=next((c for c in existing['inputs']['candidates'] if c['agent_id']==choice),None)
    other=engine.store.get('agent',choice,{})
    if not proposal or not compatible(other) or digest(other['result'])!=proposal['digest']:raise RouteError('Reuse source changed; run independently or review again')
    checked=check_output(other['result']['content'],agent.get('output_schema'),agent.get('validators'))
    if not checked['passed']:raise RouteError('Reuse source fails the target contract')
    result=copy.deepcopy(other['result']);result.update(node_id=spec['id'],name=agent['name'],reused_from=choice)
    engine.append_message(agent['id'],'assistant',result['content'],source='duplicate-reuse')
    engine.store.event(run['id'],'duplicate_reused',agent_id=agent['id'],message='Owner approved reuse from '+choice)
    return result
