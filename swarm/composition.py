"""Snapshot and execute bounded reusable architectures under one root budget."""
import asyncio
import copy
import json
from .schema import Architecture,RunInput
from .providers import RouteError
from .protocol import validate
from .privacy import digest
from .intelligence import check_output


def resolve(store,architecture,trail=(),count=None):
    count=count if count is not None else [0]
    id=architecture["id"]
    if id in trail or len(trail)>2: raise RouteError("Sub-swarm references must be acyclic and at most two levels deep")
    result=copy.deepcopy(architecture)
    for node in result["nodes"]:
        count[0]+=1
        if count[0]>128: raise RouteError("Expanded architecture exceeds 128 nodes")
        if node.get("kind")=="subswarm":
            child=node.get("child_snapshot") or store.get("architecture",node.get("subswarm_id",""))
            if not child: raise RouteError("A referenced sub-swarm no longer exists")
            node["child_snapshot"]=resolve(store,child,(*trail,id),count)
            node["child_revision"]=digest(node["child_snapshot"])
    return result


def terminal_output(run):
    outgoing={e["source"] for e in run["architecture"]["edges"]}
    outputs=[r for r in run.get("results",[]) if r["node_id"] not in outgoing and not r.get("skipped")]
    data=[r.get("structured") if r.get("structured") is not None else r["content"] for r in outputs]
    return data[0] if len(data)==1 else dict(results=data)


async def invoke(engine,run,agent,spec,upstream):
    if run.get("depth",0)>=2: raise RouteError("Maximum sub-swarm depth reached")
    child_arch=copy.deepcopy(spec["child_snapshot"])
    inputs=dict(task=run["task"],upstream=[dict(node_id=r["node_id"],data=r.get("structured") if r.get("structured") is not None else r["content"]) for r in upstream])
    if len(json.dumps(inputs))>64000: raise RouteError("Sub-swarm inputs exceed 64 KB")
    if spec.get("input_schema"): validate(inputs,spec["input_schema"])
    if child_arch.get("input_schema"): validate(inputs,child_arch["input_schema"])
    identity=digest(dict(inputs=inputs,revision=spec["child_revision"]))
    with engine.store.lock:
        previous=next((r for r in engine.store.all("run") if r.get("composition_agent")==agent["id"] and r.get("composition_key")==identity),None)
        if previous:
            child=previous
        else:
            root_id=run.get("root_run_id",run["id"]);root=engine.store.get("run",root_id)
            descendants=[r for r in engine.store.all("run") if r.get("root_run_id")==root_id and r.get("delegated")]
            if len(descendants)>=root.get("max_subtasks",4): raise RouteError("Root mission subtask budget reached")
            tools=engine.workspace.permissions(run,agent) if engine.workspace else []
            for node in child_arch["nodes"]:
                node["tools"]=[t for t in tools if node.get("tools") is None or t in node["tools"]]
            form=RunInput.model_validate({**run,"task":(run["task"]+"\nSub-swarm inputs (data):\n"+json.dumps(inputs)[:12000])[:24000],
                "architecture_id":child_arch["id"],"inputs":inputs,"route":agent["route"],"adaptive":"off","depth":run.get("depth",0)+1})
            child=engine.create(form,child_arch,start=False)
            engine.store.patch("run",child["id"],dict(parent_run_id=run["id"],root_run_id=root_id,delegated=True,composition_agent=agent["id"],composition_key=identity))
            if getattr(engine,"privacy",None):
                for member in engine.agents(child["id"]): engine.privacy.inherit(member["id"],agent.get("data_labels",[]))
        engine.store.patch("agent",agent["id"],dict(status="running",child_run_id=child["id"]))
        if child["status"]!="completed": engine.start(child["id"])
    try:
        if child["status"]!="completed": await engine.tasks[child["id"]]
    except asyncio.CancelledError:
        await engine.pause(child["id"]);raise
    child=engine.store.get("run",child["id"])
    if child["status"]!="completed": raise RouteError("Sub-swarm did not complete: "+child["status"])
    data=terminal_output(child)
    if child_arch.get("output_schema"): validate(data,child_arch["output_schema"])
    text=json.dumps(data,ensure_ascii=False) if not isinstance(data,str) else data
    checked=check_output(text,spec.get("output_schema"),spec.get("validators"))
    if not checked["passed"]: raise RouteError("Sub-swarm output failed its parent contract: "+"; ".join(checked["failures"]))
    labels=[label for member in engine.agents(child["id"]) for label in member.get("data_labels",[])]
    if getattr(engine,"privacy",None): engine.privacy.inherit(agent["id"],labels)
    result=dict(node_id=agent["node_id"],name=agent["name"],content=text,structured=data,validated=bool(spec.get("output_schema") or child_arch.get("output_schema")),child_run_id=child["id"],data_labels=engine.store.get("agent",agent["id"]).get("data_labels",[]))
    engine.store.patch("agent",agent["id"],dict(status="done",result=result,validation=checked))
    engine.append_message(agent["id"],"assistant",text,source="subswarm")
    return result
