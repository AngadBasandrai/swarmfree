"""Inference-free briefs, graph diagnostics, explicit outcome edges and provenance assembly."""
import copy
import difflib
import re
import time
from .schema import Architecture, MissionBrief
from .dataflow import result_data, select, bounded_json
from .providers import RouteError
from .privacy import digest
from .store import uid


def brief_text(brief):
    value=MissionBrief.model_validate(brief).model_dump()
    if not any(value.values()):return ''
    return '\n\nApproved mission brief (constraints describe intent; tool grants are enforced separately):\n'+bounded_json(value)


def outcome_matches(edge,result):
    return not result.get('skipped') and (edge.get('on','success')=='always' or bool(result.get('failed'))==(edge.get('on','success')=='failure'))


def projected_schema(schema,path):
    current=schema
    for part in path.split('.') if path else []:
        if current.get('type')=='array' and part.isdecimal():current=current.get('items',{})
        elif part in current.get('properties',{}):current=current['properties'][part]
        else:return None if current.get('additionalProperties') is False else {}
    return current


def lint(store,value,project_id=''):
    value=Architecture.model_validate(value).model_dump();issues=[];nodes={n['id']:n for n in value['nodes']}
    def add(code,message,node=None,severity='warning',fix=None):issues.append(dict(code=code,message=message,node_id=node,severity=severity,fix=fix))
    names=set(value.get('parameters',{}).get('properties',{}))
    project=store.get('project',project_id,{}) if project_id else None
    for node in nodes.values():
        for name in set(re.findall(r'\{\{([A-Za-z][A-Za-z0-9_]*)\}\}',node['instructions']))-names:
            add('missing_parameter',f"{node['name']} uses an undeclared parameter: {name}",node['id'],'error',dict(kind='parameter',name=name))
        incoming=[e for e in value['edges'] if e['target']==node['id']]
        mapped={b['target_name'] for e in incoming for b in e['bindings']}|set(node['inputs_from_mission'])
        if node['input_schema'] and node['kind'] not in {'subswarm','recursive','fanout'}:
            for name in set(node['input_schema'].get('required',[]))-mapped:add('missing_input',f"{node['name']} requires an unconnected input: {name}",node['id'],'error')
        for edge in incoming:
            source_schema=nodes[edge['source']]['output_schema']
            if edge.get('on')=='failure':source_schema=dict(type='object',properties=dict(error=dict(type='string'),failed_node=dict(type='string')),additionalProperties=False)
            elif edge.get('on')=='always':source_schema={}
            for binding in edge['bindings']:
                if not source_schema:continue
                schema=projected_schema(source_schema,binding['source_path'])
                target=node['input_schema'].get('properties',{}).get(binding['target_name'],{})
                if schema is None:add('missing_port',f"Output field {binding['source_path']} is absent from {edge['source']}'s closed schema",node['id'],'error')
                elif schema.get('type') and target.get('type') and schema['type']!=target['type'] and not (schema['type']=='integer' and target['type']=='number'):
                    add('port_type',f"{edge['source']} → {node['name']}: {schema['type']} cannot fill {target['type']} input {binding['target_name']}",node['id'],'error')
        if not incoming and node['run_if']:add('unreachable_condition','This conditioned node has no incoming edge',node['id'],'error')
        if node['kind'] not in {'join','condition','vote'} and node['request_budget']==0:add('zero_budget',f"{node['name']} admits no model requests",node['id'])
        if node['kind']=='router' and not node['output_schema']:add('router_contract','Declare a JSON decision schema for this router',node['id'],fix=dict(kind='router_schema',node_id=node['id']))
        if project is not None and node['tools'] is not None:
            for tool in set(node['tools'])-set(project.get('tools',[])):add('tool_grant',f"{tool} is unavailable in the selected project",node['id'],'error',dict(kind='remove_tool',node_id=node['id'],tool=tool))
        if any(e['source']==node['id'] and e['on']=='failure' for e in value['edges']) and not node['timeout_seconds']:
            add('repair_timeout','Set a stage timeout to bound this failure branch',node['id'],fix=dict(kind='timeout',node_id=node['id']))
    try:
        from .composition import preview,resolve
        estimate=preview(store,value)
    except RouteError as e:add('composition',str(e),severity='error');estimate=None
    return dict(issues=issues,errors=sum(i['severity']=='error' for i in issues),warnings=sum(i['severity']=='warning' for i in issues),estimate=estimate)


def fix_lint(value,fix):
    value=Architecture.model_validate(value).model_dump();kind=fix.get('kind')
    if kind=='parameter':
        value['parameters'].setdefault('type','object');value['parameters'].setdefault('properties',{})[fix['name']]={'type':'string','default':''}
    else:
        node=next((n for n in value['nodes'] if n['id']==fix.get('node_id')),None)
        if not node:raise RouteError('Quick fix references a missing node')
        if kind=='timeout':node['timeout_seconds']=120
        elif kind=='remove_tool':node['tools']=[t for t in node.get('tools') or [] if t!=fix['tool']]
        elif kind=='router_schema':node['output_schema']=dict(type='object',properties=dict(decision=dict(type='string')),required=['decision'],additionalProperties=False)
        else:raise RouteError('Unknown lint fix')
    return Architecture.model_validate(value).model_dump()


def compose(store,run_id,sections,decisions=None):
    run=store.get('run',run_id)
    if not run:raise RouteError('Mission not found')
    decisions=decisions or {};fields={};provenance={};conflicts=[];labels=[];modes={};atoms={};locations={};tables={}
    def contribute(key,location,data,agent,path):
        entry=dict(agent_id=agent['id'],node_id=agent['node_id'],path=path,digest=digest(data))
        if any(p['agent_id']==agent['id'] and p['path']!=path and p['digest']!=entry['digest'] for p in provenance.get(key,[])):raise RouteError('Use separate artifact fields for different values from the same contributor')
        provenance.setdefault(key,[]).append(entry);locations[key]=location
        if key not in atoms:atoms[key]=data
        elif atoms[key]!=data:conflicts.append(dict(field=key))
    if not 1<=len(sections)<=64:raise RouteError('Select 1–64 artifact fields')
    for item in sections:
        agent=store.get('agent',run_id+':'+item['node_id'])
        if not agent or agent['status']!='done' or not agent.get('result') or agent['result'].get('failed'):raise RouteError('Assembly uses successfully completed work only')
        key=item['name']
        if not isinstance(key,str) or not re.fullmatch(r'[A-Za-z][A-Za-z0-9_-]{0,95}',key):raise RouteError('Use a valid artifact field name')
        path=item.get('path','');data=select(result_data(agent['result']),path);mode=item.get('mode','field')
        if mode not in {'field','object','table'} or (key in modes and modes[key]!=mode):raise RouteError('Use one consistent merge mode for each artifact field')
        modes[key]=mode;labels.extend(agent.get('data_labels',[]))
        if mode=='field':contribute(key,(key,),data,agent,path)
        elif mode=='object':
            if not isinstance(data,dict):raise RouteError('Object merge needs a JSON object')
            fields.setdefault(key,{})
            for column,value in data.items():
                if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,95}',column):raise RouteError('Object merge uses selector-compatible field names')
                contribute(key+'.'+column,(key,column),value,agent,'.'.join(filter(None,[path,column])))
        else:
            row_key=item.get('row_key','id')
            if not isinstance(data,list) or len(data)>1000 or any(not isinstance(r,dict) for r in data):raise RouteError('Table merge needs at most 1,000 JSON object rows')
            tables.setdefault(key,{})
            for index,row in enumerate(data):
                identity=str(row.get(row_key,'')) if isinstance(row,dict) else ''
                if not re.fullmatch(r'[A-Za-z0-9_-]{1,100}',identity):raise RouteError('Table rows need a short unique string or integer identity')
                if identity in {str(r.get(row_key,'')) for r in data[:index]}:raise RouteError('Table row identities must be unique within each contributor')
                tables[key].setdefault(identity,{})
                for column,value in row.items():
                    if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_-]{0,95}',column):raise RouteError('Table columns use selector-compatible field names')
                    contribute(key+'.'+identity+'.'+column,(key,identity,column),value,agent,'.'.join(filter(None,[path,str(index),column])))
    conflicts=[dict(field=key,choices=[p['agent_id'] for p in provenance[key]]) for key in dict.fromkeys(c['field'] for c in conflicts)]
    for conflict in conflicts:
        key=conflict['field'];chosen=decisions.get(key)
        if chosen is not None:
            entry=next((p for p in provenance[key] if p['agent_id']==chosen),None)
            if not entry:raise RouteError('A conflict decision must select one of its contributors')
            atoms[key]=select(result_data(store.get('agent',chosen)['result']),entry['path'])
            conflict['resolved_by']=chosen
    for atom,value in atoms.items():
        location=locations[atom];key=location[0]
        if len(location)==1:fields[key]=value
        elif len(location)==2:fields.setdefault(key,{})[location[1]]=value
        else:tables[key][location[1]][location[2]]=value
    for key,rows in tables.items():fields[key]=list(rows.values())
    bounded_json(fields);row=dict(id=uid('assembly_'),run_id=run_id,fields=fields,provenance=provenance,conflicts=conflicts,
        ready=all('resolved_by' in c for c in conflicts),data_labels=list({digest(l):l for l in labels}.values()),created_at=time.time())
    return store.put('assembly',row['id'],row)


def compare_runs(store,left_id,right_id):
    runs=[store.get('run',id) for id in (left_id,right_id)]
    if not all(runs):raise RouteError('Select two existing missions')
    nodes=set(n['node_id'] for r in runs for n in r.get('results',[]));changes=[]
    for id in sorted(nodes):
        texts=[next((n['content'] for n in r.get('results',[]) if n['node_id']==id),'') for r in runs]
        if texts[0]!=texts[1]:changes.append(dict(node_id=id,diff='\n'.join(difflib.unified_diff(texts[0].splitlines(),texts[1].splitlines(),fromfile=left_id,tofile=right_id))[:30000]))
    return dict(left=left_id,right=right_id,changes=changes,statuses=[r['status'] for r in runs])
