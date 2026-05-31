from __future__ import annotations

import asyncio
import importlib
import sys
import types
from types import SimpleNamespace

y_py_module = sys.modules.get("y_py")
if y_py_module is None:
    y_py_module = types.SimpleNamespace()
    sys.modules["y_py"] = y_py_module
if not hasattr(y_py_module, "YDoc"):
    y_py_module.YDoc = type("YDoc", (), {})
if not hasattr(y_py_module, "YMap"):
    y_py_module.YMap = type("YMap", (), {})
if not hasattr(y_py_module, "YArray"):
    y_py_module.YArray = type("YArray", (), {})
if not hasattr(y_py_module, "encode_state_vector"):
    y_py_module.encode_state_vector = lambda *args, **kwargs: b""
if not hasattr(y_py_module, "encode_state_as_update"):
    y_py_module.encode_state_as_update = lambda *args, **kwargs: b""
if not hasattr(y_py_module, "apply_update"):
    y_py_module.apply_update = lambda *args, **kwargs: None
if "ypy_websocket.ystore" not in sys.modules:
    ystore_module = types.ModuleType("ypy_websocket.ystore")
    ystore_module.BaseYStore = type("BaseYStore", (), {})
    ystore_module.YDocNotFound = type("YDocNotFound", (Exception,), {})
    sys.modules["ypy_websocket.ystore"] = ystore_module
if "ypy_websocket" not in sys.modules:
    pkg = types.ModuleType("ypy_websocket")
    pkg.ystore = sys.modules["ypy_websocket.ystore"]
    sys.modules["ypy_websocket"] = pkg

mod = importlib.import_module("adaos.services.subnet.link_client")


def test_member_snapshot_heartbeat_carries_core_build_version(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    monkeypatch.setattr(mod, "BUILD_INFO", SimpleNamespace(version="0.1.0", build_date="2026-05-22T09:17:56+03:00"))
    monkeypatch.setattr(
        mod,
        "get_ctx",
        lambda: SimpleNamespace(
            config=SimpleNamespace(
                node_id="member-1",
                subnet_id="sn-1",
                role="member",
                node_settings=SimpleNamespace(node_names=["Mediapoint"]),
                primary_node_name="Mediapoint",
            )
        ),
    )
    monkeypatch.setattr(mod, "runtime_lifecycle_snapshot", lambda: {"node_state": "ready", "reason": "", "draining": False})
    monkeypatch.setattr(
        mod,
        "active_slot_manifest",
        lambda: {
            "slot": "A",
            "target_rev": "HEAD",
            "target_version": "6ae4ddbc8bc4ad25f391bf18f0ed868052d11a92",
            "base_version": "0.1.0",
            "build_version": "0.1.0+1.6ae4ddb",
            "build_date": "2026-05-22T09:17:56+03:00",
            "git_commit": "6ae4ddbc8bc4ad25f391bf18f0ed868052d11a92",
            "git_short_commit": "6ae4ddb",
            "git_subject": "Fix core update launch timeout clock",
        },
    )
    monkeypatch.setattr(mod, "slot_status", lambda: {"active_slot": "A", "previous_slot": "B"})
    monkeypatch.setattr(
        mod,
        "read_core_update_status",
        lambda: {"state": "succeeded", "phase": "validate", "target_slot": "A"},
    )
    monkeypatch.setattr(mod, "read_core_update_last_result", lambda: {})

    snapshot = client._local_node_snapshot_heartbeat()

    assert snapshot["build"]["runtime_version"] == "0.1.0+1.6ae4ddb"
    assert snapshot["build"]["runtime_build_version"] == "0.1.0+1.6ae4ddb"
    assert snapshot["build"]["runtime_base_version"] == "0.1.0"
    assert snapshot["build"]["runtime_target_version"] == "6ae4ddbc8bc4ad25f391bf18f0ed868052d11a92"
    assert snapshot["slots"]["active_manifest"]["build_version"] == "0.1.0+1.6ae4ddb"


def test_member_link_client_does_not_forward_unqualified_node_webio_streams(monkeypatch) -> None:
    class _FakeBus:
        def __init__(self) -> None:
            self.subscriber = None

        def subscribe(self, prefix, handler) -> None:
            assert prefix == "*"
            self.subscriber = handler

    fake_bus = _FakeBus()
    fake_ctx = SimpleNamespace(bus=fake_bus, config=SimpleNamespace(node_id="member-1"))
    monkeypatch.setattr(mod, "get_ctx", lambda: fake_ctx)

    client = mod.MemberLinkClient()
    client._connected.set()
    client._bus_prefixes = None
    client._ensure_bus_subscription()

    assert fake_bus.subscriber is not None
    fake_bus.subscriber(
        SimpleNamespace(
            type="webio.stream.homepoint.browsers.devices",
            payload={
                "receiver": "browsers.devices",
                "node_id": "member-1",
                "data": [],
                "_meta": {"webspace_id": "homepoint", "node_id": "member-1"},
            },
            source="sdk.io.out",
            ts=123.0,
        )
    )

    assert client._out_q.empty()


def test_member_link_client_forwards_node_qualified_webio_streams(monkeypatch) -> None:
    class _FakeBus:
        def __init__(self) -> None:
            self.subscriber = None

        def subscribe(self, prefix, handler) -> None:
            assert prefix == "*"
            self.subscriber = handler

    fake_bus = _FakeBus()
    fake_ctx = SimpleNamespace(bus=fake_bus, config=SimpleNamespace(node_id="member-1"))
    monkeypatch.setattr(mod, "get_ctx", lambda: fake_ctx)

    client = mod.MemberLinkClient()
    client._connected.set()
    client._bus_prefixes = None
    client._ensure_bus_subscription()

    assert fake_bus.subscriber is not None
    fake_bus.subscriber(
        SimpleNamespace(
            type="webio.stream.homepoint.nodes.member-1.browsers.devices",
            payload={
                "receiver": "browsers.devices",
                "node_id": "member-1",
                "data": [],
                "_meta": {"webspace_id": "homepoint", "node_id": "member-1"},
            },
            source="sdk.io.out",
            ts=123.0,
        )
    )

    queued = client._out_q.get_nowait()
    assert queued["event"]["type"] == "webio.stream.homepoint.nodes.member-1.browsers.devices"


def test_member_link_client_skips_hub_follow_when_node_config_disables_updates(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    monkeypatch.delenv("ADAOS_MEMBER_FOLLOW_HUB_UPDATE", raising=False)
    monkeypatch.delenv("ENV_TYPE", raising=False)
    monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(core_update_enabled=False)))

    def _fail_post_local_admin(*_args, **_kwargs):
        raise AssertionError("local admin must not be called")

    monkeypatch.setattr(mod.MemberLinkClient, "_post_local_admin", staticmethod(_fail_post_local_admin))

    asyncio.run(
        client._follow_hub_core_update(
            {
                "state": "countdown",
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "abc123",
            }
        )
    )

    assert client._last_follow_key == ""
    assert client._last_follow_result == {}


def test_member_link_client_skips_hub_follow_in_dev_environment(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    monkeypatch.delenv("ADAOS_MEMBER_FOLLOW_HUB_UPDATE", raising=False)
    monkeypatch.delenv("ADAOS_DEV_ALLOW_CORE_UPDATE", raising=False)
    monkeypatch.setenv("ENV_TYPE", "dev")
    monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(core_update_enabled=True)))

    def _fail_post_local_admin(*_args, **_kwargs):
        raise AssertionError("local admin must not be called")

    monkeypatch.setattr(mod.MemberLinkClient, "_post_local_admin", staticmethod(_fail_post_local_admin))

    asyncio.run(
        client._follow_hub_core_update(
            {
                "state": "countdown",
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "abc123",
            }
        )
    )

    assert client._last_follow_key == ""
    assert client._last_follow_result == {}


def test_member_link_client_catches_up_after_hub_succeeded_status(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    calls: list[tuple[str, dict]] = []
    monkeypatch.delenv("ADAOS_MEMBER_FOLLOW_HUB_UPDATE", raising=False)
    monkeypatch.delenv("ENV_TYPE", raising=False)
    monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(core_update_enabled=True)))
    monkeypatch.setattr(mod, "core_update_reactions_disabled_reason", lambda: None)
    monkeypatch.setattr(mod, "read_core_update_status", lambda: {"state": "succeeded", "target_version": "old1234"})
    monkeypatch.setattr(mod, "active_slot_manifest", lambda: {"target_version": "old1234", "git_short_commit": "old1234"})

    def _post_local_admin(path, body):
        calls.append((path, dict(body)))
        return {"ok": True, "accepted": True}

    monkeypatch.setattr(mod.MemberLinkClient, "_post_local_admin", staticmethod(_post_local_admin))

    asyncio.run(
        client._follow_hub_core_update(
            {
                "state": "succeeded",
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "new1234567890",
            }
        )
    )

    assert calls == [
        (
            "/api/admin/update/start",
            {
                "reason": "hub.member_follow.catchup",
                "target_rev": "rev2026",
                "target_version": "new1234567890",
                "countdown_sec": 30.0,
                "drain_timeout_sec": 10.0,
                "signal_delay_sec": 0.25,
            },
        )
    ]
    assert client._last_follow_result == {"ok": True, "accepted": True}


def test_member_link_client_skips_hub_succeeded_status_when_already_current(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    monkeypatch.delenv("ADAOS_MEMBER_FOLLOW_HUB_UPDATE", raising=False)
    monkeypatch.delenv("ENV_TYPE", raising=False)
    monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(core_update_enabled=True)))
    monkeypatch.setattr(mod, "core_update_reactions_disabled_reason", lambda: None)
    monkeypatch.setattr(mod, "read_core_update_status", lambda: {"state": "succeeded", "target_version": "new1234567890"})
    monkeypatch.setattr(mod, "active_slot_manifest", lambda: {"target_version": "new1234567890"})

    def _fail_post_local_admin(*_args, **_kwargs):
        raise AssertionError("local admin must not be called")

    monkeypatch.setattr(mod.MemberLinkClient, "_post_local_admin", staticmethod(_fail_post_local_admin))

    asyncio.run(
        client._follow_hub_core_update(
            {
                "state": "succeeded",
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "new1234567890",
            }
        )
    )

    assert client._last_follow_result == {}


def test_member_link_client_catchup_reads_target_from_hub_manifest(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    calls: list[tuple[str, dict]] = []
    monkeypatch.delenv("ADAOS_MEMBER_FOLLOW_HUB_UPDATE", raising=False)
    monkeypatch.delenv("ENV_TYPE", raising=False)
    monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(core_update_enabled=True)))
    monkeypatch.setattr(mod, "core_update_reactions_disabled_reason", lambda: None)
    monkeypatch.setattr(mod, "read_core_update_status", lambda: {"state": "succeeded", "target_version": "old1234"})
    monkeypatch.setattr(mod, "active_slot_manifest", lambda: {"target_version": "old1234"})

    def _post_local_admin(path, body):
        calls.append((path, dict(body)))
        return {"ok": True}

    monkeypatch.setattr(mod.MemberLinkClient, "_post_local_admin", staticmethod(_post_local_admin))

    asyncio.run(
        client._follow_hub_core_update(
            {
                "state": "succeeded",
                "action": "update",
                "manifest": {
                    "target_rev": "rev2026",
                    "git_commit": "feed1234567890",
                },
            }
        )
    )

    assert calls[0][0] == "/api/admin/update/start"
    assert calls[0][1]["target_rev"] == "rev2026"
    assert calls[0][1]["target_version"] == "feed1234567890"


def test_member_link_schedules_yjs_node_state_in_background(monkeypatch) -> None:
    async def _exercise() -> None:
        client = mod.MemberLinkClient()
        client._loop = asyncio.get_running_loop()
        started = asyncio.Event()
        release = asyncio.Event()

        async def _slow_queue(*, webspace_id: str, reason: str) -> None:
            assert webspace_id == "desktop"
            assert reason == "member_link_connected"
            started.set()
            await release.wait()

        monkeypatch.setattr(client, "_queue_yjs_node_state", _slow_queue)
        monkeypatch.setattr(client, "_yjs_node_state_debounce_s", lambda: 0.0)

        assert client._schedule_yjs_node_state(webspace_id="desktop", reason="member_link_connected") is True
        await asyncio.wait_for(started.wait(), timeout=1.0)
        release.set()
        await asyncio.sleep(0)

    asyncio.run(_exercise())


def test_member_link_coalesces_yjs_node_state_schedules(monkeypatch) -> None:
    async def _exercise() -> None:
        client = mod.MemberLinkClient()
        client._loop = asyncio.get_running_loop()
        calls: list[tuple[str, str]] = []

        async def _queue(*, webspace_id: str, reason: str) -> None:
            calls.append((webspace_id, reason))

        monkeypatch.setattr(client, "_queue_yjs_node_state", _queue)
        monkeypatch.setattr(client, "_yjs_node_state_debounce_s", lambda: 0.01)

        assert client._schedule_yjs_node_state(webspace_id="desktop", reason="first") is True
        assert client._schedule_yjs_node_state(webspace_id="desktop", reason="second") is True
        assert client._schedule_yjs_node_state(webspace_id="desktop", reason="third") is True

        await asyncio.sleep(0.05)

        assert calls == [("desktop", "third")]
        assert client._yjs_node_state_tasks == {}
        assert client._yjs_node_state_reasons == {}

    asyncio.run(_exercise())


def test_member_link_yjs_node_state_snapshot_times_out(monkeypatch) -> None:
    class _FakeYDoc:
        def get_map(self, _name: str):
            return SimpleNamespace(to_json=lambda: {"nodes": {"member-1": {"ready": True}}})

    class _SlowStore:
        def __init__(self) -> None:
            self.stopped = False

        async def start(self) -> None:
            return None

        async def apply_updates(self, _ydoc) -> None:
            await asyncio.Future()

        def stop(self) -> None:
            self.stopped = True

    client = mod.MemberLinkClient()
    store = _SlowStore()
    monkeypatch.setattr(mod.Y, "YDoc", _FakeYDoc)
    monkeypatch.setattr(mod, "get_ystore_for_webspace", lambda _webspace_id: store)
    monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="member-1")))
    monkeypatch.setattr(client, "_yjs_node_state_timeout_s", lambda: 0.01)

    asyncio.run(client._queue_yjs_node_state(webspace_id="desktop", reason="member_link_connected"))

    assert client._yjs_snapshot_failed_total == 1
    assert client._last_yjs_node_state_timeout_at > 0
    assert client._out_q.empty()
    assert store.stopped is True
