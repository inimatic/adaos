from __future__ import annotations

import asyncio
import sys
import types

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

from adaos.services.subnet import link_manager as mod


class _FakeBus:
    def __init__(self) -> None:
        self.events = []

    def publish(self, event) -> None:
        self.events.append(event)


class _FailingBus:
    def publish(self, _event) -> None:
        raise RuntimeError("bus publish failed")


class _FakeCtx:
    def __init__(self, bus) -> None:
        self.bus = bus


class _FakeDirectory:
    def __init__(self) -> None:
        self.calls = []
        self.heartbeats = []

    def on_member_runtime_snapshot(self, node_id: str, snapshot: dict) -> None:
        self.calls.append((node_id, dict(snapshot)))

    def on_member_runtime_snapshot_heartbeat(
        self,
        node_id: str,
        *,
        captured_at: float | None = None,
        node_state: str | None = None,
    ) -> None:
        self.heartbeats.append((node_id, captured_at, node_state))


class _FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_json(self, msg: dict) -> None:
        self.messages.append(msg)
        return None


async def _noop_push(*_args, **_kwargs) -> None:
    return None


def test_broadcast_yjs_update_suppresses_large_hub_updates(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SUBNET_HUB_YJS_BROADCAST_MAX_BYTES", "8")
    fake_ws = _FakeWebSocket()
    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=fake_ws)

    asyncio.run(
        manager.broadcast_yjs_update(
            webspace_id="desktop",
            update=b"x" * 32,
            origin_node_id=None,
            metadata={"source": "projection_service", "channel": "projection.yjs"},
        )
    )

    assert fake_ws.messages == []
    yjs = manager.snapshot()["yjs_replication"]
    assert yjs["broadcast_total"] == 0
    assert yjs["broadcast_suppressed_total"] == 1
    assert yjs["last_broadcast_suppressed_reason"] == "payload_too_large"
    assert yjs["last_broadcast_suppressed_webspace_id"] == "desktop"


def test_broadcast_yjs_update_suppresses_subnet_echo_sources(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SUBNET_HUB_YJS_BROADCAST_MAX_BYTES", "1024")
    fake_ws = _FakeWebSocket()
    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=fake_ws)

    asyncio.run(
        manager.broadcast_yjs_update(
            webspace_id="desktop",
            update=b"small",
            origin_node_id=None,
            metadata={"source": "subnet.link_manager.node_state", "channel": "core.subnet.link.node_state"},
        )
    )

    assert fake_ws.messages == []
    yjs = manager.snapshot()["yjs_replication"]
    assert yjs["broadcast_suppressed_total"] == 1
    assert yjs["last_broadcast_suppressed_reason"] == "source_suppressed"
    assert yjs["last_broadcast_suppressed_source"] == "subnet.link_manager.node_state"


def test_update_member_snapshot_publishes_only_material_changes(monkeypatch) -> None:
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=_FakeWebSocket())

    snapshot = {
        "captured_at": 100.0,
        "node_id": "member-1",
        "subnet_id": "sn-1",
        "role": "member",
        "ready": True,
        "node_state": "ready",
        "route_mode": "ws",
        "connected_to_hub": True,
        "capacity": {
            "io": [{"io_type": "webrtc_media"}],
            "skills": [{"name": "voice_chat_skill"}],
            "scenarios": [{"name": "web_desktop"}],
        },
        "build": {"runtime_version": "rev1", "runtime_git_short_commit": "abc1234"},
        "update_status": {"state": "succeeded", "phase": "validate", "action": "update"},
    }

    first = asyncio.run(manager.update_member_snapshot("member-1", snapshot=snapshot))
    second = asyncio.run(
        manager.update_member_snapshot(
            "member-1",
            snapshot={**snapshot, "captured_at": 101.0},
        )
    )
    third = asyncio.run(
        manager.update_member_snapshot(
            "member-1",
            snapshot={
                **snapshot,
                "captured_at": 102.0,
                "update_status": {"state": "applying", "phase": "apply", "action": "update"},
            },
        )
    )

    changed_events = [event for event in fake_bus.events if event.type == "subnet.member.snapshot.changed"]
    assert len(changed_events) == 2
    assert first["changed"] is True
    assert second["changed"] is False
    assert third["changed"] is True
    assert len(fake_directory.calls) == 2
    assert fake_directory.heartbeats == [("member-1", 101.0, "ready")]

    payload = changed_events[0].payload
    assert "snapshot" not in payload
    assert payload["snapshot_connected_to_subnet"] is None
    assert payload["snapshot_capacity"] == {"io_total": 1, "skill_total": 1, "scenario_total": 1}
    assert payload["snapshot_build"]["runtime_git_short_commit"] == "abc1234"
    assert payload["snapshot_update"]["state"] == "succeeded"


def test_update_member_snapshot_heartbeat_refreshes_runtime_snapshot(monkeypatch) -> None:
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=_FakeWebSocket())

    asyncio.run(
        manager.update_member_snapshot(
            "member-1",
            snapshot={
                "captured_at": 100.0,
                "node_id": "member-1",
                "node_names": ["Mediapoint"],
                "node_state": "ready",
                "build": {"runtime_git_short_commit": "78a00fe"},
                "slots": {
                    "active_slot": "A",
                    "active_manifest": {"git_short_commit": "78a00fe"},
                },
                "desktop_catalog": {"apps": [{"id": "infrastate"}], "widgets": []},
            },
        )
    )
    result = asyncio.run(
        manager.update_member_snapshot_heartbeat(
            "member-1",
            snapshot={
                "captured_at": 120.0,
                "node_id": "member-1",
                "node_names": ["Mediapoint"],
                "node_state": "ready",
                "build": {
                    "runtime_build_version": "0.1.0+1.6ae4ddb",
                    "runtime_git_short_commit": "6ae4ddb",
                },
                "update_status": {"state": "succeeded", "phase": "validate", "action": "update"},
                "slots": {
                    "active_slot": "A",
                    "active_manifest": {
                        "build_version": "0.1.0+1.6ae4ddb",
                        "git_short_commit": "6ae4ddb",
                    },
                },
            },
        )
    )

    link = manager._links["member-1"]
    assert result["changed"] is True
    assert link.node_snapshot["build"]["runtime_git_short_commit"] == "6ae4ddb"
    assert link.node_snapshot["slots"]["active_manifest"]["build_version"] == "0.1.0+1.6ae4ddb"
    assert link.node_snapshot["desktop_catalog"]["apps"][0]["id"] == "infrastate"
    assert fake_directory.calls[-1][0] == "member-1"
    assert fake_directory.calls[-1][1]["build"]["runtime_git_short_commit"] == "6ae4ddb"
    changed_events = [event for event in fake_bus.events if event.type == "subnet.member.snapshot.changed"]
    assert len(changed_events) == 2
    assert changed_events[-1].payload["snapshot_build"]["runtime_git_short_commit"] == "6ae4ddb"


def test_update_member_snapshot_heartbeat_does_not_publish_refresh_event_for_unchanged_desktop_material(monkeypatch) -> None:
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=_FakeWebSocket())
    snapshot = {
        "captured_at": 100.0,
        "node_id": "member-1",
        "node_names": ["Mediapoint"],
        "node_state": "ready",
        "build": {"runtime_git_short_commit": "78a00fe"},
        "slots": {
            "active_slot": "A",
            "active_manifest": {"git_short_commit": "78a00fe"},
        },
        "desktop_catalog": {"apps": [{"id": "infrastate_app"}], "widgets": [{"id": "infrastate_widget"}]},
    }

    asyncio.run(manager.update_member_snapshot("member-1", snapshot=snapshot))
    result = asyncio.run(
        manager.update_member_snapshot_heartbeat(
            "member-1",
            snapshot={
                "captured_at": 120.0,
                "node_id": "member-1",
                "node_names": ["Mediapoint"],
                "node_state": "ready",
                "build": {"runtime_git_short_commit": "78a00fe"},
                "slots": {
                    "active_slot": "A",
                    "active_manifest": {"git_short_commit": "78a00fe"},
                },
            },
        )
    )

    assert result["changed"] is False
    refreshed_events = [event for event in fake_bus.events if event.type == "subnet.member.snapshot.refreshed"]
    assert refreshed_events == []


def test_member_infrastate_projection_carries_core_slot_version() -> None:
    projection = mod._member_infrastate_projection(
        "member-1",
        node_names=["Mediapoint"],
        captured_at=120.0,
        snapshot={
            "node_id": "member-1",
            "node_names": ["Mediapoint"],
            "node_state": "ready",
            "build": {
                "runtime_build_version": "0.1.0+1.16fcc7a",
                "runtime_git_short_commit": "16fcc7a",
            },
            "update_status": {"state": "succeeded", "phase": "validate", "action": "update"},
            "slots": {
                "active_slot": "B",
                "active_manifest": {
                    "slot": "B",
                    "build_version": "0.1.0+1.16fcc7a",
                    "git_short_commit": "16fcc7a",
                },
            },
        },
    )

    assert projection["summary"]["subtitle"] == "slot B | 0.1.0 | 16fcc7a"
    assert projection["summary"]["selected_node_id"] == "member-1"
    assert projection["slots_meta"]["active_slot"] == "B"
    assert projection["build_meta"]["runtime_build_version"] == "0.1.0+1.16fcc7a"


def test_member_infrastate_projection_infers_version_for_stale_default_manifest() -> None:
    projection = mod._member_infrastate_projection(
        "member-1",
        node_names=["Mediapoint"],
        captured_at=120.0,
        snapshot={
            "node_id": "member-1",
            "node_names": ["Mediapoint"],
            "node_state": "ready",
            "build": {
                "runtime_build_version": "0.1.0+1.b10da50",
                "runtime_git_short_commit": "b10da50",
            },
            "update_status": {"state": "succeeded", "phase": "validate", "action": "update"},
            "slots": {
                "active_slot": "A",
                "active_manifest": {
                    "slot": "A",
                    "build_version": "0.1.0+1.b10da50",
                    "git_short_commit": "b10da50",
                    "git_subject": "chore: bump adaos version to 0.1.217",
                },
            },
        },
    )

    assert projection["summary"]["subtitle"] == "slot A | 0.1.217 | b10da50"


def test_member_node_state_ingest_preserves_hub_infrastate_projection() -> None:
    existing = {
        "desktop": {"theme": "dark"},
        "infrastate": {
            "last_refresh_ts": 140.0,
            "summary": {
                "subtitle": "slot A | 0.1.2 | 72c87e4",
                "source": "subnet.member.snapshot",
                "updated_at": 140.0,
            },
            "projection_diag": {"source": "subnet.link_manager.member_snapshot"},
        },
    }
    incoming = {
        "desktop": {"theme": "light"},
        "infrastate": {
            "summary": {
                "subtitle": "slot A | 78a00fe",
                "source": "skill.infrastate_skill",
            },
            "projection_diag": {"source": "skill_infrastate_skill"},
        },
    }

    merged = mod._member_node_state_for_ingest(existing, incoming, now=150.0)

    assert merged["desktop"]["theme"] == "light"
    assert merged["infrastate"]["summary"]["subtitle"] == "slot A | 0.1.2 | 72c87e4"


def test_member_node_state_ingest_drops_stale_hub_infrastate_projection() -> None:
    existing = {
        "desktop": {"theme": "dark"},
        "infrastate": {
            "last_refresh_ts": 10.0,
            "summary": {
                "subtitle": "slot B | 0.1.191 | 84f6164",
                "source": "subnet.member.snapshot",
                "updated_at": 10.0,
            },
            "projection_diag": {"source": "subnet.link_manager.member_snapshot", "captured_at": 10.0},
        },
    }
    incoming = {
        "desktop": {"theme": "light"},
        "infrastate": {
            "summary": {
                "subtitle": "slot A | 78a00fe",
                "source": "skill.infrastate_skill",
            },
            "projection_diag": {"source": "skill_infrastate_skill"},
        },
    }

    merged = mod._member_node_state_for_ingest(existing, incoming, now=300.0)

    assert merged == {"desktop": {"theme": "light"}}


def test_member_node_state_ingest_drops_timestampless_hub_infrastate_projection() -> None:
    existing = {
        "desktop": {"theme": "dark"},
        "infrastate": {
            "summary": {
                "subtitle": "slot B | 0.1.191 | 84f6164",
                "source": "subnet.member.snapshot",
            },
            "projection_diag": {"source": "subnet.link_manager.member_snapshot"},
        },
    }
    incoming = {"desktop": {"theme": "light"}}

    merged = mod._member_node_state_for_ingest(existing, incoming, now=300.0)

    assert merged == {"desktop": {"theme": "light"}}


def test_member_node_state_ingest_drops_untrusted_infrastate_without_hub_projection() -> None:
    incoming = {
        "desktop": {"theme": "light"},
        "infrastate": {
            "summary": {
                "subtitle": "slot A | 78a00fe",
                "source": "skill.infrastate_skill",
            }
        },
    }

    merged = mod._member_node_state_for_ingest({}, incoming)

    assert merged == {"desktop": {"theme": "light"}}


def test_update_member_snapshot_heartbeat_publishes_member_infrastate_projection(monkeypatch) -> None:
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(
        node_id="member-1",
        websocket=_FakeWebSocket(),
        node_names=["Mediapoint"],
    )
    projections: list[dict] = []

    async def _capture_projection(node_id: str, **kwargs) -> None:
        projections.append({"node_id": node_id, **kwargs})

    monkeypatch.setattr(manager, "_publish_member_infrastate_projection", _capture_projection)

    asyncio.run(
        manager.update_member_snapshot_heartbeat(
            "member-1",
            snapshot={
                "captured_at": 120.0,
                "node_id": "member-1",
                "node_names": ["Mediapoint"],
                "node_state": "ready",
                "build": {
                    "runtime_build_version": "0.1.0+1.16fcc7a",
                    "runtime_git_short_commit": "16fcc7a",
                },
                "update_status": {"state": "succeeded", "phase": "validate", "action": "update"},
                "slots": {
                    "active_slot": "B",
                    "active_manifest": {
                        "build_version": "0.1.0+1.16fcc7a",
                        "git_short_commit": "16fcc7a",
                    },
                },
            },
        )
    )

    assert projections
    assert projections[-1]["node_id"] == "member-1"
    assert projections[-1]["captured_at"] == 120.0
    projection = mod._member_infrastate_projection(
        projections[-1]["node_id"],
        node_names=projections[-1]["node_names"],
        snapshot=projections[-1]["snapshot"],
        captured_at=projections[-1]["captured_at"],
    )
    assert projection["summary"]["subtitle"] == "slot B | 0.1.0 | 16fcc7a"


def test_update_member_snapshot_ignores_nested_capacity_timestamps(monkeypatch) -> None:
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=_FakeWebSocket())

    snapshot = {
        "captured_at": 100.0,
        "node_id": "member-1",
        "subnet_id": "sn-1",
        "role": "member",
        "ready": True,
        "node_state": "ready",
        "route_mode": "ws",
        "connected_to_hub": True,
        "capacity": {
            "io": [{"io_type": "webrtc_media", "updated_at": 10.0}],
            "skills": [{"name": "voice_chat_skill", "updated_at": 10.0}],
            "scenarios": [{"name": "web_desktop", "updated_at": 10.0}],
        },
    }

    first = asyncio.run(manager.update_member_snapshot("member-1", snapshot=snapshot))
    second = asyncio.run(
        manager.update_member_snapshot(
            "member-1",
            snapshot={
                **snapshot,
                "captured_at": 101.0,
                "capacity": {
                    "io": [{"io_type": "webrtc_media", "updated_at": 20.0}],
                    "skills": [{"name": "voice_chat_skill", "updated_at": 20.0}],
                    "scenarios": [{"name": "web_desktop", "updated_at": 20.0}],
                },
            },
        )
    )

    changed_events = [event for event in fake_bus.events if event.type == "subnet.member.snapshot.changed"]
    assert first["changed"] is True
    assert second["changed"] is False
    assert len(changed_events) == 1


def test_update_member_snapshot_event_payload_carries_connected_to_subnet_alias(monkeypatch) -> None:
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=_FakeWebSocket())

    snapshot = {
        "captured_at": 100.0,
        "node_id": "member-1",
        "subnet_id": "sn-1",
        "role": "member",
        "ready": True,
        "node_state": "ready",
        "route_mode": "p2p",
        "connected_to_subnet": False,
    }

    asyncio.run(manager.update_member_snapshot("member-1", snapshot=snapshot))

    changed_event = next(event for event in fake_bus.events if event.type == "subnet.member.snapshot.changed")
    assert changed_event.payload["snapshot_connected_to_subnet"] is False
    assert changed_event.payload["snapshot_connected_to_hub"] is None


def test_update_member_snapshot_logs_publish_failure(monkeypatch) -> None:
    fake_directory = _FakeDirectory()
    warnings: list[tuple[str, tuple, dict]] = []

    def _capture_warning(message: str, *args, **kwargs) -> None:
        warnings.append((message, args, kwargs))

    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(_FailingBus()))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)
    monkeypatch.setattr(mod._log, "warning", _capture_warning)

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=_FakeWebSocket())

    result = asyncio.run(
        manager.update_member_snapshot(
            "member-1",
            snapshot={
                "captured_at": 100.0,
                "node_id": "member-1",
                "node_state": "ready",
            },
        )
    )

    assert result["ok"] is True
    assert result["changed"] is True
    assert warnings
    assert warnings[0][0] == "failed to publish subnet link event type=%s node_id=%s"
    assert warnings[0][1] == ("subnet.member.status.changed", "member-1")
    assert warnings[0][2].get("exc_info") is True


def test_update_member_snapshot_logs_directory_failure(monkeypatch) -> None:
    fake_bus = _FakeBus()
    warnings: list[tuple[str, tuple, dict]] = []

    def _capture_warning(message: str, *args, **kwargs) -> None:
        warnings.append((message, args, kwargs))

    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr(mod._log, "warning", _capture_warning)

    class _FailingDirectory:
        def on_member_runtime_snapshot(self, _node_id: str, _snapshot: dict) -> None:
            raise RuntimeError("directory unavailable")

    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: _FailingDirectory())

    manager = mod.HubLinkManager()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=_FakeWebSocket())

    result = asyncio.run(
        manager.update_member_snapshot(
            "member-1",
            snapshot={
                "captured_at": 100.0,
                "node_id": "member-1",
                "node_state": "ready",
            },
        )
    )

    assert result["ok"] is True
    assert warnings
    assert warnings[0][0] == "failed to update subnet directory from member status node_id=%s"
    assert warnings[0][1] == ("member-1",)
    assert warnings[0][2].get("exc_info") is True


def test_broadcast_event_sends_node_targeted_payload_only_to_matching_member() -> None:
    manager = mod.HubLinkManager()
    member_1_ws = _FakeWebSocket()
    member_2_ws = _FakeWebSocket()
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=member_1_ws)
    manager._links["member-2"] = mod.HubMemberLink(node_id="member-2", websocket=member_2_ws)

    result = asyncio.run(
        manager.broadcast_event(
            event_type="weather.city_changed",
            payload={"city": "Berlin", "target_node_id": "member-2"},
            source="hub",
        )
    )

    assert result["sent"] == 1
    assert member_1_ws.messages == []
    assert len(member_2_ws.messages) == 1
    assert member_2_ws.messages[0]["event"]["payload"]["target_node_id"] == "member-2"


def test_register_requests_initial_member_refresh_after_ack(monkeypatch) -> None:
    manager = mod.HubLinkManager()
    ws = _FakeWebSocket()
    monkeypatch.setattr(manager, "_push_node_display_assignment", _noop_push)
    monkeypatch.setattr(manager, "_push_current_core_update_status", _noop_push)

    asyncio.run(
        manager.register(
            "member-1",
            ws,
            hostname="member.local",
            roles=["member"],
            node_names=["Node 1"],
        )
    )
    asyncio.run(manager.refresh_member_after_connect("member-1"))

    refresh_requests = [msg for msg in ws.messages if msg.get("t") in {"node.status.request", "node.catalog.request"}]
    assert [msg.get("t") for msg in refresh_requests] == ["node.status.request", "node.catalog.request"]
    assert {msg["reason"] for msg in refresh_requests} == {"member_link_connected"}


def test_register_does_not_send_member_refresh_before_hello_ack(monkeypatch) -> None:
    manager = mod.HubLinkManager()
    ws = _FakeWebSocket()
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)
    monkeypatch.setattr(manager, "_push_node_display_assignment", _noop_push)
    monkeypatch.setattr(manager, "_push_current_core_update_status", _noop_push)

    async def _exercise() -> None:
        await manager.register(
            "member-1",
            ws,
            hostname="member.local",
            roles=["member"],
            node_names=["Node 1"],
        )

    asyncio.run(_exercise())

    assert [msg for msg in ws.messages if msg.get("t") in {"node.status.request", "node.catalog.request"}] == []


def test_refresh_member_after_connect_requests_status_and_catalog(monkeypatch) -> None:
    manager = mod.HubLinkManager()
    ws = _FakeWebSocket()
    fake_bus = _FakeBus()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr(mod, "get_ctx", lambda: _FakeCtx(fake_bus))
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)
    monkeypatch.setattr(manager, "_push_node_display_assignment", _noop_push)
    monkeypatch.setattr(manager, "_push_current_core_update_status", _noop_push)

    async def _exercise() -> None:
        await manager.register(
            "member-1",
            ws,
            hostname="member.local",
            roles=["member"],
            node_names=["Node 1"],
        )
        await manager.refresh_member_after_connect("member-1", reason="test")

    asyncio.run(_exercise())

    refresh_requests = [msg for msg in ws.messages if msg.get("t") in {"node.status.request", "node.catalog.request"}]
    assert [msg.get("t") for msg in refresh_requests] == ["node.status.request", "node.catalog.request"]
    assert {msg["reason"] for msg in refresh_requests} == {"test"}


def test_update_member_status_reconciles_stale_core_version(monkeypatch) -> None:
    manager = mod.HubLinkManager()
    ws = _FakeWebSocket()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)
    monkeypatch.setattr(manager, "_push_node_display_assignment", _noop_push)
    monkeypatch.setattr(manager, "_publish_member_infrastate_projection", _noop_push)
    monkeypatch.setattr(
        "adaos.services.core_update.read_status",
        lambda: {
            "state": "succeeded",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "new-target-abcdef0",
            "manifest": {
                "target_rev": "rev2026",
                "target_version": "new-target-abcdef0",
                "build_version": "0.1.111+1.abcdef0",
            },
        },
    )
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=ws)

    result = asyncio.run(
        manager.update_member_status(
            "member-1",
            status={
                "node_id": "member-1",
                "role": "member",
                "slots": {
                    "active_manifest": {
                        "target_version": "old-target-1234567",
                        "build_version": "0.1.109+1.1234567",
                    }
                },
                "update_status": {"state": "succeeded", "action": "update", "target_version": "old-target-1234567"},
            },
        )
    )

    update_requests = [msg for msg in ws.messages if msg.get("t") == "core.update.request"]
    assert result["ok"] is True
    assert len(update_requests) == 1
    assert update_requests[0]["action"] == "update"
    assert update_requests[0]["target_rev"] == "rev2026"
    assert update_requests[0]["target_version"] == "new-target-abcdef0"
    assert update_requests[0]["reason"] == "hub.member_reconcile.snapshot"


def test_update_member_status_does_not_reconcile_current_core_version(monkeypatch) -> None:
    manager = mod.HubLinkManager()
    ws = _FakeWebSocket()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)
    monkeypatch.setattr(manager, "_push_node_display_assignment", _noop_push)
    monkeypatch.setattr(manager, "_publish_member_infrastate_projection", _noop_push)
    monkeypatch.setattr(
        "adaos.services.core_update.read_status",
        lambda: {
            "state": "succeeded",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "new-target-abcdef0",
            "manifest": {"target_version": "new-target-abcdef0", "build_version": "0.1.111+1.abcdef0"},
        },
    )
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=ws)

    asyncio.run(
        manager.update_member_status(
            "member-1",
            status={
                "node_id": "member-1",
                "role": "member",
                "slots": {
                    "active_manifest": {
                        "target_version": "new-target-abcdef0",
                        "build_version": "0.1.111+1.abcdef0",
                    }
                },
                "update_status": {"state": "succeeded", "action": "update", "target_version": "new-target-abcdef0"},
            },
        )
    )

    assert [msg for msg in ws.messages if msg.get("t") == "core.update.request"] == []


def test_update_member_status_retries_failed_member_control_request(monkeypatch) -> None:
    manager = mod.HubLinkManager()
    ws = _FakeWebSocket()
    fake_directory = _FakeDirectory()
    monkeypatch.setattr("adaos.services.registry.subnet_directory.get_directory", lambda: fake_directory)
    monkeypatch.setattr(manager, "_push_node_display_assignment", _noop_push)
    monkeypatch.setattr(manager, "_publish_member_infrastate_projection", _noop_push)
    monkeypatch.setattr(
        "adaos.services.core_update.read_status",
        lambda: {
            "state": "succeeded",
            "action": "update",
            "target_rev": "rev2026",
            "target_version": "new-target-abcdef0",
            "manifest": {"target_version": "new-target-abcdef0", "build_version": "0.1.111+1.abcdef0"},
        },
    )
    manager._links["member-1"] = mod.HubMemberLink(node_id="member-1", websocket=ws)

    asyncio.run(
        manager.update_member_status(
            "member-1",
            status={
                "node_id": "member-1",
                "role": "member",
                "slots": {"active_manifest": {"target_version": "old-target-1234567"}},
                "update_status": {"state": "preparing", "action": "update", "target_version": "new-target-abcdef0"},
                "hub_control_request": {
                    "request": {"target_version": "new-target-abcdef0"},
                    "result": {"ok": False, "error": "supervisor_update_route_unavailable"},
                },
            },
        )
    )

    update_requests = [msg for msg in ws.messages if msg.get("t") == "core.update.request"]
    assert len(update_requests) == 1
    assert update_requests[0]["target_version"] == "new-target-abcdef0"
