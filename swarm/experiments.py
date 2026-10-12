"""Frozen, bounded repeated evaluations, ablations and promotion evidence."""
import asyncio
import copy
import time
from .schema import Architecture,RunInput,AgentSpec
from .composition import resolve,terminal_output
from .dataflow import bounded_json
from .providers import RouteError
from .intelligence import check_output
from .privacy import digest
from .store import uid


def fingerprint(store,architecture,*,frozen=False):
    value=copy.deepcopy(architecture) if frozen else resolve(store,Architecture.model_validate(architecture).model_dump())
    def clean(team):
        for key in ('id','name','description','builtin','revision','regression_gate_id'):team.pop(key,None)
        for node in team['nodes']:
            node.pop('x',None);node.pop('y',None)
            if node.get('child_snapshot'):clean(node['child_snapshot']);node.pop('child_revision',None)
    clean(value)
    return digest(value)


def ablate(value,nodes,edges):
    value=copy.deepcopy(value);chosen=set(nodes)
    if not chosen<={n['id'] for n in value['nodes']}:raise RouteError('Ablation references an unknown agent')
    value['nodes']=[n for n in value['nodes'] if n['id'] not in chosen]
    value['edges']=[e for e in value['edges'] if e['source'] not in chosen and e['target'] not in chosen and [e['source'],e['target']] not in edges]
    value['channels']=[c for c in value.get('channels',[]) if not chosen.intersection(c['members'])]
    value['output_ports']=[p for p in value.get('output_ports',[]) if p['source'] not in chosen]
    for node in value['nodes']:
        if node.get('run_if',{}).get('source') in chosen:node['run_if']={}
        if node.get('task_source') in chosen:node['task_source']=''
    try:
        validated=Architecture.model_validate(value).model_dump()
        if value.get('composition_version')==2:
            validated['composition_version']=2
            originals={n['id']:n for n in value['nodes']}
            for node in validated['nodes']:
                for key in ('child_snapshot','child_revision','recursion_leaf'):
                    if key in originals[node['id']]:node[key]=copy.deepcopy(originals[node['id']][key])
        return validated
    except ValueError as e:raise RouteError('Ablation leaves an invalid graph or input contract; edit the variant explicitly') from e


def require_gate(store,value):
    requested=value.get('regression_gate_id')
    old=store.get('architecture',value['id'],{})
    if old.get('regression_gate_id') and requested!=old['regression_gate_id']:raise RouteError('Clear the existing promotion gate explicitly before changing its policy')
    if not requested:return
    gate=store.get('regression_gate',requested)
    if not gate:raise RouteError('Promotion gate not found')
    proof=store.get('experiment',gate.get('proof_id',''),{})
    if proof.get('status')!='completed' or not proof.get('passed') or proof.get('architecture_digest')!=fingerprint(store,value) or proof.get('gate_digest')!=digest(gate['policy']):raise RouteError('This revision needs a passing repeated evaluation under its promotion policy')


class Experiments:
    def __init__(self,store,engine):self.store,self.engine=store,engine

    def prepare(self,form):
        architecture=resolve(self.store,Architecture.model_validate(form['architecture']).model_dump())
        tasks=form.get('tasks',[]);repeats=form.get('repeats',2);variants=form.get('ablations',[])
        if not 1<=len(tasks)<=12 or not 1<=repeats<=5 or len(variants)>8 or (len(variants)+1)*len(tasks)*repeats>60:raise RouteError('Limit experiments to 60 trials, 12 tasks, five repeats and eight ablations')
        for task in tasks:
            if not task.get('task') or len(task['task'])>24000:raise RouteError('Each evaluation task needs a bounded prompt')
            AgentSpec(id='checks',name='Checks',role='Evaluate',validators=task.get('checks',[]),output_schema=task.get('schema',{}))
        compiled=[dict(name='Baseline',architecture=architecture)]
        for variant in variants:
            if not variant.get('name') or len(variant['name'])>80:raise RouteError('Name each ablation')
            compiled.append(dict(name=variant['name'],architecture=ablate(architecture,variant.get('remove_nodes',[]),variant.get('remove_edges',[]))))
        if len({v['name'] for v in compiled})!=len(compiled):raise RouteError('Use distinct variant names')
        mission=RunInput.model_validate({**form.get('mission',{}),'task':tasks[0]['task'],'architecture_id':architecture['id'],'adaptive':'off','queue_if_unavailable':False}).model_dump()
        from .reliability import lint
        for variant in compiled:
            errors=[i['message'] for i in lint(self.store,variant['architecture'],mission.get('project_id',''))['issues'] if i['severity']=='error']
            if errors:raise RouteError(variant['name']+' requires graph fixes: '+'; '.join(errors)[:1500])
        policy=dict(tasks=tasks,repeats=repeats,required_nodes=form.get('required_nodes',[]),forbidden_tools=form.get('forbidden_tools',[]))
        if not set(policy['required_nodes'])<={n['id'] for n in architecture['nodes']}:raise RouteError('Trajectory checks reference existing nodes')
        gate=self.store.get('regression_gate',form.get('gate_id',''))
        if form.get('gate_id') and (not gate or gate['policy']!=policy):raise RouteError('Evaluation must use the exact approved gate policy')
        row=dict(id=uid('experiment_'),status='ready',created_at=time.time(),tasks=tasks,repeats=repeats,variants=compiled,mission=mission,policy=policy,
            gate_id=form.get('gate_id',''),gate_digest=digest(policy),architecture_digest=fingerprint(self.store,architecture,frozen=True),rows=[])
        return self.store.put('experiment',row['id'],row)

    async def run(self,id):
        row=self.store.get('experiment',id);rows=[];root=None;active=None
        try:
            for variant in row['variants']:
                for task in row['tasks']:
                    for repeat in range(row['repeats']):
                        if root:
                            usage=self.engine.budget.usage(self.store.get('run',root))
                            if usage['remaining_requests']<1 or usage['remaining_tokens']<32 or usage['remaining_seconds']<=0:raise RouteError('Shared experiment budget reached')
                        form=RunInput.model_validate({**row['mission'],'task':task['task']})
                        mission=self.engine.create(form,variant['architecture'],start=False);active=mission['id']
                        if root:self.store.patch('run',active,dict(root_run_id=root,experiment_id=id))
                        else:root=active;self.store.patch('run',active,dict(experiment_id=id))
                        self.store.patch('experiment',id,dict(status='running',active_run_id=active,root_run_id=root,rows=rows))
                        self.engine.start(active);await self.engine.tasks[active]
                        run=self.store.get('run',active);data=terminal_output(run)
                        family={active};missions=self.store.all('run')
                        for _ in range(5):
                            added={r['id'] for r in missions if r.get('delegated') and r.get('parent_run_id') in family}
                            if added<=family:break
                            family.update(added)
                        checked=check_output(data if isinstance(data,str) else bounded_json(data),task.get('schema'),task.get('checks'))
                        completed={r['node_id'] for r in run.get('results',[]) if not r.get('skipped') and not r.get('failed')}
                        forbidden=[o['tool'] for o in self.store.all('operation') if o.get('run_id') in family and o['tool'] in row['policy']['forbidden_tools']]
                        failures=checked['failures']+(['Mission '+run['status']] if run['status']!='completed' else [])
                        failures += ['Missing trajectory node: '+n for n in row['policy']['required_nodes'] if n not in completed]
                        failures += ['Forbidden tool: '+t for t in forbidden]
                        reservations=[r for r in self.store.all('reservation') if r['run_id'] in family]
                        rows.append(dict(variant=variant['name'],task=task['task'],repeat=repeat+1,run_id=active,passed=not failures,failures=failures,requests=sum(r.get('units',1) for r in reservations),tokens=sum(r['tokens'] for r in reservations),nodes=sorted(completed)))
            summaries=[dict(name=v['name'],passed=all(r['passed'] for r in rows if r['variant']==v['name']),tokens=sum(r['tokens'] for r in rows if r['variant']==v['name']),requests=sum(r['requests'] for r in rows if r['variant']==v['name'])) for v in row['variants']]
            passed=summaries[0]['passed']
            self.store.patch('experiment',id,dict(status='completed',rows=rows,summaries=summaries,passed=passed,finished_at=time.time()))
            if row['gate_id'] and passed:self.store.patch('regression_gate',row['gate_id'],dict(proof_id=id))
        except asyncio.CancelledError:
            if active:await self.engine.pause(active)
            self.store.patch('experiment',id,dict(status='cancelled',rows=rows));raise
        except Exception as error:
            if active:await self.engine.pause(active)
            self.store.patch('experiment',id,dict(status='failed',rows=rows,error=str(error) if isinstance(error,RouteError) else 'Experiment failed; inspect the trial'))
