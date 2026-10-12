"""Project and file provenance rules, plus explicit outbound secret review."""
import copy
import fnmatch
import hashlib
import json
import re
import time
from .providers import RouteError


DESTINATIONS={"local","private","cloud","public"}
PATTERNS=[
    ("private-key",re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----")),
    ("provider-key",re.compile(r"\b(?:sk-[A-Za-z0-9_-]{20,}|AIza[A-Za-z0-9_-]{30,}|AKIA[A-Z0-9]{16})\b")),
    ("credential",re.compile(r"(?im)\b(?:api[_-]?key|password|access[_-]?token|client[_-]?secret)\s*[:=]\s*[\"']?([^\s\"',;]{8,})")),
]


def canonical(value): return json.dumps(value,sort_keys=True,separators=(",",":"),ensure_ascii=False)
def digest(value): return hashlib.sha256(canonical(value).encode()).hexdigest()


def scan(value):
    found=[]
    def visit(item,path="root",depth=0):
        if depth>16: return
        if isinstance(item,str):
            for label,pattern in PATTERNS:
                for match in list(pattern.finditer(item[:300000]))[:12]:
                    found.append(dict(kind=label,location=path,start=match.start(),end=match.end()))
        elif isinstance(item,dict):
            for key,child in item.items():
                if key not in {"images","data","image_url","audio_url"}: visit(child,path+"."+str(key),depth+1)
        elif isinstance(item,list):
            for index,child in enumerate(item[:100]): visit(child,path+"."+str(index),depth+1)
    visit(value)
    return found[:40]


def redact(value):
    if isinstance(value,str):
        if PATTERNS[0][1].search(value): return "[redacted:private-key]"
        for label,pattern in PATTERNS: value=pattern.sub("[redacted:"+label+"]",value)
        return value
    if isinstance(value,list): return [redact(v) for v in value]
    if isinstance(value,dict): return {k:redact(v) if k not in {"images","data","image_url","audio_url"} else v for k,v in value.items()}
    return value


class Privacy:
    def __init__(self,store): self.store=store

    def policy(self,project_id):
        return self.store.get("data_policy",project_id,dict(id=project_id,destinations=sorted(DESTINATIONS),profile_ids=[],device_ids=[],file_rules=[],scan_secrets=True))

    def save(self,project_id,form):
        if not self.store.get("project",project_id): raise RouteError("Project no longer exists")
        rules=form.get("file_rules",[])
        for rule in [form,*rules]:
            if not rule.get("destinations") or not set(rule["destinations"])<=DESTINATIONS: raise RouteError("Select valid data destinations")
        if len(rules)>30 or any(not isinstance(r.get("pattern"),str) or not 1<=len(r["pattern"])<=200 or ".." in r["pattern"].split("/") for r in rules): raise RouteError("Invalid project file rules")
        return self.store.put("data_policy",project_id,dict(id=project_id,**form,updated_at=time.time()))

    def mark(self,agent_id,project_id,paths):
        agent=self.store.get("agent",agent_id)
        if not agent: return
        labels=agent.get("data_labels",[])
        for path in paths:
            item=dict(project_id=project_id,path=str(path).replace("\\","/"))
            if item not in labels: labels.append(item)
        if len(labels)>1000: raise RouteError("Data provenance limit reached")
        self.store.patch("agent",agent_id,dict(data_labels=labels))

    def inherit(self,agent_id,labels):
        for label in labels: self.mark(agent_id,label["project_id"],[label["path"]])

    def destination(self,kind,data):
        if kind in {"local","runtime"}: return "local"
        if kind=="device": return "private"
        if kind=="profile": return "public" if data.get("provider")=="community" else "cloud"
        return "cloud"

    def admit(self,kind,data,run,agent):
        destination=self.destination(kind,data)
        project_id=run.get("project_id","")
        labels=agent.get("data_labels",[])
        for id in {project_id,*[v["project_id"] for v in labels]}-{""}:
            policy=self.policy(id)
            if destination not in policy["destinations"]: return False
            if kind=="profile" and policy.get("profile_ids") and data["id"] not in policy["profile_ids"]: return False
            if kind=="device" and policy.get("device_ids") and data["device_id"] not in policy["device_ids"]: return False
            for label in [v for v in labels if v["project_id"]==id]:
                for rule in policy.get("file_rules",[]):
                    if fnmatch.fnmatchcase(label["path"],rule["pattern"]) and destination not in rule["destinations"]: return False
        return True

    def prepare(self,messages,kind,data,run,agent):
        agent=self.store.get("agent",agent.get("id"),agent)
        if not self.admit(kind,data,run,agent): raise RouteError("Project/file provenance excludes this data destination")
        destination=self.destination(kind,data)
        if destination=="local" or not self.policy(run.get("project_id",""))["scan_secrets"]: return copy.deepcopy(messages)
        findings=scan(messages)
        if not findings: return copy.deepcopy(messages)
        target=data.get("id",data.get("device_id",str(data))) if isinstance(data,dict) else str(data)
        policies=[self.policy(id) for id in sorted({run.get("project_id",""),*[v["project_id"] for v in agent.get("data_labels",[])]})]
        id=digest(dict(messages=messages,destination=destination,target=target,policies=policies))
        review=self.store.get("egress_review",id)
        if not review:
            self.store.put("egress_review",id,dict(id=id,run_id=run["id"],agent_id=agent.get("id"),destination=destination,target=target,
                status="pending",findings=findings,preview=redact(messages),created_at=time.time()))
            self.store.event(run["id"],"egress_review",agent_id=agent.get("id"),message="Sensitive-content review required before sharing")
        elif review["status"]=="approved": return copy.deepcopy(messages)
        elif review["status"]=="redact": return redact(messages)
        raise RouteError("Review flagged outbound content in Data rules, then resume this mission")

    def decide(self,id,decision):
        row=self.store.get("egress_review",id)
        if not row or row["status"]!="pending" or decision not in {"approved","redact","blocked"}: raise RouteError("Invalid outbound review decision")
        return self.store.patch("egress_review",id,dict(status=decision,reviewed_at=time.time()))
