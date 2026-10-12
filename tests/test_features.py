import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import time
import httpx
import pytest
from fastapi.testclient import TestClient
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from swarm.app import create_app
from swarm.engine import Engine
from swarm.extensions import Extensions
from swarm.gpu import linux_gpus
from swarm.hub import create_hub
from swarm.locking import WorkspaceLock
from swarm.maintenance import create_backup, restore_backup
from swarm.network import PrivatePool
from swarm.pairing import generate_certificate, create_bundle
from swarm.private_listener import PrivateNetwork, save_settings
from swarm.providers import Providers, RouteError
from swarm.research import public_fetch, research_tool
from swarm.schema import Architecture, RunInput, ProfileInput
from swarm.store import Store
from swarm.workspace import Workspace, scoped_path


async def until(predicate, timeout=15):
    async with asyncio.timeout(timeout):
        while not predicate(): await asyncio.sleep(.01)


def setup_project(store, root, tools, checks=None):
    project = dict(id="p", name="Project", root=str(root), tools=tools, checks=checks or {}, search_url="")
    store.put("project", "p", project)
    return project


async def test_tools_require_both_grants_and_staging_is_idempotent_with_conflict_checked_undo(store, tmp_path):
    project = setup_project(store, tmp_path, ["artifact.stage", "workspace.read"])
    workspace = Workspace(store, tmp_path)
    run = dict(id="r", project_id="p", tools_enabled=True)
    agent = dict(id="a", tools=["artifact.stage"])
    file = tmp_path / "code.py"
    file.write_text("old\n")
    with pytest.raises(RouteError): await workspace.execute(run, agent, {"tool":"workspace.read","arguments":{"path":"code.py"}})
    request = {"tool":"artifact.stage","arguments":{"path":"code.py","content":"new\n"}}
    artifact = await workspace.execute(run, agent, request)
    assert file.read_text() == "old\n"
    assert (await workspace.execute(run, agent, request))["id"] == artifact["id"]
    assert len(store.all("artifact")) == 1
    file.write_text("someone else\n")
    with pytest.raises(RouteError): workspace.apply(artifact["id"])
    file.write_text("old\n")
    workspace.apply(artifact["id"])
    assert file.read_text() == "new\n"
    workspace.apply(artifact["id"], undo=True)
    assert file.read_text() == "old\n"
    assert store.all("operation")[0]["status"] == "completed"


@pytest.mark.parametrize("path", ["../escape", ".env", ".git/config", ".ssh/key", ".ENV.secret", ".Git/config"])
def test_project_paths_reject_escape_and_private_files(store, tmp_path, path):
    with pytest.raises(RouteError): scoped_path(setup_project(store, tmp_path, []), path)


async def test_named_check_runs_real_process_once_and_cancellation_stops_it(store, tmp_path):
    project = setup_project(store, tmp_path, ["checks.run"], checks={"count":[sys.executable,"-c","from pathlib import Path; p=Path('counter'); p.write_text(str(int(p.read_text())+1) if p.exists() else '1')"]})
    workspace = Workspace(store, tmp_path)
    run, agent = dict(id="r",project_id="p",tools_enabled=True), dict(id="a",tools=None)
    request = {"tool":"checks.run","arguments":{"name":"count"}}
    assert (await workspace.execute(run,agent,request))["exit_code"] == 0
    await workspace.execute(run,agent,request)
    assert (tmp_path/"counter").read_text() == "1"
    slow = asyncio.create_task(workspace.check("slow",project,[sys.executable,"-c","import time; time.sleep(30)"]))
    await until(lambda:"slow" in workspace.processes)
    process = workspace.processes["slow"]
    slow.cancel()
    await asyncio.gather(slow,return_exceptions=True)
    assert process.returncode is not None and not workspace.processes


async def test_shared_memory_is_project_scoped_and_retrieved_source_has_provenance(store, tmp_path, monkeypatch):
    project = setup_project(store,tmp_path,["research.fetch"])
    workspace = Workspace(store,tmp_path)
    workspace.remember("p","Widget decision","Use a widget cache",tags=["decision"])
    assert workspace.memory("p","widget")[0]["title"] == "Widget decision"
    assert workspace.memory("other","widget") == []
    monkeypatch.setattr("swarm.research.public_fetch",lambda url:(b"<html><script>bad()</script><p>Evidence paragraph</p></html>","text/html"))
    result = await research_tool(store,project,{"id":"r"},{"id":"a"},"research.fetch",{"url":"https://example.com/evidence"})
    assert "Evidence paragraph" in result["text"] and "bad()" not in result["text"]
    source = store.get("source",result["source_id"])
    assert source["agent_id"] == "a" and source["content_hash"] and not source["verified"]


def test_web_retrieval_blocks_private_dns_and_non_https(monkeypatch):
    monkeypatch.setattr("swarm.research.socket.getaddrinfo",lambda *args,**kw:[(2,1,6,"",("127.0.0.1",443))])
    with pytest.raises(RouteError): public_fetch("https://rebinding.example/secrets")
    with pytest.raises(RouteError): public_fetch("http://example.com/")


async def test_bounded_rounds_conditions_and_vote_are_executable(store,vault,tmp_path,monkeypatch,model_fixture):
    store.put("settings","budgets",dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    calls = []
    async def response(name,*args):
        calls.append(name)
        yield '{"decision":"yes"}'
    model_fixture(store,vault,response)
    arch = Architecture(id="rounds",name="Rounds",max_rounds=2,nodes=[
        dict(id="one",name="One",role="decide"),dict(id="two",name="Two",role="decide"),
        dict(id="skip",name="Skipped",role="help",run_if={"source":"one","contains":"nope"}),
        dict(id="vote",name="Vote",role="vote",kind="vote")],edges=[dict(source="one",target="skip"),dict(source="one",target="vote"),dict(source="two",target="vote")]).model_dump()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/"cp.sqlite")) as saver:
        engine = Engine(store,Providers(store,vault,"http://127.0.0.1:1"),PrivatePool(store),saver)
        run = engine.create(RunInput(task="Choose",architecture_id="rounds",mode="live"),arch)
        await until(lambda:store.get("run",run["id"])["status"] in {"completed","interrupted"})
        final = store.get("run",run["id"])
        assert final["status"] == "completed" and calls.count("One") == 2 and "Skipped" not in calls
        assert json.loads(next(r["content"] for r in final["results"] if r["node_id"] == "vote"))["decision"] == "yes"
        branch = engine.branch(final,arch,{"kind":"auto"},"one")
        await until(lambda:store.get("run",branch["id"])["status"] in {"completed","interrupted"})
        assert store.get("run",branch["id"])["parent_run_id"] == run["id"]
        await engine.stop()


async def test_live_agent_uses_tools_then_hands_off_real_artifact_and_memory(store,vault,tmp_path,monkeypatch):
    project = setup_project(store,tmp_path,["artifact.stage","memory.save"])
    store.put("settings","budgets",dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    providers = Providers(store,vault,"http://127.0.0.1:1")
    p = providers.add(ProfileInput(provider="google",label="Fake",api_key="fake",model="fake",free_eligible=True))
    replies = [json.dumps({"tool":"artifact.stage","arguments":{"path":"answer.py","content":"answer = 42\n"}}),"Proposed answer.py; ready for owner review"]
    captured = []
    async def response(profile,messages):
        captured.append(messages)
        yield replies.pop(0)
    monkeypatch.setattr(providers,"compatible",response)
    workspace = Workspace(store,tmp_path)
    arch = Architecture(id="single",name="Single",nodes=[dict(id="a",name="Coder",role="Build")]).model_dump()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/"cp.sqlite")) as saver:
        engine = Engine(store,providers,PrivatePool(store),saver,workspace)
        run = engine.create(RunInput(task="Make answer.py",architecture_id="single",project_id="p",tools_enabled=True,route={"kind":"profile","profile_id":p["id"]}),arch)
        await until(lambda:store.get("run",run["id"])["status"] in {"completed","interrupted"})
        assert store.get("run",run["id"])["status"] == "completed"
        assert not (tmp_path/"answer.py").exists() and store.all("artifact")[0]["status"] == "staged"
        assert "Tool result" in json.dumps(captured[1]) and workspace.memory("p","answer")
        assert len(store.all("call")) == 2
        await engine.stop()


async def test_offline_queue_recovers_when_capacity_returns_and_public_routes_require_consent(store,vault,tmp_path,monkeypatch):
    store.put("settings","budgets",dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True))
    providers = Providers(store,vault,"http://127.0.0.1:1")
    async def no_models(url): return dict(models=[],loaded=[])
    monkeypatch.setattr("swarm.engine.inventory",no_models)
    async def response(*args): yield "Recovered"
    monkeypatch.setattr(providers,"compatible",response)
    arch = Architecture(id="single",name="Single",nodes=[dict(id="a",name="Agent",role="Help")]).model_dump()
    async with AsyncSqliteSaver.from_conn_string(str(tmp_path/"cp.sqlite")) as saver:
        engine = Engine(store,providers,PrivatePool(store),saver)
        run = engine.create(RunInput(task="Wait for capacity",architecture_id="single",queue_if_unavailable=True),arch)
        await until(lambda:store.get("run",run["id"])["status"] == "queued")
        providers.add(ProfileInput(provider="google",label="Fake",api_key="fake",model="fake",free_eligible=True))
        store.patch("run",run["id"],dict(next_retry=0))
        await until(lambda:store.get("run",run["id"])["status"] == "completed")
        public = providers.add(ProfileInput(provider="community",label="Hub",api_key="hub-key",base_url="https://hub.example/v1",model="model",free_eligible=True))
        with pytest.raises(RouteError): await engine.candidates(dict(free_only=True),dict(route=dict(kind="profile",profile_id=public["id"],model="")))
        assert await engine.candidates(dict(free_only=True,public_allowed=True),dict(route=dict(kind="profile",profile_id=public["id"],model="")))
        await engine.stop()


def test_owner_features_api_lab_and_backup_restore_guard(tmp_path,vault):
    root = tmp_path/"state"
    with TestClient(create_app(root,token="owner",vault=vault)) as client:
        assert client.get("/api/features").status_code == 401
        client.headers["Authorization"] = "Bearer owner"
        project = client.post("/api/projects",json=dict(name="Project",root=str(tmp_path),tools=["memory.search"],checks={})).json()
        assert project["id"]
        assert client.post(f"/api/projects/{project['id']}/memory",json=dict(title="Decision",content="Preserve context")).status_code == 201
        lab = client.post("/api/labs",json=dict(task="Compare",architecture_ids=["pipeline"],mode="live")).json()
        assert lab["id"]
        data = client.get("/api/features").json()
        assert {r["architecture"] for r in data["labs"][0]["rows"]} == {"Single-agent baseline","Pipeline"}
        backup = client.post("/api/backups").json()
        with pytest.raises(ValueError): restore_backup(backup["path"],root)
        assert client.get("/api/features").json()["backups"]
    restore_backup(backup["path"],root)
    restored = Store(root/"workbench.sqlite")
    assert restored.all("project")[0]["id"] == project["id"]
    restored.close()


def test_schema_migrates_old_database_and_future_versions_are_rejected(tmp_path):
    path = tmp_path/"old.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE records(kind TEXT,id TEXT,value TEXT,PRIMARY KEY(kind,id))")
        db.execute("INSERT INTO records VALUES('memory','old','{}')")
    store = Store(path)
    assert store.get("memory","old") == {} and store.db.execute("PRAGMA user_version").fetchone()[0] == 5
    store.close()
    with sqlite3.connect(path) as db: db.execute("PRAGMA user_version=99")
    with pytest.raises(ValueError): Store(path)


def test_linux_gpu_inventory_reads_actual_free_vram_without_double_counting(tmp_path):
    device = tmp_path/"card0"/"device"
    device.mkdir(parents=True)
    (device/"vendor").write_text("0x1002")
    (device/"mem_info_vram_total").write_text(str(8*1024**3))
    (device/"mem_info_vram_used").write_text(str(2*1024**3))
    gpu = linux_gpus(tmp_path)[0]
    assert gpu["total_gb"] == 8 and gpu["free_gb"] == 6 and gpu["backend"] == "rocm"


def test_generated_certificate_matches_hostname_and_bundle_contains_no_private_key(store,tmp_path):
    import base64
    from cryptography import x509
    certificate = generate_certificate(tmp_path,"127.0.0.1")
    settings = PrivateNetwork(enabled=True,public_origin="https://127.0.0.1:8443",cert_file=certificate["cert_file"],key_file=certificate["key_file"])
    save_settings(tmp_path,settings)
    bundle = create_bundle(tmp_path,PrivatePool(store))["bundle"]
    data = json.loads(base64.urlsafe_b64decode(bundle))
    assert data["origin"] == settings.public_origin and "PRIVATE KEY" not in data["ca_pem"]
    assert x509.load_pem_x509_certificate(data["ca_pem"].encode())


async def test_volunteer_hub_streams_leased_inference_and_enforces_client_fairness(tmp_path):
    app = create_hub(tmp_path,token="owner")
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://testserver") as client:
            owner = {"Authorization":"Bearer owner"}
            grant = (await client.post("/api/hub/clients",headers=owner,json=dict(name="Client",rpm=1))).json()
            invite = (await client.post("/api/devices/invite",headers=owner)).json()
            joined = (await client.post("/api/worker/join",json=dict(invite=invite["token"],name="Volunteer",hardware={},models=[dict(name="model",size=1)],budgets=dict(ram_gb=4,vram_gb=0,context=4096,allow_cpu=True)))).json()
            await client.post(f"/api/devices/{joined['device_id']}/approve",headers=owner)
            worker_headers = {"Authorization":"Bearer "+joined["token"]}
            client_headers = {"Authorization":"Bearer "+grant["api_key"]}
            assert (await client.get("/v1/models")).status_code == 401
            assert (await client.get("/v1/models",headers=client_headers)).json()["data"][0]["id"] == "model"
            pending = asyncio.create_task(client.post("/v1/chat/completions",headers=client_headers,json=dict(model="model",messages=[dict(role="user",content="Non-sensitive task")],stream=True)))
            await until(lambda:bool(app.state.store.all("job")))
            path = "/api/worker/"+joined["device_id"]
            job = (await client.post(path+"/lease",headers=worker_headers)).json()["job"]
            assert (await client.post(path+"/lease",headers=worker_headers)).json()["job"] is None
            assert (await client.post(path+"/jobs/"+job["id"],headers=worker_headers,json=dict(lease=job["lease"],text="Volunteer output",done=True))).status_code == 200
            result = await pending
            assert "Volunteer output" in result.text and "[DONE]" in result.text
            assert (await client.post("/v1/chat/completions",headers=client_headers,json=dict(model="model",messages=[dict(role="user",content="Again")]))).status_code == 429
            assert app.state.store.get("device",joined["device_id"])["completed_jobs"] == 1
            assert (await client.post("/api/hub/clients",headers=worker_headers,json=dict(name="Unauthorized"))).status_code == 401
