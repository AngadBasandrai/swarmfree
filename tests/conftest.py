import pytest
from swarm.store import Store


class MemoryVault:
    def __init__(self):
        self.keys = {}

    def status(self):
        return dict(persistent=False, message="Test session vault")

    def set(self, id, key, ephemeral=False):
        if key:
            self.keys[id] = key
            return "session"
        return "none"

    def get(self, id):
        return self.keys.get(id)

    def delete(self, id):
        self.keys.pop(id, None)


@pytest.fixture
def store(tmp_path):
    value = Store(tmp_path / "workbench.sqlite")
    yield value
    value.close()


@pytest.fixture
def vault():
    return MemoryVault()


@pytest.fixture
def model_fixture(monkeypatch):
    from scripts.test_model_fixture import install_model_fixture
    return lambda store, vault, response=None: install_model_fixture(store, vault, monkeypatch, response)
