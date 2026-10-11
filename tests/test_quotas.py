import json
import sqlite3
import time
import httpx
import pytest
from swarm.providers import Providers, RouteError
from swarm.schema import ProfileInput


def google(label, key, scope=""):
    return ProfileInput(provider="google", label=label, api_key=key, account=label, quota_scope=scope,
        model="test-model", free_eligible=True, requests_per_minute=1, ephemeral=True)


@pytest.mark.asyncio
async def test_account_labels_and_duplicate_keys_do_not_create_quota(store, vault):
    p = Providers(store, vault, "http://127.0.0.1:11434")
    first = p.add(google("account-one", "key-1", "project-a"))
    second = p.add(google("account-two", "key-2", "PROJECT-A"))
    third = p.add(google("account-three", "key-3", "project-b"))
    await p.claim(first)
    with pytest.raises(RouteError, match="quota group"):
        await p.claim(second)
    await p.claim(third)
    with pytest.raises(ValueError, match="already linked"):
        p.add(google("duplicate", "key-1", "different-project"))


@pytest.mark.asyncio
async def test_unknown_scopes_are_shared_and_429_blocks_sibling_keys(store, vault, monkeypatch):
    p = Providers(store, vault, "http://127.0.0.1:11434")
    first = p.add(google("one", "key-1"))
    second = p.add(google("two", "key-2"))
    calls = []

    def response(request):
        calls.append(request)
        return httpx.Response(429, headers={"retry-after": "120"})

    original = httpx.AsyncClient
    monkeypatch.setattr("swarm.providers.httpx.AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(response), **kwargs))
    with pytest.raises(RouteError, match="rate limit"):
        async for _ in p.compatible(first, [dict(role="user", content="hello")]):
            pass
    with pytest.raises(RouteError, match="cooling down"):
        async for _ in p.compatible(second, [dict(role="user", content="hello")]):
            pass
    assert len(calls) == 1
    assert p.public()[1]["cooldown_seconds"] >= 115


def test_keys_never_enter_record_database_or_api_profiles(store, vault):
    secret = "super-secret-provider-credential"
    p = Providers(store, vault, "http://127.0.0.1:11434")
    profile = p.add(google("personal", secret, "my-project"))
    assert secret not in json.dumps(store.all("profile"))
    assert secret not in json.dumps(p.public())
    assert vault.get(profile["id"]) == secret


def test_free_only_excludes_paid_openrouter_models_and_unverifiable_gateways(store, vault):
    p = Providers(store, vault, "http://127.0.0.1:11434")
    paid = p.add(ProfileInput(provider="openrouter", label="paid", api_key="a", model="paid/model", free_eligible=True))
    free = p.add(ProfileInput(provider="openrouter", label="free", api_key="b", model="openrouter/free", free_eligible=True))
    gateway = p.add(ProfileInput(provider="freellmpool", label="proxy", model="auto", free_eligible=True))
    assert not p.eligible(paid, True)
    assert p.eligible(free, True)
    assert not p.eligible(gateway, True)
    assert p.eligible(gateway, False)


@pytest.mark.asyncio
async def test_truncated_provider_stream_is_never_marked_complete(store, vault, monkeypatch):
    p = Providers(store, vault, "http://127.0.0.1:11434")
    profile = p.add(google("one", "key"))
    original = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text='data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'))
    monkeypatch.setattr("swarm.providers.httpx.AsyncClient", lambda **kwargs: original(transport=transport, **kwargs))
    content = []
    with pytest.raises(RouteError, match="incomplete stream"):
        async for text in p.compatible(profile, [dict(role="user", content="hi")]):
            content.append(text)
    assert content == ["partial"]
