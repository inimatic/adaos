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


def test_member_link_ws_compression_disabled_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SUBNET_LINK_WS_COMPRESSION", raising=False)

    assert mod._member_link_ws_compression() is None


def test_member_link_ws_compression_can_be_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SUBNET_LINK_WS_COMPRESSION", "1")
    assert mod._member_link_ws_compression() == "deflate"

    monkeypatch.setenv("ADAOS_SUBNET_LINK_WS_COMPRESSION", "custom")
    assert mod._member_link_ws_compression() == "custom"


def _install_rpc_tool_fakes(monkeypatch, calls: list[tuple]) -> None:
    class _Manager:
        def __init__(self, **_kwargs) -> None:
            return None

        def run_tool(self, skill: str, tool: str, arguments: dict, timeout=None):
            calls.append((skill, tool, arguments, timeout))
            return {"ok": True}

    monkeypatch.setattr(
        mod,
        "get_ctx",
        lambda: SimpleNamespace(
            skills_repo=None,
            sql=None,
            git=None,
            paths=None,
            bus=None,
            caps=None,
            settings=None,
        ),
    )
    monkeypatch.setattr(mod, "SkillManager", _Manager)
    monkeypatch.setattr(mod, "SqliteSkillRegistry", lambda *_args, **_kwargs: None)


def test_member_rpc_allows_manifest_verified_read_during_drain(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_rpc_tool_fakes(monkeypatch, calls)
    monkeypatch.setattr(mod, "is_accepting_new_work", lambda: False)
    monkeypatch.setattr(mod, "declared_tool_side_effects", lambda *_args, **_kwargs: "none")

    result = mod.MemberLinkClient._run_tool(
        "research:list_directions", {"webspace_id": "desktop"}, None, False, "read",
    )

    assert result == {"ok": True}
    assert len(calls) == 1


def test_member_rpc_rejects_mutation_during_drain(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_rpc_tool_fakes(monkeypatch, calls)
    monkeypatch.setattr(mod, "is_accepting_new_work", lambda: False)
    monkeypatch.setattr(mod, "declared_tool_side_effects", lambda *_args, **_kwargs: "filesystem")

    try:
        mod.MemberLinkClient._run_tool(
            "research:create_direction", {"title": "TLP"}, None, False, "mutation",
        )
        assert False, "mutation must not start while the member is draining"
    except RuntimeError as exc:
        assert "node_draining" in str(exc)
    assert calls == []


def test_member_rpc_rejects_untrusted_read_hint(monkeypatch) -> None:
    calls: list[tuple] = []
    _install_rpc_tool_fakes(monkeypatch, calls)
    monkeypatch.setattr(mod, "is_accepting_new_work", lambda: True)
    monkeypatch.setattr(mod, "declared_tool_side_effects", lambda *_args, **_kwargs: "local_write")

    try:
        mod.MemberLinkClient._run_tool(
            "research:create_direction", {"title": "TLP"}, None, False, "read",
        )
        assert False, "a caller hint must not downgrade a mutating tool"
    except PermissionError as exc:
        assert "tool_intent_mismatch" in str(exc)
    assert calls == []


def test_member_rpc_failure_is_observable_without_argument_values(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    responses: list[dict] = []
    warnings: list[str] = []

    def _fail(*_args, **_kwargs):
        raise PermissionError("tool_intent_mismatch:slideshow_skill:get_status")

    async def _send(_ws, message, **_kwargs):
        responses.append(message)

    monkeypatch.setattr(client, "_run_tool", _fail)
    monkeypatch.setattr(client, "_send_ws_message", _send)
    monkeypatch.setattr(
        mod._log,
        "warning",
        lambda message, *args, **_kwargs: warnings.append(message % args if args else message),
    )

    asyncio.run(
        client._on_rpc(
            object(),
            {
                "id": "rpc-1",
                "method": "tools.call",
                "params": {
                    "tool": "slideshow_skill:get_status",
                    "arguments": {"source_dir": "secret-path", "webspace_id": "desktop"},
                    "intent": "read",
                },
            },
        )
    )

    assert responses[-1]["ok"] is False
    assert client.snapshot()["rpc"]["failed_total"] == 1
    assert client.snapshot()["rpc"]["last_result"]["error_code"] == "tool_intent_mismatch"
    rendered = "\n".join(warnings)
    assert "slideshow_skill:get_status" in rendered
    assert "source_dir" in rendered
    assert "secret-path" not in rendered


def test_member_rpc_scheduler_does_not_block_receiver(monkeypatch) -> None:
    async def _run() -> None:
        client = mod.MemberLinkClient()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def _slow_rpc(_ws, _msg):
            entered.set()
            await release.wait()

        monkeypatch.setattr(client, "_on_rpc", _slow_rpc)
        await client._schedule_rpc(object(), {"id": "rpc-1", "params": {"tool": "slow:tool"}})
        await asyncio.wait_for(entered.wait(), timeout=0.5)

        assert len(client._rpc_tasks) == 1
        release.set()
        await asyncio.gather(*client._rpc_tasks)

    asyncio.run(_run())


def test_member_link_requires_hello_ack_before_connected(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    client._connected.set()
    client._connected_at = 100.0
    client._last_message_at = 100.0
    client._last_pong_at = 100.0
    monkeypatch.setattr(mod.time, "time", lambda: 110.0)
    monkeypatch.setattr(client, "_pong_stale_after_s", lambda: 35.0)

    assert client.is_connected() is False
    snapshot = client.snapshot()
    assert snapshot["connected"] is False
    assert snapshot["hello_ack_ok"] is False


def test_member_link_accepts_recent_hello_ack_as_initial_hub_activity(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    client._connected.set()
    client._connected_at = 100.0
    client._hello_ack_ok = True
    client._hello_ack_at = 100.0
    client._last_message_at = 100.0
    monkeypatch.setattr(mod.time, "time", lambda: 110.0)
    monkeypatch.setattr(client, "_pong_stale_after_s", lambda: 35.0)

    assert client.is_connected() is True
    snapshot = client.snapshot()
    assert snapshot["connected"] is True
    assert snapshot["hello_ack_ok"] is True
    assert snapshot["hello_ack_ago_s"] == 10.0


def test_member_link_hello_ack_failure_preserves_relay_close_reason() -> None:
    class _ClosedBeforeAck(Exception):
        reason = "no_upstream"

    assert mod.MemberLinkClient._hello_ack_failure_reason(_ClosedBeforeAck()) == "no_upstream"
    assert mod.MemberLinkClient._hello_ack_failure_reason(RuntimeError("boom"), fallback="hub_open_ack_timeout") == "hub_open_ack_timeout"
    assert mod.MemberLinkClient._hello_ack_failure_reason(RuntimeError("boom")) == "RuntimeError"


def test_member_link_allocator_trim_is_rate_limited(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    calls: list[int] = []
    ticks = [100.0, 120.0, 161.0]

    def _clock() -> float:
        return ticks.pop(0) if len(ticks) > 1 else ticks[0]

    # Patch the protected logger before replacing the process-wide time module;
    # logger patch registration itself records a timestamp.
    monkeypatch.setattr(mod._log, "info", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(mod.time, "time", _clock)
    monkeypatch.setattr(mod, "_subnet_link_malloc_trim_min_interval_s", lambda: 60.0)
    monkeypatch.setattr(mod, "_trim_allocator_after_member_link_cycle", lambda: calls.append(1) or True)

    assert client._maybe_trim_allocator_after_link_cycle(reason="first") is True
    assert client._maybe_trim_allocator_after_link_cycle(reason="second") is False
    assert client._maybe_trim_allocator_after_link_cycle(reason="third") is True
    assert len(calls) == 2
    assert client._allocator_trim_total == 2


def test_member_snapshot_heartbeat_carries_core_build_version(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    import adaos.services.voice_runtime as voice_runtime

    voice_projection = {"enabled": True, "state": "ready", "owner": "room"}
    monkeypatch.setattr(voice_runtime, "listening_service_projection", lambda: voice_projection)
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
    assert snapshot["environment"]["voice"]["listening"] == voice_projection
    assert snapshot["environment"]["voice"]["stt"] == "endpoint_audio"
    assert snapshot["environment"]["voice"]["tts"] == "native_or_browser"
    assert snapshot["services"]["voice_listening"] == voice_projection


def test_member_snapshot_heartbeat_repairs_stale_default_manifest_version(monkeypatch) -> None:
    client = mod.MemberLinkClient()
    monkeypatch.setattr(mod, "BUILD_INFO", SimpleNamespace(version="0.1.259", build_date="2026-06-15T15:39:36+03:00"))
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
            "slot": "B",
            "target_rev": "HEAD",
            "target_version": "29cb4c250049a7e7bdbf675c97ded8e011b84999",
            "base_version": "0.1.0",
            "build_version": "0.1.0+1.29cb4c2",
            "build_date": "2026-06-15T15:39:36+03:00",
            "git_commit": "29cb4c250049a7e7bdbf675c97ded8e011b84999",
            "git_short_commit": "29cb4c2",
            "git_subject": "chore: update adaos client to 0.0.80",
        },
    )
    monkeypatch.setattr(mod, "slot_status", lambda: {"active_slot": "B", "previous_slot": "A"})
    monkeypatch.setattr(mod, "read_core_update_status", lambda: {"state": "succeeded", "phase": "validate", "target_slot": "B"})
    monkeypatch.setattr(mod, "read_core_update_last_result", lambda: {})

    snapshot = client._local_node_snapshot_heartbeat()

    assert snapshot["build"]["runtime_version"] == "0.1.259+1.29cb4c2"
    assert snapshot["build"]["runtime_build_version"] == "0.1.259+1.29cb4c2"
    assert snapshot["slots"]["active_manifest"]["build_version"] == "0.1.0+1.29cb4c2"


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


def test_member_control_keeps_supervisor_read_timeout_pending(monkeypatch) -> None:
    class _FakeWebSocket:
        def __init__(self) -> None:
            self.messages: list[dict] = []

        async def send(self, payload: str) -> None:
            import json

            self.messages.append(json.loads(payload))

    client = mod.MemberLinkClient()
    ws = _FakeWebSocket()

    def _ambiguous_timeout(*_args, **_kwargs):
        raise RuntimeError(
            "supervisor_update_route_unavailable: http://127.0.0.1:8776: "
            "ReadTimeout: HTTPConnectionPool read timed out"
        )

    monkeypatch.setattr(mod.MemberLinkClient, "_post_local_admin", staticmethod(_ambiguous_timeout))
    asyncio.run(
        client._on_core_update_request(
            ws,
            {
                "request_id": "member-update-1",
                "action": "update",
                "target_rev": "rev2026",
                "target_version": "target-abcdef0",
            },
        )
    )

    assert client._last_control_result["ok"] is None
    assert client._last_control_result["pending"] is True
    assert client._last_control_result["state"] == "submission_unconfirmed"
    assert client._last_control_request["state"] == "submission_unconfirmed"
    assert client._last_control_request["ok"] is None
    assert ws.messages[-1]["t"] == "core.update.result"
    assert ws.messages[-1]["result"]["pending"] is True


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


def test_member_link_reuses_cached_yjs_node_state_for_reconnect(monkeypatch) -> None:
    node_state = {"ready": True, "seq": 1}

    class _FakeYDoc:
        def get_map(self, _name: str):
            return SimpleNamespace(to_json=lambda: {"nodes": {"member-1": dict(node_state)}})

    class _Store:
        def __init__(self) -> None:
            self.starts = 0
            self.applies = 0
            self.stops = 0

        async def start(self) -> None:
            self.starts += 1

        async def apply_updates(self, _ydoc) -> None:
            self.applies += 1

        def stop(self) -> None:
            self.stops += 1

    async def _exercise() -> None:
        client = mod.MemberLinkClient()
        store = _Store()
        monkeypatch.setattr(mod.Y, "YDoc", _FakeYDoc)
        monkeypatch.setattr(mod, "get_ystore_for_webspace", lambda _webspace_id: store)
        monkeypatch.setattr(mod, "get_ctx", lambda: SimpleNamespace(config=SimpleNamespace(node_id="member-1")))
        monkeypatch.setattr(client, "_yjs_node_state_timeout_s", lambda: None)

        await client._queue_yjs_node_state(webspace_id="desktop", reason="member_link_connected")
        first = client._out_q.get_nowait()
        assert first["state"]["seq"] == 1

        node_state["seq"] = 2
        await client._queue_yjs_node_state(webspace_id="desktop", reason="member_link_connected")
        cached = client._out_q.get_nowait()
        assert cached["state"]["seq"] == 1
        assert store.applies == 1
        assert store.stops == 1
        assert client._yjs_node_state_full_read_total == 1
        assert client._yjs_node_state_cache_hit_total == 1

        client._yjs_node_state_dirty.add("desktop")
        await client._queue_yjs_node_state(webspace_id="desktop", reason="member_link_connected")
        refreshed = client._out_q.get_nowait()
        assert refreshed["state"]["seq"] == 2
        assert store.applies == 2
        assert store.stops == 2
        assert client._yjs_node_state_full_read_total == 2
        assert client._yjs_node_state_cache_hit_total == 1
        assert client._yjs_node_state_dirty == set()

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
