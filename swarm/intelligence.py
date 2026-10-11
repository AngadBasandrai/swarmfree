"""Measured routing, dated catalogs, local vector search and repeatable evaluations."""
import asyncio
import hashlib
import json
import math
import os
from pathlib import Path
import time
import httpx
from .providers import RouteError
from .store import uid
from .workspace import BLOCKED, scoped_path, sha
from .protocol import validate, validate_schema


def route_key(kind, data):
    if kind == "profile": return "profile:"+data["id"]+":"+data["model"]
    if kind == "device": return "device:"+data["device_id"]+":"+data["model"]
    if kind == "runtime": return "runtime:"+data["id"]
    return kind+":"+str(data)


def task_class(run, agent):
    if run.get("tools_enabled") and agent.get("tools")!=[]: return "tool-use"
    return run.get("preset", "general")


def check_output(text, schema=None, checks=None):
    failures, parsed = [], None
    if schema:
        try:
            parsed = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
            validate(parsed, schema)
        except (ValueError, RouteError) as error: failures.append(str(error)[:180])
    for check in checks or []:
        kind, value = check.get("kind"), check.get("value", "")
        if kind == "contains" and str(value).casefold() not in text.casefold(): failures.append("Missing expected text: "+str(value)[:100])
        elif kind == "excludes" and str(value).casefold() in text.casefold(): failures.append("Contains excluded text: "+str(value)[:100])
        elif kind == "json":
            try:
                parsed = json.loads(text.strip().removeprefix("```json").removesuffix("```").strip())
                validate(parsed, check.get("schema", {"type":"object"}))
            except (ValueError, RouteError): failures.append("Invalid expected JSON")
        elif kind == "min_length" and len(text) < int(value): failures.append("Output is too short")
        elif kind not in {"contains", "excludes", "json", "min_length", "named_check"}: failures.append("Unknown validation rule")
    return dict(passed=not failures, failures=failures, structured=parsed)


class Intelligence:
    def __init__(self, store, providers, workspace):
        self.store, self.providers, self.workspace = store, providers, workspace
        self.embedding_lock = asyncio.Lock()

    def outcome(self, run, agent, kind, data, result, elapsed_ms, *, measured=None):
        id = uid("outcome_")
        return self.store.put("outcome", id, dict(id=id, route=route_key(kind,data), task_class=task_class(run,agent),
            run_id=run["id"], agent_id=agent["id"], passed=result["passed"], failures=result["failures"],
            measured=bool(agent.get("output_schema") or agent.get("validators")) if measured is None else measured,elapsed_ms=elapsed_ms,created_at=time.time()))

    def rank(self, run, agent, options):
        policy=run.get("routing_policy", "smallest")
        if policy == "placement": return options
        outcomes=self.store.all("outcome")
        catalog=self.store.all("catalog")
        devices={d["id"]:d for d in self.store.all("device")}
        jobs=self.store.all("job")
        def score(item):
            kind,data=item; key=route_key(kind,data)
            model=data.get("model",data.get("name","")) if isinstance(data,dict) else str(data)
            history=[o for o in outcomes if o["route"]==key and o["task_class"]==task_class(run,agent) and o.get("measured")][-30:]
            quality=(1+sum(o["passed"] for o in history))/(2+len(history))
            info=next((m for m in catalog if m.get("model")==model),{})
            size=info.get("parameters_b") or (data.get("size",0)/1e9 if kind=="runtime" else 0)
            if not size:
                import re
                match=re.search(r"(?:^|[:_-])(\d+(?:\.\d+)?)b(?:$|[:_-])",model.lower())
                size=float(match[1]) if match else 20
            load, resident, pressure=0,False,0
            if kind=="device":
                device=devices.get(data["device_id"],{})
                load=sum(j.get("device_id")==data["device_id"] and j["status"] in {"queued","leased"} and j["deadline"]>time.time() for j in jobs)
                resident=any(m.get("name")==model and m.get("loaded") for m in device.get("models",[]))
                hw=device.get("hardware",{})
                pressure=max(0,device.get("budgets",{}).get("ram_gb",0)-hw.get("ram_available_gb",0))
            elif kind=="local": resident=model in self.store.get("settings","scheduler",{}).get("loaded",[])
            elif kind=="runtime": resident=data.get("status")=="ready"
            rank=(-quality,size,load,pressure,not resident) if policy=="quality" else (load,pressure,size,-quality,not resident)
            return rank
        ranked=sorted(options,key=score)
        if agent.get("id"):
            self.store.patch("agent",agent["id"],dict(routing_scores=[dict(route=route_key(k,d),score=list(score((k,d))),policy=policy) for k,d in ranked]))
        return ranked

    def catalog(self, query=""):
        rows=self.store.all("catalog")
        if not rows:
            for size in (.6,1.7,4,8,14):
                model="qwen3:"+str(size).removesuffix(".0")+"b"
                row=dict(id="ollama:"+model,model=model,name="Qwen3 "+str(size)+"B",source="local",parameters_b=size,
                    download_gb=round(size*.65,2),estimated_ram_gb=round(size*.65+1.5,2),capabilities=["text","tools"],license="Apache-2.0",
                    source_url="https://huggingface.co/Qwen/Qwen3-0.6B",docs="https://ollama.com/library/qwen3",checked_at="2026-10-11",
                    free_note="Open weights; compute and electricity are yours. Q4 size and RAM are estimates; verify the selected quantization.")
                self.store.put("catalog",row["id"],row)
            self.store.put("catalog","ollama:qwen3-embedding:0.6b",dict(id="ollama:qwen3-embedding:0.6b",model="qwen3-embedding:0.6b",name="Qwen3 Embedding 0.6B",source="local",parameters_b=.6,estimated_ram_gb=2,capabilities=["embeddings"],license="Apache-2.0",docs="https://ollama.com/library/qwen3-embedding",source_url="https://arxiv.org/abs/2506.05176",checked_at="2026-10-11",free_note="Local embeddings; install separately."))
            rows=self.store.all("catalog")
        now=time.time()
        return [dict(row,stale=(now-row.get("observed_at",0)>7*86400) if row.get("observed_at") else True) for row in rows if query.casefold() in json.dumps(row).casefold()]

    async def refresh_catalog(self):
        async with httpx.AsyncClient(timeout=20,follow_redirects=False,trust_env=False) as client:
            response=await client.get("https://openrouter.ai/api/v1/models")
            response.raise_for_status()
            if len(response.content)>4_000_000: raise RouteError("Model catalog exceeds the response limit")
            rows=response.json().get("data",[])
        found=[]
        for row in rows[:3000]:
            try: free=float(row["pricing"]["prompt"])==float(row["pricing"]["completion"])==0 and row["id"].endswith(":free")
            except (KeyError,ValueError,TypeError): continue
            if not free: continue
            record=dict(id="openrouter:"+row["id"],model=row["id"],name=row.get("name",row["id"]),source="openrouter",context_tokens=row.get("context_length"),
                capabilities=row.get("supported_parameters",[]),license="Check upstream model license",docs="https://openrouter.ai/models/"+row["id"],
                checked_at=time.strftime("%Y-%m-%d",time.gmtime()),observed_at=time.time(),free_note="Catalog currently reports zero input/output price. Free quotas and upstream availability can change; additional tools may incur charges.")
            self.store.put("catalog",record["id"],record);found.append(record["id"])
        for old in self.store.all("catalog"):
            if old["source"]=="openrouter" and old["id"] not in found: self.store.delete("catalog",old["id"])
        return dict(updated=len(found),observed_at=time.time())

    async def embed(self, texts, model):
        from .hardware import inventory, hardware, fit_model
        if not texts: return []
        async with self.providers.local_lock:
            inv=await inventory(self.providers.ollama)
            selected=next((m for m in inv["models"] if m["name"]==model),None)
            if not selected or not fit_model(selected,hardware(),self.store.get("settings","budgets"),inv["loaded"])["fits"]:
                raise RouteError("Install an embedding model that fits the local memory allocation first")
            async with httpx.AsyncClient(timeout=120,trust_env=False,follow_redirects=False) as client:
                r=await client.post(self.providers.ollama+"/api/embed",json=dict(model=model,input=texts,truncate=False,keep_alive=60))
                r.raise_for_status()
                if len(r.content)>8_000_000: raise RouteError("Embedding response exceeds the limit")
                vectors=r.json().get("embeddings",[])
        if len(vectors)!=len(texts) or any(not isinstance(v,list) or not 1<=len(v)<=8192 or any(not isinstance(n,(float,int)) or not math.isfinite(n) for n in v) for v in vectors):
            raise RouteError("Embedding runtime returned invalid vectors")
        return vectors

    def documents(self, project):
        rows,total=[],0
        for directory,dirs,files in os.walk(project["root"],followlinks=False):
            dirs[:]=[d for d in dirs if d.casefold() not in BLOCKED and not d.casefold().startswith(".env") and not (Path(directory)/d).is_symlink()]
            if len(rows)>3000: break
            for name in files:
                try:
                    rel=(Path(directory)/name).relative_to(project["root"]).as_posix();path=scoped_path(project,rel)
                    if path.stat().st_size>120000 or path.is_symlink(): continue
                    text=path.read_text(encoding="utf-8")
                    if "\x00" in text: continue
                    total+=len(text)
                    if total>4_000_000: return rows
                    lines=text.splitlines()
                    for start in range(0,len(lines),30):
                        content="\n".join(lines[start:start+40])[:3500]
                        rows.append(dict(path=rel,line=start+1,content=content,content_hash=sha(text),kind="file"))
                except (OSError,UnicodeError,RouteError): continue
        for kind in ("memory","source"):
            for note in self.store.all(kind):
                if note.get("project_id")==project["id"]:
                    text=note.get("title","")+"\n"+note.get("content","")
                    for offset in range(0,min(len(text),24000),3000):
                        rows.append(dict(path=kind+":"+note["id"],line=offset//3000+1,content=text[offset:offset+3500],content_hash=sha(text),kind=kind,data_labels=note.get("data_labels",[])))
        return rows[:1200]

    async def index(self, project_id, model):
        project=self.store.get("project",project_id)
        if not project: raise RouteError("Project not found")
        async with self.embedding_lock:
            docs=await asyncio.to_thread(self.documents,project)
            rows=[]
            for start in range(0,len(docs),16):
                batch=docs[start:start+16];vectors=await self.embed([d["content"] for d in batch],model)
                rows.extend(dict(d,vector=v) for d,v in zip(batch,vectors))
            self.store.put("semantic_index",project_id,dict(id=project_id,model=model,rows=rows,root=project["root"],indexed_at=time.time()))
        return dict(chunks=len(rows),model=model,indexed_at=time.time())

    def stale(self, project, row):
        try:
            if row["kind"]=="file": return sha(scoped_path(project,row["path"]).read_text(encoding="utf-8"))!=row["content_hash"]
            kind,id=row["path"].split(":",1);note=self.store.get(kind,id)
            return not note or note.get("project_id")!=project["id"] or sha(note.get("title","")+"\n"+note.get("content",""))!=row["content_hash"]
        except (OSError,UnicodeError,RouteError): return True

    async def search(self, project_id, query, limit=8):
        project=self.store.get("project",project_id);index=self.store.get("semantic_index",project_id)
        if not project or not index or project["root"]!=index["root"]: raise RouteError("Build a local semantic index for this project first")
        vector=(await self.embed([query[:3000]],index["model"]))[0]
        def cosine(row):
            other=row["vector"]
            if len(other)!=len(vector): raise RouteError("Embedding dimensions changed; rebuild the index")
            den=math.sqrt(sum(x*x for x in vector)*sum(x*x for x in other))
            return sum(a*b for a,b in zip(vector,other))/den if den else 0
        ranked=sorted(index["rows"],key=cosine,reverse=True)[:min(limit,20)]
        return dict(model=index["model"],indexed_at=index["indexed_at"],matches=[dict(path=r["path"],kind=r["kind"],data_labels=r.get("data_labels",[]),line=r["line"],text=r["content"],score=round(cosine(r),4),stale=self.stale(project,r)) for r in ranked])


class Evaluations:
    def __init__(self,store,engine): self.store,self.engine=store,engine

    async def run(self,id,form):
        from .schema import RunInput,Architecture
        rows=[]
        try:
            for variant in form["variants"]:
                architecture=self.store.get("architecture",variant["architecture_id"])
                if not architecture: raise RouteError("Evaluation architecture not found")
                architecture=Architecture.model_validate(architecture).model_dump()
                for task in form["tasks"]:
                    self.store.patch("evaluation_job",id,dict(status="running",rows=rows))
                    base={**form.get("mission",{}),"task":task["task"],"architecture_id":architecture["id"],"route":variant.get("route",{}),"adaptive":"off"}
                    if variant.get("instructions"):
                        architecture=dict(architecture,nodes=[dict(n,instructions=n.get("instructions","")+"\n"+variant["instructions"]) for n in architecture["nodes"]])
                    run=self.engine.create(RunInput.model_validate(base),architecture)
                    self.store.patch("evaluation_job",id,dict(active_run_id=run["id"]))
                    await self.engine.tasks[run["id"]]
                    run=self.store.get("run",run["id"])
                    terminal={n["id"] for n in architecture["nodes"] if not any(e["source"]==n["id"] for e in architecture["edges"])}
                    text="\n".join(r["content"] for r in run["results"] if r["node_id"] in terminal)
                    result=check_output(text,task.get("schema"),task.get("checks"))
                    check_results=[]
                    if run["status"]=="completed":
                        for check in task.get("checks",[]):
                            if check.get("kind")!="named_check": continue
                            agent=next(a for a in self.engine.agents(run["id"]) if a["node_id"] in terminal)
                            try:
                                used=sum(o["agent_id"]==agent["id"] for o in self.store.all("operation"))
                                if used>=run.get("tool_budget",8): raise RouteError("Evaluation agent tool budget reached")
                                self.engine.budget.begin(run["id"])
                                async with asyncio.timeout(max(.01,self.engine.budget.usage(run)["remaining_seconds"])):
                                    outcome=await self.engine.workspace.execute(run,agent,dict(tool="checks.run",arguments=dict(name=check["value"])))
                            except (RouteError,TimeoutError) as error: outcome=dict(error=str(error) or "Validation check timed out")
                            finally: self.engine.budget.end(run["id"])
                            check_results.append(dict(name=check["value"],result=outcome))
                            if outcome.get("error") or outcome.get("exit_code")!=0:
                                result["passed"]=False;result["failures"].append("Named check failed: "+check["value"])
                    if run["status"]!="completed": result=dict(result,passed=False,failures=result["failures"]+[run.get("error") or run["status"]])
                    rows.append(dict(variant=variant["name"],run_id=run["id"],task=task["task"],team_size=len(architecture["nodes"]),**result,check_results=check_results,usage=self.engine.budget.usage(run)))
            summaries=[]
            for variant in form["variants"]:
                subset=[r for r in rows if r["variant"]==variant["name"]]
                summaries.append(dict(name=variant["name"],passed=all(r["passed"] for r in subset),team_size=max(r["team_size"] for r in subset),tokens=sum(r["usage"]["tokens"] for r in subset)))
            passing=sorted([v for v in summaries if v["passed"]],key=lambda v:(v["team_size"],v["tokens"]))
            self.store.patch("evaluation_job",id,dict(status="completed",rows=rows,summaries=summaries,recommendation=passing[0] if passing else None,finished_at=time.time()))
        except asyncio.CancelledError:
            active=self.store.get("evaluation_job",id).get("active_run_id")
            if active: await self.engine.pause(active)
            self.store.patch("evaluation_job",id,dict(status="cancelled",rows=rows));raise
        except Exception as error:
            self.store.patch("evaluation_job",id,dict(status="failed",rows=rows,error=str(error) if isinstance(error,RouteError) else "Evaluation failed; inspect mission and check definitions"))
