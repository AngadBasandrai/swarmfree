import asyncio
import time
import pytest
from swarm.network import PrivatePool
from swarm.schema import JoinInput


def join(pool):
    invite = pool.invite()
    return pool.join(JoinInput(invite=invite["token"], name="Other device", budgets=dict(ram_gb=4, vram_gb=0)))


@pytest.mark.asyncio
async def test_pairing_requires_owner_approval_and_rejects_reused_invites(store):
    pool = PrivatePool(store)
    invite = pool.invite()
    form = JoinInput(invite=invite["token"], name="Device", budgets=dict(ram_gb=4, vram_gb=0))
    joined = pool.join(form)
    with pytest.raises(ValueError, match="already used"):
        pool.join(form)
    device = pool.authenticate(joined["device_id"], joined["token"])
    assert pool.lease(device) is None
    stream = pool.infer(device["id"], "model", [])
    with pytest.raises(Exception, match="not approved"):
        await anext(stream)
    assert "token_hash" not in pool.public()[0]


@pytest.mark.asyncio
async def test_worker_stream_and_cancel_fence_stale_results(store):
    pool = PrivatePool(store)
    joined = join(pool)
    store.patch("device", joined["device_id"], {"approved": True})
    device = pool.authenticate(joined["device_id"], joined["token"])
    stream = pool.infer(device["id"], "test-model", [dict(role="user", content="work")])
    waiting = asyncio.create_task(anext(stream))
    await asyncio.sleep(.02)
    job = pool.lease(device)
    assert job and job["messages"][0]["content"] == "work"
    pool.result(device, job["id"], job["lease"], dict(text="hello"))
    assert await waiting == "hello"
    await stream.aclose()
    assert store.get("job", job["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="cancelled"):
        pool.result(device, job["id"], job["lease"], dict(text="hello stale", done=True))


@pytest.mark.asyncio
async def test_expired_lease_is_rejected_even_with_correct_token(store):
    pool = PrivatePool(store)
    joined = join(pool)
    store.patch("device", joined["device_id"], {"approved": True})
    device = pool.authenticate(joined["device_id"], joined["token"])
    stream = pool.infer(device["id"], "model", [])
    task = asyncio.create_task(anext(stream))
    await asyncio.sleep(.02)
    job = pool.lease(device)
    store.patch("job", job["id"], {"lease_until": time.time()-1})
    with pytest.raises(ValueError, match="expired"):
        pool.result(device, job["id"], job["lease"], dict(text="untrusted", done=True))
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    await stream.aclose()
