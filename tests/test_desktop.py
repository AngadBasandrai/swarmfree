from fastapi.testclient import TestClient
from swarm.app import create_app
from swarm.private_listener import WorkerOnly


def test_desktop_engine_requires_native_credentials_and_has_no_web_ui(tmp_path):
    with TestClient(create_app(tmp_path, "native-test-owner-token", desktop=True)) as client:
        assert client.get("/").status_code == 404
        assert client.get("/api/health").json()["status"] == "ok"
        assert client.post("/api/session", json={}).status_code == 401
        assert client.get("/api/snapshot").status_code == 401
        assert client.get("/api/snapshot", headers={"Authorization": "Bearer native-test-owner-token"}).status_code == 200


def test_private_ingress_never_exposes_the_owner_api(tmp_path):
    app = create_app(tmp_path, "owner-secret", desktop=True)
    with TestClient(app, headers={"Authorization": "Bearer owner-secret"}) as owner:
        worker = TestClient(WorkerOnly(app))
        assert worker.get("/").status_code == 404
        assert worker.get("/api/snapshot", headers={"Authorization": "Bearer owner-secret"}).status_code == 404
        assert worker.post("/api/session", json={"token": "owner-secret"}).status_code == 404
        invite = owner.post("/api/devices/invite").json()["token"]
        result = worker.post("/api/worker/join", json=dict(invite=invite, name="TLS worker", hardware={}, models=[],
            budgets=dict(ram_gb=4, vram_gb=0, context=4096, allow_cpu=True)))
        assert result.status_code == 200
        assert owner.get("/api/snapshot").json()["devices"][0]["name"] == "TLS worker"


def test_private_listener_settings_validate_and_persist(tmp_path):
    with TestClient(create_app(tmp_path, "owner", desktop=True), headers={"Authorization": "Bearer owner"}) as client:
        assert client.get("/api/private-network").json()["enabled"] is False
        bad = dict(enabled=True, public_origin="http://unsafe:8443", cert_file="none", key_file="none")
        assert client.put("/api/private-network", json=bad).status_code == 422
        bad["public_origin"] = "https://safe:8443"
        assert client.put("/api/private-network", json=bad).status_code == 400
        assert not (tmp_path / "private-network.json").exists()
        assert client.put("/api/private-network", json=dict(enabled=False, port=9555)).status_code == 200
        assert client.get("/api/private-network").json()["port"] == 9555
