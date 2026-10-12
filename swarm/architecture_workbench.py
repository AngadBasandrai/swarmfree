"""Architecture revisions, reusable agents/components and scoped co-editing."""
import copy
import time
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from .schema import Architecture, AgentSpec
from .composition import resolve
from .privacy import digest
from .providers import RouteError
from .store import uid


def normalized(architecture):
    value=copy.deepcopy(architecture)
    value['nodes']={n['id']:n for n in value['nodes']}
    value['edges']={e['source']+'->'+e['target']:e for e in value['edges']}
    return value


def denormalized(value):
    value=copy.deepcopy(value)
    value['nodes']=list(value['nodes'].values());value['edges']=list(value['edges'].values())
    return value


MISSING=object()
def changes(before,after,path=()):
    if isinstance(before,dict) and isinstance(after,dict):
        result=[]
        for key in sorted(set(before)|set(after)):
            if key not in after: result.append(((*path,key),MISSING))
            elif key not in before: result.append(((*path,key),copy.deepcopy(after[key])))
            else: result.extend(changes(before[key],after[key],(*path,key)))
        return result
    return [] if before==after else [(path,copy.deepcopy(after))]


def apply_changes(value,patch):
    result=copy.deepcopy(value)
    for path,data in patch:
        cursor=result
        for key in path[:-1]: cursor=cursor[key]
        if data is MISSING: cursor.pop(path[-1],None)
        else: cursor[path[-1]]=copy.deepcopy(data)
    return result


class Workshop:
    def __init__(self,store): self.store=store

    def save(self,data,actor='owner'):
        value=Architecture.model_validate(data).model_dump()
        resolve(self.store,value)
        from .experiments import require_gate
        require_gate(self.store,value)
        with self.store.transaction():
            old=self.store.get('architecture',value['id'])
            if old and old.get('builtin'): raise RouteError("Save a custom copy of a built-in architecture")
            current=old.get('revision') or 0 if old else 0
            if value.get('revision') is not None and value['revision']!=current:
                raise HTTPException(409,"Architecture changed; reload its current revision before saving")
            if old and not self.store.get('architecture_revision',value['id']+':'+str(current)):
                self.record(old,current,'migration')
            value.update(builtin=False,revision=current+1)
            self.store.put('architecture',value['id'],value)
            self.record(value,current+1,actor)
        return value

    def record(self,value,number,actor):
        row=dict(id=value['id']+':'+str(number),architecture_id=value['id'],number=number,
            actor=actor,created_at=time.time(),digest=digest(value),architecture=copy.deepcopy(value))
        self.store.put('architecture_revision',row['id'],row)

    def versions(self,id):
        return sorted((r for r in self.store.all('architecture_revision') if r['architecture_id']==id),key=lambda r:r['number'],reverse=True)

    def restore(self,id,number,expected):
        record=self.store.get('architecture_revision',id+':'+str(number))
        if not record: raise HTTPException(404,"Architecture revision not found")
        return self.save({**record['architecture'],'revision':expected})

    def diff(self,id,left,right):
        rows=[self.store.get('architecture_revision',id+':'+str(n)) for n in (left,right)]
        if not all(rows): raise HTTPException(404,"Architecture revision not found")
        patch=changes(normalized(rows[0]['architecture']),normalized(rows[1]['architecture']))
        return [dict(path=list(path),deleted=data is MISSING,value=None if data is MISSING else data) for path,data in patch]

    def create_session(self,project_id,architecture):
        if not self.store.get('project',project_id): raise RouteError("Choose a project to share this design")
        value=Architecture.model_validate(architecture).model_dump()
        resolve(self.store,value)
        source=self.store.get('architecture',value['id'])
        row=dict(id=uid('design_'),project_id=project_id,architecture_id=source['id'] if source else '',
            source_revision=(source.get('revision') or 0) if source else 0,revision=0,draft=value,created_at=time.time(),updated_at=time.time())
        with self.store.transaction():
            self.store.put('architecture_session',row['id'],row)
            self.store.put('architecture_draft',row['id']+':0',dict(id=row['id']+':0',draft=value))
        return row

    def session(self,id):
        row=self.store.get('architecture_session',id)
        if not row: raise HTTPException(404,"Shared design not found")
        return row

    def view(self,id,actor='owner',name='Owner'):
        row=self.session(id)
        self.store.put('architecture_presence',id+':'+actor,dict(id=id+':'+actor,session_id=id,actor=actor,name=name,seen_at=time.time()))
        presence=[p for p in self.store.all('architecture_presence') if p['session_id']==id and p['seen_at']>time.time()-15]
        history=[h for h in self.store.all('architecture_edit') if h['session_id']==id][-30:]
        return dict(**row,presence=presence,edits=history)

    def edit(self,id,base_revision,architecture,actor='owner',name='Owner'):
        candidate=Architecture.model_validate(architecture).model_dump()
        with self.store.transaction():
            row=self.session(id)
            base=self.store.get('architecture_draft',id+':'+str(base_revision))
            if not base: raise HTTPException(409,"Draft revision expired; reload the shared design")
            if candidate['id']!=row['draft']['id']: raise RouteError("A shared design keeps its architecture ID")
            local=changes(normalized(base['draft']),normalized(candidate))
            remote=changes(normalized(base['draft']),normalized(row['draft']))
            conflicts=[list(a) for a,_ in local for b,_ in remote if a[:len(b)]==b or b[:len(a)]==a]
            if conflicts: raise HTTPException(409,dict(message="Conflicting edits; reload or preserve your draft for manual merge",paths=conflicts,revision=row['revision']))
            merged=Architecture.model_validate(denormalized(apply_changes(normalized(row['draft']),local))).model_dump()
            resolve(self.store,merged)
            if local:
                revision=row['revision']+1
                if revision>1000: raise RouteError("This design reached 1,000 edits; publish it and start another session")
                row=self.store.patch('architecture_session',id,dict(revision=revision,draft=merged,updated_at=time.time()))
                self.store.put('architecture_draft',id+':'+str(revision),dict(id=id+':'+str(revision),draft=merged))
                edit=dict(id=uid('edit_'),session_id=id,revision=revision,actor=actor,paths=[list(p) for p,_ in local],created_at=time.time())
                self.store.put('architecture_edit',edit['id'],edit)
        return self.view(id,actor,name)

    def publish(self,id,revision):
        with self.store.transaction():
            row=self.session(id)
            if row['revision']!=revision: raise HTTPException(409,"Shared draft changed; review the newest revision before publishing")
            value=copy.deepcopy(row['draft']);source=self.store.get('architecture',row['architecture_id'])
            if not source or source.get('builtin'):
                value.update(id=uid('custom_'),builtin=False,revision=0)
            else: value['revision']=row['source_revision']
            saved=self.save(value)
            self.store.patch('architecture_session',id,dict(architecture_id=saved['id'],source_revision=saved['revision'],published_revision=revision))
        return saved

    def extract(self,architecture,node_ids,name):
        with self.store.transaction():
            return self._extract(architecture,node_ids,name)

    def _extract(self,architecture,node_ids,name):
        value=Architecture.model_validate(architecture).model_dump();chosen=set(node_ids)
        if not chosen or not chosen<={n['id'] for n in value['nodes']}: raise RouteError("Select existing nodes for the component")
        nodes=[copy.deepcopy(n) for n in value['nodes'] if n['id'] in chosen]
        if any((n.get('task_source') and n['task_source'] not in chosen) or (n.get('run_if') and n['run_if']['source'] not in chosen) for n in nodes):
            raise RouteError("Include the external task/condition source in the selection before extracting this component")
        component_id=uid('component_');node_id=uid('block_')
        internal=[e for e in value['edges'] if e['source'] in chosen and e['target'] in chosen]
        outputs={e['source'] for e in value['edges'] if e['source'] in chosen and e['target'] not in chosen}
        outputs|={n['id'] for n in nodes if not any(e['source']==n['id'] for e in internal)}
        ports=[dict(name=id,source=id) for id in sorted(outputs)]
        incoming={};outgoing={};input_properties={'task':{'type':'string'},'upstream':{'type':'array'}}
        for edge in value['edges']:
            if edge['source'] not in chosen and edge['target'] in chosen:
                bindings=[]
                for b in edge['bindings']:
                    name_key='input_'+str(len(input_properties))
                    bindings.append({**b,'target_name':name_key});input_properties[name_key]={}
                    next(n for n in nodes if n['id']==edge['target'])['inputs_from_mission'][b['target_name']]=name_key
                incoming.setdefault(edge['source'],[]).extend(bindings)
            elif edge['source'] in chosen and edge['target'] not in chosen:
                bindings=[{**b,'source_path':edge['source']+('.'+b['source_path'] if b['source_path'] else '')} for b in edge['bindings']]
                outgoing.setdefault(edge['target'],[]).extend(bindings)
        component=Architecture(id=component_id,name=name,nodes=nodes,edges=internal,output_ports=ports,
            parameters=value.get('parameters',{}),input_schema=dict(type='object',properties=input_properties,required=['task','upstream'])).model_dump()
        self.save(component)
        block=AgentSpec(id=node_id,name=name,role='Run the reusable component',kind='subswarm',subswarm_id=component_id,
            x=min(n['x'] for n in nodes),y=min(n['y'] for n in nodes),input_schema=component['input_schema']).model_dump()
        remaining=[copy.deepcopy(n) for n in value['nodes'] if n['id'] not in chosen]
        for n in remaining:
            if n.get('task_source') in chosen:
                original=n['task_source'];n['task_source']=node_id;n['task_path']=original+('.'+n['task_path'] if n.get('task_path') else '')
            if n.get('run_if',{}).get('source') in chosen:
                old=n['run_if']['source'];n['run_if'].update(source=node_id,path=old+('.'+n['run_if']['path'] if n['run_if'].get('path') else ''))
        edges=[e for e in value['edges'] if e['source'] not in chosen and e['target'] not in chosen]
        edges += [dict(source=source,target=node_id,bindings=b) for source,b in incoming.items()]
        edges += [dict(source=node_id,target=target,bindings=b) for target,b in outgoing.items()]
        parent=Architecture.model_validate({**value,'nodes':[*remaining,block],'edges':edges,
            'channels':[c for c in value['channels'] if not chosen.intersection(c['members'])],
            'output_ports':[{**p,'source':node_id,'path':p['source']+('.'+p['path'] if p['path'] else '')} if p['source'] in chosen else p for p in value['output_ports']]}).model_dump()
        resolve(self.store,parent)
        return dict(component=self.store.get('architecture',component_id),architecture=parent,node_id=node_id)


class RestoreInput(BaseModel):
    number:int=Field(ge=0)
    expected_revision:int=Field(ge=0)
class ExtractInput(BaseModel):
    architecture:Architecture
    node_ids:list[str]=Field(min_length=1,max_length=16)
    name:str=Field(min_length=1,max_length=80)
class SessionInput(BaseModel):
    project_id:str
    architecture:Architecture
class EditInput(BaseModel):
    base_revision:int=Field(ge=0)
    architecture:Architecture
class PublishInput(BaseModel):
    revision:int=Field(ge=0)
class LibraryInput(BaseModel):
    spec:AgentSpec
    name:str=Field(min_length=1,max_length=80)


def install(app):
    router=APIRouter(prefix='/api/architecture-workbench')
    @router.get('')
    async def status():
        s=app.state.store
        return dict(agents=s.all('agent_blueprint'),sessions=s.all('architecture_session'))
    @router.get('/versions/{id}')
    async def versions(id:str): return app.state.architectures.versions(id)
    @router.get('/versions/{id}/diff')
    async def diff(id:str,left:int,right:int): return app.state.architectures.diff(id,left,right)
    @router.post('/versions/{id}/restore')
    async def restore(id:str,form:RestoreInput): return app.state.architectures.restore(id,form.number,form.expected_revision)
    @router.post('/extract')
    async def extract(form:ExtractInput): return app.state.architectures.extract(form.architecture.model_dump(),form.node_ids,form.name)
    @router.post('/agents')
    async def library(form:LibraryInput):
        row=dict(id=uid('blueprint_'),name=form.name,spec=form.spec.model_dump(),created_at=time.time())
        return app.state.store.put('agent_blueprint',row['id'],row)
    @router.delete('/agents/{id}')
    async def delete_library(id:str): app.state.store.delete('agent_blueprint',id);return dict(deleted=True)
    @router.post('/sessions')
    async def session_create(form:SessionInput): return app.state.architectures.create_session(form.project_id,form.architecture.model_dump())
    @router.get('/sessions/{id}')
    async def session_view(id:str): return app.state.architectures.view(id)
    @router.post('/sessions/{id}/edits')
    async def session_edit(id:str,form:EditInput): return app.state.architectures.edit(id,form.base_revision,form.architecture.model_dump())
    @router.post('/sessions/{id}/publish')
    async def session_publish(id:str,form:PublishInput): return app.state.architectures.publish(id,form.revision)
    app.include_router(router)

    team=APIRouter(prefix='/api/team/architecture-sessions')
    def authorize(request,id):
        actor=app.state.team.actor(request);row=app.state.architectures.session(id)
        app.state.team.project(actor,row['project_id'],{'architect'})
        return actor
    @team.get('')
    async def shared_list(request:Request):
        actor=app.state.team.actor(request)
        if actor['role']!='architect': raise HTTPException(403,"An architect credential is required")
        return [r for r in app.state.store.all('architecture_session') if r['project_id'] in actor['project_ids']]
    @team.get('/{id}')
    async def shared_view(id:str,request:Request):
        actor=authorize(request,id);return app.state.architectures.view(id,actor['id'],actor['name'])
    @team.post('/{id}/edits')
    async def shared_edit(id:str,request:Request,form:EditInput):
        actor=authorize(request,id)
        result=app.state.architectures.edit(id,form.base_revision,form.architecture.model_dump(),actor['id'],actor['name'])
        app.state.team.audit(actor['id'],'architecture.edit',id);return result
    app.include_router(team)
