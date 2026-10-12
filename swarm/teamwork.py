"""Project-scoped team sessions served through the private TLS listener."""
import hashlib
import json
import secrets
import ssl
import time
from urllib.parse import urlparse
import httpx
from fastapi import APIRouter,HTTPException,Request
from pydantic import BaseModel,Field
from typing import Literal
from .schema import RunInput,ChatInput
from .providers import RouteError,validate_base_url
from .store import uid


def token_hash(token): return hashlib.sha256(token.encode()).hexdigest()


class MemberInput(BaseModel):
    name:str=Field(min_length=1,max_length=80)
    role:Literal["viewer","operator","reviewer","architect"]="viewer"
    project_ids:list[str]=Field(min_length=1,max_length=40)
    days:int=Field(default=30,ge=1,le=365)


class ConnectInput(BaseModel):
    origin:str=Field(max_length=500)
    token:str=Field(min_length=20,max_length=4096)
    ca_file:str=Field(default="",max_length=4000)
    ephemeral:bool=True


class Teamwork:
    def __init__(self,store,vault,engine,workspace): self.store,self.vault,self.engine,self.workspace=store,vault,engine,workspace

    def audit(self,actor,action,resource):
        records=self.store.all("team_audit")
        row=dict(id=uid("audit_"),actor=actor,action=action,resource=resource,time=time.time(),previous=records[-1]["digest"] if records else "")
        row["digest"]=hashlib.sha256(json.dumps(row,sort_keys=True).encode()).hexdigest()
        self.store.put("team_audit",row["id"],row)

    def add(self,form):
        if any(not self.store.get("project",p) for p in form.project_ids): raise RouteError("Select existing projects")
        if len(self.store.all("team_member"))>=100: raise RouteError("Team member limit reached")
        token=secrets.token_urlsafe(40);id=uid("member_")
        row=dict(id=id,name=form.name,role=form.role,project_ids=list(set(form.project_ids)),expires_at=time.time()+form.days*86400,enabled=True,token_hash=token_hash(token),created_at=time.time())
        self.store.put("team_member",id,row);self.audit("owner","member.add",id)
        return dict(member={k:v for k,v in row.items() if k!="token_hash"},token=token)

    def public(self): return [{k:v for k,v in m.items() if k!="token_hash"} for m in self.store.all("team_member")]

    def actor(self,request):
        bearer=request.headers.get("authorization","").removeprefix("Bearer ")
        digest=token_hash(bearer)
        row=next((m for m in self.store.all("team_member") if m["enabled"] and m["expires_at"]>time.time() and secrets.compare_digest(m["token_hash"],digest)),None)
        if not row: raise HTTPException(401,"A valid individual team credential is required")
        return row

    def project(self,actor,id,roles=None):
        row=self.store.get("project",id)
        if id not in actor["project_ids"] or not row: raise HTTPException(404,"Project not found")
        if roles and actor["role"] not in roles: raise HTTPException(403,"This team role cannot perform that action")
        return row

    def run(self,actor,id,roles=None):
        row=self.store.get("run",id)
        if not row: raise HTTPException(404,"Mission not found")
        self.project(actor,row.get("project_id",""),roles);return row

    async def revoke(self,id):
        if not self.store.get("team_member",id): raise RouteError("Member no longer exists")
        self.store.patch("team_member",id,dict(enabled=False));self.audit("owner","member.revoke",id)
        for run in self.store.all("run"):
            if run.get("team_actor")==id and run["status"] in {"running","ready","queued"}: await self.engine.pause(run["id"])
        return dict(revoked=True)

    def connect(self,form):
        origin=validate_base_url(form.origin).rstrip("/");parsed=urlparse(origin)
        if parsed.path or parsed.query or parsed.fragment: raise RouteError("Use a coordinator origin without an API path")
        if form.ca_file: ssl.create_default_context(cafile=form.ca_file)
        self.vault.set("team_remote",form.token,form.ephemeral)
        return self.store.put("settings","team_remote",dict(id="team_remote",origin=origin,ca_file=form.ca_file,enabled=True,ephemeral=form.ephemeral))

    async def remote(self,path,method="GET",body=None):
        connection=self.store.get("settings","team_remote",{})
        token=self.vault.get("team_remote")
        if not connection.get("enabled") or not token: raise RouteError("Connect an individual team credential first")
        if not path.startswith("/api/team/") or ".." in path or len(path)>500: raise RouteError("Invalid team request path")
        context=ssl.create_default_context(cafile=connection["ca_file"] or None)
        try:
            async with httpx.AsyncClient(timeout=30,verify=context,trust_env=False,follow_redirects=False) as client:
                async with client.stream(method,connection["origin"]+path,headers={"Authorization":"Bearer "+token},json=body if body is not None else None) as response:
                    data=b""
                    async for chunk in response.aiter_bytes():
                        data+=chunk
                        if len(data)>4000000: raise RouteError("Team response exceeds 4 MB")
                    if response.status_code>=300: raise RouteError("Remote coordinator rejected this team action ("+str(response.status_code)+")")
                    return json.loads(data)
        except (httpx.HTTPError,ValueError) as error: raise RouteError("Cannot reach this trusted team coordinator") from error


def install_team(app):
    router=APIRouter(prefix="/api/team")
    class RedeemInput(BaseModel):
        invite_id:str=Field(max_length=100)
        invite:str=Field(min_length=20,max_length=200)
    class AnswerInput(BaseModel):
        answer:object=None
        cancel:bool=False
    @router.post("/companion/redeem")
    async def redeem(form:RedeemInput):
        s=app.state.store
        with s.lock:
            row=s.get("companion_invite",form.invite_id)
            if not row or row["redeemed"] or row["expires_at"]<time.time() or not secrets.compare_digest(row["invite_hash"],token_hash(form.invite)):
                raise HTTPException(401,"Pairing invitation expired or was already used")
            member=s.get("team_member",row["member_id"])
            token=app.state.vault.get(row["id"])
            if not member or not member["enabled"] or member["expires_at"]<time.time() or not token: raise HTTPException(401,"Pairing credential is unavailable; create a new invitation")
            s.patch("companion_invite",row["id"],dict(redeemed=True))
            app.state.vault.delete(row["id"])
            app.state.team.audit(member["id"],"companion.paired",row["id"])
            return dict(token=token,member={k:v for k,v in member.items() if k!="token_hash"})
    @router.get("/questions")
    async def questions(request:Request):
        actor=app.state.team.actor(request)
        runs={r["id"] for r in app.state.store.all("run") if r.get("project_id") in actor["project_ids"]}
        return [q for q in app.state.store.all("question") if q["run_id"] in runs and q["status"]=="pending"][-50:]
    @router.post("/questions/{id}/answer")
    async def answer(id:str,form:AnswerInput,request:Request):
        team=app.state.team;actor=team.actor(request);question=team.store.get("question",id)
        if not question: raise HTTPException(404,"Question not found")
        team.run(actor,question["run_id"],{"operator"})
        result=app.state.clarifications.answer(id,form.answer,form.cancel)
        team.audit(actor["id"],"question.answer",id);return result
    @router.get("/snapshot")
    async def snapshot(request:Request):
        team=app.state.team;actor=team.actor(request)
        projects=[{k:v for k,v in p.items() if k not in {"root","checks","browser_sites","execution_devices"}} for p in team.store.all("project") if p["id"] in actor["project_ids"]]
        runs=[dict(id=r["id"],task=r["task"][:200],status=r["status"],project_id=r["project_id"],created_at=r["created_at"]) for r in team.store.all("run") if r.get("project_id") in actor["project_ids"]][-100:]
        allowed={id for project in projects for id in project.get("team_architectures",[])}
        architectures=[a for a in team.store.all("architecture") if a.get("builtin") or a["id"] in allowed]
        return dict(actor={k:v for k,v in actor.items() if k!="token_hash"},projects=projects,runs=runs,architectures=architectures)

    @router.get("/runs/{id}")
    async def mission(id:str,request:Request):
        team=app.state.team;actor=team.actor(request);run=team.run(actor,id)
        # Explicit projection avoids checkpoint IDs, local absolute paths and credentials.
        agents=[{k:a.get(k) for k in ("id","node_id","name","role","status","result","messages","error","child_run_id")} for a in team.engine.agents(id)]
        return dict(id=id,task=run["task"],status=run["status"],project_id=run["project_id"],agents=agents,error=run.get("error",""),usage=team.engine.budget.usage(run))

    @router.post("/runs")
    async def create(form:RunInput,request:Request):
        team=app.state.team;actor=team.actor(request);project=team.project(actor,form.project_id,{"operator"})
        if form.tools_enabled and not project.get("team_tools_allowed",False): raise HTTPException(403,"The owner has not granted team operators project tools")
        if form.public_allowed: raise HTTPException(403,"Team missions cannot opt a project into public workers")
        if form.route.kind!="auto": raise HTTPException(403,"Team operators use owner-controlled automatic routing")
        architecture=team.store.get("architecture",form.architecture_id)
        if not architecture: raise HTTPException(404,"Architecture not found")
        if not architecture.get("builtin") and architecture["id"] not in project.get("team_architectures",[]): raise HTTPException(403,"This architecture is not shared with the project")
        run=team.engine.create(form,architecture,start=False)
        team.store.patch("run",run["id"],dict(team_actor=actor["id"]))
        team.audit(actor["id"],"mission.create",run["id"]);team.engine.start(run["id"])
        return run

    @router.post("/runs/{id}/{action}")
    async def control(id:str,action:str,request:Request):
        team=app.state.team;actor=team.actor(request);team.run(actor,id,{"operator"})
        if action=="pause": await team.engine.pause(id)
        elif action=="resume": team.engine.start(id)
        else: raise HTTPException(404,"Unknown mission action")
        team.audit(actor["id"],"mission."+action,id);return dict(ok=True)

    @router.post("/agents/{id}/chat")
    async def chat(id:str,form:ChatInput,request:Request):
        team=app.state.team;actor=team.actor(request);agent=team.store.get("agent",id)
        if not agent: raise HTTPException(404,"Agent not found")
        team.run(actor,agent["run_id"],{"operator"});content=form.content
        if not isinstance(content,str) or not 1<=len(content)<=12000: raise RouteError("Agent messages need 1–12000 characters")
        team.engine.steer(id,content);team.audit(actor["id"],"agent.chat",id);return dict(ok=True)

    @router.get("/artifacts")
    async def artifacts(request:Request):
        team=app.state.team;actor=team.actor(request)
        return [{k:v for k,v in a.items() if k!="workspace_root"} for a in team.store.all("artifact") if a["project_id"] in actor["project_ids"]][-100:]

    @router.post("/artifacts/{id}/apply")
    async def apply(id:str,request:Request):
        team=app.state.team;actor=team.actor(request);artifact=team.store.get("artifact",id)
        if not artifact: raise HTTPException(404,"Artifact not found")
        team.project(actor,artifact["project_id"],{"reviewer"})
        result=team.workspace.apply(id);team.audit(actor["id"],"artifact.apply",id);return result
    app.include_router(router)
