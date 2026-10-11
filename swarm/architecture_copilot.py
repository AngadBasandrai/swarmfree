"""Reviewable offline graph design and optional schema-constrained model designs."""
import copy
import re
from .schema import Architecture
from .providers import RouteError
from .privacy import digest
from .store import uid


ROLES={"planner":"Plan and divide the work","researcher":"Gather and cite evidence","coder":"Produce reviewable code","critic":"Find gaps and unsupported conclusions","reviewer":"Review correctness and assumptions","tester":"Verify the work with permitted checks","writer":"Write the final deliverable","synthesizer":"Combine completed work"}
DESIGN_SCHEMA=dict(type="object",required=["name","nodes","edges"],properties={
    "name":dict(type="string",minLength=1,maxLength=80),"description":dict(type="string",maxLength=400),
    "nodes":dict(type="array",minItems=1,maxItems=16,items=dict(type="object",required=["id","name","role"],properties={"id":dict(type="string"),"name":dict(type="string"),"role":dict(type="string"),"instructions":dict(type="string"),"x":dict(type="number"),"y":dict(type="number")})),
    "edges":dict(type="array",maxItems=120,items=dict(type="object",required=["source","target"],properties={"source":dict(type="string"),"target":dict(type="string")}))})


def design(description,topology="auto"):
    text=description.casefold()
    matches=sorted((text.find(role),role) for role in ROLES if role in text)
    roles=[role for _,role in matches]
    if not roles: roles=["planner","researcher","reviewer","writer"]
    if topology=="auto": topology="parallel" if any(t in text for t in ("parallel","independent","simultaneous")) else "supervisor" if "supervisor" in text else "pipeline"
    if topology in {"parallel","supervisor"} and len(roles)>1 and roles[-1] not in {"writer","synthesizer"}: roles.append("synthesizer")
    nodes=[dict(id=role,name=role.capitalize(),role=ROLES[role],tools=[],x=80+(i%4)*230,y=80+(i//4)*160,
        instructions="Work as "+role+" on this owner specification:\n"+description[:4000]+"\nUse completed inputs and state unresolved issues.") for i,role in enumerate(roles)]
    edges=[]
    if topology=="pipeline": edges=[dict(source=a["id"],target=b["id"]) for a,b in zip(nodes,nodes[1:])]
    elif topology=="parallel": edges=[dict(source=n["id"],target=nodes[-1]["id"]) for n in nodes[:-1]]
    elif topology=="supervisor":
        edges=[dict(source=nodes[0]["id"],target=n["id"]) for n in nodes[1:]]
        edges += [dict(source=n["id"],target=nodes[-1]["id"]) for n in nodes[1:-1]]
    architecture=Architecture(id=uid("copilot_"),name="Designed "+topology+" team",description=description[:400],nodes=nodes,edges=edges).model_dump()
    return preview(architecture,"Offline role extraction and topology rules; edit the preview before saving")


def preview(data,note="Schema-constrained model design; tools require separate grants"):
    data=copy.deepcopy(data);data["id"]=data.get("id") or uid("copilot_");data["builtin"]=False
    for node in data.get("nodes",[]): node["tools"]=[]
    try: architecture=Architecture.model_validate(data).model_dump()
    except ValueError as error: raise RouteError("Design failed graph/schema validation") from error
    return dict(architecture=architecture,sha256=digest(architecture),note=note)
