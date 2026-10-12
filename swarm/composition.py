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

    def expand(source, trail=(), depth=0, pinned=False, multiplier=1):
        if depth > MAX_DEPTH:
            raise RouteError("Nested teams support at most four levels")
        result = copy.deepcopy(source)
        pinned = pinned or source.get("composition_version") == 2
        for node in result["nodes"]:
            count[0] += multiplier
            if count[0] > MAX_EXPANDED_NODES:
                raise RouteError("Expanded architecture exceeds 128 nodes; reduce branching or depth")
            kind = node.get("kind")
            if kind not in {"subswarm", "recursive", "fanout"}:
                continue
            if pinned:
                if kind in {"recursive","fanout"} and node.get("recursion_leaf"):
                    continue
                child = node.get("child_snapshot")
                if not child:
                    raise RouteError("A frozen child architecture is missing")
            else:
                target = node.get("subswarm_id") or ("self" if kind in {"recursive","fanout"} else "")
                target = source["id"] if target == "self" and kind in {"recursive","fanout"} else target
                child = node.get("child_snapshot") or sources.get(target) or store.get("architecture", target)
                if not child:
                    raise RouteError("A referenced sub-swarm no longer exists: " + target)
                sources.setdefault(child["id"], child)
                if kind in {"recursive","fanout"} and depth >= node.get("max_depth", 2):
                    node["recursion_leaf"] = True
                    node.pop("child_snapshot", None)
                    node.pop("child_revision", None)
                    continue
                if kind == "subswarm" and child["id"] in (*trail, source["id"]):
                    raise RouteError("Circular team references need an explicit recursive node with a depth limit")
            width=node.get('max_fanout',2) if kind=='fanout' else 1
            node["child_snapshot"] = expand(child, (*trail, source["id"]), depth + 1, pinned, multiplier*width)
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
    def visit(team, depth=0, rounds=1, multiplier=1):
        rounds *= team.get("max_rounds", 1)
        stats["max_depth"] = max(stats["max_depth"], depth)
        for node in team["nodes"]:
            stats["expanded_nodes"] += multiplier
            if node.get("child_snapshot"):
                width=node.get('max_fanout',2) if node.get('kind')=='fanout' else 1
                stats["child_calls"] += rounds*multiplier*width
                visit(node["child_snapshot"], depth+1, rounds,multiplier*width)
            elif node.get("kind", "agent") not in {"vote","join","condition"}:
                stats["model_calls"] += multiplier
                stats["worst_model_calls"] += rounds*multiplier
    visit(snapshot)
    return dict(**stats, revision=digest(snapshot), note="Upper bounds before conditional skips, early stops, retries and tool calls. All child runs share the root mission limits.")


def terminal_output(run):
    if run['architecture'].get('output_ports'):
        from .dataflow import select,result_data
        values={}
        for port in run['architecture']['output_ports']:
            source=next((r for r in run.get('results',[]) if r['node_id']==port['source']),None)
            if source is None: raise RouteError('A named output has no completed source')
            values[port['name']]=select(result_data(source),port.get('path',''))
        return values
    outgoing={e["source"] for e in run["architecture"]["edges"]}
    outputs=[r for r in run.get("results",[]) if r["node_id"] not in outgoing and not r.get("skipped")]
    data=[r.get("structured") if r.get("structured") is not None else r["content"] for r in outputs]
    return data[0] if len(data)==1 else dict(results=data)


from .composition_calls import invoke
