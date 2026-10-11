"""Atomic mission-wide reservations, including delegated work and retries.

Tokens and prices are conservative estimates, not a provider billing ledger.
Unknown paid prices cannot pass a configured spending ceiling.
"""
import math
import time
from .providers import RouteError
from .schema import MissionLimits


class BudgetExceeded(RouteError):
    pass


def token_estimate(messages):
    return sum(math.ceil(len(str(m.get("content", ""))) / 2) + 16 + 2048 * len(m.get("images", [])) for m in messages)


class MissionBudget:
    def __init__(self, store):
        self.store = store
        self.activity = {}
        # Pending reservations from a crash remain charged at their reserved maximum.
        for record in store.all("reservation"):
            if record.get("status") == "pending":
                store.patch("reservation", record["id"], dict(status="uncertain", finished_at=time.time()))

    def root(self, run):
        return self.store.get("run", run.get("root_run_id", run["id"]), run)

    def usage(self, run):
        root = self.root(run)
        reservations = [r for r in self.store.all("reservation") if r["root_id"] == root["id"]]
        used = dict(requests=sum(r.get("units",1) for r in reservations), tokens=sum(r["tokens"] for r in reservations),
                    estimated_spend_usd=round(sum(r.get("cost", 0) for r in reservations), 8))
        used["reported_usage"] = {key:sum(r.get("usage",{}).get(key,0) for r in reservations) for key in ("input_tokens","output_tokens","cache_tokens")}
        used["reported_requests"] = sum(bool(r.get("usage")) for r in reservations)
        limits = MissionLimits.model_validate(root.get("limits", {})).model_dump()
        started = root.get("active_since")
        seconds = root.get("active_seconds", 0) + (max(0, time.time()-started) if started else 0)
        return dict(**used, active_seconds=round(seconds, 2), limits=limits,
                    remaining_requests=max(0, limits["max_requests"]-used["requests"]),
                    remaining_tokens=max(0, limits["max_tokens"]-used["tokens"]),
                    remaining_seconds=max(0, limits["max_seconds"]-seconds),
                    note="Conservative token estimates and owner-declared prices; external tools may have independent fees")

    def reserve(self, run, id, kind, data, messages):
        with self.store.lock:
            root = self.root(run)
            usage = self.usage(root)
            limits = usage["limits"]
            input_tokens = token_estimate(messages)
            units=2 if kind=="profile" and data.get("provider")=="community" and data.get("verify_duplicates") else 1
            output_tokens = min(1536, self.store.get("settings", "budgets", {}).get("context", 4096)//3)
            if usage["remaining_seconds"] <= 0:
                raise BudgetExceeded("Mission runtime budget reached")
            if usage["remaining_requests"] < units:
                raise BudgetExceeded("Mission request budget reached")
            output_tokens = min(output_tokens, usage["remaining_tokens"]//units-input_tokens)
            if output_tokens < 32:
                raise BudgetExceeded("Mission token budget cannot fit another request")
            cost = 0
            if run.get("mode") != "demo" and kind in {"profile", "acp"}:
                profile = data if isinstance(data, dict) else {}
                declared_free = kind == "profile" and run.get("free_only") and profile.get("free_eligible")
                rates = (profile.get("input_usd_per_million"), profile.get("output_usd_per_million"))
                if not declared_free and None in rates:
                    if limits["max_spend_usd"] is not None:
                        raise BudgetExceeded("Route has no declared model prices; configure prices or explicitly remove the spending ceiling")
                elif not declared_free:
                    cost = units*(input_tokens*rates[0]+output_tokens*rates[1])/1_000_000
            ceiling = limits["max_spend_usd"]
            if ceiling is not None and usage["estimated_spend_usd"] + cost > ceiling:
                raise BudgetExceeded("Mission estimated spending budget reached")
            record = dict(id=id, root_id=root["id"], run_id=run["id"], status="pending", tokens=units*(input_tokens+output_tokens),units=units,
                          input_tokens=input_tokens, output_limit=output_tokens, cost=cost, created_at=time.time())
            self.store.put("reservation", id, record)
            return record

    def finish(self, id, output, *, uncertain=False, usage=None):
        with self.store.lock:
            record = self.store.get("reservation", id)
            if not record: return
            actual = min(record["output_limit"], math.ceil(len(output)/2))
            tokens = record["tokens"] if uncertain or record.get("units",1)>1 else record["input_tokens"]+actual
            if usage and usage.get("reported") and not uncertain:
                tokens = (usage["input_tokens"]+usage["output_tokens"])*record.get("units",1)
            # Keep the reserved price: failed requests can still be charged by providers.
            self.store.patch("reservation", id, dict(status="uncertain" if uncertain else "completed", tokens=tokens, usage=usage or {}, finished_at=time.time()))

    def begin(self, run_id):
        root = self.root(self.store.get("run", run_id))
        count=self.activity.get(root["id"],0)
        self.activity[root["id"]]=count+1
        if not count: self.store.patch("run", root["id"], dict(active_since=time.time()))

    def end(self, run_id):
        root = self.root(self.store.get("run", run_id))
        count=max(0,self.activity.get(root["id"],1)-1)
        self.activity[root["id"]]=count
        if not count and root.get("active_since"):
            self.store.patch("run", root["id"], dict(active_seconds=root.get("active_seconds", 0)+max(0,time.time()-root["active_since"]), active_since=None))
