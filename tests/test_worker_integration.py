import asyncio
import httpx
from swarm.app import create_app


async def test_owner_graph_runs_through_authenticated_private_worker(tmp_path, vault):
    app = create_app(tmp_path, token="owner", vault=vault)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://127.0.0.1:8000", headers={"Authorization": "Bearer owner"}) as owner:
            invite = (await owner.post("/api/devices/invite")).json()["token"]
            paired = (await owner.post("/api/worker/join", json=dict(invite=invite, name="Private inference worker", hardware={}, models=[dict(name="test-model", size=1000)], budgets=dict(ram_gb=4, vram_gb=0)))).json()
            device = paired["device_id"]
            await owner.post(f"/api/devices/{device}/approve")
            run = (await owner.post("/api/runs", json=dict(task="Distributed task", architecture_id="pipeline", route=dict(kind="device", device_id=device, model="test-model")))).json()
            headers = {"Authorization": "Bearer " + paired["token"]}
            contexts = []
            async with asyncio.timeout(10):
                while True:
                    status = (await owner.get(f"/api/runs/{run['id']}")).json()
                    if status["status"] in {"completed", "interrupted"}:
                        break
                    response = await owner.post(f"/api/worker/{device}/lease", headers=headers)
                    job = response.json()["job"]
                    if not job:
                        await asyncio.sleep(.02)
                        continue
                    contexts.append(job["messages"])
                    response = await owner.post(f"/api/worker/{device}/jobs/{job['id']}", headers=headers,
                        json=dict(lease=job["lease"], text=f"Worker output {len(contexts)}", done=True))
                    assert response.status_code == 200
            assert status["status"] == "completed", status["error"]
            assert len(contexts) == 3
            assert any("Worker output 1" in m["content"] for m in contexts[1])
            assert any("Worker output 2" in m["content"] for m in contexts[2])
            assert all("private-key" not in str(c) for c in contexts)
            await owner.post(f"/api/devices/{device}/revoke")
            assert (await owner.post(f"/api/worker/{device}/lease", headers=headers)).status_code == 401
