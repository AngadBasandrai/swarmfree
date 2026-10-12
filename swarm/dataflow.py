"""Bounded selectors and parameter substitution; no expressions or evaluation."""
import copy
import json
import re
from .providers import RouteError
from .protocol import validate

PATH = re.compile(r"^(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+)(?:\.(?:[A-Za-z_][A-Za-z0-9_-]*|[0-9]+))*$")


def check_path(path):
    if path and (len(path)>300 or len(path.split('.'))>24 or not PATH.fullmatch(path)):
        raise ValueError("Use a field path such as sources.0.url; expressions are not supported")


def bounded_json(value,limit=64000):
    try: raw=json.dumps(value,ensure_ascii=False,allow_nan=False)
    except (ValueError,TypeError) as error: raise RouteError("Input data must be finite JSON") from error
    if len(raw.encode())>limit: raise RouteError("Input data exceeds its size allowance")
    return raw


def select(value,path):
    check_path(path)
    for segment in path.split('.') if path else []:
        if isinstance(value,dict) and segment in value: value=value[segment]
        elif isinstance(value,list) and segment.isdigit() and int(segment)<len(value): value=value[int(segment)]
        else: raise RouteError("Missing field in typed connection: "+path)
    return copy.deepcopy(value)


def result_data(result):
    if result.get('skipped'): raise RouteError("A required typed input was skipped: "+result['node_id'])
    if result.get('structured') is not None: return result['structured']
    try: return json.loads(result['content'].strip().removeprefix('```json').removesuffix('```').strip())
    except (ValueError,AttributeError): return result['content']


def inputs_for(run,spec,upstream):
    values={name:select(run.get('inputs',{}),path) for name,path in spec.get('inputs_from_mission',{}).items()}
    for edge in run['architecture']['edges']:
        if edge['target']!=spec['id']: continue
        for binding in edge.get('bindings',[]):
            source=next((r for r in upstream if r['node_id']==edge['source']),None)
            if source is None: raise RouteError("A required typed input has no upstream result")
            name=binding['target_name']
            if name in values: raise RouteError("Duplicate typed input: "+name)
            values[name]=select(result_data(source),binding.get('source_path',''))
    bounded_json(values)
    return values


def parameters(schema,values):
    result={name:copy.deepcopy(prop['default']) for name,prop in schema.get('properties',{}).items() if 'default' in prop}
    result.update(values);bounded_json(result)
    if schema: validate(result,schema)
    return result


def render(text,values):
    def replace(match):
        name=match.group(1)
        if name not in values: raise RouteError("Missing architecture parameter: "+name)
        value=values[name]
        return value if isinstance(value,str) else json.dumps(value,ensure_ascii=False)
    result=re.sub(r"\{\{([A-Za-z][A-Za-z0-9_]*)\}\}",replace,text)
    if len(result)>12000: raise RouteError("Expanded agent instructions exceed 12,000 characters")
    return result
