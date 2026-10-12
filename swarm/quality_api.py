"""Owner desktop controls for mission quality and managed infrastructure."""
import asyncio
import json
import time
from fastapi import APIRouter,HTTPException,Request
from fastapi.routing import APIRoute
from pydantic import BaseModel,Field
from .schema import Architecture,Route
from .providers import RouteError
from .privacy import digest
from .store import uid
from .experiments import Experiments
from .runtime_install import RuntimeInstaller
from .private_cluster import PrivateCluster
from . import reliability,workspace_sync,checkpoint_tools


class Payload(BaseModel):
    value:dict=Field(default_factory=dict)


class InputRoute(APIRoute):
    def get_route_handler(self):
        original=super().get_route_handler()
        async def checked(request):
            try:return await original(request)
            except (KeyError,ValueError,TypeError) as error:raise HTTPException(400,'Malformed quality or managed-systems request; check the required fields and data types') from error
        return checked


def install(app,root):
    router=APIRouter(prefix='/api',route_class=InputRoute)
    def services():
        if not hasattr(app.state,'experiments'):
            app.state.experiments=Experiments(app.state.store,app.state.engine)
            app.state.runtime_installer=RuntimeInstaller(app.state.store,root,app.state.runtimes)
            app.state.private_cluster=PrivateCluster(app.state.store,app.state.pool)
        return app.state

    @router.get('/quality')
    async def quality():
        s=services().store
        jobs=[]
        for row in s.all('experiment')[-40:]:
            summary={k:v for k,v in row.items() if k not in {'variants','mission','tasks','policy','rows'}}
            summary['rows']=[{**r,'task':r.get('task','')[:400]} for r in row['rows']];jobs.append(summary)
        return dict(experiments=jobs,gates=s.all('regression_gate')[-100:],assemblies=[{k:r.get(k) for k in ('id','run_id','ready','created_at')} for r in s.all('assembly')[-40:]])

    @router.post('/quality/lint')
    async def lint(form:Payload):return reliability.lint(services().store,form.value['architecture'],form.value.get('project_id',''))

    @router.post('/quality/lint/fix')
    async def fix(form:Payload):return reliability.fix_lint(form.value['architecture'],form.value['fix'])

    @router.post('/quality/assemble')
    async def assemble(form:Payload):return reliability.compose(services().store,form.value['run_id'],form.value['sections'],form.value.get('decisions'))

    @router.post('/quality/compare')
    async def compare(form:Payload):
        state=services();row=reliability.compare_runs(state.store,form.value['left'],form.value['right'])
        row['usage']=[state.engine.budget.usage(state.store.get('run',id)) for id in (form.value['left'],form.value['right'])]
        return row

    @router.get('/quality/runs/{id}/checkpoints')
    async def checkpoints(id:str):return await checkpoint_tools.history(services().engine,id)

    @router.post('/quality/runs/{id}/fork')
    async def fork(id:str,form:Payload):
        route=Route.model_validate(form.value['route']).model_dump() if form.value.get('route') else None
        return await checkpoint_tools.fork(services().engine,id,form.value['checkpoint_id'],route)

    @router.post('/quality/duplicates/{id}')
    async def duplicate(id:str,form:Payload):
        state=services();row=state.store.get('execution_gate',id)
        if not row or row['phase']!='duplicate' or row['status']!='waiting':raise RouteError('Duplicate proposal is not awaiting review')
        choice=form.value.get('reuse_agent_id')
        if choice and choice not in [c['agent_id'] for c in row['inputs']['candidates']]:raise RouteError('Choose a proposed contributor')
        state.store.patch('execution_gate',id,dict(status='approved',reuse_agent_id=choice,reviewed_at=time.time()))
        from .execution_control import family_start
        family_start(state.engine,row['run_id']);return dict(approved=True)

    @router.post('/quality/experiments')
    async def experiment(form:Payload):
        state=services();row=state.experiments.prepare(form.value)
        state.feature_tasks[row['id']]=asyncio.create_task(state.experiments.run(row['id']));return row

    @router.post('/quality/experiments/{id}/cancel')
    async def cancel_experiment(id:str):
        state=services();task=state.feature_tasks.get(id)
        if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        return state.store.get('experiment',id)

    @router.post('/quality/gates')
    async def gate(form:Payload):
        state=services();policy=form.value
        # Validate policy using an actual design before storing its approval contract.
        row=state.experiments.prepare({**policy,'ablations':[],'gate_id':''})
        state.store.delete('experiment',row['id']);id=uid('gate_')
        return state.store.put('regression_gate',id,dict(id=id,name=policy.get('name','Promotion checks')[:100],policy=row['policy'],created_at=time.time()))

    @router.post('/quality/architectures/{id}/clear-gate')
    async def clear_gate(id:str):
        state=services()
        with state.store.transaction():
            value=state.store.get('architecture',id)
            if not value or value.get('builtin'):raise RouteError('Select a custom architecture')
            state.store.patch('architecture',id,dict(regression_gate_id=''))
            return state.architectures.save({**value,'regression_gate_id':''})

    @router.get('/systems')
    async def systems():
        s=services().store;selection=[]
        for kind in workspace_sync.KINDS:
            for row in s.all(kind)[-100:]:
                selection.append(dict(kind=kind,id=row['id'],name=row.get('name',row.get('title',row.get('task',row['id'])))[:160],approved=bool(s.get('knowledge_sync_approval',row['id'],{}).get('digest')==digest(row)) if kind=='knowledge' else True))
        archive=lambda kind:[{k:r.get(k) for k in ('id','title','task','read_only','requires_local_review')} for r in s.all('synced_'+kind)[-60:]]
        return dict(runtime_installs=s.all('runtime_install')[-40:],active_runtime=s.get('settings','active_runtime',{}),clusters=s.all('private_cluster')[-40:],cluster_previews=s.all('cluster_preview')[-20:],sync_items=selection,sync_inbox=[{k:v for k,v in r.items() if k!='envelope'} for r in s.all('sync_inbox')],synced_knowledge=archive('knowledge'),synced_runs=archive('run'),sync_receipts=s.all('sync_receipt')[-100:],devices=app.state.pool.public())

    @router.get('/systems/sync/inbox/{id}')
    async def sync_bundle(id:str):
        row=services().store.get('sync_inbox',id)
        if not row:raise HTTPException(404,'Encrypted inbox bundle not found')
        return row

    @router.get('/systems/sync/archive/{kind}/{id}')
    async def sync_archive(kind:str,id:str):
        if kind not in {'knowledge','run'}:raise HTTPException(404,'Archive kind not found')
        row=services().store.get('synced_'+kind,id)
        if not row:raise HTTPException(404,'Archive item not found')
        return row

    @router.post('/systems/runtime/catalog')
    async def runtime_catalog():return await services().runtime_installer.catalog()

    @router.post('/systems/runtime/install')
    async def runtime_install(form:Payload):
        state=services();id=uid('install_');row=state.store.put('runtime_install',id,dict(id=id,status='ready',created_at=time.time()))
        state.feature_tasks[id]=asyncio.create_task(state.runtime_installer.install(id,form.value['asset_id']));return row

    @router.post('/systems/runtime/{id}/activate')
    async def activate(id:str):return await services().runtime_installer.activate(id)

    @router.post('/systems/runtime/import-model')
    async def import_model(form:Payload):
        state=services();active=state.store.get('settings','active_runtime',{});installed=state.store.get('runtime_install',active.get('id',''))
        if not installed:raise RouteError('Activate a verified runtime package first')
        server=await asyncio.to_thread(state.runtime_installer.verify,installed)
        row=state.runtimes.add({**form.value,'server_path':server,'trusted':True})
        return state.store.patch('runtime',row['id'],dict(managed_install_id=installed['id']))

    @router.put('/systems/extensions/{id}/isolation')
    async def extension_isolation(id:str,form:Payload):
        from .extension_isolation import policy
        state=services();row=state.store.get('extension',id)
        if not row or row['transport']!='stdio':raise RouteError('Container isolation applies to local stdio extensions')
        value=policy(form.value) if form.value.get('enabled',True) else dict(enabled=False)
        if value['enabled']:
            from .isolation import command
            try:
                info=json.loads(await command(['docker','image','inspect',value['image']],timeout=15))
                if not info or info[0].get('Os')!='linux':raise RouteError('Select an installed Linux sandbox image')
            except OSError as error:raise RouteError('Install and start Docker before configuring extension isolation') from error
        return state.store.patch('extension',id,dict(isolation=value))

    @router.post('/systems/knowledge/{id}/approve-sync')
    async def approve_knowledge(id:str):
        state=services();row=state.store.get('knowledge',id)
        if not row or state.project_ops.knowledge_status(id):raise RouteError('Review current knowledge and its evidence before approving sync')
        return state.store.put('knowledge_sync_approval',id,dict(id=id,digest=digest(row),approved_at=time.time()))

    @router.post('/systems/sync/export')
    async def export_sync(form:Payload):return await asyncio.to_thread(workspace_sync.seal,services().store,form.value['selection'],form.value['password'])

    @router.post('/systems/sync/preview')
    async def preview_sync(form:Payload):return await asyncio.to_thread(workspace_sync.preview,services().store,form.value['envelope'],form.value['password'])

    @router.post('/systems/sync/apply')
    async def apply_sync(form:Payload):
        state=services();inbox=state.store.get('sync_inbox',form.value.get('inbox_id',''))
        if inbox:
            body=workspace_sync.open_bundle(form.value['envelope'],form.value['password'])
            for row in body['rows']:
                if row['kind'] in {'run','knowledge'} and row['value'].get('project_id') not in inbox['project_ids']:raise RouteError('Sync credential is not scoped to this project')
            if digest(form.value['envelope'])!=digest(inbox['envelope']):raise RouteError('Inbox envelope changed')
        return await asyncio.to_thread(workspace_sync.apply,state.store,form.value['envelope'],form.value['password'],form.value['review_digest'],form.value.get('choices',{}))

    @router.post('/systems/sync/send')
    async def send_sync(form:Payload):
        envelope=form.value['envelope']
        if len(json.dumps(envelope))>1_400_000:raise RouteError('Sync envelope exceeds the transfer limit')
        return await services().team.remote('/api/team/sync','POST',dict(value=dict(envelope=envelope)))

    @router.post('/team/sync')
    async def receive_sync(request:Request,form:Payload):
        state=services();member=state.team.actor(request)
        if member['role']!='sync':raise HTTPException(403,'Use a project-scoped sync credential')
        envelope=form.value['envelope']
        if len(json.dumps(envelope))>1_400_000 or envelope.get('version')!=1:raise RouteError('Invalid encrypted sync envelope')
        if len(state.store.all('sync_inbox'))>=100:raise RouteError('Review or remove existing sync bundles before receiving more')
        id=uid('inbox_');state.store.put('sync_inbox',id,dict(id=id,member_id=member['id'],project_ids=member['project_ids'],envelope=envelope,status='pending',received_at=time.time()))
        return dict(id=id,status='pending-owner-review')

    @router.delete('/systems/sync/inbox/{id}')
    async def remove_sync(id:str):services().store.delete('sync_inbox',id);return dict(deleted=True)

    @router.put('/systems/devices/{id}/cluster')
    async def cluster_approval(id:str,form:Payload):
        state=services();device=state.store.get('device',id)
        if not device or not device.get('cluster',{}).get('enabled'):raise RouteError('Worker must explicitly opt in to private clustering')
        state.store.patch('device',id,dict(cluster_approved=bool(form.value.get('approved'))));return dict(updated=True)

    @router.post('/systems/cluster/preview')
    async def cluster_preview(form:Payload):
        state=services();id=uid('inspect_');row=state.store.put('cluster_preview',id,dict(id=id,status='running',created_at=time.time()))
        async def inspect():
            try:
                plan=await state.private_cluster.preview(form.value['devices'],form.value['model'],form.value.get('tensor_parallel',1))
                state.store.patch('cluster_preview',id,dict(status='completed',placement=plan))
            except asyncio.CancelledError:state.store.patch('cluster_preview',id,dict(status='cancelled'));raise
            except Exception as error:state.store.patch('cluster_preview',id,dict(status='failed',error=str(error) if isinstance(error,RouteError) else 'Private cluster inspection failed'))
        state.feature_tasks[id]=asyncio.create_task(inspect());return row

    @router.post('/systems/cluster/deploy')
    async def cluster_deploy(form:Payload):
        state=services();id=uid('cluster_');row=state.store.put('private_cluster',id,dict(id=id,status='ready',created_at=time.time()))
        state.feature_tasks[id]=asyncio.create_task(state.private_cluster.deploy(id,form.value['placement_id'],form.value['review_digest']));return row

    @router.post('/systems/cluster/{id}/stop')
    async def cluster_stop(id:str):
        state=services();task=state.feature_tasks.get(id)
        if task and not task.done():task.cancel();await asyncio.gather(task,return_exceptions=True)
        return state.store.get('private_cluster',id)

    app.include_router(router)
