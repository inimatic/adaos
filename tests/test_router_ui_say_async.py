import asyncio
from pathlib import Path
import sys
import types

import pytest

from adaos.domain import Event
from adaos.services.eventbus import LocalEventBus

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.router import service as router_service_module
from adaos.services.router.service import RouterService


pytestmark = pytest.mark.anyio


async def test_ui_say_handler_is_async() -> None:
    """
    ui.say can be emitted during boot (e.g. greet_on_boot_skill). The router must not
    block the event loop in a synchronous handler because that can stall NATS WS
    handshakes and cause connect timeouts.
    """

    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    handlers = list(getattr(bus, "_subs", {}).get("ui.say") or [])
    assert handlers, "expected RouterService to subscribe ui.say"
    assert any(asyncio.iscoroutinefunction(h) for h in handlers)


async def test_io_out_stream_publish_routes_to_webspace_scoped_browser_topic() -> None:
    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[object] = []
    bus.subscribe("webio.stream.default.telemetry_feed", lambda ev: seen.append(ev))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": 42},
                "_meta": {"webspace_id": "default"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert len(seen) == 1
    event = seen[0]
    assert getattr(event, "type", "") == "webio.stream.default.telemetry_feed"
    assert getattr(event, "payload", {}).get("data") == {"value": 42}
    assert getattr(event, "payload", {}).get("webspace_id") == "default"


async def test_io_out_stream_publish_routes_when_receiver_metadata_times_out(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBIO_RECEIVER_METADATA_TIMEOUT_S", "0.01")

    async def _slow_metadata(_webspace_id: str, _receiver: str) -> dict[str, object]:
        await asyncio.sleep(1.0)
        return {"owner": "skill:slow"}

    monkeypatch.setattr(router_service_module, "_read_webio_receiver_metadata", _slow_metadata)
    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[object] = []
    bus.subscribe("webio.stream.default.telemetry_feed", lambda ev: seen.append(ev))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "owner": "skill:test",
                "data": {"value": 42},
                "_meta": {"webspace_id": "default"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert len(seen) == 1
    assert getattr(seen[0], "payload", {}).get("data") == {"value": 42}


async def test_io_out_stream_publish_unwraps_nested_webspace_id() -> None:
    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[object] = []
    bus.subscribe("webio.stream.default.telemetry_feed", lambda ev: seen.append(ev))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": 42},
                "_meta": {"webspace_id": {"webspace_id": "default"}},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert len(seen) == 1
    assert getattr(seen[0], "type", "") == "webio.stream.default.telemetry_feed"


async def test_io_out_stream_publish_unwraps_stringified_webspace_id() -> None:
    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[object] = []
    bus.subscribe("webio.stream.default.telemetry_feed", lambda ev: seen.append(ev))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": 42},
                "_meta": {"webspace_id": "{'webspace_id': 'default'}"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert len(seen) == 1
    assert getattr(seen[0], "type", "") == "webio.stream.default.telemetry_feed"


async def test_io_out_stream_publish_emits_node_qualified_topics_when_node_owned() -> None:
    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[str] = []
    bus.subscribe("webio.stream.default.nodes.member-01.telemetry_feed", lambda ev: seen.append(getattr(ev, "type", "")))
    bus.subscribe("webio.stream.nodes.member-01.telemetry_feed", lambda ev: seen.append(getattr(ev, "type", "")))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": 42},
                "_meta": {"webspace_id": "default", "node_id": "member-01"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert "webio.stream.default.nodes.member-01.telemetry_feed" in seen
    assert "webio.stream.nodes.member-01.telemetry_feed" in seen


async def test_io_out_stream_publish_keeps_remote_node_events_off_unqualified_topic(monkeypatch) -> None:
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: types.SimpleNamespace(config=types.SimpleNamespace(node_id="hub-node")),
    )
    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[str] = []
    bus.subscribe("webio.stream.default.telemetry_feed", lambda ev: seen.append(getattr(ev, "type", "")))
    bus.subscribe("webio.stream.default.nodes.member-01.telemetry_feed", lambda ev: seen.append(getattr(ev, "type", "")))
    bus.subscribe("webio.stream.nodes.member-01.telemetry_feed", lambda ev: seen.append(getattr(ev, "type", "")))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": 42},
                "_meta": {"webspace_id": "default", "node_id": "member-01"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert "webio.stream.default.telemetry_feed" not in seen
    assert "webio.stream.default.nodes.member-01.telemetry_feed" in seen
    assert "webio.stream.nodes.member-01.telemetry_feed" in seen


async def test_io_out_stream_publish_keeps_local_node_events_on_unqualified_topic(monkeypatch) -> None:
    monkeypatch.setattr(
        router_service_module,
        "get_ctx",
        lambda: types.SimpleNamespace(config=types.SimpleNamespace(node_id="hub-node")),
    )
    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    await router.start()

    seen: list[str] = []
    bus.subscribe("webio.stream.default.telemetry_feed", lambda ev: seen.append(getattr(ev, "type", "")))
    bus.subscribe("webio.stream.default.nodes.hub-node.telemetry_feed", lambda ev: seen.append(getattr(ev, "type", "")))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": 42},
                "_meta": {"webspace_id": "default", "node_id": "hub-node"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert "webio.stream.default.telemetry_feed" in seen
    assert "webio.stream.default.nodes.hub-node.telemetry_feed" in seen


async def test_webio_stream_guard_denied_owner_does_not_crash(monkeypatch) -> None:
    import adaos.services.yjs.owner_guard as owner_guard

    monkeypatch.setenv("ADAOS_WEBIO_STREAM_WARN_BYTES", "8")
    monkeypatch.setenv("ADAOS_WEBIO_STREAM_BLOCK_BYTES", "16")
    monkeypatch.setattr(
        owner_guard,
        "admit_owner_work",
        lambda **kwargs: {
            "allowed": False,
            "owner": kwargs["owner"],
            "reason": "browser_stream_payload_blocked",
            "retry_after_s": 45.0,
        },
    )

    assert (
        router_service_module._webio_stream_admit(
            webspace_id="desktop",
            receiver="infrascope.inspector.local",
            owner="skill:infrascope_skill",
            payload_bytes=2048,
        )
        is False
    )


async def test_webio_stream_guard_uses_declared_receiver_budget(monkeypatch) -> None:
    import adaos.services.yjs.owner_guard as owner_guard

    captured: dict[str, object] = {}
    with router_service_module._WEBIO_STREAM_GUARD_STATS_LOCK:
        router_service_module._WEBIO_STREAM_GUARD_STATS.clear()

    def _admit_owner_work(**kwargs):
        captured.update(kwargs)
        return {
            "allowed": False,
            "owner": kwargs["owner"],
            "reason": kwargs["policy"]["reason"],
            "retry_after_s": 30.0,
        }

    monkeypatch.setenv("ADAOS_WEBIO_STREAM_WARN_BYTES", "65536")
    monkeypatch.setenv("ADAOS_WEBIO_STREAM_BLOCK_BYTES", "262144")
    monkeypatch.setattr(owner_guard, "admit_owner_work", _admit_owner_work)

    result = router_service_module._webio_stream_admit(
        webspace_id="desktop",
        receiver="telemetry_feed",
        owner="skill:telemetry_skill",
        payload_bytes=2048,
        fanout_total=1,
        receiver_meta={
            "mode": "replace",
            "origin": "skill:telemetry_skill",
            "snapshotPolicy": "on_subscribe",
            "budget": {"maxPayloadBytes": 1024, "maxFanout": 4},
            "guardVisibility": {"degradedState": "Telemetry stream paused", "quarantine": True},
            "route": {
                "kind": "stream",
                "surface": "widget:telemetry",
                "owner": "telemetry_skill",
            },
        },
    )

    assert result is False
    assert captured["path"] == "stream/telemetry_feed"
    assert captured["owner"] == "skill:telemetry_skill"
    policy = captured["policy"]
    assert policy["policy_state"] == "block"
    assert policy["reason"] == "browser_stream_declared_payload_budget_exceeded"
    assert policy["declared_max_payload_bytes"] == 1024
    assert policy["receiver_origin"] == "skill:telemetry_skill"
    assert policy["receiver_mode"] == "replace"
    assert policy["snapshot_policy"] == "on_subscribe"
    assert policy["route"]["surface"] == "widget:telemetry"
    assert policy["guard_visibility"]["degradedState"] == "Telemetry stream paused"
    snapshot = router_service_module.webio_stream_guard_snapshot(
        webspace_id="desktop",
        receiver="telemetry_feed",
        owner="skill:telemetry_skill",
    )
    assert snapshot["total"] == 1
    row = snapshot["items"][0]
    assert row["attempted_total"] == 1
    assert row["suppressed_total"] == 1
    assert row["declared_max_payload_bytes"] == 1024
    assert row["surface"] == "widget:telemetry"


async def test_router_stream_publish_uses_materialized_receiver_owner(monkeypatch) -> None:
    import adaos.services.yjs.owner_guard as owner_guard

    captured: dict[str, object] = {}
    with router_service_module._WEBIO_STREAM_GUARD_STATS_LOCK:
        router_service_module._WEBIO_STREAM_GUARD_STATS.clear()

    def _admit_owner_work(**kwargs):
        captured.update(kwargs)
        return {
            "allowed": False,
            "owner": kwargs["owner"],
            "reason": kwargs["policy"]["reason"],
            "retry_after_s": 30.0,
        }

    async def _receiver_metadata(_webspace_id: str, _receiver: str):
        return {
            "origin": "skill:telemetry_skill",
            "budget": {"maxPayloadBytes": 64},
            "route": {"kind": "stream", "surface": "widget:telemetry"},
        }

    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    monkeypatch.setenv("ADAOS_WEBIO_STREAM_WARN_BYTES", "65536")
    monkeypatch.setenv("ADAOS_WEBIO_STREAM_BLOCK_BYTES", "262144")
    monkeypatch.setattr(owner_guard, "admit_owner_work", _admit_owner_work)
    monkeypatch.setattr(router, "_webio_receiver_metadata", _receiver_metadata)

    await router.start()

    seen: list[object] = []
    bus.subscribe("webio.stream.desktop.telemetry_feed", lambda ev: seen.append(ev))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": "x" * 128},
                "_meta": {"webspace_id": "desktop"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert seen == []
    assert captured["owner"] == "skill:telemetry_skill"
    assert captured["policy"]["route"]["surface"] == "widget:telemetry"
    snapshot = router_service_module.webio_stream_guard_snapshot(receiver="telemetry_feed")
    assert snapshot["items"][0]["suppressed_total"] == 1
    assert snapshot["items"][0]["owner"] == "skill:telemetry_skill"


async def test_router_stream_guard_snapshot_tracks_published_receiver(monkeypatch) -> None:
    with router_service_module._WEBIO_STREAM_GUARD_STATS_LOCK:
        router_service_module._WEBIO_STREAM_GUARD_STATS.clear()

    async def _receiver_metadata(_webspace_id: str, _receiver: str):
        return {
            "origin": "skill:telemetry_skill",
            "budget": {"maxPayloadBytes": 4096},
            "route": {"kind": "stream", "surface": "widget:telemetry"},
        }

    bus = LocalEventBus()
    router = RouterService(eventbus=bus, base_dir=Path("."))
    monkeypatch.setattr(router, "_webio_receiver_metadata", _receiver_metadata)

    await router.start()

    seen: list[object] = []
    bus.subscribe("webio.stream.desktop.telemetry_feed", lambda ev: seen.append(ev))
    bus.publish(
        Event(
            type="io.out.stream.publish",
            source="test",
            ts=123.0,
            payload={
                "receiver": "telemetry_feed",
                "data": {"value": 42},
                "_meta": {"webspace_id": "desktop"},
            },
        )
    )

    await bus.wait_for_idle(timeout=1.0)
    assert len(seen) == 1
    snapshot = router_service_module.webio_stream_guard_snapshot(
        webspace_id="desktop",
        receiver="telemetry_feed",
        owner="skill:telemetry_skill",
    )
    assert snapshot["total"] == 1
    row = snapshot["items"][0]
    assert row["attempted_total"] == 1
    assert row["published_total"] == 1
    assert row["published_fanout_total"] == 1
    assert row["surface"] == "widget:telemetry"
    assert row["last_event"] == "published"

