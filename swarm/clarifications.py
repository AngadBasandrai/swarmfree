"""Durable questions suspend one agent without stopping independent branches."""
import asyncio
import time
from .providers import RouteError
from .protocol import validate, validate_schema
from .workspace import sha


class Clarifications:
    def __init__(self,store): self.store=store

    async def ask(self,run,agent,args):
        question=str(args["question"])[:2000]
        options=args.get("options",[])[:10]
        if not question.strip(): raise RouteError("Question must not be empty")
        id=sha(agent["id"]+question+str(options))[:32]
        record=self.store.get("question",id)
        if not record:
            schema=args.get("schema") or dict(type="string",minLength=1,maxLength=12000)
            validate_schema(schema)
            record=dict(id=id,run_id=run["id"],agent_id=agent["id"],question=question,options=options,schema=schema,status="pending",created_at=time.time())
            self.store.put("question",id,record)
            self.store.event(run["id"],"clarification_requested",agent_id=agent["id"],message=question)
        self.store.patch("agent",agent["id"],dict(status="awaiting_input",question_id=id))
        try:
            while True:
                record=self.store.get("question",id)
                if record["status"]=="answered": return dict(question_id=id,answer=record["answer"])
                if record["status"]=="cancelled": return dict(question_id=id,cancelled=True)
                await asyncio.sleep(.2)
        finally:
            self.store.patch("agent",agent["id"],dict(status="running",question_id=None))

    def answer(self,id,value,cancel=False):
        record=self.store.get("question",id)
        if not record or record["status"]!="pending": raise RouteError("Question is no longer pending")
        if not cancel: validate(value,record["schema"])
        result=self.store.patch("question",id,dict(status="cancelled" if cancel else "answered",answer=value,answered_at=time.time()))
        self.store.event(record["run_id"],"clarification_answered",agent_id=record["agent_id"],message="Owner cancelled the question" if cancel else "Owner supplied clarification")
        return result
