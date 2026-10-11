"""Owned fault simulations exercise engine and lease code without live providers."""
import asyncio
import json
from pathlib import Path
import tempfile
import time
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from .engine import Engine
from .providers import Providers,RouteError
from .network import PrivatePool
from .store import Store
from .schema import Architecture,RunInput,ProfileInput,JoinInput
from .workspace import Workspace
from .privacy import Privacy


SCENARIOS={"stream_disconnect":"Interrupted output stays a draft; resume makes a fresh response",
    "quota_exhaustion":"Quota failure is visible and can recover at a saved checkpoint",
    "invalid_output":"A failed output contract is retried within the mission budget",
    "malformed_tools":"Ungrantable native tool calls are rejected before any effect",
    "timeout":"A slow response stops at the mission runtime ceiling",
    "crash_recovery":"Saved history survives rebuilding the engine; incomplete drafts stay excluded",
    "worker_disconnect":"Expired leases cannot commit results after reassignment",
    "untrusted_instruction":"A tool request embedded in untrusted content cannot expand tool grants"}


class FixtureVault:
    def __init__(self): self.values={}
    def set(self,id,value,ephemeral=False): self.values[id]=value;return "session" if value else "none"
    def get(self,id): return self.values.get(id)
    def delete(self,id): self.values.pop(id,None)
    def status(self): return dict(persistent=False)


async def settled(engine,id,timeout=60):
    await asyncio.wait_for(asyncio.shield(engine.tasks[id]),timeout)
    return engine.store.get("run",id)


async def scenario(name,root):
    with tempfile.TemporaryDirectory(prefix="fixture-",dir=root) as folder:
        path=Path(folder);store=Store(path/"workbench.sqlite");engine=None
        store.put("settings","budgets",dict(ram_gb=2,vram_gb=0,context=4096,allow_cpu=True))
        providers=Providers(store,FixtureVault(),"http://127.0.0.1:9")
        profile=providers.add(ProfileInput(provider="compatible",label="Owned fault fixture",model="fixture",base_url="http://127.0.0.1:9/v1",ephemeral=True,free_eligible=True,input_usd_per_million=0,output_usd_per_million=0))
        workspace=Workspace(store,path);pool=PrivatePool(store)
        calls=0
        async def fixture(config,messages):
            nonlocal calls
            calls+=1
            if name in {"stream_disconnect","crash_recovery"} and calls==1:
                yield "INCOMPLETE_FIXTURE_DRAFT"
                raise RouteError("Injected stream disconnection")
            if name=="quota_exhaustion" and calls==1: raise RouteError("Injected quota exhaustion",retry_after=1)
            if name=="invalid_output" and calls==1: yield "Output missing its required marker";return
            if name=="malformed_tools":
                config["_native_calls"].append(dict(id="invalid",function=dict(name="ungranted_terminal",arguments='{}')));return
            if name=="timeout": await asyncio.sleep(60)
            if name=="untrusted_instruction":
                yield json.dumps(dict(tool="checks.run",arguments=dict(name="invented-command")));return
            yield "Verified fixture completion"
        providers.compatible=fixture
        store.put("project","fixture",dict(id="fixture",name="Owned fixture",root=folder,tools=["workspace.read"],checks={}))
        validator=[dict(kind="contains",value="Verified")] if name=="invalid_output" else []
        arch=Architecture(id="fixture",name="Owned fault fixture",nodes=[dict(id="worker",name="Fixture worker",role="Exercise an injected failure",validators=validator)]).model_dump()
        observations=[]
        try:
            async with AsyncSqliteSaver.from_conn_string(str(path/"checkpoints.sqlite")) as saver:
                engine=Engine(store,providers,pool,saver,workspace)
                engine.privacy=Privacy(store)
                run=engine.create(RunInput(task="Controlled failure simulation",architecture_id="fixture",project_id="fixture",tools_enabled=name in {"malformed_tools","untrusted_instruction"},native_tools=name=="malformed_tools",
                    free_only=False,route=dict(kind="profile",profile_id=profile["id"]),limits=dict(max_seconds=10 if name=="timeout" else 60,max_requests=6,max_tokens=50000,max_spend_usd=0)),arch)
                if name=="worker_disconnect":
                    invitation=pool.invite()
                    joined=pool.join(JoinInput(invite=invitation["token"],name="Owned simulated worker",hardware={},models=[],budgets=dict(ram_gb=2,vram_gb=0,context=4096,allow_cpu=True)))
                    device=store.get("device",joined["device_id"]);store.patch("device",device["id"],dict(approved=True))
                    store.put("job","lease-fixture",dict(id="lease-fixture",device_id=device["id"],model="fixture",messages=[],status="queued",created_at=time.time(),deadline=time.time()+60,text=""))
                    lease=pool.lease(store.get("device",device["id"]))
                    store.patch("job","lease-fixture",dict(lease_until=time.time()-1))
                    try: accepted=pool.result(store.get("device",device["id"]),"lease-fixture",lease["lease"],dict(text="stale",done=True,error=False))
                    except ValueError: accepted=False
                    passed=not accepted and store.get("job","lease-fixture")["text"]==""
                    observations.append("Expired result accepted: "+str(bool(accepted)))
                    await settled(engine,run["id"])
                else:
                    run=await settled(engine,run["id"])
                    observations.append("First outcome: "+run["status"])
                    if name in {"stream_disconnect","quota_exhaustion","crash_recovery"}:
                        if name=="crash_recovery":
                            await engine.stop();engine=Engine(store,providers,pool,saver,workspace);engine.privacy=Privacy(store)
                            observations.append("Engine rebuilt from saved record/checkpoint databases")
                        engine.start(run["id"]);run=await settled(engine,run["id"])
                        histories=engine.agents(run["id"])[0]["messages"]
                        drafts=[m for m in histories if m.get("interrupted")]
                        sent=engine.messages(run,engine.agents(run["id"])[0])
                        passed=run["status"]=="completed" and (name=="quota_exhaustion" or bool(drafts) and all("INCOMPLETE_FIXTURE_DRAFT" not in m["content"] for m in sent))
                        observations.append("After resume: "+run["status"])
                    elif name=="invalid_output": passed=run["status"]=="completed" and calls==2
                    elif name=="malformed_tools": passed=run["status"]=="interrupted" and not store.all("operation")
                    elif name=="untrusted_instruction": passed=run["status"] in {"interrupted","budget_exceeded"} and not any(o["tool"]=="checks.run" and o["status"]=="completed" for o in store.all("operation"))
                    elif name=="timeout": passed=run["status"]=="budget_exceeded"
                    else: passed=False
                return dict(scenario=name,passed=passed and (calls>0 or name=="worker_disconnect"),model_calls=calls,observations=observations,events=store.events(run["id"],25),
                    note="Controlled fixtures test containment/recovery, not a model's resistance to arbitrary prompt injection")
        finally:
            if engine: await engine.stop()
            store.close()


class FaultLab:
    def __init__(self,store,root): self.store,self.root=store,Path(root)/"fault-lab";self.root.mkdir(exist_ok=True)
    async def run(self,id,names):
        rows=[]
        try:
            for name in names:
                started=time.monotonic()
                try: result=await scenario(name,self.root)
                except Exception as error: result=dict(scenario=name,passed=False,error=type(error).__name__,observations=["Simulation failed to finish; inspect its fixture"])
                rows.append(dict(result,elapsed_ms=round((time.monotonic()-started)*1000)))
                self.store.patch("fault_job",id,dict(rows=rows))
            self.store.patch("fault_job",id,dict(status="completed",passed=all(r["passed"] for r in rows),finished_at=time.time()))
        except asyncio.CancelledError:
            self.store.patch("fault_job",id,dict(status="cancelled",rows=rows));raise
