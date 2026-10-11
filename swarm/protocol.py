"""Bounded JSON schemas and native model/tool wire adapters."""
import json
import re
from jsonschema import Draft202012Validator, SchemaError, ValidationError
from .providers import RouteError


def validate_schema(schema):
    if not isinstance(schema, dict) or len(json.dumps(schema)) > 20000:
        raise RouteError("Schema must be an object of at most 20 KB")
    def check(value, depth=0):
        if depth > 12: raise RouteError("Schema is too deeply nested")
        if isinstance(value, dict):
            if any(k in value for k in ("$ref", "$dynamicRef", "$recursiveRef", "pattern", "patternProperties")):
                raise RouteError("References and regular-expression schemas are excluded")
            for item in value.values(): check(item, depth+1)
        elif isinstance(value, list):
            for item in value: check(item, depth+1)
    check(schema)
    try: Draft202012Validator.check_schema(schema)
    except SchemaError as e: raise RouteError("Invalid JSON schema") from e
    return schema


def validate(value, schema):
    validate_schema(schema)
    try: Draft202012Validator(schema).validate(value)
    except ValidationError as e:
        location = ".".join(str(p) for p in e.absolute_path) or "root"
        raise RouteError("Schema mismatch at " + location + ": " + e.validator) from e
    return value


def argument_schema(example):
    properties = {}
    for name, value in example.items():
        properties[name] = dict(type="array", items=dict(type="string"), maxItems=10) if isinstance(value, list) else dict(type="string", maxLength=120000 if name == "content" else 24000)
    # Optional arguments are explicit; missing required fields fail before execution.
    optional = {"path", "query", "tags", "role", "options"}
    required = [k for k in properties if k not in optional]
    if "path" in properties and len(properties) == 1: required = ["path"]
    if "query" in properties: required.append("query")
    if "content" in properties and "path" in properties: required.append("path")
    return dict(type="object", properties=properties, required=list(set(required)), additionalProperties=False)


def wire_name(name):
    return re.sub(r"[^a-zA-Z0-9_-]", "_", name)[:64]


def tool_definitions(workspace, run, agent):
    from .workspace import TOOLS
    output, mapping = [], {}
    for name in workspace.permissions(run, agent):
        schema = argument_schema(TOOLS[name]) if name in TOOLS else None
        if name.startswith("mcp."):
            for extension in workspace.store.all("extension"):
                for tool in extension.get("tools", []):
                    if name == "mcp."+extension["id"]+"."+tool["name"]: schema = tool.get("inputSchema", {})
        if schema is None: continue
        validate_schema(schema)
        alias = wire_name(name)
        if alias in mapping: raise RouteError("Native tool names collide; rename the extension")
        mapping[alias] = (name, schema)
        output.append(dict(type="function", function=dict(name=alias, description="Granted project tool: "+name, parameters=schema)))
    return output, mapping


def decode_calls(calls, mapping):
    if len(calls) > 8: raise RouteError("Model requested too many simultaneous tools")
    requests = []
    for call in calls:
        function = call.get("function", {})
        name = function.get("name", "")
        if name not in mapping: raise RouteError("Model requested an ungranted native tool")
        args = function.get("arguments", {})
        if isinstance(args, str):
            if len(args) > 120000: raise RouteError("Native arguments exceed the limit")
            try: args = json.loads(args)
            except ValueError as e: raise RouteError("Malformed native tool arguments") from e
        tool, schema = mapping[name]
        validate(args, schema)
        requests.append(dict(tool=tool, arguments=args, call_id=str(call.get("id", ""))[:100]))
    return requests


def native_history(messages, *, ollama=False):
    """Use native roles only for complete call/result groups in the retained window."""
    output=[]
    answered={m.get("tool_call_id") for m in messages if m.get("tool_call_id")}
    accepted=set()
    for message in messages:
        clean={k:v for k,v in message.items() if k in {"role","content","images","mime"}}
        calls=message.get("native_calls",[])
        if calls and all(c.get("id") in answered for c in calls):
            clean["tool_calls"]=[]
            for call in calls:
                clone=dict(call,function=dict(call["function"]))
                args=clone["function"].get("arguments",{})
                if ollama and isinstance(args,str): clone["function"]["arguments"]=json.loads(args)
                if not ollama and not isinstance(args,str): clone["function"]["arguments"]=json.dumps(args)
                if ollama: clone.pop("id",None);clone.pop("type",None)
                else: clone["type"]="function"
                clean["tool_calls"].append(clone);accepted.add(call["id"])
            clean["content"]=""
        if message.get("tool_call_id") in accepted:
            clean["role"]="tool"
            if ollama: clean["tool_name"]=wire_name(message.get("tool_name",""))
            else: clean["tool_call_id"]=message["tool_call_id"]
        output.append(clean)
    return output


def usage_counts(data, *, ollama=False):
    source = data if ollama else data.get("usage", {})
    if not isinstance(source, dict): return {}
    names = ("prompt_eval_count", "eval_count") if ollama else ("prompt_tokens", "completion_tokens")
    values = [source.get(n) for n in names]
    if any(isinstance(v, bool) or not isinstance(v, int) or v < 0 or v > 100000000 for v in values): return {}
    cached = source.get("prompt_eval_cached_count", 0) if ollama else (source.get("prompt_tokens_details") or {}).get("cached_tokens", 0)
    if not isinstance(cached, int) or cached < 0: cached = 0
    return dict(input_tokens=values[0], output_tokens=values[1], cache_tokens=min(cached, values[0]),
                source="ollama" if ollama else "provider", reported=True)
