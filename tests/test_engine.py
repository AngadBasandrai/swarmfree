import asyncio
import pytest
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.engine import Engine
from swarm.network import PrivatePool
from swarm.providers import Providers, RouteError
from swarm.schema import Architecture, RunInput, ProfileInput
from swarm.templates import TEMPLATES


async def until(predicate, timeout=4):
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(.01)


@pytest.mark.asyncio
async def test_join_waits_for_both_branches_and_receives_their_work(store, vault, tmp_path, monkeypatch, model_fixture):
    store.put("settings", "budgets", dict(ram_gb=4, vram_gb=0, context=4096, allow_cpu=True))
    calls = []

    async def response(name, role, task, prior, steering=""):
        calls.append((name, [p["name"] for p in prior]))
        if name == "Skeptic":
            await asyncio.sleep(.05)
        yield name + " output"

    model_fixture(store, vault, response)
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        engine = Engine(store, Providers(store, vault, "http://127.0.0.1:1"), PrivatePool(store), saver)
        run = engine.create(RunInput(task="Analyze it", architecture_id="parallel", mode="live"), next(t for t in TEMPLATES if t["id"] == "parallel"))
        await until(lambda: store.get("run", run["id"])["status"] in {"completed", "interrupted"})
        assert store.get("run", run["id"])["status"] == "completed"
        assert calls[-1][0] == "Synthesizer" and set(calls[-1][1]) == {"Analyst", "Skeptic"}
        context = engine.agents(run["id"])[-1]["messages"][0]["content"]
        assert "Analyst output" in context and "Skeptic output" in context
        await engine.stop()


@pytest.mark.asyncio
async def test_pause_and_process_restart_resume_without_repeating_completed_agent(store, vault, tmp_path, monkeypatch, model_fixture):
    store.put("settings", "budgets", dict(ram_gb=4, vram_gb=0, context=4096, allow_cpu=True))
    calls = []
    gate = asyncio.Event()

    async def response(name, role, task, prior, steering=""):
        calls.append(name)
        if name == "Builder" and not gate.is_set():
            yield "interrupted draft"
            await gate.wait()
        yield name + " completed"

    model_fixture(store, vault, response)
    path = str(tmp_path / "checkpoints.sqlite")
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        engine = Engine(store, Providers(store, vault, "http://127.0.0.1:1"), PrivatePool(store), saver)
        run = engine.create(RunInput(task="Continue work", architecture_id="pipeline", mode="live"), next(t for t in TEMPLATES if t["id"] == "pipeline"))
        await until(lambda: "Builder" in calls)
        await engine.pause(run["id"])
        assert store.get("run", run["id"])["status"] == "paused"
        await engine.stop()
    gate.set()
    async with AsyncSqliteSaver.from_conn_string(path) as saver:
        engine = Engine(store, Providers(store, vault, "http://127.0.0.1:1"), PrivatePool(store), saver)
        engine.start(run["id"])
        await until(lambda: store.get("run", run["id"])["status"] in {"completed", "interrupted"})
        assert store.get("run", run["id"])["status"] == "completed"
        assert calls.count("Planner") == 1
        assert calls.count("Builder") == 2
        assert calls.count("Reviewer") == 1
        builder = next(a for a in engine.agents(run["id"]) if a["name"] == "Builder")
        assert any(not m["complete"] for m in builder["messages"])
        history = engine.messages(store.get("run", run["id"]), builder)
        assert all("interrupted draft" not in m["content"] for m in history)
        await engine.stop()


@pytest.mark.asyncio
async def test_provider_fallback_keeps_history_and_excludes_partial_draft(store, vault, tmp_path, monkeypatch, model_fixture):
    store.put("settings", "budgets", dict(ram_gb=4, vram_gb=0, context=4096, allow_cpu=True))
    providers = Providers(store, vault, "http://127.0.0.1:1")
    for i in range(2):
        providers.add(ProfileInput(provider="google", label=f"profile-{i}", api_key=f"key-{i}", quota_scope=f"project-{i}", model="test", free_eligible=True))
    captured = []

    async def compatible(profile, messages):
        captured.append(messages)
        if profile["label"] == "profile-0":
            yield "partial output"
            raise RouteError("connection interrupted")
        yield "final answer from another model"

    async def no_local(url):
        return dict(models=[], loaded=[])

    monkeypatch.setattr(providers, "compatible", compatible)
    monkeypatch.setattr("swarm.engine.inventory", no_local)
    arch = Architecture(id="single", name="Single", nodes=[dict(id="agent", name="Agent", role="Help")]).model_dump()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path / "checkpoints.sqlite")) as saver:
        engine = Engine(store, providers, PrivatePool(store), saver)
        run = engine.create(RunInput(task="A real task", architecture_id="single"), arch)
        await until(lambda: store.get("run", run["id"])["status"] in {"completed", "interrupted"})
        assert store.get("run", run["id"])["status"] == "completed"
        assert captured[0] == captured[1]
        messages = engine.agents(run["id"])[0]["messages"]
        assert messages[1]["content"] == "partial output" and not messages[1]["complete"]
        assert messages[2]["complete"]
        await engine.stop()
