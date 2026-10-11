import asyncio
import time
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.engine import Engine
from swarm.network import PrivatePool
from swarm.providers import Providers
from swarm.schema import JoinInput


async def test_auto_routing_prefers_idle_devices_and_defers_busy_local_runtime(store, vault, tmp_path, monkeypatch):
    budgets = dict(ram_gb=12, vram_gb=0, context=4096, allow_cpu=True)
    store.put("settings", "budgets", budgets)
    pool = PrivatePool(store)
    devices = []
    for name in ("busy", "idle"):
        joined = pool.join(JoinInput(invite=pool.invite()["token"], name=name, models=[dict(name="model", size=2*1024**3)], budgets=budgets))
        store.patch("device", joined["device_id"], {"approved": True})
        devices.append(joined["device_id"])
    store.put("job", "occupied", dict(id="occupied", device_id=devices[0], status="queued", deadline=time.time()+60))
    providers = Providers(store, vault, "http://127.0.0.1:1")

    async def installed(url):
        return dict(models=[dict(name="local", size=2*1024**3)], loaded=[])

    monkeypatch.setattr("swarm.engine.inventory", installed)
    monkeypatch.setattr("swarm.engine.hardware", lambda: dict(unified_memory=False, ram_available_gb=16, gpus=[]))
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        engine = Engine(store, providers, pool, saver)
        run = dict(free_only=True)
        agent = dict(route=dict(kind="auto"))
        candidates = await engine.candidates(run, agent)
        assert candidates[0] == ("local", "local")
        async with providers.local_lock:
            candidates = await engine.candidates(run, agent)
            assert candidates[0][1]["device_id"] == devices[1]
            assert candidates[-1] == ("local", "local")


def test_only_one_active_lease_per_device_even_if_worker_is_started_twice(store):
    pool = PrivatePool(store)
    joined = pool.join(JoinInput(invite=pool.invite()["token"], name="device", budgets=dict(ram_gb=4, vram_gb=0)))
    store.patch("device", joined["device_id"], {"approved": True})
    device = pool.authenticate(joined["device_id"], joined["token"])
    for id in ("one", "two"):
        store.put("job", id, dict(id=id, device_id=device["id"], status="queued", model="m", messages=[], deadline=time.time()+60))
    assert pool.lease(device)["id"] == "one"
    assert pool.lease(device) is None
