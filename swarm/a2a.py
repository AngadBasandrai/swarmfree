"""Owner-approved A2A text tasks over HTTP+JSON and JSON-RPC bindings."""
import json
import time
from urllib.parse import urlparse,quote
import httpx
from .providers import RouteError,validate_base_url
from .store import uid


METHODS={"send":("SendMessage","message/send"),"poll":("GetTask","tasks/get"),"cancel":("CancelTask","tasks/cancel"),"subscribe":("SubscribeToTask","tasks/resubscribe")}


class A2A:
    def __init__(self,store,vault,privacy): self.store,self.vault,self.privacy=store,vault,privacy

    def add(self,form):
        origin=validate_base_url(form["origin"]).rstrip("/")
        parsed=urlparse(origin)
        if parsed.path or parsed.query or parsed.fragment: raise RouteError("Supply an agent origin without a path")
        id=uid("a2a_")
        if form.get("token"): self.vault.set(id,form["token"],form.get("ephemeral",True))
        row={k:v for k,v in form.items() if k!="token"}
        return self.store.put("a2a_endpoint",id,dict(row,id=id,origin=origin,enabled=True,card=None,created_at=time.time()))

    def endpoint(self,id):
        row=self.store.get("a2a_endpoint",id)
        if not row or not row["enabled"]: raise RouteError("A2A endpoint is disabled or missing")
        return row

    def headers(self,row):
        headers={"Accept":"application/a2a+json,application/json","A2A-Version":row.get("version","1.0")}
        secret=self.vault.get(row["id"])
        if secret: headers[row.get("auth_header","Authorization")]=("Bearer "+secret) if row.get("auth_header","Authorization")=="Authorization" else secret
        return headers

    async def fetch(self,row,url,method="GET",body=None):
        if urlparse(url).netloc!=urlparse(row["origin"]).netloc or urlparse(url).scheme!=urlparse(row["origin"]).scheme: raise RouteError("Agent Card cannot redirect authority to another origin")
        try:
            async with httpx.AsyncClient(timeout=40,follow_redirects=False,trust_env=False) as client:
                async with client.stream(method,url,headers=self.headers(row),json=body if body is not None else None) as response:
                    if response.status_code>=300: raise RouteError("A2A request rejected ("+str(response.status_code)+")")
                    data=b""
                    async for chunk in response.aiter_bytes():
                        data+=chunk
                        if len(data)>1000000: raise RouteError("A2A response exceeds 1 MB")
                    return json.loads(data)
        except (httpx.HTTPError,ValueError) as error: raise RouteError("A2A connection/protocol failed") from error

    async def discover(self,id):
        row=self.endpoint(id);card=await self.fetch(row,row["origin"]+"/.well-known/agent-card.json")
        if not isinstance(card,dict) or not isinstance(card.get("name"),str): raise RouteError("Invalid Agent Card")
        interfaces=card.get("supportedInterfaces") or [dict(url=card.get("url"),protocolBinding=card.get("preferredTransport","JSONRPC"),protocolVersion=card.get("protocolVersion","0.3"))]
        chosen=next((i for i in interfaces if i.get("protocolBinding") in {"HTTP+JSON","JSONRPC"} and i.get("url")),None)
        if not chosen: raise RouteError("This card needs an unsupported A2A transport")
        if chosen.get("tenant"): raise RouteError("Tenant-scoped interfaces are not supported yet")
        endpoint=validate_base_url(chosen["url"])
        if urlparse(endpoint).netloc!=urlparse(row["origin"]).netloc or urlparse(endpoint).scheme!=urlparse(row["origin"]).scheme: raise RouteError("Agent Card points to a different origin; connect that origin explicitly")
        version=str(chosen.get("protocolVersion") or card.get("protocolVersion","1.0"))
        if not version.startswith(("0.3","1.0")): raise RouteError("Supported A2A versions are 0.3 and 1.0")
        return self.store.patch("a2a_endpoint",id,dict(card=card,endpoint=endpoint.rstrip("/"),binding=chosen["protocolBinding"],version=version,discovered_at=time.time()))

    def request(self,row,action,body):
        endpoint=row.get("endpoint")
        if not endpoint: raise RouteError("Discover and inspect this Agent Card first")
        if row["binding"]=="JSONRPC":
            method=METHODS[action][0 if row["version"].startswith("1.") else 1]
            return endpoint,"POST",dict(jsonrpc="2.0",id=uid(),method=method,params=body)
        if action=="send": return endpoint+"/message:send","POST",body
        path=endpoint+"/tasks/"+quote(body["id"],safe="")
        return path+(":cancel" if action=="cancel" else ":subscribe" if action=="subscribe" else ""),"GET" if action=="poll" else "POST",{} if action!="poll" else None

    def remember(self,row,result,run_id,agent_id):
        if not isinstance(result,dict): raise RouteError("Invalid A2A task response")
        if result.get("error"): raise RouteError("Remote agent reported a task error")
        result=result.get("result",result)
        task=result.get("task",result)
        remote_id=task.get("id") or task.get("taskId") or result.get("message",{}).get("taskId")
        id=row["id"]+":"+str(remote_id) if remote_id else uid("a2at_")
        if len(id)>400: raise RouteError("Invalid remote task ID")
        return self.store.put("a2a_task",id,dict(id=id,endpoint_id=row["id"],remote_id=remote_id,run_id=run_id,agent_id=agent_id,result=result,
            state=task.get("status",{}).get("state","completed" if result.get("message") or task.get("parts") else "unknown"),updated_at=time.time()))

    async def action(self,project,run,agent,action,args):
        row=self.endpoint(args["endpoint_id"])
        if row["id"] not in project.get("a2a_endpoints",[]): raise RouteError("This project has not granted that external agent")
        if run.get("free_only") and not row.get("free_eligible"): raise RouteError("This external agent has no owner-declared free eligibility")
        if action=="send":
            messages=self.privacy.prepare([dict(role="user",content=str(args["content"])[:24000])],"tool",dict(id=row["id"]),run,agent)
            message=dict(role="ROLE_USER" if row["version"].startswith("1.") else "user",messageId=uid(),parts=[dict(text=messages[0]["content"]) if row["version"].startswith("1.") else dict(kind="text",text=messages[0]["content"])])
            if args.get("task_id"): message["taskId"]=args["task_id"]
            body=dict(message=message)
        else:
            task=self.store.get("a2a_task",args["task_id"])
            if not task or task["endpoint_id"]!=row["id"] or task["run_id"]!=run["id"]: raise RouteError("Task belongs to another mission or endpoint")
            if not task.get("remote_id"): raise RouteError("This immediate response has no remote task")
            body=dict(id=task["remote_id"])
        url,method,payload=self.request(row,action,body)
        result=await self.fetch(row,url,method,payload)
        return self.remember(row,result,run["id"],agent["id"])

    async def subscribe(self,id):
        task=self.store.get("a2a_task",id)
        if not task or not task.get("remote_id"): raise RouteError("Task has no remote subscription ID")
        row=self.endpoint(task["endpoint_id"])
        if not row.get("card",{}).get("capabilities",{}).get("streaming"): raise RouteError("This agent does not advertise streaming")
        url,method,body=self.request(row,"subscribe",dict(id=task["remote_id"]))
        async with httpx.AsyncClient(timeout=60,follow_redirects=False,trust_env=False) as client:
            async with client.stream(method,url,headers={**self.headers(row),"Accept":"text/event-stream"},json=body) as response:
                if response.status_code>=300: raise RouteError("A2A subscription rejected")
                buffer="";count=0
                async for line in response.aiter_lines():
                    if line.startswith("data:"): buffer+=line[5:].strip()
                    if len(buffer)>100000: raise RouteError("A2A event exceeds the limit")
                    if not line and buffer:
                        count+=1;event=json.loads(buffer);buffer=""
                        if count>200: raise RouteError("A2A subscription event limit reached")
                        previous=self.store.get("a2a_task",id)
                        updates=previous.get("events",[])[-39:]+[event]
                        self.store.patch("a2a_task",id,dict(events=updates,updated_at=time.time()))
                        if event.get("task") or event.get("result",{}).get("task"):
                            fresh=self.remember(row,event,task["run_id"],task["agent_id"])
                            self.store.patch("a2a_task",fresh["id"],dict(events=updates))
                return self.store.get("a2a_task",id)
