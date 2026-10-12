"""Snapshot and execute bounded reusable architectures under one root budget."""
import asyncio
import copy
import json
from .schema import Architecture,RunInput
from .providers import RouteError
from .protocol import validate
from .privacy import digest
from .intelligence import check_output


MAX_DEPTH = 4
MAX_EXPANDED_NODES = 128


def resolve(store, architecture):
    """Freeze a finite call tree, including self calls, before creating a run.

    Snapshots contain no circular JSON references. Children and resumed/branched
    runs keep these exact revisions even if a saved architecture is later edited.
    Only an explicit recursive node may close a reference cycle.
    """
    count = [0]
    sources = {architecture["id"]: architecture}

    def expand(source, trail=(), depth=0, pinned=False):
        if depth > MAX_DEPTH:
            raise RouteError("Nested teams support at most four levels")
        result = copy.deepcopy(source)
        pinned = pinned or source.get("composition_version") == 2
        for node in result["nodes"]:
            count[0] += 1
            if count[0] > MAX_EXPANDED_NODES:
                raise RouteError("Expanded architecture exceeds 128 nodes; reduce branching or depth")
            kind = node.get("kind")
            if kind not in {"subswarm", "recursive"}:
                continue
            if pinned:
                if kind == "recursive" and node.get("recursion_leaf"):
                    continue
                child = node.get("child_snapshot")
                if not child:
                    raise RouteError("A frozen child architecture is missing")
            else:
                target = node.get("subswarm_id") or ("self" if kind == "recursive" else "")
                target = source["id"] if target == "self" else target
                child = node.get("child_snapshot") or sources.get(target) or store.get("architecture", target)
                if not child:
                    raise RouteError("A referenced sub-swarm no longer exists: " + target)
                sources.setdefault(child["id"], child)
                if kind == "recursive" and depth >= node.get("max_depth", 2):
                    node["recursion_leaf"] = True
                    node.pop("child_snapshot", None)
                    node.pop("child_revision", None)
                    continue
                if kind == "subswarm" and child["id"] in (*trail, source["id"]):
                    raise RouteError("Circular team references need an explicit recursive node with a depth limit")
            node["child_snapshot"] = expand(child, (*trail, source["id"]), depth + 1, pinned)
            node["child_revision"] = digest(node["child_snapshot"])
        result["composition_version"] = 2
        return result

    return expand(architecture)


def base_case(run, spec, upstream):
    """Return a visible reason to execute the recursive node as a model leaf."""
    if spec.get("recursion_leaf") or run.get("depth", 0) >= spec.get("max_depth", 2):
        return "depth limit"
    marker = spec.get("recursion_stop_contains", "")
    source = spec.get("task_source")
    relevant = [r for r in upstream if not source or r["node_id"] == source]
    if marker and any(marker.casefold() in r["content"].casefold() for r in relevant):
        return "stop condition"
    return ""


def preview(store, architecture):
    snapshot = resolve(store, architecture)
    stats = dict(expanded_nodes=0, child_calls=0, model_calls=0, max_depth=0, worst_model_calls=0)
    def visit(team, depth=0, rounds=1):
        rounds *= team.get("max_rounds", 1)
        stats["max_depth"] = max(stats["max_depth"], depth)
        for node in team["nodes"]:
            stats["expanded_nodes"] += 1
            if node.get("child_snapshot"):
                stats["child_calls"] += rounds
                visit(node["child_snapshot"], depth+1, rounds)
            elif node.get("kind", "agent") != "vote":
                stats["model_calls"] += 1
                stats["worst_model_calls"] += rounds
    visit(snapshot)
    return dict(**stats, revision=digest(snapshot), note="Upper bounds before conditional skips, early stops, retries and tool calls. All child runs share the root mission limits.")


def terminal_output(run):
    outgoing={e["source"] for e in run["architecture"]["edges"]}
    outputs=[r for r in run.get("results",[]) if r["node_id"] not in outgoing and not r.get("skipped")]
    data=[r.get("structured") if r.get("structured") is not None else r["content"] for r in outputs]
    return data[0] if len(data)==1 else dict(results=data)


async def invoke(engine,run,agent,spec,upstream):
    if run.get("depth",0)>=MAX_DEPTH: raise RouteError("Maximum nested team depth reached")
    child_arch=copy.deepcopy(spec["child_snapshot"])
    task=run.get("composition_task",run["task"])
    if spec.get("task_source"):
        selected=next((r for r in upstream if r["node_id"]==spec["task_source"] and not r.get("skipped")),None)
        if not selected: raise RouteError("The child task source did not produce work")
        data=selected.get("structured") if selected.get("structured") is not None else selected["content"]
        task=data.get("task") if isinstance(data,dict) and isinstance(data.get("task"),str) else data if isinstance(data,str) else json.dumps(data,ensure_ascii=False)
        if not task.strip() or len(task)>12000: raise RouteError("The child task must contain 1 to 12,000 characters")
    inputs=dict(task=task,upstream=[dict(node_id=r["node_id"],data=r.get("structured") if r.get("structured") is not None else r["content"]) for r in upstream])
    if len(json.dumps(inputs).encode())>64000: raise RouteError("Sub-swarm inputs exceed 64 KB")
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
            form=RunInput.model_validate({**run,"task":(task+"\nParent team findings (data):\n"+json.dumps(inputs)[:12000])[:24000],
                "architecture_id":child_arch["id"],"inputs":inputs,"route":agent["route"],"adaptive":"off","depth":run.get("depth",0)+1})
            child=engine.create(form,child_arch,start=False)
            engine.store.patch("run",child["id"],dict(parent_run_id=run["id"],root_run_id=root_id,delegated=True,composition_agent=agent["id"],composition_key=identity,composition_task=task))
            if getattr(engine,"privacy",None):
                for member in engine.agents(child["id"]): engine.privacy.inherit(member["id"],agent.get("data_labels",[]))
        engine.store.patch("agent",agent["id"],dict(status="running",child_run_id=child["id"]))
        engine.store.event(run["id"],"team_call",agent_id=agent["id"],message=f"{agent['name']} → {child_arch['name']} · depth {child['depth']} · {child['id']}")
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
