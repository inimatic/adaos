from __future__ import annotations

import asyncio
import time
import threading
from types import SimpleNamespace

import pytest

from adaos.services.webrtc import ice


@pytest.fixture(autouse=True)
def isolated_ice(monkeypatch):
    ice._leases.clear()
    ice._selections.clear()
    ice._next_region.clear()
    ice._unavailable_until.clear()
    ice._fetching.clear()
    monkeypatch.setattr(ice, "_root_client", lambda: None)


def lease():
    turn = lambda host: {"urls": [f"turns:{host}:443?transport=tcp"], "username": "expiring-fixture", "credential": "fixture-hmac"}
    return {"schema": "inimatic.webrtc.ice.v1", "expires_at": time.time()+3600,
            "iceServers": [{"urls": ["stun:ru.turn.inimatic.com:3478"]}, turn("ru.turn.inimatic.com"), turn("eu.turn.inimatic.com")],
            "nodeIceServers": [turn("ru.turn.inimatic.com"), turn("eu.turn.inimatic.com")]}


@pytest.mark.asyncio
async def test_missing_managed_root_preserves_existing_discovery():
    configuration = await ice.browser_configuration("actor:page", "one")
    assert configuration["source"] == "bootstrap"
    assert configuration["iceServers"] == ice.FALLBACK_SERVERS
    assert await ice.native_configuration("actor:page", "one") is None


@pytest.mark.asyncio
async def test_failed_root_cycles_native_tls_regions_on_fresh_generations(monkeypatch):
    monkeypatch.setattr(ice, "_load_lease", lambda peer: lease())
    first = await ice.browser_configuration("actor:page", "one")
    assert first["region_index"] == 0
    assert "nodeIceServers" not in first
    assert (await ice.native_configuration("actor:page", "one"))[0]["urls"] == ["turns:ru.turn.inimatic.com:443?transport=tcp"]
    assert (await ice.browser_configuration("actor:page", "one"))["region_index"] == 0
    assert (await ice.browser_configuration("actor:page", "two"))["region_index"] == 1
    assert (await ice.native_configuration("actor:page", "two"))[0]["urls"] == ["turns:eu.turn.inimatic.com:443?transport=tcp"]
    ice.mark_connected("actor:page", "two")
    assert (await ice.browser_configuration("actor:page", "three"))["region_index"] == 1


@pytest.mark.asyncio
async def test_other_browser_actor_cannot_borrow_a_pending_lease(monkeypatch):
    monkeypatch.setattr(ice, "_load_lease", lambda peer: lease())
    await ice.browser_configuration("owner:page", "same-generation")
    await ice.browser_configuration("owner:page", "other-generation")
    assert (await ice.browser_configuration("guest:page", "same-generation"))["region_index"] == 0


@pytest.mark.asyncio
async def test_expired_credentials_are_not_returned_from_native_cache(monkeypatch):
    monkeypatch.setattr(ice, "_load_lease", lambda peer: lease())
    await ice.browser_configuration("actor:page", "one")
    ice._selections["actor:page", "one"]["expires_at"] = time.time()-1
    monkeypatch.setattr(ice, "_load_lease", lambda peer: None)
    assert await ice.native_configuration("actor:page", "one") is None


@pytest.mark.asyncio
async def test_credentials_are_detached_from_cache_and_owner_loop_keeps_running(monkeypatch):
    def blocking(peer):
        time.sleep(0.05)
        return lease()
    monkeypatch.setattr(ice, "_load_lease", blocking)
    job = asyncio.create_task(ice.browser_configuration("actor:page", "one"))
    await asyncio.sleep(0.01)
    assert not job.done()
    configuration = await job
    configuration["iceServers"][0]["urls"].clear()
    assert (await ice.browser_configuration("actor:page", "one"))["iceServers"][0]["urls"]


@pytest.mark.parametrize("mutate", [
    lambda value: value.update(expires_at=time.time()-1),
    lambda value: value.update(iceServers=[]),
    lambda value: value["nodeIceServers"][0].pop("credential"),
    lambda value: value["iceServers"][0].update(urls=["https://example.com"]),
    lambda value: value["nodeIceServers"][0].update(urls=["stun:example.com:3478"]),
    lambda value: value["nodeIceServers"][0].update(credential=""),
    lambda value: value.update(expires_at=float("nan")),
])
def test_invalid_root_configuration_is_rejected(mutate):
    value = lease()
    mutate(value)
    with pytest.raises(ValueError):
        ice._validate(value)


def test_root_configuration_fetch_is_authenticated_bounded_and_cached(monkeypatch):
    calls = []
    def request(method, path, **kwargs):
        calls.append((method, path, kwargs))
        return lease()
    monkeypatch.setattr(ice, "_root_client", lambda: SimpleNamespace(base_url="https://ru.api.inimatic.com", request=request))
    assert ice._load_lease("actor:page")
    assert ice._load_lease("actor:page")
    assert len(calls) == 1
    assert calls[0][0:2] == ("POST", "/v1/hub/webrtc/ice")
    assert calls[0][2]["timeout"] == 2.0
    assert calls[0][2]["json"]["peer_id"] != "actor:page"


def test_cache_observes_root_changes_after_one_minute_and_partitions_identity(monkeypatch, tmp_path):
    calls = []
    clock = [100.0]
    certificate = tmp_path / "identity.pem"
    certificate.write_text("first public certificate")
    client = SimpleNamespace(base_url="https://ru.api.inimatic.com", cert=(str(certificate), "unused"),
                             request=lambda *args, **kwargs: calls.append(kwargs) or lease())
    monkeypatch.setattr(ice, "_root_client", lambda: client)
    monkeypatch.setattr(ice.time, "monotonic", lambda: clock[0])
    ice._load_lease("actor:page")
    ice._load_lease("actor:page")
    assert len(calls) == 1
    clock[0] += 61
    ice._load_lease("actor:page")
    assert len(calls) == 2
    certificate.write_text("rotated public certificate")
    ice._load_lease("actor:page")
    assert len(calls) == 3


def test_failed_root_lookup_is_not_repeated_by_the_following_offer(monkeypatch):
    calls = []
    clock = [100.0]
    def request(*args, **kwargs):
        calls.append(kwargs)
        raise TimeoutError()
    monkeypatch.setattr(ice, "_root_client", lambda: SimpleNamespace(base_url="https://ru.api.inimatic.com", request=request))
    monkeypatch.setattr(ice.time, "monotonic", lambda: clock[0])
    assert ice._load_lease("actor:page") is None
    assert ice._load_lease("actor:page") is None
    assert len(calls) == 1
    clock[0] += 6
    assert ice._load_lease("actor:page") is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_parallel_lookups_do_not_start_duplicate_requests_or_clear_another_fetch(monkeypatch):
    entered, release = threading.Event(), threading.Event()
    calls = []
    def request(*args, **kwargs):
        calls.append(kwargs)
        entered.set()
        assert release.wait(2)
        return lease()
    monkeypatch.setattr(ice, "_root_client", lambda: SimpleNamespace(base_url="https://ru.api.inimatic.com", request=request))
    job = asyncio.create_task(ice.browser_configuration("actor:page", "one"))
    try:
        assert await asyncio.to_thread(entered.wait, 1)
        assert ice._load_lease("actor:page") is None
        assert ice._load_lease("actor:page") is None
        assert len(calls) == 1 and len(ice._fetching) == 1
    finally:
        release.set()
    assert (await job)["source"] == "root"
    assert not ice._fetching
