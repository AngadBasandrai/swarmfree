"""Claim-to-source linking with exact quotations and explicit owner judgments."""
import re
import time
from .providers import RouteError
from .store import uid


def normalize(value):
    return " ".join(value.casefold().split())


class Evidence:
    def __init__(self,store): self.store=store

    def extract(self,run_id):
        run=self.store.get("run",run_id)
        if not run: raise RouteError("Mission not found")
        existing=[c for c in self.store.all("claim") if c["run_id"]==run_id]
        if existing: return existing
        text="\n".join(r["content"] for r in run.get("results",[]) if not any(e["source"]==r["node_id"] for e in run["architecture"]["edges"]))
        sentences=re.split(r"(?<=[.!?])\s+|\n+",text)
        sources=[s for s in self.store.all("source") if s.get("run_id")==run_id]
        output=[]
        for sentence in sentences:
            if len(sentence.strip())<25: continue
            id=uid("claim_")
            links=[s["id"] for s in sources if s["id"] in sentence]
            output.append(self.store.put("claim",id,dict(id=id,run_id=run_id,text=sentence.strip()[:2000],source_ids=links,
                status="unreviewed" if links else "unsupported",evidence=[],conflicts=[],created_at=time.time())))
            if len(output)>=40: break
        return output

    def review(self,id,source_id,quote,judgment,note="",conflicts=None):
        claim=self.store.get("claim",id)
        if not claim: raise RouteError("Claim not found")
        if judgment not in {"supported","contradicted","uncertain","unsupported"}: raise RouteError("Unknown evidence judgment")
        links=list(claim["evidence"])
        source=self.store.get("source",source_id) if source_id else None
        if source_id:
            if not source or source.get("run_id")!=claim["run_id"]: raise RouteError("Choose evidence retrieved for this mission")
            if not quote.strip() or normalize(quote) not in normalize(source.get("content","")):
                raise RouteError("The supporting passage must occur in the retrieved source text")
            links.append(dict(source_id=source_id,quote=quote[:4000],content_hash=source["content_hash"],retrieved_at=source["retrieved_at"]))
        if judgment in {"supported","contradicted"} and not links: raise RouteError("This judgment needs a source passage")
        for other in conflicts or []:
            value=self.store.get("claim",other)
            if not value or value["run_id"]!=claim["run_id"] or other==id: raise RouteError("Choose another claim in this mission")
        return self.store.patch("claim",id,dict(status=judgment,evidence=links[-20:],note=note[:2000],conflicts=conflicts or [],reviewed_by="owner",reviewed_at=time.time()))
