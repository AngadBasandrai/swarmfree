"""Portable, inert swarm packages. Imports never enable tools or execute code."""
import hashlib
import json
import time
from pydantic import BaseModel,Field
from .schema import Architecture
from .providers import RouteError
from .store import uid


class Evaluation(BaseModel):
    task: str=Field(min_length=1,max_length=24000)
    rubric: str=Field(default="",max_length=4000)


class SwarmPackage(BaseModel):
    format: str=Field(default="swarm-workbench-package",pattern="^swarm-workbench-package$")
    format_version: int=Field(default=1,ge=1,le=1)
    name: str=Field(min_length=1,max_length=100)
    version: str=Field(default="1.0.0",pattern=r"^\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-zA-Z0-9.-]{1,40})?$")
    description: str=Field(default="",max_length=2000)
    architecture: Architecture
    requested_permissions: list[str]=Field(default_factory=list,max_length=40)
    evaluations: list[Evaluation]=Field(default_factory=list,max_length=20)

    model_config={"extra":"forbid"}


def preview(value):
    encoded=json.dumps(value)
    if len(encoded)>500000: raise RouteError("Swarm package exceeds 500 KiB")
    try: package=SwarmPackage.model_validate(value)
    except ValueError as error: raise RouteError("Invalid swarm package: "+str(error)[:1000]) from error
    permissions=set(package.requested_permissions)
    for node in package.architecture.nodes: permissions.update(node.tools or [])
    return dict(package=package.model_dump(),permissions=sorted(permissions),
        requirements=sorted({r for n in package.architecture.nodes for r in n.requires}),
        sha256=hashlib.sha256(encoded.encode()).hexdigest(),note="Import creates a new architecture. Project tool grants remain an owner decision")


def install(store,value):
    checked=preview(value)
    package=checked["package"]
    id=uid("package_")
    architecture=dict(package["architecture"],id=uid("imported_"),builtin=False)
    store.put("architecture",architecture["id"],architecture)
    return store.put("package",id,{**package,"id":id,"architecture":architecture,"permissions":checked["permissions"],"sha256":checked["sha256"],"installed_at":time.time()})
