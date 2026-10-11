import pytest
from fastapi.testclient import TestClient
from swarm.app import create_app
from swarm.schema import Architecture


def test_auth_origin_and_secret_boundaries(tmp_path, vault):
    with TestClient(create_app(tmp_path, token="owner", vault=vault)) as client:
        assert client.get("/api/snapshot").status_code == 401
        assert client.post("/api/session", json={"token": "wrong"}).status_code == 401
        assert client.post("/api/session", json={"token": "owner"}).status_code == 200
        assert client.post("/api/devices/invite", headers={"origin": "https://evil.example"}).status_code == 403
        r = client.post("/api/profiles", json=dict(provider="google", label="Test key", api_key="private-key", model="test", ephemeral=True))
        assert r.status_code == 201
        assert "private-key" not in r.text
        r = client.get("/api/snapshot")
        assert r.status_code == 200
        assert "private-key" not in r.text
        assert r.json()["profiles"][0]["quota_group"] == "google:unknown-shared"


def test_architecture_validation_and_builtin_protection(tmp_path, vault):
    with TestClient(create_app(tmp_path, token="owner", vault=vault)) as client:
        client.post("/api/session", json={"token": "owner"})
        data = client.get("/api/snapshot").json()["architectures"][0]
        assert client.post("/api/architectures", json=data).status_code == 400
        data["id"] = "custom"
        assert client.post("/api/architectures", json=data).status_code == 201
        data["edges"].append(dict(source="synthesizer", target="planner"))
        assert client.post("/api/architectures", json=data).status_code == 422


def test_custom_endpoint_cannot_embed_credentials_or_send_keys_over_remote_http(tmp_path, vault):
    with TestClient(create_app(tmp_path, token="owner", vault=vault)) as client:
        client.post("/api/session", json={"token": "owner"})
        for url in ("https://key:secret@remote.example/v1", "http://remote.example/v1", "https://remote.example/v1?key=secret"):
            r = client.post("/api/profiles", json=dict(provider="compatible", label="Endpoint", base_url=url, model="test"))
            assert r.status_code == 400
