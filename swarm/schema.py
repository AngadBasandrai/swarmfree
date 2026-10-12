from typing import Literal
from pydantic import BaseModel, Field, model_validator


class AgentSpec(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=80)
    role: str = Field(min_length=1, max_length=160)
    instructions: str = Field(default="", max_length=12000)
    tools: list[str] | None = Field(default=None, max_length=40)
    requires: list[Literal["json", "vision"]] = Field(default_factory=list)
    kind: Literal["agent", "vote", "subswarm", "recursive", "fanout", "router", "condition", "join"] = "agent"
    subswarm_id: str = Field(default="",max_length=80)
    max_depth: int = Field(default=2, ge=1, le=4)
    task_source: str = Field(default="", max_length=48)
    task_path: str = Field(default="", max_length=300)
    tasks_path: str = Field(default="tasks", max_length=300)
    max_fanout: int = Field(default=2, ge=1, le=8)
    recursion_stop_contains: str = Field(default="", max_length=120)
    break_before: bool = False
    detect_repetition: bool = False
    request_budget: int | None = Field(default=None, ge=0, le=10000)
    token_budget: int | None = Field(default=None, ge=0, le=10000000)
    preferred_route: dict = Field(default_factory=dict)
    parameter_values: dict = Field(default_factory=dict)
    inputs_from_mission: dict[str, str] = Field(default_factory=dict)
    input_schema: dict = Field(default_factory=dict)
    message_budget: int = Field(default=8,ge=0,le=50)
    run_if: dict[str, str] = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    validators: list[dict] = Field(default_factory=list, max_length=10)
    x: float = Field(default=0, allow_inf_nan=False)
    y: float = Field(default=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def validate_contract(self):
        from .protocol import validate_schema
        if self.output_schema: validate_schema(self.output_schema)
        if self.input_schema: validate_schema(self.input_schema)
        if self.kind=="subswarm" and not self.subswarm_id: raise ValueError("Choose a sub-swarm architecture")
        if self.kind in {"recursive","fanout"} and not self.subswarm_id: self.subswarm_id="self"
        from .dataflow import check_path, bounded_json
        for path in [self.task_path,self.tasks_path,*self.inputs_from_mission.values()]: check_path(path)
        bounded_json(self.parameter_values)
        if self.preferred_route:
            if set(self.preferred_route)-set(Route.model_fields): raise ValueError("Model preferences contain only route IDs, never credentials")
            self.preferred_route=Route.model_validate(self.preferred_route).model_dump()
        for rule in self.validators:
            if rule.get("kind") not in {"contains","excludes","json","min_length"}: raise ValueError("Unknown output validator")
            if rule.get("kind")=="json": validate_schema(rule.get("schema",{"type":"object"}))
            if rule.get("kind")=="min_length" and (not isinstance(rule.get("value"),int) or not 1<=rule["value"]<=100000): raise ValueError("Invalid minimum output length")
            if rule.get("kind") in {"contains","excludes"} and (not isinstance(rule.get("value"),str) or not 1<=len(rule["value"])<=1000): raise ValueError("Invalid text validator")
        return self


class FieldBinding(BaseModel):
    source_path: str = Field(default="", max_length=300)
    target_name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_]{0,95}$")

    @model_validator(mode="after")
    def valid_path(self):
        from .dataflow import check_path
        check_path(self.source_path)
        return self


class Edge(BaseModel):
    source: str
    target: str
    bindings: list[FieldBinding] = Field(default_factory=list, max_length=24)


class OutputPort(BaseModel):
    name: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")
    source: str
    path: str = Field(default="", max_length=300)

    @model_validator(mode="after")
    def valid_path(self):
        from .dataflow import check_path
        check_path(self.path)
        return self


class MessageChannel(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")
    members: list[str] = Field(min_length=2,max_length=16)
    max_messages: int = Field(default=20,ge=1,le=100)


class Architecture(BaseModel):
    id: str = Field(pattern=r"^[a-zA-Z][a-zA-Z0-9_-]{0,47}$")
    name: str = Field(min_length=1, max_length=80)
    description: str = Field(default="", max_length=400)
    category: str = Field(default="general", max_length=40)
    nodes: list[AgentSpec] = Field(min_length=1, max_length=16)
    edges: list[Edge] = Field(default_factory=list, max_length=120)
    builtin: bool = False
    revision: int | None = Field(default=None, ge=0)
    max_rounds: int = Field(default=1, ge=1, le=5)
    stop_when_contains: str = Field(default="", max_length=120)
    channels: list[MessageChannel] = Field(default_factory=list,max_length=12)
    input_schema: dict = Field(default_factory=dict)
    output_schema: dict = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)
    output_ports: list[OutputPort] = Field(default_factory=list, max_length=16)

    @model_validator(mode="after")
    def validate_graph(self):
        ids = [n.id for n in self.nodes]
        if "results" in ids:
            raise ValueError("The agent ID 'results' is reserved by the graph engine")
        if len(set(ids)) != len(ids):
            raise ValueError("Every agent needs a unique ID")
        from .protocol import validate_schema
        if self.input_schema: validate_schema(self.input_schema)
        if self.output_schema: validate_schema(self.output_schema)
        if self.parameters: validate_schema(self.parameters)
        if len({p.name for p in self.output_ports})!=len(self.output_ports) or any(p.source not in ids for p in self.output_ports): raise ValueError("Named outputs need unique names and existing nodes")
        if len({c.id for c in self.channels})!=len(self.channels): raise ValueError("Channel IDs must be unique")
        if any(len(set(c.members))!=len(c.members) or not set(c.members)<=set(ids) for c in self.channels): raise ValueError("Channels reference unique members of this architecture")
        adjacency = {i: [] for i in ids}
        degree = {i: 0 for i in ids}
        pairs = set()
        bound_names = set()
        for e in self.edges:
            if e.source not in adjacency or e.target not in adjacency:
                raise ValueError("An edge references an unknown agent")
            if (e.source, e.target) in pairs:
                raise ValueError("Duplicate connections are not allowed")
            pairs.add((e.source, e.target))
            for binding in e.bindings:
                target_node=next(n for n in self.nodes if n.id==e.target)
                if target_node.kind in {"subswarm","recursive","fanout"} and binding.target_name in {"task","upstream"}:
                    raise ValueError("task and upstream are reserved child inputs; choose a task source instead")
                key=(e.target,binding.target_name)
                if key in bound_names: raise ValueError("An input field can have only one binding")
                bound_names.add(key)
            adjacency[e.source].append(e.target)
            degree[e.target] += 1
        queue = [i for i, d in degree.items() if d == 0]
        visited = 0
        while queue:
            current = queue.pop()
            visited += 1
            for target in adjacency[current]:
                degree[target] -= 1
                if degree[target] == 0:
                    queue.append(target)
        if visited != len(ids):
            raise ValueError("Use max_rounds for bounded revision loops; raw dependency cycles are not allowed")
        for node in self.nodes:
            if node.run_if and (not {"source","contains"}<=set(node.run_if) or set(node.run_if)-{"source","contains","path"} or node.run_if["source"] not in ids):
                raise ValueError("A condition needs source and contains fields referencing an upstream node")
            if node.run_if:
                from .dataflow import check_path
                check_path(node.run_if.get("path",""))
            if node.run_if or node.task_source:
                pending, seen = [node.id], set()
                while pending:
                    current = pending.pop()
                    for edge in self.edges:
                        if edge.target == current and edge.source not in seen:
                            seen.add(edge.source)
                            pending.append(edge.source)
                if node.run_if and node.run_if["source"] not in seen:
                    raise ValueError("A condition can inspect only an upstream node")
                if node.task_source and node.task_source not in seen:
                    raise ValueError("A child task source must be an upstream node")
        return self


class ProfileInput(BaseModel):
    provider: Literal["google", "groq", "openrouter", "mistral", "compatible", "freellmpool", "community", "exo"]
    label: str = Field(min_length=1, max_length=80)
    account: str = Field(default="", max_length=120)
    quota_scope: str = Field(default="", max_length=160)
    api_key: str = Field(default="", max_length=4096)
    base_url: str = Field(default="", max_length=500)
    model: str = Field(default="", max_length=200)
    free_eligible: bool = False
    enabled: bool = True
    requests_per_minute: int = Field(default=6, ge=1, le=600)
    ephemeral: bool = False
    input_usd_per_million: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    output_usd_per_million: float | None = Field(default=None, ge=0, le=10000, allow_inf_nan=False)
    verify_duplicates: bool=False
    model_revision: str = Field(default="",max_length=200)


class Route(BaseModel):
    kind: Literal["auto", "profile", "local", "device", "acp", "runtime"] = "auto"
    profile_id: str = ""
    device_id: str = ""
    model: str = Field(default="", max_length=200)
    extension_id: str = Field(default="", max_length=80)
    runtime_id: str = Field(default="", max_length=80)


class MissionLimits(BaseModel):
    max_seconds: int = Field(default=1800, ge=10, le=86400)
    max_requests: int = Field(default=100, ge=1, le=10000)
    max_tokens: int = Field(default=200000, ge=256, le=10000000)
    max_spend_usd: float | None = Field(default=0, ge=0, le=10000, allow_inf_nan=False)


class RunInput(BaseModel):
    task: str = Field(min_length=1, max_length=24000)
    architecture_id: str
    preset: Literal["general", "coding", "research"] = "general"
    mode: Literal["live"] = "live"
    free_only: bool = True
    route: Route = Field(default_factory=Route)
    project_id: str = Field(default="", max_length=80)
    tools_enabled: bool = False
    tool_budget: int = Field(default=8, ge=0, le=24)
    low_quota: bool = False
    queue_if_unavailable: bool = False
    public_allowed: bool = False
    max_subtasks: int = Field(default=4, ge=0, le=32)
    depth: int = Field(default=0, ge=0, le=4)
    lab_id: str = Field(default="", max_length=80)
    limits: MissionLimits = Field(default_factory=MissionLimits)
    adaptive: Literal["off", "speed", "quality", "resources"] = "off"
    isolated_worktrees: bool = False
    attachments: list[str] = Field(default_factory=list, max_length=4)
    routing_policy: Literal["placement", "smallest", "quality", "learned"] = "smallest"
    native_tools: bool = False
    validation_attempts: int = Field(default=3, ge=1, le=5)
    reuse_cache: bool = False
    model_revision: str = Field(default="",max_length=200)
    inputs: dict = Field(default_factory=dict)
    parameters: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded_inputs(self):
        import json
        if len(json.dumps(self.inputs,allow_nan=False).encode())>64000: raise ValueError("Typed mission inputs exceed 64 KB")
        if len(json.dumps(self.parameters,allow_nan=False).encode())>64000: raise ValueError("Mission parameters exceed 64 KB")
        return self


class ChatInput(BaseModel):
    content: str = Field(min_length=1, max_length=12000)


class BudgetInput(BaseModel):
    ram_gb: float = Field(ge=0.5, le=2048)
    vram_gb: float = Field(ge=0, le=2048)
    context: int = Field(default=4096, ge=512, le=32768)
    allow_cpu: bool = True


class JoinInput(BaseModel):
    invite: str = Field(max_length=256)
    name: str = Field(min_length=1, max_length=100)
    hardware: dict = Field(default_factory=dict)
    models: list[dict] = Field(default_factory=list, max_length=100)
    budgets: BudgetInput
    execution: dict = Field(default_factory=dict)
