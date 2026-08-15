from __future__ import annotations

import asyncio
import gc
import importlib
import json
import sys
import threading
import time
import types
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from adaos.services.webio_snapshot_demand import clear_snapshot_demand_for_tests

try:
    import y_py  # noqa: F401
except ImportError:
    sys.modules["y_py"] = types.SimpleNamespace(
        YDoc=object,
        apply_update=lambda *args, **kwargs: None,
        encode_state_as_update=lambda *args, **kwargs: b"",
        encode_state_vector=lambda *args, **kwargs: b"",
    )

existing_ypy_websocket = sys.modules.get("ypy_websocket")
if existing_ypy_websocket is None or not hasattr(existing_ypy_websocket, "__path__"):
    ystore_mod = types.ModuleType("ypy_websocket.ystore")
    ystore_mod.BaseYStore = object
    ystore_mod.YDocNotFound = RuntimeError

    class _StubStarted:
        async def wait(self) -> None:
            return None

        def is_set(self) -> bool:
            return False

    class _StubWebsocketServer:
        def __init__(self, *args, **kwargs) -> None:
            self.rooms = {}
            self.rooms_ready = SimpleNamespace()
            self.log = SimpleNamespace()
            self.started = _StubStarted()

        async def start(self) -> None:
            return None

        def stop(self) -> None:
            return None

        async def start_room(self, room) -> None:  # noqa: ARG002
            return None

        async def get_room(self, name) -> object:
            room = self.rooms.get(name)
            if room is None:
                room = _StubYRoom()
                self.rooms[name] = room
            return room

        async def serve(self, adapter) -> None:  # noqa: ARG002
            return None

    class _StubMap(dict):
        pass

    class _StubYDoc:
        def get_map(self, name: str) -> _StubMap:  # noqa: ARG002
            return _StubMap()

    class _StubYRoom:
        def __init__(self, *, ready=None, ystore=None, log=None) -> None:
            self.ready = ready
            self.ystore = ystore
            self.log = log
            self.ydoc = _StubYDoc()

        async def stop(self) -> None:
            return None

    ypy_websocket_mod = types.ModuleType("ypy_websocket")
    ypy_websocket_mod.__path__ = []  # type: ignore[attr-defined]
    ypy_websocket_mod.ystore = ystore_mod

    websocket_mod = types.ModuleType("ypy_websocket.websocket")
    websocket_mod.Websocket = object

    websocket_server_mod = types.ModuleType("ypy_websocket.websocket_server")
    websocket_server_mod.WebsocketServer = _StubWebsocketServer

    yroom_mod = types.ModuleType("ypy_websocket.yroom")
    yroom_mod.YRoom = _StubYRoom

    yutils_mod = types.ModuleType("ypy_websocket.yutils")
    yutils_mod.create_update_message = lambda update: b"update:" + bytes(update or b"")

    sys.modules["ypy_websocket"] = ypy_websocket_mod
    sys.modules["ypy_websocket.ystore"] = ystore_mod
    sys.modules["ypy_websocket.websocket"] = websocket_mod
    sys.modules["ypy_websocket.websocket_server"] = websocket_server_mod
    sys.modules["ypy_websocket.yroom"] = yroom_mod
    sys.modules["ypy_websocket.yutils"] = yutils_mod

from adaos.services.workspaces import ensure_workspace, set_workspace_current_scenario_overlay, set_workspace_manifest
from adaos.services.yjs import gateway_ws as gateway_module
from adaos.services.yjs.update_origin import mark_backend_room_update, reset_backend_room_update_markers


class _FakeYStore:
    def __init__(self) -> None:
        self.stop_calls = 0
        self.apply_updates_calls = 0

    async def stop(self) -> None:
        self.stop_calls += 1

    async def apply_updates(self, ydoc) -> None:  # noqa: ARG002
        self.apply_updates_calls += 1


class _FakeWriteYStore:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.compaction_requests: list[dict[str, object]] = []

    async def write(self, update: bytes) -> None:
        self.writes.append(update)

    async def request_runtime_compaction(self, **kwargs) -> bool:
        self.compaction_requests.append(dict(kwargs))
        return True


class _FakeBus:
    def __init__(self) -> None:
        self.subscriptions: list[tuple[str, object]] = []

    def subscribe(self, prefix: str, handler: object) -> None:
        self.subscriptions.append((prefix, handler))


class _FakeEventWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def send_text(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


def _clear_yws_guard_state() -> None:
    gateway_module._YWS_OPEN_HISTORY.clear()
    gateway_module._YWS_CLIENT_OPEN_HISTORY.clear()
    gateway_module._YWS_ATTEMPT_HISTORY.clear()
    gateway_module._YWS_CLIENT_ATTEMPT_HISTORY.clear()
    gateway_module._YWS_CLIENT_SHORT_SESSION_HISTORY.clear()
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL.clear()
    gateway_module._YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL.clear()
    gateway_module._YWS_GUARD_INCIDENTS.clear()


def _fake_log() -> SimpleNamespace:
    return SimpleNamespace(
        debug=lambda *args, **kwargs: None,
        warning=lambda *args, **kwargs: None,
        error=lambda *args, **kwargs: None,
    )


def test_extract_inbound_y_sync_payload_distinguishes_valid_and_malformed_frames(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "read_sync_message", lambda payload: b"decoded:" + payload)

    sync_type, payload = gateway_module._extract_inbound_y_sync_payload(b"\x00\x00vector")
    assert sync_type == int(gateway_module.YSyncMessageType.SYNC_STEP1)
    assert payload == b"decoded:vector"

    monkeypatch.setattr(
        gateway_module,
        "read_sync_message",
        lambda _payload: (_ for _ in ()).throw(ValueError("malformed")),
    )
    sync_type, payload = gateway_module._extract_inbound_y_sync_payload(b"\x00\x02broken")
    assert sync_type == int(gateway_module.YSyncMessageType.SYNC_UPDATE)
    assert payload is None

    sync_type, payload = gateway_module._extract_inbound_y_sync_payload(b"\x00")
    assert sync_type == -1
    assert payload is None


def test_native_y_sync_preflight_accepts_valid_update() -> None:
    current_doc = y_py.YDoc()
    update_doc = y_py.YDoc()
    current = y_py.encode_state_as_update(current_doc)
    update = y_py.encode_state_as_update(update_doc)

    accepted, reason = gateway_module._preflight_inbound_y_sync_payload(
        current,
        update,
        sync_type=int(gateway_module.YSyncMessageType.SYNC_UPDATE),
    )

    assert accepted is True
    assert reason == "ok"


def test_native_y_sync_preflight_accepts_valid_state_vector() -> None:
    current_doc = y_py.YDoc()
    client_doc = y_py.YDoc()
    current = y_py.encode_state_as_update(current_doc)
    state_vector = y_py.encode_state_vector(client_doc)

    accepted, reason = gateway_module._preflight_inbound_y_sync_payload(
        current,
        state_vector,
        sync_type=int(gateway_module.YSyncMessageType.SYNC_STEP1),
    )

    assert accepted is True
    assert reason == "ok"


def test_native_y_sync_preflight_fails_closed_on_subprocess_abort(monkeypatch) -> None:
    monkeypatch.setattr(
        gateway_module.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(returncode=134, stderr=b"native panic"),
    )

    accepted, reason = gateway_module._preflight_inbound_y_sync_payload(
        b"current",
        b"update",
        sync_type=int(gateway_module.YSyncMessageType.SYNC_STEP1),
    )

    assert accepted is False
    assert "returncode=134" in reason


def test_room_serve_blocks_malformed_state_update_before_native_apply(monkeypatch) -> None:
    processed: list[bytes] = []

    class _Websocket:
        path = "/yws/desktop-dev"

        def __init__(self) -> None:
            self._messages = iter([b"\x00\x01broken"])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

        async def send(self, _message: bytes) -> None:
            return None

    async def _sync(_ydoc, _websocket, _log) -> None:
        return None

    async def _process(message, _ydoc, _websocket, _log) -> None:
        processed.append(message)

    monkeypatch.setattr(gateway_module, "sync", _sync)
    monkeypatch.setattr(gateway_module, "process_sync_message", _process)
    monkeypatch.setattr(
        gateway_module,
        "read_sync_message",
        lambda _payload: (_ for _ in ()).throw(ValueError("malformed")),
    )
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_INITIAL_REPLAY", False)

    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room.clients = []
    asyncio.run(room.serve(_Websocket()))

    assert processed == []
    assert room._diag_native_preflight_block_total == 1
    assert room._diag_native_preflight_last_reason == "malformed_sync_frame"


def test_room_serve_preflights_state_vector_before_native_call(monkeypatch) -> None:
    processed: list[bytes] = []
    preflight_types: list[int] = []

    class _Websocket:
        path = "/yws/desktop-dev"

        def __init__(self) -> None:
            self._messages = iter([b"\x00\x00vector"])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

        async def send(self, _message: bytes) -> None:
            return None

    async def _sync(_ydoc, _websocket, _log) -> None:
        return None

    async def _process(message, _ydoc, _websocket, _log) -> None:
        processed.append(message)

    def _preflight(_current, _payload, *, sync_type):
        preflight_types.append(sync_type)
        return False, "native_panic"

    monkeypatch.setattr(gateway_module, "sync", _sync)
    monkeypatch.setattr(gateway_module, "process_sync_message", _process)
    monkeypatch.setattr(gateway_module, "read_sync_message", lambda _payload: b"vector")
    monkeypatch.setattr(gateway_module, "_preflight_inbound_y_sync_payload", _preflight)
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_INITIAL_REPLAY", False)

    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room.clients = []
    room.ydoc = y_py.YDoc()
    asyncio.run(room.serve(_Websocket()))

    assert preflight_types == [int(gateway_module.YSyncMessageType.SYNC_STEP1)]
    assert processed == []
    assert room._diag_native_preflight_block_total == 1
    assert room._diag_native_preflight_last_reason == "native_panic"


def test_tracked_client_send_prunes_failed_transport_without_failing_room() -> None:
    class _FailedClient:
        def __init__(self) -> None:
            self.closed = False

        async def send(self, _message: bytes) -> None:
            raise RuntimeError("transport_closed")

        def close(self) -> None:
            self.closed = True

    failed = _FailedClient()
    healthy = object()
    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room.clients = [failed, healthy]

    asyncio.run(room._tracked_client_send(failed, b"update", 6))

    assert failed.closed is True
    assert room.clients == [healthy]
    assert room._diag_pending_send_tasks == 0


def test_room_serve_keeps_initial_browser_sync_server_authoritative(monkeypatch) -> None:
    processed: list[bytes] = []

    class _Websocket:
        path = "/yws/desktop-dev"

        def __init__(self) -> None:
            self._messages = iter([b"\x00\x01empty-update"])

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

        async def send(self, _message: bytes) -> None:
            return None

    async def _sync(_ydoc, _websocket, _log) -> None:
        return None

    async def _process(message, _ydoc, _websocket, _log) -> None:
        processed.append(message)

    monkeypatch.setattr(gateway_module, "sync", _sync)
    monkeypatch.setattr(gateway_module, "process_sync_message", _process)
    monkeypatch.setattr(gateway_module, "read_sync_message", lambda _payload: b"\x00\x00")
    monkeypatch.setattr(
        gateway_module,
        "_preflight_inbound_y_sync_payload",
        lambda *_args, **_kwargs: (True, "ok"),
    )
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_INITIAL_REPLAY", False)
    monkeypatch.setattr(gateway_module, "_YROOM_SERVER_AUTHORITATIVE_INITIAL_SYNC", True)

    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room.clients = []
    room.ydoc = y_py.YDoc()
    asyncio.run(room.serve(_Websocket()))

    assert processed == []
    assert room._diag_authoritative_initial_skip_total == 1
    assert room._diag_authoritative_initial_skip_bytes == 2
    assert room._diag_authoritative_initial_last_sync_type == "SYNC_STEP2"


def test_room_serve_answers_step1_and_applies_updates_after_authoritative_initial_sync(monkeypatch) -> None:
    processed: list[bytes] = []

    class _Websocket:
        path = "/yws/desktop-dev"

        def __init__(self) -> None:
            self._messages = iter(
                [
                    b"\x00\x00client-vector",
                    b"\x00\x01initial-client-state",
                    b"\x00\x02subsequent-update",
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

        async def send(self, _message: bytes) -> None:
            return None

    async def _sync(_ydoc, _websocket, _log) -> None:
        return None

    async def _process(message, _ydoc, _websocket, _log) -> None:
        processed.append(message)

    monkeypatch.setattr(gateway_module, "sync", _sync)
    monkeypatch.setattr(gateway_module, "process_sync_message", _process)
    monkeypatch.setattr(gateway_module, "read_sync_message", lambda payload: payload)
    monkeypatch.setattr(
        gateway_module,
        "_preflight_inbound_y_sync_payload",
        lambda *_args, **_kwargs: (True, "ok"),
    )
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_INITIAL_REPLAY", False)
    monkeypatch.setattr(gateway_module, "_YROOM_SERVER_AUTHORITATIVE_INITIAL_SYNC", True)

    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room.clients = []
    room.ydoc = y_py.YDoc()
    asyncio.run(room.serve(_Websocket()))

    assert processed == [b"\x00client-vector", b"\x02subsequent-update"]
    assert room._diag_authoritative_initial_skip_total == 1
    assert room._diag_authoritative_initial_last_sync_type == "SYNC_STEP2"


def test_room_serve_uses_protocol_step1_without_redundant_effective_replay(
    monkeypatch,
) -> None:
    processed: list[bytes] = []
    replay_calls: list[str] = []

    class _Websocket:
        path = "/yws/dev1-dev"

        def __init__(self) -> None:
            self._messages = iter(
                [
                    b"\x00\x00client-vector",
                    b"\x00\x01initial-client-state",
                ]
            )

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self._messages)
            except StopIteration as exc:
                raise StopAsyncIteration from exc

        async def send(self, _message: bytes) -> None:
            return None

    async def _sync(_ydoc, _websocket, _log) -> None:
        return None

    async def _process(message, _ydoc, _websocket, _log) -> None:
        processed.append(message)

    async def _replay(self, websocket) -> bool:
        replay_calls.append(websocket.path)
        return True

    monkeypatch.setattr(gateway_module, "sync", _sync)
    monkeypatch.setattr(gateway_module, "process_sync_message", _process)
    monkeypatch.setattr(gateway_module, "read_sync_message", lambda payload: payload)
    monkeypatch.setattr(
        gateway_module,
        "_preflight_inbound_y_sync_payload",
        lambda *_args, **_kwargs: (True, "ok"),
    )
    monkeypatch.setattr(gateway_module, "_YROOM_SERVER_AUTHORITATIVE_INITIAL_SYNC", True)
    monkeypatch.setattr(
        gateway_module.DiagnosticYRoom,
        "_send_initial_effective_state_replay",
        _replay,
    )

    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room.clients = []
    room.ydoc = y_py.YDoc()
    asyncio.run(room.serve(_Websocket()))

    assert processed == [b"\x00client-vector"]
    assert replay_calls == []
    assert room._diag_effective_initial_replay_dedupe_total == 1
    assert room._diag_authoritative_initial_skip_total == 1


def test_repair_room_effective_branches_runs_directly_on_owner_thread(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def _repair(webspace_id, _ystore, _room, *, reason):
        calls.append((webspace_id, reason))
        return b"repair"

    monkeypatch.setattr(gateway_module, "_repair_room_effective_branches", _repair)
    monkeypatch.setattr(gateway_module.threading, "get_ident", lambda: 100)
    room = SimpleNamespace(_thread_id=100, _loop=None)

    update, mode = asyncio.run(
        gateway_module._repair_room_effective_branches_on_owner_loop(
            "desktop-dev",
            None,
            room,
            reason="unit",
        )
    )

    assert update == b"repair"
    assert mode == "direct_owner_context"
    assert calls == [("desktop-dev", "unit")]


def test_repair_room_effective_branches_skips_wrong_thread_without_owner_loop(monkeypatch) -> None:
    calls: list[tuple[str, str]] = []

    async def _repair(webspace_id, _ystore, _room, *, reason):
        calls.append((webspace_id, reason))
        return b"repair"

    monkeypatch.setattr(gateway_module, "_repair_room_effective_branches", _repair)
    monkeypatch.setattr(gateway_module.threading, "get_ident", lambda: 200)
    room = SimpleNamespace(_thread_id=100, _loop=None)

    update, mode = asyncio.run(
        gateway_module._repair_room_effective_branches_on_owner_loop(
            "desktop-dev",
            None,
            room,
            reason="unit",
        )
    )

    assert update == b""
    assert mode == "skipped_no_owner_loop"
    assert calls == []


def test_authoritative_selector_drift_is_repaired_before_update_broadcast(monkeypatch) -> None:
    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room._webspace_id = "selector-guard"
    room.ydoc = y_py.YDoc()
    with room.ydoc.begin_transaction() as txn:
        room.ydoc.get_map("ui").set(txn, "current_scenario", "builder")
    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()
    gateway_module.note_authoritative_current_scenario(
        "selector-guard",
        "test04_recipes",
        reason="unit_switch",
    )
    calls: list[tuple[int, str]] = []

    async def _repair(*, update_bytes: int, reason: str) -> bytes:
        calls.append((update_bytes, reason))
        return b"selector-repair"

    monkeypatch.setattr(room, "_repair_effective_branches_after_client_update", _repair)

    repair = asyncio.run(room._repair_authoritative_selector_after_update(update_bytes=123))

    assert repair == b"selector-repair"
    assert calls == [(123, "authoritative_selector_drift")]

    with room.ydoc.begin_transaction() as txn:
        room.ydoc.get_map("ui").set(txn, "current_scenario", "test04_recipes")
    assert asyncio.run(room._repair_authoritative_selector_after_update(update_bytes=10)) is None
    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()


def test_pending_effective_repair_replay_flushes_to_yws_adapter(monkeypatch) -> None:
    room = gateway_module.DiagnosticYRoom(log=_fake_log())
    room._webspace_id = "desktop"
    sent: list[bytes] = []

    class _Adapter:
        async def send(self, message: bytes) -> None:
            sent.append(message)

    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_REPAIR_REPLAY_FLUSH_SEC", 0.01)
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_REPAIR_REPLAY_INTERVAL_SEC", 0.005)

    room._queue_effective_repair_replay(b"repair-update", reason="initial_client_update_reconcile")

    asyncio.run(
        gateway_module._flush_pending_effective_repair_replays(
            room,
            _Adapter(),
            webspace_id="desktop",
            attempt_id="yws-test",
            client_attempt_id="cyws-test",
        )
    )

    entries = room._effective_repair_replay_entries()
    assert len(sent) == 1
    assert b"repair-update" in sent[0]
    assert entries[0]["sent_total"] == 1


def test_gateway_coerces_legacy_default_webspace_to_runtime_default() -> None:
    assert gateway_module._coerce_gateway_webspace_id("") == "desktop"
    assert gateway_module._coerce_gateway_webspace_id("default") == "desktop"
    assert gateway_module._coerce_gateway_webspace_id("lab") == "lab"


def test_gateway_initial_effective_repair_is_opt_in_by_default() -> None:
    assert gateway_module._YROOM_EFFECTIVE_GUARD_REPAIR_INITIAL_UPDATES == 0


def test_gateway_default_required_branches_do_not_require_scenarios_branch() -> None:
    assert "data.scenarios" not in gateway_module._YROOM_EFFECTIVE_DEFAULT_REQUIRED_BRANCHES


def test_gateway_default_required_branches_match_effective_materialization_contract() -> None:
    required = set(gateway_module._YROOM_EFFECTIVE_DEFAULT_REQUIRED_BRANCHES)
    assert {
        "ui.application",
        "data.catalog",
        "data.installed",
        "data.desktop",
        "data.webio",
        "data.routing",
        "registry.merged",
    }.issubset(required)
    assert "data.webspaces" not in required
    assert "data.builder" not in required
    assert "data.dialog" not in required


def test_room_bootstrap_rebuild_status_finalizer_is_lightweight() -> None:
    class _Doc:
        def get_map(self, name: str) -> dict[str, object]:
            if name == "ui":
                return {
                    "application": {
                        "desktop": {"pageSchema": {"widgets": []}},
                        "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                    }
                }
            if name == "data":
                return {
                    "catalog": {"apps": [], "widgets": []},
                    "installed": {"apps": [], "widgets": []},
                    "desktop": {},
                    "webio": {},
                    "routing": {},
                    "webspaces": {},
                    "pending_actions": {},
                    "nodes": {},
                    "builder": {},
                    "dialog": {},
                    "scenarios": {},
                }
            if name == "registry":
                return {"merged": {}}
            return {}

    seed_result: dict[str, object] = {}
    asyncio.run(
        gateway_module._finalize_room_bootstrap_rebuild_status(
            "desktop",
            seed_result=seed_result,
            room=SimpleNamespace(ydoc=_Doc()),
        )
    )

    assert seed_result["room_bootstrap_rebuild_status"] == "ready"
    assert seed_result["room_bootstrap_rebuild_error"] is None


def test_gateway_effective_guard_requires_installed_arrays(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_GUARD_SNAPSHOT_DETAILS", True)

    class _Doc:
        def __init__(self, state: dict[str, dict[str, object]]) -> None:
            self._state = state

        def get_map(self, name: str) -> dict[str, object]:
            return self._state.setdefault(name, {})

    ready_doc = _Doc(
        {
            "ui": {
                "application": {
                    "desktop": {"pageSchema": {"widgets": []}},
                    "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                }
            },
            "data": {
                "catalog": {"apps": [], "widgets": []},
                "installed": {"apps": [], "widgets": []},
                "desktop": {},
                "webio": {},
                "routing": {},
            },
            "registry": {"merged": {}},
        }
    )
    partial_installed_doc = _Doc(
        {
            "ui": {
                "application": {
                    "desktop": {"pageSchema": {"widgets": []}},
                    "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                }
            },
            "data": {
                "catalog": {"apps": [], "widgets": []},
                "installed": {},
                "desktop": {},
                "webio": {},
                "routing": {},
            },
            "registry": {"merged": {}},
        }
    )

    assert gateway_module._room_effective_branches_ready(ready_doc) is True
    assert gateway_module._room_effective_top_level_ready(ready_doc) is True
    assert gateway_module._room_effective_branches_ready(partial_installed_doc) is False
    assert gateway_module._room_effective_top_level_ready(partial_installed_doc) is False
    snapshot = gateway_module._room_effective_branch_snapshot(partial_installed_doc)
    assert snapshot["ready"] is False
    assert snapshot["has_installed_apps"] is False
    assert snapshot["has_installed_widgets"] is False


def test_gateway_effective_guard_accepts_y_map_effective_branches(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_GUARD_SNAPSHOT_DETAILS", True)

    doc = y_py.YDoc()
    with doc.begin_transaction() as txn:
        ui = doc.get_map("ui")
        data = doc.get_map("data")
        registry = doc.get_map("registry")
        runtime = doc.get_map("runtime")

        application = y_py.YMap({})
        desktop = y_py.YMap({})
        page_schema = y_py.YMap({})
        page_schema.set(txn, "widgets", [])
        desktop.set(txn, "pageSchema", page_schema)
        modals = y_py.YMap({})
        modals.set(txn, "apps_catalog", {})
        modals.set(txn, "widgets_catalog", {})
        application.set(txn, "desktop", desktop)
        application.set(txn, "modals", modals)
        ui.set(txn, "application", application)
        ui.set(txn, "current_scenario", "todo")

        catalog = y_py.YMap({})
        catalog.set(txn, "apps", [])
        catalog.set(txn, "widgets", [])
        data.set(txn, "catalog", catalog)
        installed = y_py.YMap({})
        installed.set(txn, "apps", [])
        installed.set(txn, "widgets", [])
        data.set(txn, "installed", installed)
        data.set(txn, "desktop", y_py.YMap({}))
        data.set(txn, "webio", y_py.YMap({}))
        data.set(txn, "routing", y_py.YMap({}))
        registry.set(txn, "merged", y_py.YMap({}))

        materialization = y_py.YMap({})
        materialization.set(txn, "scenario_id", "todo")
        materialization.set(
            txn,
            "required_branches",
            ["ui.application", "data.catalog", "data.installed", "data.desktop", "data.webio", "data.routing"],
        )
        environment = y_py.YMap({})
        environment.set(txn, "materialization", materialization)
        runtime.set(txn, "environment", environment)

    assert gateway_module._room_effective_branches_ready(doc) is True
    assert gateway_module._room_effective_top_level_ready(doc) is True
    snapshot = gateway_module._room_effective_branch_snapshot(doc)
    assert snapshot["ready"] is True
    assert snapshot["has_application"] is True
    assert snapshot["has_application_page_schema"] is True
    assert snapshot["has_catalog_apps"] is True
    assert snapshot["has_installed_widgets"] is True
    assert snapshot["current_scenario"] == "todo"
    assert snapshot["materialized_scenario"] == "todo"


def test_gateway_effective_guard_rejects_materialization_scenario_mismatch(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_GUARD_SNAPSHOT_DETAILS", True)

    class _Doc:
        def __init__(self, state: dict[str, dict[str, object]]) -> None:
            self._state = state

        def get_map(self, name: str) -> dict[str, object]:
            return self._state.setdefault(name, {})

    doc = _Doc(
        {
            "ui": {
                "current_scenario": "web_desktop",
                "application": {
                    "desktop": {"pageSchema": {"widgets": []}},
                    "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                },
            },
            "data": {
                "catalog": {"apps": [], "widgets": []},
                "installed": {"apps": [], "widgets": []},
                "desktop": {},
                "webio": {},
                "routing": {},
            },
            "registry": {"merged": {}},
            "runtime": {
                "environment": {
                    "materialization": {
                        "scenario_id": "prompt_engineer_scenario",
                        "required_branches": [
                            "ui.application",
                            "data.catalog",
                            "data.installed",
                            "data.desktop",
                        ],
                    }
                }
            },
        }
    )

    assert gateway_module._room_effective_branches_ready(doc) is False
    assert gateway_module._room_effective_top_level_ready(doc) is False
    snapshot = gateway_module._room_effective_branch_snapshot(doc)
    assert snapshot["ready"] is False
    assert snapshot["current_scenario"] == "web_desktop"
    assert snapshot["materialized_scenario"] == "prompt_engineer_scenario"
    assert snapshot["materialization_mismatch"] is True

    doc.get_map("runtime")["environment"]["materialization"]["scenario_id"] = "web_desktop"
    assert gateway_module._room_effective_branches_ready(doc) is True
    assert gateway_module._room_effective_top_level_ready(doc) is True


def test_gateway_effective_guard_rejects_missing_materialization_marker(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_GUARD_SNAPSHOT_DETAILS", True)

    class _Doc:
        def __init__(self, state: dict[str, dict[str, object]]) -> None:
            self._state = state

        def get_map(self, name: str) -> dict[str, object]:
            return self._state.setdefault(name, {})

    doc = _Doc(
        {
            "ui": {
                "current_scenario": "prompt_engineer_scenario",
                "application": {
                    "desktop": {"pageSchema": {"id": "todo_list_5b9319fa", "widgets": []}},
                    "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                },
            },
            "data": {
                "catalog": {"apps": [], "widgets": []},
                "installed": {"apps": [], "widgets": []},
                "desktop": {},
                "webio": {},
                "routing": {},
            },
            "registry": {"merged": {}},
            "runtime": {"environment": {}},
        }
    )

    assert gateway_module._room_effective_branches_ready(doc) is False
    assert gateway_module._room_effective_top_level_ready(doc) is False
    snapshot = gateway_module._room_effective_branch_snapshot(doc)
    assert snapshot["ready"] is False
    assert snapshot["current_scenario"] == "prompt_engineer_scenario"
    assert snapshot["materialized_scenario"] is None
    assert snapshot["materialization_mismatch"] is True


def test_gateway_effective_guard_uses_declarative_runtime_required_branches(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YROOM_EFFECTIVE_GUARD_SNAPSHOT_DETAILS", True)

    class _Doc:
        def __init__(self, state: dict[str, dict[str, object]]) -> None:
            self._state = state

        def get_map(self, name: str) -> dict[str, object]:
            return self._state.setdefault(name, {})

    partial_doc = _Doc(
        {
            "ui": {
                "application": {
                    "desktop": {"pageSchema": {"widgets": []}},
                    "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                }
            },
            "data": {
                "catalog": {"apps": [], "widgets": []},
                "installed": {"apps": [], "widgets": []},
                "desktop": {},
            },
            "registry": {"merged": {}},
            "runtime": {
                "environment": {
                    "materialization": {
                        "required_branches": [
                            "ui.application",
                            "data.catalog",
                            "data.installed",
                            "data.desktop",
                            "data.pending_actions",
                            "data.webio",
                        ]
                    }
                }
            },
        }
    )
    ready_doc = _Doc(
        {
            "ui": {
                "application": {
                    "desktop": {"pageSchema": {"widgets": []}},
                    "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                }
            },
            "data": {
                "catalog": {"apps": [], "widgets": []},
                "installed": {"apps": [], "widgets": []},
                "desktop": {},
                "pending_actions": {},
                "webio": {},
            },
            "registry": {"merged": {}},
            "runtime": {
                "environment": {
                    "materialization": {
                        "required_branches": [
                            "ui.application",
                            "data.catalog",
                            "data.installed",
                            "data.desktop",
                            "data.pending_actions",
                            "data.webio",
                        ]
                    }
                }
            },
        }
    )

    assert gateway_module._room_effective_top_level_ready(partial_doc) is False
    snapshot = gateway_module._room_effective_branch_snapshot(partial_doc)
    assert snapshot["ready"] is False
    assert snapshot["missing_required_branches"] == ["data.pending_actions", "data.webio"]
    assert gateway_module._room_effective_top_level_ready(ready_doc) is True


def test_browser_auth_response_marks_denial_as_terminal_login() -> None:
    payload = gateway_module._browser_auth_response_payload(
        dev_id="dev_tv",
        webspace_id="default",
        allowed=False,
        reason="revoked",
    )

    assert payload["allowed"] is False
    assert payload["reason"] == "revoked"
    assert payload["connection_state"] == "revoked"
    assert payload["next"] == "login"
    assert payload["terminal"] is True
    assert payload["webspace_id"] == "desktop"


def test_browser_session_authorize_reports_revoked_device(monkeypatch) -> None:
    touched: list[dict[str, object]] = []
    owner_thread = threading.get_ident()
    access_threads: list[int] = []

    from adaos.services import access_links

    def authorize_link(kind, entry_id):  # noqa: ANN001, ANN202, ARG001
        access_threads.append(threading.get_ident())
        return False, "revoked"

    def touch_browser_session(device_id, **kwargs):  # noqa: ANN001, ANN202
        access_threads.append(threading.get_ident())
        touched.append({"device_id": device_id, **kwargs})
        return {}

    monkeypatch.setattr(
        access_links,
        "authorize_link",
        authorize_link,
    )
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        touch_browser_session,
    )

    payload = asyncio.run(
        gateway_module.browser_session_authorize(
            dev="dev_tv",
            ws="default",
            browser_family="Chrome",
            os_name="Android",
            form_factor="TV",
            user_agent="ua",
            media_route_status_level="warning",
            media_route_status_reason="device_changed",
            media_route_recent_device_change="true",
        )
    )

    assert payload["allowed"] is False
    assert payload["reason"] == "revoked"
    assert payload["next"] == "login"
    assert access_threads
    assert all(thread_id != owner_thread for thread_id in access_threads)
    assert touched == [
        {
            "device_id": "dev_tv",
            "webspace_id": "desktop",
            "connection_state": "revoked",
            "online": False,
            "browser_family": "Chrome",
            "os_name": "Android",
            "form_factor": "TV",
            "user_agent": "ua",
            "media_route_status_level": "warning",
            "media_route_status_reason": "device_changed",
            "media_route_recent_device_change": "true",
        }
    ]


def test_browser_session_authorize_rejects_client_below_min_version(monkeypatch) -> None:
    touched: list[dict[str, object]] = []

    from adaos.services import access_links

    monkeypatch.setenv("ADAOS_BROWSER_MIN_CLIENT_BUILD_VERSION", "0.0.62")
    monkeypatch.setattr(
        access_links,
        "authorize_link",
        lambda kind, entry_id: (_ for _ in ()).throw(AssertionError("policy lookup should not run")),
    )
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        lambda device_id, **kwargs: touched.append({"device_id": device_id, **kwargs}) or {},
    )

    payload = asyncio.run(
        gateway_module.browser_session_authorize(
            dev="dev_old",
            ws="desktop",
            client_build_id="old-build",
            client_build_version="0.0.61+old-build",
        )
    )

    assert payload["allowed"] is False
    assert payload["reason"] == "client_version_unsupported"
    assert touched == [
        {
            "device_id": "dev_old",
            "webspace_id": "desktop",
            "connection_state": "client_version_unsupported",
            "online": False,
            "client_build_id": "old-build",
            "client_build_version": "0.0.61+old-build",
        }
    ]


def test_yws_denied_browser_accepts_before_policy_close(monkeypatch) -> None:
    touched: list[dict[str, object]] = []

    class FakeWebSocket:
        query_params = {
            "dev": "dev_tv",
            "browser_family": "Chrome",
            "os_name": "Android",
            "form_factor": "TV",
        }

        def __init__(self) -> None:
            self.accepted = False
            self.closed: dict[str, object] | None = None

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed = {"code": code, "reason": reason}

    from adaos.services import access_links

    monkeypatch.setattr(
        access_links,
        "authorize_link",
        lambda kind, entry_id: (False, "revoked"),
    )
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        lambda device_id, **kwargs: touched.append({"device_id": device_id, **kwargs}) or {},
    )

    websocket = FakeWebSocket()
    asyncio.run(gateway_module._yws_impl(websocket, room="default"))

    assert websocket.accepted is True
    assert websocket.closed == {"code": 1008, "reason": "device_revoked"}
    assert touched == [
        {
            "device_id": "dev_tv",
            "webspace_id": "desktop",
            "connection_state": "revoked",
            "online": False,
            "browser_family": "Chrome",
            "os_name": "Android",
            "form_factor": "TV",
        }
    ]


def test_yws_denies_env_revoked_browser_before_guard(monkeypatch) -> None:
    touched: list[dict[str, object]] = []

    class FakeWebSocket:
        query_params = {
            "dev": "dev_storm",
            "browser_session_id": "bs-1",
            "client_build_version": "0.0.62+current",
        }

        def __init__(self) -> None:
            self.accepted = False
            self.closed: dict[str, object] | None = None

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed = {"code": code, "reason": reason}

    from adaos.services import access_links

    monkeypatch.setenv("ADAOS_BROWSER_REVOKED_DEVICE_IDS", "dev_storm")
    monkeypatch.setattr(
        access_links,
        "authorize_link",
        lambda kind, entry_id: (_ for _ in ()).throw(AssertionError("policy lookup should not run")),
    )
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        lambda device_id, **kwargs: touched.append({"device_id": device_id, **kwargs}) or {},
    )
    monkeypatch.setattr(
        gateway_module,
        "_record_yws_guard_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guard should not run")),
    )

    websocket = FakeWebSocket()
    asyncio.run(gateway_module._yws_impl(websocket, room="desktop"))

    assert websocket.accepted is True
    assert websocket.closed == {"code": 1008, "reason": "device_revoked"}
    assert touched == [
        {
            "device_id": "dev_storm",
            "webspace_id": "desktop",
            "connection_state": "revoked",
            "online": False,
            "client_build_version": "0.0.62+current",
        }
    ]


def test_yws_rejects_old_client_with_reload_compatible_reason(monkeypatch) -> None:
    touched: list[dict[str, object]] = []

    class FakeWebSocket:
        query_params = {
            "dev": "dev_old_client",
            "browser_session_id": "bs-old",
            "client_build_id": "old-build",
            "client_build_version": "0.0.61+old-build",
        }

        def __init__(self) -> None:
            self.accepted = False
            self.closed: dict[str, object] | None = None

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed = {"code": code, "reason": reason}

    from adaos.services import access_links

    monkeypatch.setenv("ADAOS_BROWSER_MIN_CLIENT_BUILD_VERSION", "0.0.62")
    monkeypatch.setattr(
        access_links,
        "authorize_link",
        lambda kind, entry_id: (_ for _ in ()).throw(AssertionError("policy lookup should not run")),
    )
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        lambda device_id, **kwargs: touched.append({"device_id": device_id, **kwargs}) or {},
    )
    monkeypatch.setattr(
        gateway_module,
        "_record_yws_guard_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guard should not run")),
    )

    websocket = FakeWebSocket()
    asyncio.run(gateway_module._yws_impl(websocket, room="desktop"))

    assert websocket.accepted is True
    assert websocket.closed == {
        "code": 1013,
        "reason": "inbound_yws_update_payload_blocked:client_version_unsupported",
    }
    assert touched == [
        {
            "device_id": "dev_old_client",
            "webspace_id": "desktop",
            "connection_state": "client_version_unsupported",
            "online": False,
            "client_build_id": "old-build",
            "client_build_version": "0.0.61+old-build",
        }
    ]


def test_yws_direct_disabled_rejects_before_room_acquire(monkeypatch) -> None:
    _clear_yws_guard_state()
    touched: list[dict[str, object]] = []

    class FakeWebSocket:
        query_params = {
            "dev": "dev_browser",
            "browser_session_id": "bs-1",
            "client_build_version": "0.0.99+current",
        }

        def __init__(self) -> None:
            self.accepted = False
            self.closed: dict[str, object] | None = None

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed = {"code": code, "reason": reason}

    from adaos.services import access_links

    monkeypatch.setattr(access_links, "authorize_link", lambda kind, entry_id: (True, "ok"))
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        lambda device_id, **kwargs: touched.append({"device_id": device_id, **kwargs}) or {},
    )
    monkeypatch.setattr(gateway_module, "_yws_direct_transport_enabled", lambda: False)
    monkeypatch.setattr(gateway_module, "_yws_disabled_reject_hold_sec", lambda: 0.0)
    monkeypatch.setattr(
        gateway_module,
        "_record_yws_guard_attempt",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("guard should not run")),
    )

    async def _room_must_not_start(*args, **kwargs):
        raise AssertionError("disabled direct yws should avoid YRoom startup")

    monkeypatch.setattr(gateway_module, "_acquire_yws_room", _room_must_not_start)

    websocket = FakeWebSocket()
    asyncio.run(gateway_module._yws_impl(websocket, room="desktop"))

    assert websocket.accepted is True
    assert websocket.closed == {"code": 1013, "reason": "yws_guard_direct_yws_disabled"}
    assert touched == [
        {
            "device_id": "dev_browser",
            "webspace_id": "desktop",
            "connection_state": "yws_disabled",
            "online": True,
            "client_build_version": "0.0.99+current",
        }
    ]


def test_diagnostic_room_skips_duplicate_backend_persisted_update(monkeypatch) -> None:
    reset_backend_room_update_markers()
    monkeypatch.setattr(gateway_module, "_room_effective_branches_ready", lambda _ydoc: True)
    ystore = _FakeWriteYStore()
    room = gateway_module.DiagnosticYRoom(ystore=ystore, log=_fake_log())
    room._webspace_id = "desktop"

    mark_backend_room_update("desktop", b"backend-update", source="async_get_ydoc", owner="skill:infrastate_skill")

    asyncio.run(room._tracked_ystore_write(b"backend-update"))
    asyncio.run(room._tracked_ystore_write(b"backend-update"))

    assert ystore.writes == [b"backend-update"]
    assert room._diag_backend_persist_skip_total == 1
    assert room._diag_backend_persist_skip_bytes == len(b"backend-update")


def test_diagnostic_room_persists_unmarked_browser_update(monkeypatch) -> None:
    reset_backend_room_update_markers()
    monkeypatch.setattr(gateway_module, "_room_effective_branches_ready", lambda _ydoc: True)
    ystore = _FakeWriteYStore()
    room = gateway_module.DiagnosticYRoom(ystore=ystore, log=_fake_log())
    room._webspace_id = "desktop"

    asyncio.run(room._tracked_ystore_write(b"browser-update"))

    assert ystore.writes == [b"browser-update"]
    assert ystore.compaction_requests == []


def test_diagnostic_room_requests_compaction_after_large_gateway_persist(monkeypatch) -> None:
    reset_backend_room_update_markers()
    monkeypatch.setattr(gateway_module, "_room_effective_branches_ready", lambda _ydoc: True)
    ystore = _FakeWriteYStore()
    room = gateway_module.DiagnosticYRoom(ystore=ystore, log=_fake_log())
    room._webspace_id = "desktop"

    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_BYTES", 4)
    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_DELAY_SEC", 0.0)
    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_QUIET_SEC", 0.0)
    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_COOLDOWN_SEC", 0.0)

    async def _exercise() -> None:
        await room._tracked_ystore_write(b"large-gateway-update")
        await asyncio.sleep(0)

    asyncio.run(_exercise())

    assert ystore.writes == [b"large-gateway-update"]
    assert ystore.compaction_requests == [
        {
            "reason": "gateway_live_room_persist",
            "min_quiet_sec": 0.0,
        }
    ]


def test_diagnostic_room_throttles_large_gateway_persist_compaction(monkeypatch) -> None:
    reset_backend_room_update_markers()
    monkeypatch.setattr(gateway_module, "_room_effective_branches_ready", lambda _ydoc: True)
    ystore = _FakeWriteYStore()
    room = gateway_module.DiagnosticYRoom(ystore=ystore, log=_fake_log())
    room._webspace_id = "desktop-throttle"

    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_BYTES", 4)
    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_DELAY_SEC", 0.0)
    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_QUIET_SEC", 0.0)
    monkeypatch.setattr(gateway_module, "_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_COOLDOWN_SEC", 60.0)
    with gateway_module._GATEWAY_LIVE_PERSIST_COMPACTION_LOCK:
        gateway_module._GATEWAY_LIVE_PERSIST_COMPACTION_NEXT_AT.clear()

    async def _exercise() -> None:
        await room._tracked_ystore_write(b"first-large-gateway-update")
        await room._tracked_ystore_write(b"second-large-gateway-update")
        await asyncio.sleep(0)

    asyncio.run(_exercise())

    assert ystore.writes == [b"first-large-gateway-update", b"second-large-gateway-update"]
    assert ystore.compaction_requests == [
        {
            "reason": "gateway_live_room_persist",
            "min_quiet_sec": 0.0,
        }
    ]


def test_request_webio_stream_snapshots_extracts_node_qualified_receiver() -> None:
    clear_snapshot_demand_for_tests()
    published: list[object] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    gateway_module.get_agent_ctx = lambda: SimpleNamespace(bus=_Bus())

    gateway_module._request_webio_stream_snapshots(
        {"webio.stream.default.nodes.member-01.telemetry.feed"},
        transport="ws",
    )

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "payload", {}).get("webspace_id") == "desktop"
    assert getattr(event, "payload", {}).get("receiver") == "telemetry.feed"
    assert getattr(event, "payload", {}).get("node_id") == "member-01"
    assert getattr(event, "payload", {}).get("target_node_id") == "member-01"
    assert getattr(event, "payload", {}).get("_meta", {}).get("target_node_id") == "member-01"


def test_request_webio_stream_snapshots_extracts_global_node_receiver() -> None:
    clear_snapshot_demand_for_tests()
    published: list[object] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    gateway_module.get_agent_ctx = lambda: SimpleNamespace(bus=_Bus())

    gateway_module._request_webio_stream_snapshots(
        {"webio.stream.nodes.member-01.telemetry.feed"},
        transport="ws",
    )

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "payload", {}).get("webspace_id") == "desktop"
    assert getattr(event, "payload", {}).get("receiver") == "telemetry.feed"
    assert getattr(event, "payload", {}).get("node_id") == "member-01"
    assert getattr(event, "payload", {}).get("target_node_id") == "member-01"
    assert getattr(event, "payload", {}).get("_meta", {}).get("target_node_id") == "member-01"


def test_webio_yjs_projection_subscription_tracks_active_demand() -> None:
    from adaos.sdk.data.projections import clear_projection_demand, has_projection_demand

    published: list[object] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    clear_projection_demand()
    gateway_module.get_agent_ctx = lambda: SimpleNamespace(bus=_Bus())

    gateway_module._publish_webio_yjs_projection_subscription_change(
        {"webio.yjs.default.browsers.devices"},
        action="subscribed",
        transport="ws",
        connection_id="client-1",
    )

    assert has_projection_demand("browsers.devices", webspace_id="desktop") is True
    assert len(published) == 1
    event = published[0]
    assert getattr(event, "type", "") == "webio.yjs.subscription.changed"
    assert getattr(event, "payload", {}).get("webspace_id") == "desktop"
    assert getattr(event, "payload", {}).get("slot") == "browsers.devices"

    gateway_module._publish_webio_yjs_projection_subscription_change(
        {"webio.yjs.default.browsers.devices"},
        action="unsubscribed",
        transport="ws",
        connection_id="client-1",
    )

    assert has_projection_demand("browsers.devices", webspace_id="desktop") is False
    clear_projection_demand()


def test_webio_yjs_projection_subscription_dedupes_repeated_control_events() -> None:
    from adaos.sdk.data.projections import clear_projection_demand, has_projection_demand

    published: list[object] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    clear_projection_demand()
    gateway_module._WEBIO_CONTROL_DEDUPE_RECENT.clear()
    gateway_module.get_agent_ctx = lambda: SimpleNamespace(bus=_Bus())

    for _ in range(2):
        gateway_module._publish_webio_yjs_projection_subscription_change(
            {"webio.yjs.default.browsers.devices"},
            action="subscribed",
            transport="ws",
            connection_id="client-1",
        )

    assert has_projection_demand("browsers.devices", webspace_id="desktop") is True
    assert len(published) == 1
    assert getattr(published[0], "type", "") == "webio.yjs.subscription.changed"
    clear_projection_demand()
    gateway_module._WEBIO_CONTROL_DEDUPE_RECENT.clear()


def test_request_webio_yjs_projection_snapshots_extracts_node_qualified_slot() -> None:
    clear_snapshot_demand_for_tests()
    published: list[object] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    gateway_module.get_agent_ctx = lambda: SimpleNamespace(bus=_Bus())

    gateway_module._request_webio_yjs_projection_snapshots(
        {"webio.yjs.default.nodes.member-01.infrastate.summary"},
        transport="ws",
    )

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "type", "") == "webio.yjs.snapshot.requested"
    assert getattr(event, "payload", {}).get("webspace_id") == "desktop"
    assert getattr(event, "payload", {}).get("slot") == "infrastate.summary"
    assert getattr(event, "payload", {}).get("node_id") == "member-01"
    assert getattr(event, "payload", {}).get("_meta", {}).get("target_node_id") == "member-01"


def test_request_webio_yjs_projection_snapshots_dedupes_repeated_control_events() -> None:
    clear_snapshot_demand_for_tests()
    published: list[object] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    gateway_module._WEBIO_CONTROL_DEDUPE_RECENT.clear()
    gateway_module.get_agent_ctx = lambda: SimpleNamespace(bus=_Bus())

    for _ in range(2):
        gateway_module._request_webio_yjs_projection_snapshots(
            {"webio.yjs.default.nodes.member-01.infrastate.summary"},
            transport="ws",
        )

    assert len(published) == 1
    assert getattr(published[0], "type", "") == "webio.yjs.snapshot.requested"
    gateway_module._WEBIO_CONTROL_DEDUPE_RECENT.clear()
    clear_snapshot_demand_for_tests()


def test_diagnostic_room_skips_empty_y_update() -> None:
    reset_backend_room_update_markers()
    ystore = _FakeWriteYStore()
    room = gateway_module.DiagnosticYRoom(ystore=ystore, log=_fake_log())
    room._webspace_id = "desktop"

    asyncio.run(room._tracked_ystore_write(b"\x00\x00"))

    assert ystore.writes == []
    assert room._diag_empty_update_skip_total == 1
    assert room._diag_empty_update_skip_bytes == 2


def test_ensure_webspace_ready_uses_manifest_defaults(monkeypatch) -> None:
    webspace_id = "gateway-home"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Gateway Home",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )

    captured: list[dict[str, object]] = []
    fake_store = _FakeYStore()

    async def _fake_seed(
        ystore,
        *,
        webspace_id: str,
        default_scenario_id: str,
        space: str,
        ydoc=None,
        prefer_default_scenario: bool = False,
    ) -> None:  # noqa: ANN001
        captured.append(
            {
                "ystore": ystore,
                "webspace_id": webspace_id,
                "default_scenario_id": default_scenario_id,
                "space": space,
                "ydoc": ydoc,
                "prefer_default_scenario": prefer_default_scenario,
            }
        )

    monkeypatch.setattr(gateway_module, "get_ystore_for_webspace", lambda _webspace_id: fake_store)
    monkeypatch.setattr(gateway_module, "ensure_webspace_seeded_from_scenario", _fake_seed)

    asyncio.run(gateway_module.ensure_webspace_ready(webspace_id))

    assert captured == [
        {
            "ystore": fake_store,
            "webspace_id": webspace_id,
            "default_scenario_id": "prompt_engineer_scenario",
            "space": "dev",
            "ydoc": None,
            "prefer_default_scenario": True,
        }
    ]
    assert fake_store.stop_calls == 1


def test_ensure_webspace_ready_canonicalizes_legacy_default(monkeypatch) -> None:
    captured_store_ids: list[str] = []
    captured_seed: list[dict[str, object]] = []
    fake_store = _FakeYStore()

    async def _fake_seed(
        ystore,
        *,
        webspace_id: str,
        default_scenario_id: str,
        space: str,
        ydoc=None,
        prefer_default_scenario: bool = False,
    ) -> None:  # noqa: ANN001
        captured_seed.append(
            {
                "ystore": ystore,
                "webspace_id": webspace_id,
                "default_scenario_id": default_scenario_id,
                "space": space,
                "ydoc": ydoc,
                "prefer_default_scenario": prefer_default_scenario,
            }
        )

    def _fake_get_store(webspace_id: str) -> _FakeYStore:
        captured_store_ids.append(webspace_id)
        return fake_store

    monkeypatch.setattr(gateway_module, "get_ystore_for_webspace", _fake_get_store)
    monkeypatch.setattr(gateway_module, "ensure_webspace_seeded_from_scenario", _fake_seed)

    asyncio.run(gateway_module.ensure_webspace_ready("default"))

    assert captured_store_ids == ["desktop"]
    assert captured_seed[0]["webspace_id"] == "desktop"
    assert captured_seed[0]["default_scenario_id"] == "web_desktop"
    assert fake_store.stop_calls == 1


def test_ensure_webspace_ready_explicit_scenario_overrides_manifest_home(monkeypatch) -> None:
    webspace_id = "gateway-explicit"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Explicit Space",
        kind="workspace",
        source_mode="workspace",
        home_scenario="prompt_engineer_scenario",
    )

    captured: list[dict[str, object]] = []
    fake_store = _FakeYStore()

    async def _fake_seed(
        ystore,
        *,
        webspace_id: str,
        default_scenario_id: str,
        space: str,
        ydoc=None,
        prefer_default_scenario: bool = False,
    ) -> None:  # noqa: ANN001
        captured.append(
            {
                "ystore": ystore,
                "webspace_id": webspace_id,
                "default_scenario_id": default_scenario_id,
                "space": space,
                "ydoc": ydoc,
                "prefer_default_scenario": prefer_default_scenario,
            }
        )

    monkeypatch.setattr(gateway_module, "get_ystore_for_webspace", lambda _webspace_id: fake_store)
    monkeypatch.setattr(gateway_module, "ensure_webspace_seeded_from_scenario", _fake_seed)

    asyncio.run(gateway_module.ensure_webspace_ready(webspace_id, scenario_id="custom_scenario"))

    assert captured == [
        {
            "ystore": fake_store,
            "webspace_id": webspace_id,
            "default_scenario_id": "custom_scenario",
            "space": "workspace",
            "ydoc": None,
            "prefer_default_scenario": True,
        }
    ]


def test_get_room_uses_manifest_defaults_for_room_seed(monkeypatch) -> None:
    webspace_id = "gateway-room"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="DEV: Room Space",
        kind="dev",
        source_mode="dev",
        home_scenario="prompt_engineer_scenario",
    )

    captured: list[dict[str, object]] = []
    fake_store = _FakeYStore()

    async def _fake_seed(
        ystore,
        *,
        webspace_id: str,
        default_scenario_id: str,
        space: str,
        ydoc=None,
        prefer_default_scenario: bool = False,
    ) -> dict[str, object]:  # noqa: ANN001
        captured.append(
            {
                "ystore": ystore,
                "webspace_id": webspace_id,
                "default_scenario_id": default_scenario_id,
                "space": space,
                "ydoc": ydoc,
                "prefer_default_scenario": prefer_default_scenario,
            }
        )
        return {
            "used_provided_ydoc": bool(ydoc is not None),
            "mode": "scenario_projection",
            "persisted_via": "diff",
            "apply_updates_ms": 1.25,
            "total_ms": 2.5,
        }

    class _Scheduler:
        async def ensure_every(self, **kwargs) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr(gateway_module, "get_ystore_for_webspace", lambda _webspace_id: fake_store)
    monkeypatch.setattr(gateway_module, "ensure_webspace_seeded_from_scenario", _fake_seed)
    monkeypatch.setattr(gateway_module, "get_scheduler", lambda: _Scheduler())
    monkeypatch.setattr(gateway_module, "attach_room_observers", lambda _webspace_id, _ydoc: None)

    server = gateway_module.WorkspaceWebsocketServer(auto_clean_rooms=False)
    monkeypatch.setattr(server, "start_room", lambda _room: asyncio.sleep(0))
    gateway_module._YROOM_LIFECYCLE.clear()
    context_token = gateway_module._CURRENT_YWS_ATTEMPT_ID.set("yws-room-seed")
    try:
        room = asyncio.run(server.get_room(webspace_id))
    finally:
        gateway_module._CURRENT_YWS_ATTEMPT_ID.reset(context_token)

    assert room is server.rooms[webspace_id]
    assert fake_store.apply_updates_calls == 0
    assert captured == [
        {
            "ystore": fake_store,
            "webspace_id": webspace_id,
            "default_scenario_id": "prompt_engineer_scenario",
            "space": "dev",
            "ydoc": room.ydoc,
            "prefer_default_scenario": True,
        }
    ]
    room_info = gateway_module.gateway_transport_snapshot()["rooms"][webspace_id]
    assert room_info["bootstrap_total"] == 1
    assert room_info["bootstrap_success_total"] == 1
    assert room_info["last_bootstrap_yws_attempt_id"] == "yws-room-seed"
    assert room_info["last_bootstrap_state"] == "ready"
    assert room_info["last_bootstrap_step"] == "finalize_rebuild_status"
    captured.clear()
    asyncio.run(gateway_module._release_room_refs(webspace_id, room))
    server.rooms.pop(webspace_id, None)
    gateway_module._YROOM_LIFECYCLE.clear()


def test_get_room_replaces_native_doc_after_corrupt_replay(monkeypatch) -> None:
    webspace_id = "gateway-room-corrupt-replay"
    ensure_workspace(webspace_id)
    fake_store = _FakeYStore()
    seeded_docs: list[object] = []

    async def _fake_seed(_ystore, **kwargs):  # noqa: ANN001
        seeded_docs.append(kwargs["ydoc"])
        if len(seeded_docs) == 1:
            return {
                "mode": "corrupt_replay_requires_fresh_doc",
                "fresh_ydoc_required": True,
            }
        return {
            "mode": "scenario_projection",
            "scenario_id": kwargs["default_scenario_id"],
        }

    class _Scheduler:
        async def ensure_every(self, **kwargs) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr(gateway_module, "get_ystore_for_webspace", lambda _webspace_id: fake_store)
    monkeypatch.setattr(gateway_module, "ensure_webspace_seeded_from_scenario", _fake_seed)
    monkeypatch.setattr(gateway_module, "get_scheduler", lambda: _Scheduler())
    monkeypatch.setattr(gateway_module, "attach_room_observers", lambda _webspace_id, _ydoc: None)

    server = gateway_module.WorkspaceWebsocketServer(auto_clean_rooms=False)
    monkeypatch.setattr(server, "start_room", lambda _room: asyncio.sleep(0))
    gateway_module._YROOM_LIFECYCLE.clear()
    room = asyncio.run(server.get_room(webspace_id))

    assert len(seeded_docs) == 2
    assert seeded_docs[0] is not seeded_docs[1]
    assert room.ydoc is seeded_docs[1]

    asyncio.run(gateway_module._release_room_refs(webspace_id, room))
    server.rooms.pop(webspace_id, None)
    gateway_module._YROOM_LIFECYCLE.clear()


def test_get_room_bootstraps_from_materialized_payload_without_semantic_rebuild(monkeypatch) -> None:
    webspace_id = "gateway-room-materialized"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Materialized DEV Space",
        kind="dev",
        source_mode="dev",
        home_scenario="web_desktop",
    )
    fake_store = _FakeYStore()
    seed_calls: list[dict[str, object]] = []
    apply_calls: list[dict[str, object]] = []
    ready_calls: list[dict[str, object]] = []

    async def _fake_seed(ystore, **kwargs):  # noqa: ANN001
        seed_calls.append({"ystore": ystore, **kwargs})
        return {"scenario_id": kwargs["default_scenario_id"], "mode": "loaded_for_materialized_payload"}

    async def _fake_apply(webspace_id_arg, ystore, room, payload, **kwargs):  # noqa: ANN001
        apply_calls.append(
            {
                "webspace_id": webspace_id_arg,
                "ystore": ystore,
                "room": room,
                "payload": payload,
                **kwargs,
            }
        )
        return b"materialized-diff", {"ok": True, "ready": True, "snapshot": {"ready": True}}

    async def _unexpected_effective_materialize(*args, **kwargs):  # noqa: ANN002, ANN003
        raise AssertionError("materialized room bootstrap must not run the semantic materializer")

    async def _fake_finalize_ready(webspace_id_arg, ystore, room, **kwargs):  # noqa: ANN001
        ready_calls.append(
            {
                "webspace_id": webspace_id_arg,
                "ystore": ystore,
                "room": room,
                **kwargs,
            }
        )
        return {"ready": True, "persisted": True, "update_bytes": 12}

    class _Scheduler:
        async def ensure_every(self, **kwargs) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr(gateway_module, "get_ystore_for_webspace", lambda _webspace_id: fake_store)
    monkeypatch.setattr(gateway_module, "ensure_webspace_seeded_from_scenario", _fake_seed)
    monkeypatch.setattr(gateway_module, "_apply_room_materialized_payload", _fake_apply)
    monkeypatch.setattr(gateway_module, "_ensure_room_effective_materialized", _unexpected_effective_materialize)
    monkeypatch.setattr(gateway_module, "_finalize_materialized_room_bootstrap", _fake_finalize_ready)
    monkeypatch.setattr(gateway_module, "get_scheduler", lambda: _Scheduler())
    monkeypatch.setattr(gateway_module, "attach_room_observers", lambda _webspace_id, _ydoc: None)

    payload = {"scenario_id": "todo_list", "ui": {"current_scenario": "todo_list"}}
    request = {
        "webspace_id": webspace_id,
        "payload": payload,
        "reason": "test.materialized_bootstrap",
        "persist_repair": True,
        "force_full_state_update": False,
        "materialization_identity": {"key_hash": "test-key"},
    }
    server = gateway_module.WorkspaceWebsocketServer(auto_clean_rooms=False)
    monkeypatch.setattr(server, "start_room", lambda _room: asyncio.sleep(0))
    token = gateway_module._ROOM_BOOTSTRAP_MATERIALIZATION.set(request)
    try:
        room = asyncio.run(server.get_room(webspace_id))
    finally:
        gateway_module._ROOM_BOOTSTRAP_MATERIALIZATION.reset(token)

    assert "emit_event" not in seed_calls[0]
    assert seed_calls[0]["seed_if_missing"] is False
    assert apply_calls[0]["payload"] is payload
    assert apply_calls[0]["reason"] == "test.materialized_bootstrap"
    assert ready_calls[0]["scenario_id"] == "todo_list"
    assert ready_calls[0]["space"] == "dev"
    assert room._bootstrap_materialization_handoff["request"] is request
    assert room._bootstrap_materialization_handoff["update"] == b"materialized-diff"

    asyncio.run(gateway_module._release_room_refs(webspace_id, room))
    server.rooms.pop(webspace_id, None)
    gateway_module._YROOM_LIFECYCLE.clear()


def test_finalize_materialized_room_bootstrap_persists_ready_marker() -> None:
    class _Store:
        def __init__(self) -> None:
            self.updates: list[bytes] = []

        async def write_update(self, update: bytes, **_kwargs) -> bool:
            self.updates.append(bytes(update))
            return True

    store = _Store()
    room = SimpleNamespace(ydoc=y_py.YDoc())

    result = asyncio.run(
        gateway_module._finalize_materialized_room_bootstrap(
            "materialized-ready-dev",
            store,
            room,
            scenario_id="todo_list",
            space="dev",
        )
    )

    marker = dict(room.ydoc.get_map("runtime").get("bootstrap") or {})
    assert result["ready"] is True
    assert result["persisted"] is True
    assert result["update_bytes"] > 0
    assert len(store.updates) == 1
    assert marker["scenario_id"] == "todo_list"
    assert marker["state"] == "ready"
    assert marker["stage"] == "room_bootstrap_ready"
    assert marker["mode"] == "materialized_payload"


def test_get_room_uses_workspace_current_overlay_before_home(monkeypatch) -> None:
    webspace_id = "gateway-room-current-overlay"
    ensure_workspace(webspace_id)
    set_workspace_manifest(
        webspace_id,
        display_name="Room Space",
        kind="workspace",
        source_mode="workspace",
        home_scenario="web_desktop",
    )
    set_workspace_current_scenario_overlay(webspace_id, "prompt_engineer_scenario")

    captured: list[dict[str, object]] = []
    fake_store = _FakeYStore()

    async def _fake_seed(
        ystore,
        *,
        webspace_id: str,
        default_scenario_id: str,
        space: str,
        ydoc=None,
        prefer_default_scenario: bool = False,
    ) -> dict[str, object]:  # noqa: ANN001
        captured.append(
            {
                "ystore": ystore,
                "webspace_id": webspace_id,
                "default_scenario_id": default_scenario_id,
                "space": space,
                "ydoc": ydoc,
                "prefer_default_scenario": prefer_default_scenario,
            }
        )
        return {
            "scenario_id": default_scenario_id,
            "used_provided_ydoc": bool(ydoc is not None),
            "mode": "scenario_projection",
            "persisted_via": "diff",
            "apply_updates_ms": 1.25,
            "total_ms": 2.5,
        }

    class _Scheduler:
        async def ensure_every(self, **kwargs) -> None:  # noqa: ARG002
            return None

    monkeypatch.setattr(gateway_module, "get_ystore_for_webspace", lambda _webspace_id: fake_store)
    monkeypatch.setattr(gateway_module, "ensure_webspace_seeded_from_scenario", _fake_seed)
    monkeypatch.setattr(gateway_module, "get_scheduler", lambda: _Scheduler())
    monkeypatch.setattr(gateway_module, "attach_room_observers", lambda _webspace_id, _ydoc: None)

    server = gateway_module.WorkspaceWebsocketServer(auto_clean_rooms=False)
    monkeypatch.setattr(server, "start_room", lambda _room: asyncio.sleep(0))
    gateway_module._YROOM_LIFECYCLE.clear()
    try:
        room = asyncio.run(server.get_room(webspace_id))
    finally:
        gateway_module.y_server.rooms.pop(webspace_id, None)

    assert room is server.rooms[webspace_id]
    assert captured[0]["default_scenario_id"] == "prompt_engineer_scenario"
    assert captured[0]["space"] == "workspace"
    assert captured[0]["prefer_default_scenario"] is True
    captured.clear()
    asyncio.run(gateway_module._release_room_refs(webspace_id, room))
    server.rooms.pop(webspace_id, None)
    gateway_module._YROOM_LIFECYCLE.clear()


def test_reset_live_webspace_room_releases_refs_and_requests_compaction(monkeypatch) -> None:
    class _FakeRoom:
        def __init__(self) -> None:
            self.ydoc = object()
            self.ystore = _FakeYStore()
            self._loop = object()
            self._thread_id = threading.get_ident()
            self.ready = object()
            self.log = object()
            self.stop_calls = 0

        async def stop(self) -> None:
            self.stop_calls += 1

    async def _fake_close(_webspace_id: str, *, code: int = 1012, reason: str = "webspace_reload") -> int:  # noqa: ARG001
        return 0

    async def _fake_close_webrtc(_webspace_id: str, *, reason: str = "webspace_reload") -> int:  # noqa: ARG001
        return 2

    async def _fake_route_reset(*, reason: str = "route_reset", notify_browser: bool = True) -> dict[str, object]:  # noqa: ARG001
        return {"ok": True, "closed_tunnels": 1, "notify_browser": notify_browser, "reason": reason}

    room = _FakeRoom()
    backup_jobs_deleted: list[str] = []

    async def _fake_delete(name: str) -> None:
        backup_jobs_deleted.append(name)

    async def _fake_evict_ystore_for_webspace(
        webspace_id: str,
        *,
        store=None,
        persist_snapshot: bool = True,
        compact_runtime: bool = True,
        backup_kind: str = "evict",
        delete_snapshot: bool = False,
    ) -> dict[str, object]:
        assert webspace_id == "gateway-room-reset"
        assert store is room.ystore
        assert persist_snapshot is True
        assert compact_runtime is True
        assert delete_snapshot is False
        return {
            "ok": True,
            "webspace_id": webspace_id,
            "ystore_found": True,
            "persisted": True,
            "backup_skipped": False,
            "released_update_entries": 3,
            "released_update_bytes": 128,
        }

    gateway_module.y_server.rooms["gateway-room-reset"] = room
    gateway_module._room_locks["gateway-room-reset"] = asyncio.Lock()

    monkeypatch.setattr(gateway_module, "close_webspace_yws_connections", _fake_close)
    monkeypatch.setattr(gateway_module, "close_webspace_webrtc_peers", _fake_close_webrtc)
    monkeypatch.setattr(gateway_module, "reset_hub_route_runtime", _fake_route_reset)
    monkeypatch.setattr(gateway_module, "evict_ystore_for_webspace", _fake_evict_ystore_for_webspace)
    monkeypatch.setattr(gateway_module, "get_scheduler", lambda: SimpleNamespace(delete=_fake_delete))
    result = asyncio.run(gateway_module.reset_live_webspace_room("gateway-room-reset", prewarm_after_reset=False))

    assert gateway_module.y_server.rooms.get("gateway-room-reset") is None
    assert gateway_module._room_locks.get("gateway-room-reset") is None
    assert room.stop_calls == 1
    assert room.ystore is None
    assert room.ydoc is None
    assert result["room_dropped"] is True
    assert result["room_stopped"] is True
    assert result["ystore_stopped"] is True
    assert result["ystore_evicted"] is True
    assert result["ystore_snapshot_persisted"] is True
    assert result["scheduler_job_deleted"] is True
    assert result["closed_webrtc_peers"] == 2
    assert result["route_reset"]["closed_tunnels"] == 1
    assert result["runtime_compaction_requested"] is True
    assert result["room_refs_released"] is True
    assert result["owner_handoff_mode"] == "direct_owner_thread"
    assert result["prewarm_after_reset"] is False
    assert backup_jobs_deleted == ["ystores.backup.gateway-room-reset"]


def test_reset_live_webspace_room_releases_ydoc_on_owner_thread(monkeypatch) -> None:
    webspace_id = "gateway-room-owner-thread"
    owner_loop = asyncio.new_event_loop()
    owner_ready = threading.Event()
    ydoc_released = threading.Event()
    owner_state: dict[str, object] = {}

    class _OwnedYDoc:
        def __del__(self) -> None:
            owner_state["released_thread_id"] = threading.get_ident()
            ydoc_released.set()

    class _Room:
        def __init__(self) -> None:
            self.ydoc = _OwnedYDoc()
            self.ystore = None
            self.clients = []
            self._loop = owner_loop
            self._thread_id = threading.get_ident()

        def stop(self) -> None:
            return None

    def _run_owner_loop() -> None:
        asyncio.set_event_loop(owner_loop)
        room = _Room()
        owner_state["thread_id"] = threading.get_ident()
        gateway_module.y_server.rooms[webspace_id] = room
        owner_ready.set()
        owner_loop.run_forever()
        owner_loop.close()

    async def _zero(*args, **kwargs) -> int:  # noqa: ANN002, ANN003, ARG001
        return 0

    async def _delete(_name: str) -> None:
        return None

    monkeypatch.setattr(gateway_module, "close_webspace_yws_connections", _zero)
    monkeypatch.setattr(gateway_module, "close_webspace_webrtc_peers", _zero)
    monkeypatch.setattr(gateway_module, "get_scheduler", lambda: SimpleNamespace(delete=_delete))

    owner_thread = threading.Thread(target=_run_owner_loop, daemon=True)
    owner_thread.start()
    assert owner_ready.wait(timeout=2.0)
    try:
        result = asyncio.run(
            gateway_module.reset_live_webspace_room(
                webspace_id,
                reset_route_runtime=False,
                prewarm_after_reset=False,
            )
        )
        assert ydoc_released.wait(timeout=2.0)
        assert result["room_refs_released"] is True
        assert result["owner_handoff_mode"] == "threadsafe_owner_loop"
        assert owner_state["released_thread_id"] == owner_state["thread_id"]
    finally:
        gateway_module.y_server.rooms.pop(webspace_id, None)
        owner_loop.call_soon_threadsafe(owner_loop.stop)
        owner_thread.join(timeout=2.0)


def test_yws_tracking_cancels_pending_idle_room_reset(monkeypatch) -> None:
    gateway_module.y_server.rooms["idle-room"] = object()
    gateway_module._IDLE_ROOM_RESET_TASKS.clear()

    reset_calls: list[tuple[str, str]] = []

    async def _fake_reset(webspace_id: str, *, close_reason: str = "webspace_reload") -> dict[str, object]:
        reset_calls.append((webspace_id, close_reason))
        return {"ok": True}

    monkeypatch.setattr(gateway_module, "_IDLE_ROOM_EVICT_SEC", 0.05)
    monkeypatch.setattr(gateway_module, "reset_live_webspace_room", _fake_reset)
    monkeypatch.setattr(gateway_module, "_active_webrtc_peer_total_for_webspace", lambda _webspace_id: 0)

    async def _exercise() -> None:
        websocket = SimpleNamespace(query_params={"dev": "dev-1"})
        gateway_module._track_yws_connection("idle-room", websocket, device_id="dev-1")
        gateway_module._untrack_yws_connection("idle-room", websocket)
        gateway_module._track_yws_connection("idle-room", websocket, device_id="dev-1")
        await asyncio.sleep(0.08)

    asyncio.run(_exercise())

    assert reset_calls == []
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._IDLE_ROOM_RESET_TASKS.clear()
    gateway_module.y_server.rooms.clear()


def test_idle_room_reset_evicts_without_prewarm_or_route_reset(monkeypatch) -> None:
    gateway_module.y_server.rooms["idle-room-evict"] = object()
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._IDLE_ROOM_RESET_TASKS.clear()

    reset_calls: list[dict[str, object]] = []

    async def _fake_reset(
        webspace_id: str,
        *,
        close_reason: str = "webspace_reload",
        reset_route_runtime: bool = True,
        prewarm_after_reset: bool | None = None,
    ) -> dict[str, object]:
        reset_calls.append(
            {
                "webspace_id": webspace_id,
                "close_reason": close_reason,
                "reset_route_runtime": reset_route_runtime,
                "prewarm_after_reset": prewarm_after_reset,
            }
        )
        return {"ok": True}

    monkeypatch.setattr(gateway_module, "_IDLE_ROOM_EVICT_SEC", 0.02)
    monkeypatch.setattr(gateway_module, "reset_live_webspace_room", _fake_reset)
    monkeypatch.setattr(gateway_module, "_active_webrtc_peer_total_for_webspace", lambda _webspace_id: 0)

    async def _exercise() -> None:
        assert gateway_module._schedule_idle_room_reset("idle-room-evict") is True
        await asyncio.sleep(0.06)

    asyncio.run(_exercise())

    assert reset_calls == [
        {
            "webspace_id": "idle-room-evict",
            "close_reason": "idle_room_eviction",
            "reset_route_runtime": False,
            "prewarm_after_reset": False,
        }
    ]
    gateway_module._IDLE_ROOM_RESET_TASKS.clear()
    gateway_module.y_server.rooms.clear()


def test_gateway_transport_snapshot_reports_room_diagnostics() -> None:
    class _FakeStatsStream:
        def __init__(self, *, buffer_used: int, waiting_send: int, waiting_receive: int) -> None:
            self._buffer_used = buffer_used
            self._waiting_send = waiting_send
            self._waiting_receive = waiting_receive

        def statistics(self):
            return SimpleNamespace(
                current_buffer_used=self._buffer_used,
                max_buffer_size=65536,
                open_send_streams=1,
                open_receive_streams=1,
                tasks_waiting_send=self._waiting_send,
                tasks_waiting_receive=self._waiting_receive,
            )

    class _Started:
        def is_set(self) -> bool:
            return True

    class _FakeRoom:
        def __init__(self) -> None:
            self.ydoc = object()
            self.ystore = object()
            self.clients = [object(), object()]
            self._ready = True
            self._started = _Started()
            self._task_group = object()
            self._update_send_stream = _FakeStatsStream(buffer_used=5, waiting_send=2, waiting_receive=1)
            self._update_receive_stream = _FakeStatsStream(buffer_used=5, waiting_send=2, waiting_receive=1)

        def _diag_snapshot(self):
            return {
                "effective_initial_replay_total": 1,
                "effective_initial_replay_bytes": 512,
                "effective_initial_replay_skip_total": 2,
                "effective_initial_replay_dedupe_total": 3,
                "effective_initial_replay_last_reason": "malformed_preflight",
            }

    key = "gateway-room-debug"
    room = _FakeRoom()
    gateway_module.y_server.rooms[key] = room
    gateway_module._YROOM_LIFECYCLE.clear()
    gateway_module._mark_room_created(key, room)
    gateway_module._mark_room_open(
        key,
        room,
        created=True,
        open_total_ms=12.5,
        seed_result={
            "used_provided_ydoc": True,
            "mode": "scenario_projection",
            "persisted_via": "diff",
            "apply_updates_ms": 3.0,
            "total_ms": 6.0,
        },
    )
    bootstrap_attempt_id = gateway_module._mark_room_bootstrap_started(key, yws_attempt_id="yws-test-1")
    gateway_module._mark_room_bootstrap_step(key, bootstrap_attempt_id, "seed_from_scenario")
    gateway_module._mark_room_bootstrap_finished(key, bootstrap_attempt_id, state="ready")
    gateway_module._mark_room_reset(
        key,
        close_reason="manual_test",
        room=room,
        room_dropped=False,
        closed_connections=1,
        closed_webrtc_peers=2,
    )

    snapshot = gateway_module.gateway_transport_snapshot()
    room_info = snapshot["rooms"][key]
    transport = snapshot["transports"]["yws"]

    assert room_info["active"] is True
    assert room_info["generation"] == 1
    assert room_info["client_total"] == 2
    assert room_info["cold_open_total"] == 1
    assert room_info["single_pass_bootstrap_total"] == 1
    assert room_info["bootstrap_total"] == 1
    assert room_info["bootstrap_success_total"] == 1
    assert room_info["last_bootstrap_attempt_id"] == bootstrap_attempt_id
    assert room_info["last_bootstrap_yws_attempt_id"] == "yws-test-1"
    assert room_info["last_bootstrap_state"] == "ready"
    assert room_info["last_bootstrap_step"] == "seed_from_scenario"
    assert room_info["bootstrap_stuck"] is False
    assert room_info["last_open_mode"] == "cold_open"
    assert room_info["last_open_bootstrap_mode"] == "scenario_projection"
    assert room_info["update_send_stream"]["current_buffer_used"] == 5
    assert room_info["update_send_stream"]["tasks_waiting_send"] == 2
    assert room_info["diagnostic"]["effective_initial_replay_total"] == 1
    assert room_info["diagnostic"]["effective_initial_replay_bytes"] == 512
    assert room_info["diagnostic"]["effective_initial_replay_skip_total"] == 2
    assert room_info["diagnostic"]["effective_initial_replay_dedupe_total"] == 3
    assert room_info["diagnostic"]["effective_initial_replay_last_reason"] == "malformed_preflight"
    assert room_info["last_reset_reason"] == "manual_test"
    assert room_info["last_reset_closed_webrtc_peers"] == 2
    assert transport["active_room_total"] >= 1
    assert transport["room_generation_max"] >= 1
    assert transport["room_cold_open_total"] >= 1
    assert transport["room_single_pass_bootstrap_total"] >= 1
    assert transport["room_bootstrap_total"] >= 1
    assert transport["room_bootstrap_success_total"] >= 1
    assert transport["update_stream_buffer_used_total"] >= 5


def test_gateway_transport_snapshot_does_not_retain_live_ydoc_on_worker(capfd) -> None:
    entered_snapshot = threading.Event()
    release_snapshot = threading.Event()

    class _BlockingStore:
        def runtime_snapshot(self, *, now_ts: float) -> dict[str, int]:  # noqa: ARG002
            entered_snapshot.set()
            assert release_snapshot.wait(timeout=5.0)
            return {}

    class _Room:
        def __init__(self, ydoc) -> None:
            self.ydoc = ydoc
            self.ystore = _BlockingStore()
            self.clients = []

        def _diag_snapshot(self) -> dict[str, object]:
            return {}

    key = "gateway-worker-snapshot-thread-affinity"
    ydoc = y_py.YDoc()
    room = _Room(ydoc)
    gateway_module.y_server.rooms[key] = room
    gateway_module._mark_room_created(key, room)

    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            snapshot_future = pool.submit(gateway_module.gateway_transport_snapshot)
            assert entered_snapshot.wait(timeout=5.0)

            # Room teardown owns this mutation and final YDoc reference release
            # on the event-loop thread. The worker must only retain plain data.
            room.ydoc = None
            del ydoc
            gc.collect()
            release_snapshot.set()
            snapshot = snapshot_future.result(timeout=5.0)

        gc.collect()
        room_snapshot = snapshot["rooms"][key]
        assert room_snapshot["ydoc_object_id"] is not None
        assert "dropped on another thread" not in capfd.readouterr().err
    finally:
        release_snapshot.set()
        room.ydoc = None
        gateway_module.y_server.rooms.pop(key, None)
        gateway_module._YROOM_LIFECYCLE.pop(key, None)
        gc.collect()


def test_gateway_transport_snapshot_hands_room_introspection_to_owner_thread(monkeypatch) -> None:
    inspected_on: list[int] = []

    class _Room:
        def __init__(self) -> None:
            self.ydoc = y_py.YDoc()
            self.ystore = None
            self.clients = []

        def _diag_snapshot(self) -> dict[str, object]:
            inspected_on.append(threading.get_ident())
            return {}

    key = "gateway-owner-thread-snapshot"
    room = _Room()
    gateway_module.y_server.rooms[key] = room
    gateway_module._mark_room_created(key, room)
    monkeypatch.setattr(gateway_module, "_GATEWAY_SNAPSHOT_OWNER_THREAD_ID", None)
    monkeypatch.setattr(gateway_module, "_GATEWAY_SNAPSHOT_OWNER_LOOP", None)

    async def _exercise() -> tuple[int, dict[str, object]]:
        owner_thread_id = threading.get_ident()
        gateway_module._GATEWAY_SNAPSHOT_OWNER_THREAD_ID = owner_thread_id
        gateway_module._GATEWAY_SNAPSHOT_OWNER_LOOP = asyncio.get_running_loop()
        snapshot = await asyncio.to_thread(gateway_module.gateway_transport_snapshot)
        return owner_thread_id, snapshot

    try:
        owner_thread_id, snapshot = asyncio.run(_exercise())
        assert inspected_on == [owner_thread_id]
        assert snapshot["rooms"][key]["ydoc_object_id"] is not None
    finally:
        room.ydoc = None
        gateway_module.y_server.rooms.pop(key, None)
        gateway_module._YROOM_LIFECYCLE.pop(key, None)
        gc.collect()


def test_apply_materialized_payload_reports_gateway_phase_timings(monkeypatch) -> None:
    key = "gateway-phase-timings"
    update = b"phase-update"
    gateway_module.y_server.rooms[key] = SimpleNamespace(ystore=None, clients=[])
    gateway_module._LIVE_ROOM_REFRESH_PENDING.clear()
    gateway_module._LIVE_ROOM_REFRESH_RECENT.clear()

    async def _fake_apply(webspace_id, _ystore, _room, _payload, **_kwargs):
        marker = gateway_module._register_live_refresh_update(
            webspace_id,
            update,
            reason="test.materialized_payload",
            phase_timings_ms={"branch_apply": 2.5},
        )
        gateway_module._record_live_refresh_observer_broadcast_for_key(
            gateway_module._live_refresh_update_key(webspace_id, update),
            update=b"observer-delta",
            client_count=1,
            exact_update_match=False,
        )
        gateway_module._record_live_refresh_client_send(
            gateway_module._live_refresh_update_key(webspace_id, update),
            elapsed_ms=0.5,
        )
        return update, "direct_owner_context", {
            "ok": True,
            "ready": True,
            "snapshot": {"ready": True},
            "phase_timings_ms": {"branch_apply": 2.5, "owner_handoff": 0.0},
            "broadcast_diagnostics": marker,
        }

    monkeypatch.setattr(gateway_module, "_apply_room_materialized_payload_on_owner_loop", _fake_apply)
    monkeypatch.setattr(gateway_module, "_LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS", 5.0)

    result = asyncio.run(
        gateway_module.apply_materialized_payload_to_live_room(
            key,
            reason="test_refresh",
            materialized_payload={"ui": {"application": {}}},
        )
    )

    assert result["ok"] is True
    assert result["materialized_payload_applied"] is True
    assert result["phase_timings_ms"]["room_lookup"] >= 0.0
    assert result["phase_timings_ms"]["materialized_owner_apply"] >= 0.0
    assert result["phase_timings_ms"]["client_sync_wait"] >= 0.0
    assert "observer_broadcast" in result["phase_timings_ms"]
    assert "client_sync" in result["phase_timings_ms"]
    assert result["broadcast_diagnostics"]["client_sync_done"] is True
    assert result["broadcast_diagnostics"]["observer_exact_update_match"] is False
    assert result["broadcast_diagnostics"]["observer_update_bytes"] == len(b"observer-delta")

    gateway_module.y_server.rooms.pop(key, None)
    gateway_module._YROOM_LIFECYCLE.clear()


def test_apply_materialized_payload_does_not_wait_for_client_sync_without_clients(monkeypatch) -> None:
    key = "gateway-no-client-sync-wait"
    update = b"no-client-update"
    gateway_module.y_server.rooms[key] = SimpleNamespace(ystore=None, clients=[])
    gateway_module._LIVE_ROOM_REFRESH_PENDING.clear()
    gateway_module._LIVE_ROOM_REFRESH_RECENT.clear()

    async def _fake_apply(webspace_id, _ystore, _room, _payload, **_kwargs):
        marker = gateway_module._register_live_refresh_update(
            webspace_id,
            update,
            reason="test.materialized_payload",
            phase_timings_ms={"branch_apply": 1.0},
        )
        return update, "direct_owner_context", {
            "ok": True,
            "ready": True,
            "snapshot": {"ready": True},
            "phase_timings_ms": {"branch_apply": 1.0},
            "broadcast_diagnostics": marker,
        }

    monkeypatch.setattr(gateway_module, "_apply_room_materialized_payload_on_owner_loop", _fake_apply)
    monkeypatch.setattr(gateway_module, "_LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS", 1000.0)

    started = time.perf_counter()
    result = asyncio.run(
        gateway_module.apply_materialized_payload_to_live_room(
            key,
            reason="test_refresh",
            materialized_payload={"ui": {"application": {}}},
        )
    )
    elapsed_ms = (time.perf_counter() - started) * 1000.0

    assert result["ok"] is True
    assert elapsed_ms < 200.0
    assert result["phase_timings_ms"]["client_sync_wait"] < 50.0
    assert result["broadcast_diagnostics"]["client_sync_done"] is True
    assert result["broadcast_diagnostics"]["client_sync_reason"] == "no_clients"
    assert result["broadcast_diagnostics"]["timed_out"] is False

    gateway_module.y_server.rooms.pop(key, None)
    gateway_module._LIVE_ROOM_REFRESH_PENDING.clear()
    gateway_module._LIVE_ROOM_REFRESH_RECENT.clear()


def test_live_room_refresh_waits_for_client_delivery_by_default() -> None:
    assert gateway_module._LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS > 0.0


def test_apply_materialized_payload_client_sync_wait_can_be_disabled(monkeypatch) -> None:
    key = "gateway-client-sync-wait-disabled"
    update = b"wait-disabled-update"
    gateway_module.y_server.rooms[key] = SimpleNamespace(ystore=None, clients=[object()])
    gateway_module._LIVE_ROOM_REFRESH_PENDING.clear()
    gateway_module._LIVE_ROOM_REFRESH_RECENT.clear()

    async def _fake_apply(webspace_id, _ystore, _room, _payload, **_kwargs):
        marker = gateway_module._register_live_refresh_update(
            webspace_id,
            update,
            reason="test.materialized_payload",
            phase_timings_ms={"branch_apply": 1.0},
        )
        return update, "direct_owner_context", {
            "ok": True,
            "ready": True,
            "snapshot": {"ready": True},
            "phase_timings_ms": {"branch_apply": 1.0},
            "broadcast_diagnostics": marker,
        }

    monkeypatch.setattr(gateway_module, "_apply_room_materialized_payload_on_owner_loop", _fake_apply)
    monkeypatch.setattr(gateway_module, "_LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS", 0.0)

    result = asyncio.run(
        gateway_module.apply_materialized_payload_to_live_room(
            key,
            reason="test_refresh",
            materialized_payload={"ui": {"application": {}}},
        )
    )

    assert result["ok"] is True
    assert result["phase_timings_ms"]["client_sync_wait"] < 50.0
    assert result["broadcast_diagnostics"]["client_sync_done"] is False
    assert result["broadcast_diagnostics"]["client_count"] == 1
    assert result["broadcast_diagnostics"]["client_sync_reason"] == "wait_disabled"
    assert result["broadcast_diagnostics"]["timed_out"] is False

    gateway_module.y_server.rooms.pop(key, None)
    gateway_module._LIVE_ROOM_REFRESH_PENDING.clear()
    gateway_module._LIVE_ROOM_REFRESH_RECENT.clear()


def test_materialized_payload_room_history_omits_skill_decls() -> None:
    payload = {
        "schema": "adaos.webspace.materialized_payload.v1",
        "webspace_id": "desktop-dev",
        "scenario_id": "prompt_engineer_scenario",
        "application": {"desktop": {"pageSchema": {"id": "prompt"}}},
        "catalog": {"apps": [], "widgets": []},
        "registry": {"modals": [], "widgets": []},
        "installed": {"apps": [], "widgets": []},
        "desktop": {"installed": {"apps": [], "widgets": []}},
        "webio": {"receivers": {}},
        "routing": {"routes": {}},
        "skill_decls": [{"skill": "heavy", "apps": [{"id": "app"} for _ in range(50)]}],
        "skill_decls_fingerprint": "skills-fp",
        "branch_fingerprints": {"data.webio": "webio-fp"},
    }

    compact = gateway_module._compact_materialized_payload_for_room_history(payload)

    assert "skill_decls" not in compact
    assert compact["skill_decls_fingerprint"] == "skills-fp"
    assert compact["webio"] == {"receivers": {}}
    assert compact["branch_fingerprints"] == {"data.webio": "webio-fp"}


def test_materialized_payload_apply_ready_snapshot_trusts_successful_summary() -> None:
    payload = {
        "scenario_id": "prompt_engineer_scenario",
        "metadata": {
            "materialization": {
                "required_branches": ["ui.application", "data.catalog"],
            },
        },
    }

    snapshot = gateway_module._materialized_payload_apply_ready_snapshot(
        payload,
        {"failed_branches": 0, "changed_branches": 2},
    )

    assert snapshot is not None
    assert snapshot["ready"] is True
    assert snapshot["mode"] == "materialized_payload_apply_summary"
    assert snapshot["current_scenario"] == "prompt_engineer_scenario"
    assert snapshot["materialized_scenario"] == "prompt_engineer_scenario"
    assert snapshot["required_branches"] == ["ui.application", "data.catalog"]
    assert gateway_module._materialized_payload_apply_ready_snapshot(
        payload,
        {"failed_branches": 1, "failed_paths": ["data.desktop"]},
    ) is None
    assert gateway_module._materialized_payload_apply_ready_snapshot(
        payload,
        {"failed_branches": 0, "trusted_fingerprint_unchanged_branches": 1},
    ) is None
    assert gateway_module._materialized_payload_apply_ready_snapshot(
        payload,
        {"failed_branches": 0, "stale_fingerprint_branches": 1},
    ) is None


def test_materialized_payload_establishes_selector_authority_before_room_mutation(monkeypatch) -> None:
    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    key = "materialized-selector-authority"
    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()
    ydoc = y_py.YDoc()
    room = SimpleNamespace(ydoc=ydoc, clients=[])
    observed_authority: list[str | None] = []
    observed_verification: list[bool] = []

    def _fake_apply(
        self,
        target_ydoc,
        webspace_id,
        _payload,
        **_kwargs,
    ) -> None:
        observed_authority.append(gateway_module._authoritative_current_scenario(webspace_id))
        observed_verification.append(bool(_kwargs.get("verify_branch_fingerprints")))
        with target_ydoc.begin_transaction() as txn:
            target_ydoc.get_map("ui").set(txn, "current_scenario", "test04_recipes")
        self._last_apply_summary = {"failed_branches": 0, "changed_branches": 0}
        self._last_rebuild_timings_ms = {"total": 1.0}

    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "apply_materialized_payload_to_doc",
        _fake_apply,
    )

    _update, result = asyncio.run(
        gateway_module._apply_room_materialized_payload(
            key,
            None,
            room,
            {
                "scenario_id": "test04_recipes",
                "metadata": {
                    "materialization": {
                        "required_branches": [],
                    },
                },
            },
            reason="semantic_rebuild:scenario_switch_rebuild",
        )
    )

    assert result["ready"] is True
    assert observed_authority == ["test04_recipes"]
    assert observed_verification == [True]
    assert gateway_module._authoritative_current_scenario(key) == "test04_recipes"
    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()


def test_materialized_payload_force_full_state_replaces_ystore_snapshot(monkeypatch) -> None:
    import y_py as Y

    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    reset_backend_room_update_markers()
    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        ydoc.get_map("runtime").set(txn, "old_snapshot_only", "x" * 512)
    class _FakeClient:
        def __init__(self) -> None:
            self.messages: list[bytes] = []

        async def send(self, message: bytes) -> None:
            self.messages.append(bytes(message))

    client = _FakeClient()
    room = SimpleNamespace(ydoc=ydoc, clients=[client])

    class _FakeStore:
        def __init__(self) -> None:
            self.replace_calls: list[dict[str, object]] = []

        async def replace_snapshot_update(self, snapshot: bytes, **kwargs) -> dict[str, object]:
            self.replace_calls.append({"snapshot": snapshot, **kwargs})
            return {"ok": True, "snapshot_bytes": len(snapshot)}

    store = _FakeStore()

    def _fake_apply(
        self,
        target_ydoc,
        _webspace_id,
        _payload,
        *,
        materialization_identity=None,  # noqa: ARG001
        previous_payload=None,  # noqa: ARG001
        verify_branch_fingerprints=False,  # noqa: ARG001
    ) -> None:
        with target_ydoc.begin_transaction() as txn:
            target_ydoc.get_map("ui").set(
                txn,
                "application",
                {
                    "desktop": {"pageSchema": {"id": "desktop"}},
                    "modals": {"apps_catalog": {}, "widgets_catalog": {}},
                },
            )
            target_ydoc.get_map("data").set(txn, "catalog", {"apps": [], "widgets": []})
            target_ydoc.get_map("data").set(txn, "desktop", {"installed": {"apps": [], "widgets": []}})
            target_ydoc.get_map("data").set(txn, "installed", {"apps": [], "widgets": []})
        self._last_apply_summary = {"failed_branches": 0, "changed_branches": 4}
        self._last_rebuild_timings_ms = {"total": 1.0}

    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "apply_materialized_payload_to_doc",
        _fake_apply,
    )

    update, result = asyncio.run(
        gateway_module._apply_room_materialized_payload(
            "force-full-snapshot",
            store,
            room,
            {
                "schema": "adaos.webspace.materialized_payload.v1",
                "scenario_id": "web_desktop",
                "metadata": {
                    "materialization": {
                        "required_branches": ["ui.application", "data.catalog", "data.installed"],
                    }
                },
            },
            reason="unit",
            force_full_state_update=True,
        )
    )

    assert update
    assert result["ready"] is True
    assert result["force_full_state_update"] is True
    assert result["full_state_snapshot_persisted"] is True
    assert store.replace_calls
    assert result["broadcast_update_bytes"] == len(update)
    assert result["direct_client_broadcast_count"] == 1
    assert result["direct_client_broadcast_failed"] == 0
    assert client.messages == [
        gateway_module.create_update_message(store.replace_calls[-1]["snapshot"])
    ]
    assert result["direct_client_broadcast_bytes"] == len(store.replace_calls[-1]["snapshot"])
    assert result["full_state_update_bytes"] == len(store.replace_calls[-1]["snapshot"])
    assert store.replace_calls[-1]["snapshot"] != update
    assert len(store.replace_calls[-1]["snapshot"]) > len(update)
    assert store.replace_calls[-1]["persist_snapshot"] is True
    marker = gateway_module.consume_backend_room_update("force-full-snapshot", update)
    assert marker is not None
    assert marker["already_persisted"] is True
    reset_backend_room_update_markers()


def test_room_bootstrap_seed_override_beats_stale_authoritative_lease(monkeypatch) -> None:
    import y_py as Y

    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()
    gateway_module.note_authoritative_current_scenario(
        "bootstrap-lease-ws",
        "old_prompt_scenario",
        reason="unit_stale_switch",
    )

    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        ydoc.get_map("ui").set(txn, "current_scenario", "old_prompt_scenario")
        ydoc.get_map("ui").set(
            txn,
            "application",
            {
                "desktop": {"pageSchema": {"id": "desktop"}},
                "modals": {"apps_catalog": {}, "widgets_catalog": {}},
            },
        )
        ydoc.get_map("data").set(txn, "catalog", {"apps": [], "widgets": []})
        ydoc.get_map("data").set(txn, "installed", {"apps": [], "widgets": []})
        ydoc.get_map("data").set(txn, "desktop", {"installed": {"apps": [], "widgets": []}})
        ydoc.get_map("data").set(txn, "webio", {})
        ydoc.get_map("data").set(txn, "routing", {})
        ydoc.get_map("registry").set(txn, "merged", {"widgets": {}, "modals": {}})
        ydoc.get_map("runtime").set(
            txn,
            "environment",
            {"materialization": {"scenario_id": "old_prompt_scenario"}},
        )

    class _FakeStore:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        async def write_update(self, update: bytes, **_kwargs) -> bool:
            self.writes.append(bytes(update))
            return True

    seen_current: list[str] = []

    async def _fake_resolve(self, target_ydoc, _webspace_id, *, scenario_id=None, **_kwargs) -> None:
        current = str(scenario_id or target_ydoc.get_map("ui").get("current_scenario") or "")
        seen_current.append(current)
        self._last_materialized_payload = {
            "scenario_id": current,
            "source_mode": "dev",
            "application": {
                "desktop": {"pageSchema": {"id": current}},
                "modals": {"apps_catalog": {}, "widgets_catalog": {}},
            },
            "catalog": {"apps": [], "widgets": []},
            "installed": {"apps": [], "widgets": []},
            "desktop": {},
            "webio": {},
            "routing": {},
            "registry": {"widgets": {}, "modals": {}},
            "skill_decls": [],
        }
        self._last_rebuild_timings_ms = {"total": 1.0}

    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "resolve_materialized_payload_from_doc_async",
        _fake_resolve,
    )

    room = SimpleNamespace(ydoc=ydoc)
    store = _FakeStore()

    result = asyncio.run(
        gateway_module._ensure_room_effective_materialized(
            "bootstrap-lease-ws",
            store,
            room,
            seed_result={
                "scenario_id": "todo_list_5b9319fa",
                "current_scenario_overridden": True,
                "mode": "projected_seed_reuse",
                "space": "dev",
            },
        )
    )

    assert result is True
    assert seen_current == ["todo_list_5b9319fa"]
    assert gateway_module._authoritative_current_scenario("bootstrap-lease-ws") == "todo_list_5b9319fa"
    assert room._diag_effective_branch_snapshot["ready"] is True
    assert store.writes
    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()


def test_room_bootstrap_reuses_matching_persisted_effective_state(monkeypatch) -> None:
    import y_py as Y

    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()
    ydoc = Y.YDoc()
    required_branches = [
        "ui.application",
        "data.catalog",
        "data.installed",
        "data.desktop",
        "data.webio",
        "data.routing",
        "registry.merged",
    ]
    with ydoc.begin_transaction() as txn:
        ydoc.get_map("ui").set(txn, "current_scenario", "web_desktop")
        ydoc.get_map("ui").set(
            txn,
            "application",
            {
                "desktop": {"pageSchema": {"id": "desktop"}},
                "modals": {"apps_catalog": {}, "widgets_catalog": {}},
            },
        )
        ydoc.get_map("data").set(txn, "catalog", {"apps": [], "widgets": []})
        ydoc.get_map("data").set(txn, "installed", {"apps": [], "widgets": []})
        ydoc.get_map("data").set(txn, "desktop", {})
        ydoc.get_map("data").set(txn, "webio", {})
        ydoc.get_map("data").set(txn, "routing", {})
        ydoc.get_map("registry").set(txn, "merged", {})
        ydoc.get_map("runtime").set(
            txn,
            "environment",
            {
                "materialization": {
                    "scenario_id": "web_desktop",
                    "required_branches": required_branches,
                }
            },
        )

    class _FakeStore:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        async def write_update(self, update: bytes, **_kwargs) -> bool:
            self.writes.append(bytes(update))
            return True

    async def _unexpected_resolve(*_args, **_kwargs) -> None:
        raise AssertionError("matching persisted state must not be resolved again")

    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "resolve_materialized_payload_from_doc_async",
        _unexpected_resolve,
    )

    seed_result = {"scenario_id": "web_desktop", "space": "workspace"}
    room = SimpleNamespace(ydoc=ydoc)
    store = _FakeStore()
    result = asyncio.run(
        gateway_module._ensure_room_effective_materialized(
            "bootstrap-persisted-ready",
            store,
            room,
            seed_result=seed_result,
        )
    )

    assert result is False
    assert seed_result["mode"] == "persisted_effective_state"
    assert seed_result["room_effective_reused"] is True
    assert seed_result["room_effective_materialized"] is False
    assert gateway_module._room_effective_branches_ready(ydoc) is True


def test_room_bootstrap_accepts_bootstrap_validated_persisted_state(monkeypatch) -> None:
    import y_py as Y

    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        ydoc.get_map("ui").set(txn, "current_scenario", "builder")
        ydoc.get_map("runtime").set(
            txn,
            "environment",
            {
                "materialization": {
                    "scenario_id": "builder",
                    "required_branches": ["ui.application", "data.catalog"],
                }
            },
        )

    def _unexpected_full_check(_ydoc) -> bool:  # noqa: ANN001
        raise AssertionError("bootstrap-validated state must not decode effective branches again")

    monkeypatch.setattr(gateway_module, "_room_effective_branches_ready", _unexpected_full_check)
    seed_result = {
        "scenario_id": "builder",
        "space": "workspace",
        "mode": "persisted_effective_state",
        "persisted_effective_state_ready": True,
    }
    room = SimpleNamespace(ydoc=ydoc)

    result = asyncio.run(
        gateway_module._ensure_room_effective_materialized(
            "bootstrap-trusted-persisted",
            SimpleNamespace(),
            room,
            seed_result=seed_result,
        )
    )

    assert result is False
    assert seed_result["room_effective_validation"] == "trusted_persisted_marker"
    assert room._diag_effective_branch_snapshot["ready"] is True


def test_room_bootstrap_rebuilds_ready_effective_branches_after_seed_override(monkeypatch) -> None:
    import y_py as Y

    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    gateway_module._AUTHORITATIVE_SCENARIO_LEASES.clear()
    ydoc = Y.YDoc()
    with ydoc.begin_transaction() as txn:
        ydoc.get_map("ui").set(txn, "current_scenario", "web_desktop")
        ydoc.get_map("ui").set(
            txn,
            "application",
            {
                "desktop": {"pageSchema": {"id": "prompt_ide"}},
                "modals": {"apps_catalog": {}, "widgets_catalog": {}},
            },
        )
        ydoc.get_map("data").set(txn, "catalog", {"apps": [], "widgets": []})
        ydoc.get_map("data").set(txn, "installed", {"apps": [], "widgets": []})
        ydoc.get_map("data").set(txn, "desktop", {"installed": {"apps": [], "widgets": []}})
        ydoc.get_map("data").set(txn, "webio", {})
        ydoc.get_map("data").set(txn, "routing", {})
        ydoc.get_map("registry").set(txn, "merged", {"widgets": {}, "modals": {}})

    class _FakeStore:
        def __init__(self) -> None:
            self.writes: list[bytes] = []

        async def write_update(self, update: bytes, **_kwargs) -> bool:
            self.writes.append(bytes(update))
            return True

    seen_current: list[str] = []

    async def _fake_resolve(self, target_ydoc, _webspace_id, *, scenario_id=None, **_kwargs) -> None:
        current = str(scenario_id or target_ydoc.get_map("ui").get("current_scenario") or "")
        seen_current.append(current)
        self._last_materialized_payload = {
            "scenario_id": current,
            "source_mode": "workspace",
            "application": {
                "desktop": {"pageSchema": {"id": current}},
                "modals": {"apps_catalog": {}, "widgets_catalog": {}},
            },
            "catalog": {"apps": [], "widgets": []},
            "installed": {"apps": [], "widgets": []},
            "desktop": {},
            "webio": {},
            "routing": {},
            "registry": {"widgets": {}, "modals": {}},
            "skill_decls": [],
        }
        self._last_rebuild_timings_ms = {"total": 1.0}

    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "resolve_materialized_payload_from_doc_async",
        _fake_resolve,
    )

    room = SimpleNamespace(ydoc=ydoc)
    store = _FakeStore()

    result = asyncio.run(
        gateway_module._ensure_room_effective_materialized(
            "bootstrap-seed-override-ready",
            store,
            room,
            seed_result={
                "scenario_id": "web_desktop",
                "current_scenario_overridden": True,
                "mode": "projected_seed_reuse",
                "space": "workspace",
            },
        )
    )

    assert result is True
    assert seen_current == ["web_desktop"]
    assert dict(ydoc.get_map("ui").get("application") or {})["desktop"]["pageSchema"]["id"] == "web_desktop"
    assert store.writes


def test_room_bootstrap_stuck_incident_is_sticky_until_ready() -> None:
    key = "gateway-room-stuck"
    gateway_module._YROOM_LIFECYCLE.clear()

    attempt_id = gateway_module._mark_room_bootstrap_started(key, yws_attempt_id="yws-stuck-1")
    incident = gateway_module._mark_room_bootstrap_stuck(
        key,
        attempt_id,
        step="seed_from_scenario",
        reason="seed_from_scenario_timeout_after_20.000s",
    )
    gateway_module._mark_room_bootstrap_finished(
        key,
        attempt_id,
        state="timeout",
        error="TimeoutError",
    )

    assert incident["bootstrap_stuck"] is True
    snapshot = gateway_module.gateway_transport_snapshot()["rooms"][key]
    assert snapshot["bootstrap_stuck"] is True
    assert snapshot["stuck_step"] == "seed_from_scenario"
    assert snapshot["stuck_attempt_id"] == attempt_id
    assert snapshot["recommended_action"] == "reset_runtime_room"
    assert snapshot["stuck_age_s"] is not None

    ready_attempt = gateway_module._mark_room_bootstrap_started(key, yws_attempt_id="yws-ready-1")
    gateway_module._mark_room_bootstrap_finished(key, ready_attempt, state="ready")
    recovered = gateway_module.gateway_transport_snapshot()["rooms"][key]
    assert recovered["bootstrap_stuck"] is False
    assert recovered["stuck_step"] is None
    assert recovered["recommended_action"] is None

    gateway_module._YROOM_LIFECYCLE.clear()


def test_process_events_command_runs_go_home_before_ack(monkeypatch) -> None:
    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    published: list[tuple[str, dict[str, object] | None]] = []
    responses: list[dict[str, object]] = []
    captured: list[tuple[str, bool]] = []

    monkeypatch.setattr(gateway_module, "_make_publish_bus", lambda *args, **kwargs: (lambda topic, extra=None: published.append((topic, extra))))

    async def _fake_go_home(webspace_id: str, *, wait_for_rebuild: bool = True) -> dict[str, object]:
        captured.append((webspace_id, wait_for_rebuild))
        return {
            "ok": True,
            "accepted": True,
            "action": "go_home",
            "webspace_id": webspace_id,
            "scenario_id": "web_desktop",
        }

    monkeypatch.setattr(webspace_runtime_module, "go_home_webspace", _fake_go_home)

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="desktop.webspace.go_home",
            cmd_id="cmd-1",
            payload={"webspace_id": "default", "wait_for_rebuild": True},
            device_id="dev-1",
            webspace_id="default",
            send_response=_send_response,
        )
    )

    assert captured == [("desktop", True)]
    assert published == []
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"]["scenario_id"] == "web_desktop"


def test_process_events_command_publishes_generic_skill_event(monkeypatch) -> None:
    published: list[object] = []
    responses: list[dict[str, object]] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    monkeypatch.setattr(gateway_module, "get_agent_ctx", lambda: SimpleNamespace(bus=_Bus()))

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="skill.event.publish",
            cmd_id="cmd-skill-event-1",
            payload={
                "event_type": "custom.location.requested",
                "payload": {"city": "Berlin", "request_id": "req-1"},
                "node_id": "member-01",
                "webspace_id": "desktop",
                "_meta": {"trace_id": "trace-1"},
            },
            device_id="dev-1",
            webspace_id="desktop",
            send_response=_send_response,
        )
    )

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "type", "") == "custom.location.requested"
    payload = getattr(event, "payload", {})
    assert payload["city"] == "Berlin"
    assert payload["request_id"] == "req-1"
    assert payload["node_id"] == "member-01"
    assert payload["target_node_id"] == "member-01"
    assert payload["webspace_id"] == "desktop"
    assert payload["_meta"]["trace_id"] == "trace-1"
    assert payload["_meta"]["target_node_id"] == "member-01"
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"] == {"event_type": "custom.location.requested"}


def test_process_events_command_routes_subscribed_skill_update_through_coordinator(monkeypatch) -> None:
    from adaos.services import agent_context as agent_context_module
    from adaos.services import artifact_subscription_update as update_service_module

    responses: list[dict[str, object]] = []
    updates: list[dict[str, object]] = []
    ctx = SimpleNamespace()

    class _Coordinator:
        def __init__(self, value) -> None:
            assert value is ctx

        def is_subscribed(self, project_id: str) -> bool:
            return project_id == "recipe_skill"

        def select_route(self, project_id: str):
            return SimpleNamespace(package_required=project_id == "recipe_skill")

        async def update(self, kind: str, project_id: str, **kwargs):
            updates.append({"kind": kind, "project_id": project_id, **kwargs})
            return {"ok": True, "mode": "package_activation", "updated": True}

    monkeypatch.setattr(agent_context_module, "get_ctx", lambda: ctx)
    monkeypatch.setattr(update_service_module, "ArtifactSubscriptionUpdateCoordinator", _Coordinator)

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="skills.update",
            cmd_id="cmd-skill-update-1",
            payload={
                "name": "recipe_skill",
                "webspace_id": "desktop",
                "expected_plan_digest": "sha256:" + "a" * 64,
                "idempotency_key": "operator-attempt-1",
            },
            device_id="dev-1",
            webspace_id="desktop",
            send_response=_send_response,
        )
    )

    assert updates[0]["kind"] == "skill"
    assert updates[0]["expected_plan_digest"] == "sha256:" + "a" * 64
    assert updates[0]["idempotency_key"] == "operator-attempt-1"
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"]["mode"] == "package_activation"


def test_process_events_command_accepts_demo_metrics_host_action(monkeypatch) -> None:
    published: list[object] = []
    responses: list[dict[str, object]] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    monkeypatch.setattr(gateway_module, "get_agent_ctx", lambda: SimpleNamespace(bus=_Bus()))

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="demo_metrics.host_action",
            cmd_id="cmd-demo-host-1",
            payload={"action_id": "demo", "metric_id": "cpu", "webspace_id": "desktop"},
            device_id="dev-1",
            webspace_id="desktop",
            send_response=_send_response,
        )
    )

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "type", "") == "demo_metrics.host_action"
    payload = getattr(event, "payload", {})
    assert payload["action_id"] == "demo"
    assert payload["metric_id"] == "cpu"
    assert payload["webspace_id"] == "desktop"
    assert responses[-1]["ok"] is True


def test_process_events_command_records_reload_command_trace(monkeypatch) -> None:
    published: list[tuple[str, dict[str, object] | None]] = []
    responses: list[dict[str, object]] = []

    monkeypatch.setattr(gateway_module, "_make_publish_bus", lambda *args, **kwargs: (lambda topic, extra=None: published.append((topic, extra))))
    _clear_yws_guard_state()
    gateway_module._COMMAND_TRACE_HISTORY.clear()
    gateway_module._COMMAND_TRACE_STATS.update(
        {
            "reload_total": 0,
            "reload_duplicate_total": 0,
            "reset_total": 0,
            "reset_duplicate_total": 0,
        }
    )
    gateway_module._COMMAND_TRACE_SEQ = 0

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="desktop.webspace.reload",
            cmd_id="cmd-reload-1",
            payload={"webspace_id": "default", "scenario_id": "web_desktop"},
            device_id="dev-1",
            webspace_id="default",
            send_response=_send_response,
            client_label="events_ws:127.0.0.1:12345",
        )
    )

    snapshot = gateway_module.gateway_transport_snapshot()
    commands = snapshot["commands"]

    assert len(published) == 1
    topic, payload = published[0]
    assert topic == "desktop.webspace.reload"
    assert payload is not None
    assert payload["webspace_id"] == "default"
    assert payload["scenario_id"] == "web_desktop"
    meta = dict(payload["_meta"])  # type: ignore[index]
    guard_reset = meta.pop("yws_guard_reset")
    assert guard_reset == {
        "ok": True,
        "webspace_id": "default",
        "reason": "desktop.webspace.reload",
        "cleared_total": 0,
        "client_open_history_cleared": 0,
        "client_attempt_history_cleared": 0,
        "client_short_session_history_cleared": 0,
        "quarantine_cleared": 0,
        "recovery_in_flight_cleared": 0,
        "incident_cleared": 0,
        "log_cleared": 0,
        "notify_cleared": 0,
    }
    assert meta == {
        "cmd_id": "cmd-reload-1",
        "gateway_client": "events_ws:127.0.0.1:12345",
        "gateway_command_seq": 1,
        "gateway_command_fingerprint": commands["last_reload"]["fingerprint"],
    }
    assert responses[-1]["ok"] is True
    assert commands["reload_total"] == 1
    assert commands["reload_recent_60s"] == 1
    assert commands["last_reload"]["cmd_id"] == "cmd-reload-1"
    assert commands["last_reload"]["client"] == "events_ws:127.0.0.1:12345"
    gateway_module._COMMAND_TRACE_HISTORY.clear()
    gateway_module._COMMAND_TRACE_STATS.update(
        {
            "reload_total": 0,
            "reload_duplicate_total": 0,
            "reset_total": 0,
            "reset_duplicate_total": 0,
        }
    )


def test_yws_guard_manual_reset_clears_only_target_webspace(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 10)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 10.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 40.0)
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]
    gateway_module._ACTIVE_YWS_CONNECTIONS["lab"] = [object()]

    for _idx in range(6):
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")
        gateway_module._record_yws_guard_attempt("lab", "dev-hot")

    reason_desktop, _diag_desktop = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    reason_lab, _diag_lab = gateway_module._yws_guard_reject_reason("lab", "dev-hot")
    assert reason_desktop == "client_reconnect_storm"
    assert reason_lab == "client_reconnect_storm"

    reset = gateway_module.clear_yws_guard_state_for_webspace("desktop", reason="test_reload")

    assert reset["ok"] is True
    assert reset["webspace_id"] == "desktop"
    assert reset["client_attempt_history_cleared"] == 1
    assert reset["quarantine_cleared"] == 1
    assert reset["incident_cleared"] == 1
    assert gateway_module._YWS_GUARD_DIAG["last_manual_reset_webspace_id"] == "desktop"
    assert gateway_module._YWS_GUARD_DIAG["last_manual_reset_reason"] == "test_reload"

    reason_after_desktop, diag_after_desktop = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    reason_after_lab, _diag_after_lab = gateway_module._yws_guard_reject_reason("lab", "dev-hot")

    assert reason_after_desktop == ""
    assert diag_after_desktop["client_open_15s"] == 0
    assert reason_after_lab == "client_reconnect_backoff"
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()


def test_process_events_command_preserves_target_node_for_voice_chat(monkeypatch) -> None:
    published: list[object] = []
    responses: list[dict[str, object]] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    monkeypatch.setattr(gateway_module, "get_agent_ctx", lambda: SimpleNamespace(bus=_Bus()))

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="voice.chat.user",
            cmd_id="cmd-voice-1",
            payload={"text": "hello", "node_id": "member-01", "webspace_id": "desktop"},
            device_id="dev-1",
            webspace_id="desktop",
            send_response=_send_response,
        )
    )

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "type", "") == "voice.chat.user"
    payload = getattr(event, "payload", {})
    assert payload["text"] == "hello"
    assert payload["target_node_id"] == "member-01"
    assert payload["_meta"]["target_node_id"] == "member-01"
    assert responses[-1]["ok"] is True
    gateway_module._COMMAND_TRACE_SEQ = 0


def test_process_events_command_publishes_neutral_dialog_user_message(monkeypatch) -> None:
    published: list[object] = []
    responses: list[dict[str, object]] = []

    class _Bus:
        def publish(self, event: object) -> None:
            published.append(event)

    monkeypatch.setattr(gateway_module, "get_agent_ctx", lambda: SimpleNamespace(bus=_Bus()))

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="dialog.user_message",
            cmd_id="cmd-dialog-1",
            payload={"text": "hello", "node_id": "member-01", "webspace_id": "desktop"},
            device_id="dev-1",
            webspace_id="desktop",
            send_response=_send_response,
        )
    )

    assert len(published) == 1
    event = published[0]
    assert getattr(event, "type", "") == "dialog.user_message"
    payload = getattr(event, "payload", {})
    assert payload["text"] == "hello"
    assert payload["target_node_id"] == "member-01"
    assert payload["_meta"]["target_node_id"] == "member-01"
    assert payload["_meta"]["dialog_event_kind"] == "dialog.user_message"
    assert payload["_meta"]["canonical_event_kind"] == "dialog.user_message"
    assert payload["_meta"]["input_event_kind"] == "dialog.user_message"
    assert responses[-1]["ok"] is True
    gateway_module._COMMAND_TRACE_SEQ = 0


def test_process_events_command_publishes_pending_action_directly(monkeypatch) -> None:
    responses: list[dict[str, object]] = []
    calls: list[dict[str, object]] = []
    ctx = SimpleNamespace(name="ctx")

    import adaos.services.pending_actions as pending_actions_module

    async def _publish_pending_action_async(**kwargs):
        calls.append(dict(kwargs))
        return {"id": kwargs["action_id"], "status": "pending"}

    monkeypatch.setattr(gateway_module, "get_agent_ctx", lambda: ctx)
    monkeypatch.setattr(pending_actions_module, "publish_pending_action_async", _publish_pending_action_async)

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="pending_actions.publish.request",
            cmd_id="cmd-pending-publish-1",
            payload={
                "webspace_id": "desktop",
                "action_id": "pa.test",
                "kind": "test.pending",
                "title": "Pending test",
                "_meta": {"cmd_id": "ignored"},
            },
            device_id="dev-1",
            webspace_id="desktop",
            send_response=_send_response,
        )
    )

    assert calls == [
        {
            "ctx": ctx,
            "webspace_id": "desktop",
            "action_id": "pa.test",
            "kind": "test.pending",
            "title": "Pending test",
        }
    ]
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"] == {"action": {"id": "pa.test", "status": "pending"}}


def test_process_events_command_responds_pending_action_directly(monkeypatch) -> None:
    responses: list[dict[str, object]] = []
    calls: list[tuple[str, str, dict[str, object]]] = []
    ctx = SimpleNamespace(name="ctx")

    import adaos.services.pending_actions as pending_actions_module

    async def _respond_pending_action_async(action_id, response_action_id, **kwargs):
        calls.append((action_id, response_action_id, dict(kwargs)))
        return {"response": {"response_action_id": response_action_id}, "terminal": True}

    monkeypatch.setattr(gateway_module, "get_agent_ctx", lambda: ctx)
    monkeypatch.setattr(pending_actions_module, "respond_pending_action_async", _respond_pending_action_async)

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="pending_actions.respond.request",
            cmd_id="cmd-pending-respond-1",
            payload={
                "webspace_id": "desktop",
                "action_id": "pa.test",
                "response_action_id": "refuse",
                "responder": {"type": "browser"},
                "response_payload": {"source": "pending_actions"},
                "_meta": {"cmd_id": "ignored"},
            },
            device_id="dev-1",
            webspace_id="desktop",
            send_response=_send_response,
        )
    )

    assert calls == [
        (
            "pa.test",
            "refuse",
            {
                "ctx": ctx,
                "webspace_id": "desktop",
                "responder": {"type": "browser"},
                "response_payload": {"source": "pending_actions"},
            },
        )
    ]
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"] == {"response": {"response_action_id": "refuse"}, "terminal": True}


def test_process_events_command_submits_conversation_interaction_token(monkeypatch) -> None:
    responses: list[dict[str, object]] = []
    published: list[tuple[str, dict[str, object] | None]] = []
    calls: list[dict[str, object]] = []

    from adaos.services import conversation_interactions

    monkeypatch.setattr(
        gateway_module,
        "_make_publish_bus",
        lambda *args, **kwargs: (lambda topic, extra=None: published.append((topic, extra))),
    )

    def _submit_action_token(token, **kwargs):
        calls.append({"token": token, **kwargs})
        return {"interaction": {"interaction_id": "interaction.web"}, "response": {"status": "answered"}, "duplicate": False}

    monkeypatch.setattr(conversation_interactions, "submit_action_token", _submit_action_token)

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="conversation.interaction.respond.request",
            cmd_id="cmd-interaction-1",
            payload={
                "webspace_id": "dev1-dev",
                "action_token": "ia:0:abc",
                "idempotency_key": "web:m1:ia:0:abc",
                "source_message_id": "m1",
                "_meta": {"route_id": "voice_chat"},
            },
            device_id="dev-1",
            webspace_id="dev1-dev",
            send_response=_send_response,
        )
    )

    assert calls[0]["token"] == "ia:0:abc"
    assert calls[0]["idempotency_key"] == "web:m1:ia:0:abc"
    assert calls[0]["metadata"]["webspace_id"] == "dev1-dev"
    assert calls[0]["metadata"]["source_message_id"] == "m1"
    assert published[0][0] == "conversation.interaction.responded"
    assert responses[-1]["ok"] is True


def test_process_events_command_requires_scenario_id_for_set_home(monkeypatch) -> None:
    published: list[tuple[str, dict[str, object] | None]] = []
    responses: list[dict[str, object]] = []

    monkeypatch.setattr(gateway_module, "_make_publish_bus", lambda *args, **kwargs: (lambda topic, extra=None: published.append((topic, extra))))

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="desktop.webspace.set_home",
            cmd_id="cmd-2",
            payload={"webspace_id": "default"},
            device_id="dev-1",
            webspace_id="default",
            send_response=_send_response,
        )
    )

    assert published == []
    assert responses[-1]["ok"] is False
    assert responses[-1]["error"] == "scenario_id required"


def test_process_events_command_ensure_dev_returns_webspace_id(monkeypatch) -> None:
    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    responses: list[dict[str, object]] = []
    ensured: list[tuple[str, str]] = []

    async def _fake_ensure_dev(
        scenario_id: str,
        *,
        requested_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, object]:
        assert requested_id is None
        assert title == "Prompt IDE"
        return {
            "ok": True,
            "accepted": True,
            "created": True,
            "webspace_id": "dev-prompt-engineer-scenario",
            "scenario_id": scenario_id,
            "home_scenario": scenario_id,
            "kind": "dev",
            "source_mode": "dev",
        }

    async def _fake_ready(webspace_id: str, scenario_id: str | None = None) -> None:
        ensured.append((webspace_id, str(scenario_id or "")))

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    monkeypatch.setattr(webspace_runtime_module, "ensure_dev_webspace_for_scenario", _fake_ensure_dev)
    monkeypatch.setattr(gateway_module, "ensure_webspace_ready", _fake_ready)

    asyncio.run(
        gateway_module.process_events_command(
            kind="desktop.webspace.ensure_dev",
            cmd_id="cmd-3",
            payload={"scenario_id": "prompt_engineer_scenario", "title": "Prompt IDE"},
            device_id="dev-1",
            webspace_id="default",
            send_response=_send_response,
        )
    )

    assert ensured == [("dev-prompt-engineer-scenario", "prompt_engineer_scenario")]
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"] == {
        "ok": True,
        "accepted": True,
        "created": True,
        "webspace_id": "dev-prompt-engineer-scenario",
        "scenario_id": "prompt_engineer_scenario",
        "home_scenario": "prompt_engineer_scenario",
        "kind": "dev",
        "source_mode": "dev",
    }


def test_process_events_command_switches_scenario_before_using_webspace(monkeypatch) -> None:
    from adaos.services.scenario import webspace_runtime as webspace_runtime_module

    responses: list[dict[str, object]] = []
    calls: list[tuple[str, object]] = []

    async def _fake_switch(
        webspace_id: str,
        scenario_id: str,
        **kwargs: object,
    ) -> dict[str, object]:
        calls.append(("switch", (webspace_id, scenario_id, kwargs)))
        return {"ok": True, "accepted": True, "webspace_id": webspace_id, "scenario_id": scenario_id}

    async def _fake_presence(webspace_id: str, device_id: str) -> None:
        calls.append(("presence", (webspace_id, device_id)))

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    monkeypatch.setattr(webspace_runtime_module, "switch_webspace_scenario", _fake_switch)
    monkeypatch.setattr(gateway_module, "_update_device_presence", _fake_presence)
    monkeypatch.setattr(gateway_module, "_make_publish_bus", lambda *args, **kwargs: (lambda *_args, **_kwargs: None))

    selected = asyncio.run(
        gateway_module.process_events_command(
            kind="desktop.webspace.use",
            cmd_id="cmd-navigation",
            payload={"webspace_id": "dev1-dev", "scenario_id": "test04_recipes"},
            device_id="browser-1",
            webspace_id="desktop",
            send_response=_send_response,
            client_label="navigation-e2e",
        )
    )

    assert selected == "dev1-dev"
    assert calls[0][0] == "switch"
    assert calls[0][1][0:2] == ("dev1-dev", "test04_recipes")
    assert calls[0][1][2]["wait_for_rebuild"] is True
    assert calls[1] == ("presence", ("dev1-dev", "browser-1"))
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"]["scenario_id"] == "test04_recipes"


def test_process_events_command_publishes_device_registered(monkeypatch) -> None:
    published: list[tuple[str, dict[str, object] | None]] = []
    responses: list[dict[str, object]] = []

    monkeypatch.setattr(gateway_module, "_make_publish_bus", lambda *args, **kwargs: (lambda topic, extra=None: published.append((topic, extra))))

    async def _fake_start_y_server() -> None:
        return None

    async def _fake_update_device_presence(webspace_id: str, device_id: str) -> None:
        assert webspace_id == "ops"
        assert device_id == "dev-2"

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    monkeypatch.setattr(gateway_module, "start_y_server", _fake_start_y_server)
    monkeypatch.setattr(gateway_module, "_update_device_presence", _fake_update_device_presence)

    asyncio.run(
        gateway_module.process_events_command(
            kind="device.register",
            cmd_id="cmd-4",
            payload={"device_id": "dev-2", "webspace_id": "ops"},
            device_id="dev-2",
            webspace_id="default",
            send_response=_send_response,
        )
    )

    assert published == [
        (
            "device.registered",
            {"device_id": "dev-2", "webspace_id": "ops", "kind": "browser"},
        )
    ]
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"] == {"webspace_id": "ops"}


def test_device_register_rejects_missing_client_version_when_min_version_set(monkeypatch) -> None:
    responses: list[dict[str, object]] = []
    touched: list[dict[str, object]] = []

    from adaos.services import access_links

    monkeypatch.setenv("ADAOS_BROWSER_MIN_CLIENT_BUILD_VERSION", "0.0.62")
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        lambda device_id, **kwargs: touched.append({"device_id": device_id, **kwargs}) or {},
    )
    monkeypatch.setattr(
        gateway_module,
        "start_y_server",
        lambda: (_ for _ in ()).throw(AssertionError("device.register should not start Y server")),
    )

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    asyncio.run(
        gateway_module.process_events_command(
            kind="device.register",
            cmd_id="cmd-version",
            payload={"device_id": "dev-old", "webspace_id": "ops"},
            device_id="dev-old",
            webspace_id="default",
            send_response=_send_response,
        )
    )

    assert responses[-1]["ok"] is False
    assert responses[-1]["error"] == "client_version_unsupported"
    assert responses[-1]["data"] == {
        "webspace_id": "ops",
        "reason": "client_version_unsupported",
    }
    assert touched == [
        {
            "device_id": "dev-old",
            "webspace_id": "ops",
            "connection_state": "client_version_unsupported",
            "online": False,
        }
    ]


def test_device_register_skips_yjs_post_steps_when_yws_guard_is_active(monkeypatch) -> None:
    published: list[tuple[str, dict[str, object] | None]] = []
    responses: list[dict[str, object]] = []
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._YWS_OPEN_HISTORY.clear()
    gateway_module._YWS_CLIENT_OPEN_HISTORY.clear()
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL.clear()
    gateway_module._YWS_GUARD_INCIDENTS.clear()
    gateway_module._ACTIVE_YWS_CONNECTIONS["ops"] = [object()]
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_WEBSPACE", 1)
    monkeypatch.setattr(gateway_module, "_make_publish_bus", lambda *args, **kwargs: (lambda topic, extra=None: published.append((topic, extra))))

    server_start_calls: list[str] = []

    async def _fake_start_y_server() -> None:
        server_start_calls.append("start")

    async def _fake_update_device_presence(_webspace_id: str, _device_id: str) -> None:
        raise AssertionError("device.register guard should avoid YDoc writes")

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    monkeypatch.setattr(gateway_module, "start_y_server", _fake_start_y_server)
    monkeypatch.setattr(gateway_module, "_update_device_presence", _fake_update_device_presence)

    asyncio.run(
        gateway_module.process_events_command(
            kind="device.register",
            cmd_id="cmd-guard",
            payload={"device_id": "dev-guard", "webspace_id": "ops"},
            device_id="dev-guard",
            webspace_id="default",
            send_response=_send_response,
        )
    )

    assert published == [
        (
            "device.registered",
            {
                "device_id": "dev-guard",
                "webspace_id": "ops",
                "kind": "browser",
                "yjs_post_skipped": True,
                "yjs_guard_reason": "active_limit",
            },
        )
    ]
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"] == {
        "webspace_id": "ops",
        "yjs_post_skipped": True,
        "yjs_guard_reason": "active_limit",
    }
    assert server_start_calls == ["start"]
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL.clear()


def test_device_register_skips_yjs_post_steps_when_direct_yws_disabled(monkeypatch) -> None:
    published: list[tuple[str, dict[str, object] | None]] = []
    responses: list[dict[str, object]] = []
    monkeypatch.setattr(gateway_module, "_yws_direct_transport_enabled", lambda: False)
    monkeypatch.setattr(gateway_module, "_make_publish_bus", lambda *args, **kwargs: (lambda topic, extra=None: published.append((topic, extra))))

    async def _fake_start_y_server() -> None:
        raise AssertionError("device.register should not start Y server when direct yws is disabled")

    async def _fake_update_device_presence(_webspace_id: str, _device_id: str) -> None:
        raise AssertionError("device.register should not write YDoc when direct yws is disabled")

    async def _send_response(msg: dict[str, object]) -> None:
        responses.append(msg)

    monkeypatch.setattr(gateway_module, "start_y_server", _fake_start_y_server)
    monkeypatch.setattr(gateway_module, "_update_device_presence", _fake_update_device_presence)

    asyncio.run(
        gateway_module.process_events_command(
            kind="device.register",
            cmd_id="cmd-direct-disabled",
            payload={"device_id": "dev-direct-disabled", "webspace_id": "ops"},
            device_id="dev-direct-disabled",
            webspace_id="default",
            send_response=_send_response,
        )
    )

    assert published == [
        (
            "device.registered",
            {
                "device_id": "dev-direct-disabled",
                "webspace_id": "ops",
                "kind": "browser",
                "yjs_post_skipped": True,
                "yjs_guard_reason": "direct_yws_disabled",
            },
        )
    ]
    assert responses[-1]["ok"] is True
    assert responses[-1]["data"] == {
        "webspace_id": "ops",
        "yjs_post_skipped": True,
        "yjs_guard_reason": "direct_yws_disabled",
    }


def test_update_device_presence_skips_room_when_direct_yws_disabled(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_yws_direct_transport_enabled", lambda: False)

    async def _room_must_not_start(*args, **kwargs):
        raise AssertionError("device presence should not acquire YRoom when direct yws is disabled")

    monkeypatch.setattr(gateway_module.y_server, "get_room", _room_must_not_start)

    asyncio.run(gateway_module._update_device_presence("desktop", "dev-disabled"))


def test_accept_websocket_returns_false_when_handshake_already_closed() -> None:
    class _FakeWebSocket:
        async def accept(self) -> None:
            raise RuntimeError(
                "Expected ASGI message 'websocket.send' or 'websocket.close', but got 'websocket.accept'."
            )

    accepted = asyncio.run(gateway_module._accept_websocket(_FakeWebSocket(), channel="events"))

    assert accepted is False


def test_events_ws_treats_receive_before_accept_runtimeerror_as_disconnect() -> None:
    class _FakeClosedWebSocket:
        query_params: dict[str, str] = {}
        scope = {"client": ("127.0.0.1", 9347)}
        accepted = False

        async def accept(self) -> None:
            self.accepted = True

        async def receive_text(self) -> str:
            raise RuntimeError('WebSocket is not connected. Need to call "accept" first.')

    websocket = _FakeClosedWebSocket()

    asyncio.run(gateway_module.events_ws(websocket))  # type: ignore[arg-type]

    assert websocket.accepted is True


def test_events_ws_uses_rtc_payload_identity_before_device_register(monkeypatch) -> None:
    captured: dict[str, object] = {}

    async def _handle_rtc_offer(**kwargs):
        captured["offer"] = kwargs
        return {"type": "answer", "sdp": "answer-sdp"}

    async def _handle_remote_ice(device_id: str, candidate: object, generation_id=None, peer_id=None) -> None:
        captured["ice"] = {
            "device_id": device_id,
            "candidate": candidate,
            "generation_id": generation_id,
            "peer_id": peer_id,
        }

    monkeypatch.setitem(
        sys.modules,
        "adaos.services.webrtc.peer",
        SimpleNamespace(handle_rtc_offer=_handle_rtc_offer, handle_remote_ice=_handle_remote_ice),
    )

    class _FakeEventsWebSocket:
        query_params: dict[str, str] = {}
        scope = {"client": ("127.0.0.1", 9348)}
        accepted = False

        def __init__(self) -> None:
            self.sent: list[dict[str, object]] = []
            self._messages = [
                json.dumps(
                    {
                        "ch": "events",
                        "t": "cmd",
                        "id": "rtc-offer-1",
                        "kind": "rtc.offer",
                        "payload": {
                            "type": "offer",
                            "sdp": "offer-sdp",
                            "device_id": "dev-signal",
                            "peer_id": "peer-tab-1",
                            "webspace_id": "ops",
                            "generation_id": "rtc-generation-1",
                            "negotiation_mode": "fresh_peer",
                            "browser_session_id": "bs-tab-1",
                            "client_build_id": "build-1",
                            "client_build_version": "0.0.267",
                        },
                    }
                ),
                json.dumps(
                    {
                        "ch": "events",
                        "t": "cmd",
                        "id": "rtc-ice-1",
                        "kind": "rtc.ice",
                        "payload": {
                            "device_id": "dev-signal",
                            "peer_id": "peer-tab-1",
                            "webspace_id": "ops",
                            "generation_id": "rtc-generation-1",
                            "candidate": {"candidate": "candidate:1", "sdpMid": "0", "sdpMLineIndex": 0},
                        },
                    }
                ),
            ]

        async def accept(self) -> None:
            self.accepted = True

        async def receive_text(self) -> str:
            if self._messages:
                return self._messages.pop(0)
            raise gateway_module.WebSocketDisconnect()

        async def send_text(self, payload: str) -> None:
            self.sent.append(json.loads(payload))

    websocket = _FakeEventsWebSocket()

    asyncio.run(gateway_module.events_ws(websocket))  # type: ignore[arg-type]

    assert websocket.accepted is True
    assert captured["offer"]["device_id"] == "dev-signal"  # type: ignore[index]
    assert captured["offer"]["peer_id"] == "peer-tab-1"  # type: ignore[index]
    assert captured["offer"]["webspace_id"] == "ops"  # type: ignore[index]
    assert captured["offer"]["generation_id"] == "rtc-generation-1"  # type: ignore[index]
    assert captured["offer"]["negotiation_mode"] == "fresh_peer"  # type: ignore[index]
    assert captured["offer"]["browser_session_id"] == "bs-tab-1"  # type: ignore[index]
    assert captured["offer"]["client_build_id"] == "build-1"  # type: ignore[index]
    assert captured["offer"]["client_build_version"] == "0.0.267"  # type: ignore[index]
    assert captured["ice"] == {
        "device_id": "dev-signal",
        "candidate": {"candidate": "candidate:1", "sdpMid": "0", "sdpMLineIndex": 0},
        "generation_id": "rtc-generation-1",
        "peer_id": "peer-tab-1",
    }
    assert websocket.sent[0] == {
        "ch": "events",
        "t": "ack",
        "id": "rtc-offer-1",
        "ok": True,
        "data": {"type": "answer", "sdp": "answer-sdp"},
    }
    assert websocket.sent[1] == {"ch": "events", "t": "ack", "id": "rtc-ice-1", "ok": True}


def test_active_browser_session_snapshot_tracks_yws_clients() -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()

    ws = SimpleNamespace(query_params={"dev": "dev-2"})
    gateway_module._track_yws_connection("ops", ws, device_id="dev-2")

    snapshot = gateway_module.active_browser_session_snapshot(now_ts=123.0)

    assert snapshot["peer_total"] == 1
    assert snapshot["peers"] == [
        {
            "device_id": "dev-2",
            "webspace_id": "ops",
            "connection_state": "connected",
            "yjs_channel_state": "open",
            "session_count": 1,
            "source": "yws_gateway",
        }
    ]

    gateway_module._untrack_yws_connection("ops", ws)
    assert gateway_module.active_browser_session_snapshot(now_ts=123.0)["peers"] == []


def test_close_browser_yws_connections_by_device_or_session() -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()

    class _FakeWebSocket:
        def __init__(self, dev_id: str, browser_session_id: str) -> None:
            self.query_params = {"dev": dev_id, "browser_session_id": browser_session_id}
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    device_ws = _FakeWebSocket("dev-revoked", "tab-a")
    session_ws = _FakeWebSocket("dev-other", "session-revoked")
    active_ws = _FakeWebSocket("dev-active", "session-active")
    gateway_module._track_yws_connection("ops", device_ws, device_id="dev-revoked")
    gateway_module._track_yws_connection("ops", session_ws, device_id="dev-other")
    gateway_module._track_yws_connection("ops", active_ws, device_id="dev-active")

    closed_by_device = asyncio.run(gateway_module.close_browser_yws_connections("dev-revoked"))
    closed_by_session = asyncio.run(gateway_module.close_browser_yws_connections("session-revoked"))

    assert closed_by_device == 1
    assert closed_by_session == 1
    assert device_ws.closed == [(1008, "browser_access_revoked")]
    assert session_ws.closed == [(1008, "browser_access_revoked")]
    assert active_ws.closed == []
    assert gateway_module.active_browser_session_snapshot(now_ts=123.0)["peers"] == [
        {
            "device_id": "dev-active",
            "webspace_id": "ops",
            "connection_state": "connected",
            "yjs_channel_state": "open",
            "session_count": 1,
            "source": "yws_gateway",
            "client_limit_id": "session-active",
        }
    ]

    gateway_module._untrack_yws_connection("ops", active_ws)


def test_yjs_balancer_snapshot_reports_limits_usage_and_guard(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()


def test_yjs_balancer_reports_attempt_ids_for_their_exact_browser_session(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    monkeypatch.setattr(gateway_module, "_y_server_runtime_snapshot", lambda: {"ready": True, "room_total": 1})

    tab_a = SimpleNamespace(query_params={"dev": "dev-shared", "browser_session_id": "tab-a"})
    tab_b = SimpleNamespace(query_params={"dev": "dev-shared", "browser_session_id": "tab-b"})
    gateway_module._set_websocket_yws_attempt_id(tab_a, "attempt-a")
    gateway_module._set_websocket_yws_attempt_id(tab_b, "attempt-b")
    gateway_module._track_yws_connection("ops", tab_a, device_id="dev-shared")
    gateway_module._track_yws_connection("ops", tab_b, device_id="dev-shared")

    rows = gateway_module.yjs_balancer_snapshot(webspace_id="ops")["usage"]["active_client_sessions"]

    assert {
        row["client_limit_id"]: row["attempt_ids"]
        for row in rows
    } == {
        "tab-a": ["attempt-a"],
        "tab-b": ["attempt-b"],
    }

    gateway_module._untrack_yws_connection("ops", tab_a)
    gateway_module._untrack_yws_connection("ops", tab_b)
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_WEBSPACE", 4)
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_CLIENT", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 8)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 5)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 3)
    monkeypatch.setattr(gateway_module, "_y_server_runtime_snapshot", lambda: {"ready": True, "room_total": 1})

    ws = SimpleNamespace(query_params={"dev": "dev-2", "browser_session_id": "tab-a"})
    gateway_module._track_yws_connection("ops", ws, device_id="dev-2")
    gateway_module._record_yws_guard_attempt("ops", "dev-2", browser_session_id="tab-a")
    gateway_module._record_yws_guard_attempt("ops", "dev-2", browser_session_id="tab-a")

    snapshot = gateway_module.yjs_balancer_snapshot(webspace_id="ops")

    assert snapshot["schema"] == "adaos.yjs_balancer.v1"
    assert snapshot["state"] == "nominal"
    assert snapshot["reason"] == "within_limits"
    assert snapshot["usage"]["active_connections"] == 1
    assert snapshot["usage"]["active_connection_limit"] == 4
    assert snapshot["usage"]["active_clients"] == 1
    assert snapshot["limits"]["max_active_per_webspace"] == 4
    assert snapshot["limits"]["max_active_per_client"] == 2
    assert snapshot["guard"]["recent_attempts_10s"] == 2
    assert snapshot["guard"]["distinct_clients_10s"] == 1
    assert snapshot["guard"]["webspace_storm_threshold_reached"] is False
    assert snapshot["observed"]["hot_clients"][0]["device_id"] == "dev-2"
    assert snapshot["observed"]["hot_clients"][0]["client_limit_id"] == "tab-a"

    gateway_module._untrack_yws_connection("ops", ws)
    _clear_yws_guard_state()


def test_yjs_balancer_snapshot_marks_webspace_reconnect_storm(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_WEBSPACE", 6)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 10)
    monkeypatch.setattr(gateway_module, "_y_server_runtime_snapshot", lambda: {"ready": True, "room_total": 1})

    gateway_module._record_yws_guard_attempt("desktop", "dev-a")
    gateway_module._record_yws_guard_attempt("desktop", "dev-b")

    snapshot = gateway_module.yjs_balancer_snapshot(webspace_id="desktop")

    assert snapshot["state"] == "critical"
    assert snapshot["reason"] == "webspace_reconnect_storm_threshold"
    assert snapshot["guard"]["recent_attempts_10s"] == 2
    assert snapshot["guard"]["distinct_clients_10s"] == 2
    assert snapshot["guard"]["webspace_storm_threshold_reached"] is True

    _clear_yws_guard_state()


def test_yws_close_preserves_online_state_when_device_has_replacement_session() -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()

    old_ws = SimpleNamespace(query_params={"dev": "dev-2"})
    new_ws = SimpleNamespace(query_params={"dev": "dev-2"})
    gateway_module._track_yws_connection("ops", old_ws, device_id="dev-2")
    gateway_module._track_yws_connection("desktop", new_ws, device_id="dev-2")

    gateway_module._untrack_yws_connection("ops", old_ws)

    assert gateway_module._active_yws_connection_total_for_device("dev-2") == 1
    assert gateway_module._should_mark_yws_browser_session_offline("dev-2") is False

    gateway_module._untrack_yws_connection("desktop", new_ws)
    assert gateway_module._should_mark_yws_browser_session_offline("dev-2") is True


def test_yws_guard_replaces_existing_client_sessions(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_CLIENT", 1)

    class _FakeWebSocket:
        query_params = {"dev": "dev-2"}

        def __init__(self) -> None:
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    old_ws = _FakeWebSocket()
    gateway_module._track_yws_connection("ops", old_ws, device_id="dev-2")

    closed = asyncio.run(gateway_module._close_existing_yws_client_connections("ops", "dev-2"))

    assert closed == 1
    assert old_ws.closed == [(1012, "replaced_by_new_yws_session")]
    assert gateway_module._YWS_GUARD_DIAG["replaced_total"] == 1
    gateway_module._untrack_yws_connection("ops", old_ws)


def test_yws_guard_limits_browser_session_not_whole_device(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_CLIENT", 1)

    class _FakeWebSocket:
        def __init__(self, browser_session_id: str) -> None:
            self.query_params = {"dev": "dev-2", "browser_session_id": browser_session_id}
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    tab_a = _FakeWebSocket("tab-a")
    gateway_module._track_yws_connection("ops", tab_a, device_id="dev-2")

    closed = asyncio.run(
        gateway_module._close_existing_yws_client_connections(
            "ops",
            "dev-2",
            browser_session_id="tab-b",
        )
    )

    assert closed == 0
    assert tab_a.closed == []
    assert gateway_module._active_yws_connection_total_for_client(
        "ops",
        "dev-2",
        browser_session_id="tab-b",
    ) == 0
    assert gateway_module._active_yws_connection_total_for_client(
        "ops",
        "dev-2",
        browser_session_id="tab-a",
    ) == 1

    closed = asyncio.run(
        gateway_module._close_existing_yws_client_connections(
            "ops",
            "dev-2",
            browser_session_id="tab-a",
        )
    )

    assert closed == 1
    assert tab_a.closed == [(1012, "replaced_by_new_yws_session")]
    gateway_module._untrack_yws_connection("ops", tab_a)


def test_yws_guard_limits_duplicated_tabs_by_live_page_id(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_CLIENT", 1)

    class _FakeWebSocket:
        def __init__(self, browser_page_id: str) -> None:
            self.query_params = {
                "dev": "dev-2",
                "browser_session_id": "bs-shared-duplicated-tab",
                "browser_page_id": browser_page_id,
            }
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    page_a = _FakeWebSocket("page-a")
    gateway_module._track_yws_connection("ops", page_a, device_id="dev-2")

    closed = asyncio.run(
        gateway_module._close_existing_yws_client_connections(
            "ops",
            "dev-2",
            browser_page_id="page-b",
            browser_session_id="bs-shared-duplicated-tab",
        )
    )

    assert closed == 0
    assert page_a.closed == []
    assert gateway_module._active_yws_connection_total_for_client(
        "ops",
        "dev-2",
        browser_page_id="page-a",
        browser_session_id="bs-shared-duplicated-tab",
    ) == 1
    assert gateway_module._active_yws_connection_total_for_client(
        "ops",
        "dev-2",
        browser_page_id="page-b",
        browser_session_id="bs-shared-duplicated-tab",
    ) == 0

    gateway_module._untrack_yws_connection("ops", page_a)


def test_yws_guard_default_replaces_scoped_client_sessions(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YWS_GUARD_DIAG.clear()
    assert gateway_module._YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS is True
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_CLIENT", 2)

    class _FakeWebSocket:
        query_params = {"dev": "dev-2", "browser_session_id": "tab-a"}

        def __init__(self, name: str) -> None:
            self.name = name
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    first = _FakeWebSocket("first")
    gateway_module._track_yws_connection("ops", first, device_id="dev-2")

    closed = asyncio.run(
        gateway_module._close_existing_yws_client_connections(
            "ops",
            "dev-2",
            browser_session_id="tab-a",
        )
    )

    assert closed == 1
    assert first.closed == [(1012, "replaced_by_new_yws_session")]
    assert gateway_module._active_yws_connection_total_for_client(
        "ops",
        "dev-2",
        browser_session_id="tab-a",
    ) == 0
    assert gateway_module._YWS_GUARD_DIAG["scoped_replaced_total"] == 1
    gateway_module._untrack_yws_connection("ops", first)


def test_yws_guard_replaces_scoped_client_sessions(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_CLIENT", 2)
    monkeypatch.setattr(gateway_module, "_YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS", True)

    class _FakeWebSocket:
        query_params = {"dev": "dev-2", "browser_session_id": "tab-a"}

        def __init__(self, name: str) -> None:
            self.name = name
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    first = _FakeWebSocket("first")
    second = _FakeWebSocket("second")
    gateway_module._track_yws_connection("ops", first, device_id="dev-2")
    gateway_module._track_yws_connection("ops", second, device_id="dev-2")

    closed = asyncio.run(
        gateway_module._close_existing_yws_client_connections(
            "ops",
            "dev-2",
            browser_session_id="tab-a",
        )
    )

    assert closed == 2
    assert first.closed == [(1012, "replaced_by_new_yws_session")]
    assert second.closed == [(1012, "replaced_by_new_yws_session")]
    assert gateway_module._active_yws_connection_total_for_client(
        "ops",
        "dev-2",
        browser_session_id="tab-a",
    ) == 0
    assert gateway_module._YWS_GUARD_DIAG["scoped_replaced_total"] == 2
    gateway_module._untrack_yws_connection("ops", first)
    gateway_module._untrack_yws_connection("ops", second)


def test_yws_guard_can_keep_overflow_only_policy_for_scoped_clients(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_CLIENT", 2)
    monkeypatch.setattr(gateway_module, "_YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS", False)

    class _FakeWebSocket:
        query_params = {"dev": "dev-2", "browser_session_id": "tab-a"}

        def __init__(self, name: str) -> None:
            self.name = name
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    first = _FakeWebSocket("first")
    second = _FakeWebSocket("second")
    gateway_module._track_yws_connection("ops", first, device_id="dev-2")
    gateway_module._track_yws_connection("ops", second, device_id="dev-2")

    closed = asyncio.run(
        gateway_module._close_existing_yws_client_connections(
            "ops",
            "dev-2",
            browser_session_id="tab-a",
        )
    )

    assert closed == 1
    assert first.closed == [(1012, "replaced_by_new_yws_session")]
    assert second.closed == []
    gateway_module._untrack_yws_connection("ops", first)
    gateway_module._untrack_yws_connection("ops", second)


def test_yws_impl_aborts_when_room_ready_times_out(monkeypatch) -> None:
    gateway_module._TRANSPORT_STATE["yws"].update(
        {
            "active_connections": 0,
            "open_total": 0,
            "close_total": 0,
            "last_open_at": 0.0,
            "last_close_at": 0.0,
        }
    )
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YROOM_LIFECYCLE.clear()
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_MAX_S", 0.01)
    events: list[tuple[str, dict[str, object] | None]] = []

    class _FakeWebSocket:
        query_params = {"dev": "dev-timeout"}
        close_calls: list[tuple[int, str]]

        def __init__(self) -> None:
            self.close_calls = []

        async def accept(self) -> None:
            return None

        async def close(self, *, code: int, reason: str) -> None:
            self.close_calls.append((code, reason))

    async def _fake_start_y_server() -> None:
        return None

    async def _fake_get_room(_name: str) -> object:
        await asyncio.sleep(0.05)
        raise AssertionError("timed wait should cancel before room creation completes")

    monkeypatch.setattr(gateway_module, "start_y_server", _fake_start_y_server)
    monkeypatch.setattr(gateway_module, "_publish_runtime_event", lambda topic, payload=None, source="yjs.gateway": events.append((topic, payload)))
    monkeypatch.setattr(gateway_module.y_server, "get_room", _fake_get_room)

    websocket = _FakeWebSocket()
    asyncio.run(gateway_module._yws_impl(websocket, "desktop"))

    assert websocket.close_calls == [(1013, "room_ready_timeout")]
    assert events == []
    assert gateway_module._TRANSPORT_STATE["yws"]["active_connections"] == 0
    assert gateway_module._ACTIVE_YWS_CONNECTIONS == {}
    assert gateway_module._ACTIVE_YWS_CLIENTS == {}
    attempts = gateway_module._yws_storm_snapshot(time.time())["attempts"]
    assert attempts["last_room_timeout_attempt_id"]
    assert attempts["last_close_attempt_id"] == attempts["last_room_timeout_attempt_id"]
    assert attempts["last_close_reason"] == "room_ready_timeout"
    room_info = gateway_module.gateway_transport_snapshot()["rooms"]["desktop"]
    assert room_info["room_wait_timeout_total"] == 1
    assert room_info["last_wait_timeout_dev_id"] == "dev-timeout"
    assert room_info["last_wait_timeout_yws_attempt_id"] == attempts["last_room_timeout_attempt_id"]


def test_yws_impl_recovers_server_before_rejecting_room_when_active_limit_is_hit(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    gateway_module._YWS_OPEN_HISTORY.clear()
    gateway_module._YWS_CLIENT_OPEN_HISTORY.clear()
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL.clear()
    gateway_module._YWS_GUARD_INCIDENTS.clear()
    gateway_module._YWS_GUARD_DIAG.update(
        {
            "reject_total": 0,
            "last_reject_at": 0.0,
            "last_reject_reason": "",
            "last_reject_webspace_id": "",
            "last_reject_dev_id": "",
        }
    )
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_WEBSPACE", 1)
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]
    events: list[tuple[str, dict[str, object] | None]] = []
    touched: list[dict[str, object]] = []

    class _FakeWebSocket:
        query_params = {"dev": "dev-over-limit", "client_yws_attempt_id": "cyws-over-limit"}

        def __init__(self) -> None:
            self.accepted = False
            self.closed: tuple[int, str] | None = None

        async def accept(self) -> None:
            self.accepted = True

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed = (code, str(reason or ""))

    server_start_calls: list[str] = []

    async def _start_y_server() -> None:
        server_start_calls.append("start")

    from adaos.services import access_links

    monkeypatch.setattr(access_links, "authorize_link", lambda kind, entry_id: (True, "ok"))
    monkeypatch.setattr(
        access_links,
        "touch_browser_session",
        lambda device_id, **kwargs: touched.append({"device_id": device_id, **kwargs}) or {},
    )
    monkeypatch.setattr(gateway_module, "start_y_server", _start_y_server)
    monkeypatch.setattr(gateway_module, "_publish_runtime_event", lambda topic, payload=None, source="yjs.gateway": events.append((topic, payload)))
    close_existing_calls: list[tuple[object, ...]] = []

    async def _close_existing_must_not_run(*args: object, **kwargs: object) -> int:
        close_existing_calls.append(args)
        raise AssertionError("guard should reject before replacing active YWS clients")

    monkeypatch.setattr(gateway_module, "_close_existing_yws_client_connections", _close_existing_must_not_run)

    websocket = _FakeWebSocket()
    asyncio.run(gateway_module._yws_impl(websocket, "desktop"))

    assert websocket.accepted is True
    assert websocket.closed == (1013, "yws_guard_active_limit")
    assert server_start_calls == ["start"]
    assert close_existing_calls == []
    assert touched[0]["connection_state"] == "yws_guard_active_limit"
    assert events[0][0] == "browser.session.changed"
    assert events[0][1]["yjs_channel_state"] == "rejected"
    assert events[0][1]["yjs_attempt_id"]
    assert events[0][1]["client_yws_attempt_id"] == "cyws-over-limit"
    assert events[0][1]["reason"] == "active_limit"
    assert gateway_module._YWS_GUARD_DIAG["last_reject_reason"] == "active_limit"
    attempts = gateway_module._yws_storm_snapshot(time.time())["attempts"]
    assert attempts["last_guard_reject_attempt_id"] == events[0][1]["yjs_attempt_id"]
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL.clear()


def test_yws_guard_allows_single_hot_reconnecting_client_replacement(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 10.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 40.0)
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    for _idx in range(3):
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason == ""
    assert diag["client_open_15s"] == 3
    assert diag["client_reconnect_storm"] is True
    assert diag["webspace_distinct_clients_10s"] == 1
    assert diag["dependency_recovery_allowed"] is True
    assert diag["dependency_recovery_reason"] == "single_client_reconnect_storm_replacement"
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    assert gateway_module._YWS_GUARD_DIAG["last_client_reconnect_storm_dev_id"] == "dev-hot"
    _clear_yws_guard_state()


def test_yws_guard_rejects_sustained_single_client_reconnect_loop(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 10)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 10.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 40.0)
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    for _idx in range(6):
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason == "client_reconnect_storm"
    assert diag["client_open_15s"] == 6
    assert diag["client_reconnect_storm"] is True
    assert diag["webspace_distinct_clients_10s"] == 1
    assert diag["dependency_recovery_allowed"] is False
    assert diag["single_client_reconnect_escalate_at"] == 6
    assert diag["quarantine_ttl_s"] == 10.0
    assert gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    assert gateway_module._YWS_GUARD_DIAG["last_reject_reason"] == "client_reconnect_storm"

    reason_again, diag_again = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason_again == "client_reconnect_backoff"
    assert diag_again["quarantine_ttl_s"] is not None
    _clear_yws_guard_state()


def test_yws_guard_admits_single_recovery_when_no_active_yws_and_route_ready(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECOVERY_IN_PROGRESS_S", 10.0)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_route_dependency_snapshot",
        lambda *, now_ts=None: {
            "ready": True,
            "reason": "route_signal_ready",
            "route_status": "ready",
        },
    )

    for _idx in range(6):
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot", browser_session_id="tab-a")

    reason, diag = gateway_module._yws_guard_reject_reason(
        "desktop",
        "dev-hot",
        browser_session_id="tab-a",
    )

    assert reason == ""
    assert diag["active_total"] == 0
    assert diag["client_reconnect_storm"] is True
    assert diag["dependency_recovery_allowed"] is True
    assert diag["dependency_recovery_reason"] == "client_reconnect_storm_no_active_yws"
    assert diag["recovery_admission_reserved"] is True
    assert diag["recovery_in_progress_ttl_s"] == 10.0
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL

    reason_again, diag_again = gateway_module._yws_guard_reject_reason(
        "desktop",
        "dev-hot",
        browser_session_id="tab-a",
    )
    assert reason_again == "client_recovery_in_progress"
    assert diag_again["quarantine_ttl_s"] is not None
    assert diag_again["quarantine_ttl_s"] <= 10.0
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    _clear_yws_guard_state()


def test_yws_guard_reports_recovery_in_progress_for_active_scoped_client(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECOVERY_IN_PROGRESS_S", 10.0)
    client_key = gateway_module._yws_client_limit_key("dev-hot", browser_session_id="tab-a")
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]
    gateway_module._ACTIVE_YWS_CLIENTS["desktop"] = {client_key: 1}

    for _idx in range(6):
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot", browser_session_id="tab-a")

    reason, diag = gateway_module._yws_guard_reject_reason(
        "desktop",
        "dev-hot",
        browser_session_id="tab-a",
    )

    assert reason == "client_recovery_in_progress"
    assert diag["active_total"] == 1
    assert diag["active_client_total"] == 1
    assert diag["client_reconnect_storm"] is True
    assert diag["quarantine_ttl_s"] == 10.0
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    _clear_yws_guard_state()


def test_yws_guard_rejects_multi_client_reconnect_storm(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 10.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 40.0)
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    for _idx in range(3):
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")
    gateway_module._record_yws_guard_attempt("desktop", "dev-other")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason == "webspace_reconnect_storm"
    assert diag["client_open_15s"] == 3
    assert diag["client_reconnect_storm"] is True
    assert diag["webspace_reconnect_storm"] is True
    assert diag["webspace_distinct_clients_10s"] == 2
    assert diag["quarantine_ttl_s"] == 10.0
    assert gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    assert gateway_module._YWS_GUARD_DIAG["last_reject_reason"] == "webspace_reconnect_storm"

    reason_again, _diag_again = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason_again == "client_reconnect_backoff"
    storm = gateway_module._yws_storm_snapshot(time.time())
    assert storm["client_reconnect_storm_detected"] is True
    assert storm["guard"]["quarantined_total"] == 2
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()


def test_yws_guard_admits_planned_update_reconnect_burst_when_route_is_ready(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_planned_transition_snapshot",
        lambda **_kwargs: {
            "active": False,
            "recently_completed": True,
            "suppress_reconnect_guard": True,
            "reason": "planned_transition_completion_grace",
            "marker": "target-1|succeeded|validate|100",
        },
    )
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_route_dependency_snapshot",
        lambda **_kwargs: {"ready": True, "reason": "route_signal_ready"},
    )

    # Simulate pressure left by three tabs reconnecting together at cutover.
    for dev_id in ("dev-a", "dev-b", "dev-c"):
        key = gateway_module._yws_guard_client_history_key("desktop", dev_id)
        gateway_module._YWS_CLIENT_ATTEMPT_HISTORY[key] = deque([time.time(), time.time()])
    webspace_key = gateway_module._yws_guard_quarantine_key("desktop")
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL[webspace_key] = time.time() + 600.0
    gateway_module._YWS_GUARD_INCIDENTS[webspace_key] = {"count": 1.0, "last_at": time.time()}

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-c")

    assert reason == ""
    assert diag["planned_transition_recovery_allowed"] is True
    assert diag["planned_transition_cleared_total"] >= 2
    assert diag["route_dependency"]["ready"] is True
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    assert not gateway_module._YWS_CLIENT_ATTEMPT_HISTORY
    assert gateway_module._YWS_GUARD_DIAG["planned_transition_recovery_total"] == 1
    _clear_yws_guard_state()


def test_yws_guard_planned_update_does_not_bypass_active_session_limit(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    monkeypatch.setattr(gateway_module, "_YWS_MAX_ACTIVE_PER_WEBSPACE", 2)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_planned_transition_snapshot",
        lambda **_kwargs: {
            "active": True,
            "recently_completed": False,
            "suppress_reconnect_guard": True,
            "reason": "planned_transition_active",
            "marker": "target-1|restarting|launch",
        },
    )
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object(), object()]

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-c")

    assert reason == "active_limit"
    assert diag["planned_transition_recovery_allowed"] is True
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()


def test_yws_guard_does_not_count_planned_transition_attempts(monkeypatch) -> None:
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_planned_transition_snapshot",
        lambda **_kwargs: {
            "suppress_reconnect_guard": True,
            "marker": "target-1|succeeded|validate|100",
        },
    )

    gateway_module._record_yws_guard_attempt("desktop", "dev-a")

    assert not gateway_module._YWS_CLIENT_ATTEMPT_HISTORY
    assert gateway_module._YWS_GUARD_DIAG["planned_transition_attempt_ignored_total"] == 1


def test_yws_guard_reject_hold_follows_guard_quarantine_ttl(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_REJECT_HOLD_MAX_SEC", 30.0)

    assert (
        gateway_module._yws_guard_reject_hold_seconds(
            "client_reconnect_backoff",
            {"quarantine_ttl_s": 12.0},
        )
        == 12.0
    )
    assert (
        gateway_module._yws_guard_reject_hold_seconds(
            "webspace_reconnect_storm",
            {"quarantine_ttl_s": 300.0},
        )
        == 30.0
    )
    assert gateway_module._yws_guard_reject_hold_seconds("active_limit", {"quarantine_ttl_s": 300.0}) == 0.0
    assert gateway_module._yws_guard_reject_hold_seconds("client_reconnect_backoff", {}) == 0.0

    monkeypatch.setattr(gateway_module, "_YWS_GUARD_REJECT_HOLD_MAX_SEC", 0.0)
    assert (
        gateway_module._yws_guard_reject_hold_seconds(
            "client_reconnect_backoff",
            {"quarantine_ttl_s": 12.0},
        )
        == 0.0
    )


def test_yws_guard_allows_single_client_short_session_recovery(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_LIMIT", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_WINDOW_S", 60.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MIN_STABLE_SESSION_S", 20.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 4)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_route_dependency_snapshot",
        lambda *, now_ts=None: {"ready": False, "reason": "route_signal_not_ready"},
    )
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    for _idx in range(3):
        gateway_module._record_yws_short_session("desktop", "dev-hot", lifetime_s=6.0)
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")

    assert reason == ""
    assert diag["client_short_sessions"] == 3
    assert diag["client_short_session_storm"] is True
    assert diag["dependency_recovery_allowed"] is True
    assert diag["dependency_recovery_reason"] == "single_client_short_session_replacement"
    assert diag["quarantine_ttl_s"] is None
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL

    reason_again, _diag_again = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason_again == ""
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()


def test_yws_guard_rejects_sustained_single_client_short_session_loop(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 100)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_LIMIT", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_WINDOW_S", 60.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MIN_STABLE_SESSION_S", 20.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 100)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_route_dependency_snapshot",
        lambda *, now_ts=None: {"ready": False, "reason": "route_signal_not_ready"},
    )
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    for _idx in range(6):
        gateway_module._record_yws_short_session("desktop", "dev-hot", lifetime_s=6.0)

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")

    assert reason == "client_short_session_storm"
    assert diag["client_short_sessions"] == 6
    assert diag["client_short_session_storm"] is True
    assert diag["dependency_recovery_allowed"] is False
    assert diag["single_client_short_session_escalate_at"] == 6
    assert diag["quarantine_ttl_s"] == 30.0
    assert gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    assert gateway_module._YWS_GUARD_DIAG["last_reject_reason"] == "client_short_session_storm"
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()


def test_yws_guard_rejects_multi_client_short_sessions_under_webspace_storm(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_LIMIT", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_WINDOW_S", 60.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MIN_STABLE_SESSION_S", 20.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 4)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_route_dependency_snapshot",
        lambda *, now_ts=None: {"ready": False, "reason": "route_signal_not_ready"},
    )
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    for _idx in range(3):
        gateway_module._record_yws_short_session("desktop", "dev-hot", lifetime_s=6.0)
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")
    gateway_module._record_yws_guard_attempt("desktop", "dev-other")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")

    assert reason == "webspace_reconnect_storm"
    assert diag["client_short_sessions"] == 3
    assert diag["client_short_session_storm"] is True
    assert diag["webspace_distinct_clients_10s"] == 2
    assert diag["quarantine_ttl_s"] == 30.0
    assert gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()


def test_yws_guard_allows_short_session_rescue_without_active_yws(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_LIMIT", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_WINDOW_S", 60.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MIN_STABLE_SESSION_S", 20.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 30.0)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_route_dependency_snapshot",
        lambda *, now_ts=None: {"ready": False, "reason": "route_signal_not_ready"},
    )

    for _idx in range(3):
        gateway_module._record_yws_short_session("desktop", "dev-hot", lifetime_s=6.0)
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")

    assert reason == ""
    assert diag["client_short_sessions"] == 3
    assert diag["client_short_session_storm"] is True
    assert diag["dependency_recovery_allowed"] is True
    assert diag["dependency_recovery_reason"] == "client_short_session_storm_no_active_yws"
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    _clear_yws_guard_state()


def test_yws_guard_allows_short_session_recovery_when_route_dependency_ready(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_LIMIT", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_SHORT_SESSION_WINDOW_S", 60.0)
    monkeypatch.setattr(
        gateway_module,
        "_yws_guard_route_dependency_snapshot",
        lambda *, now_ts=None: {
            "ready": True,
            "reason": "fresh_lightweight_route_probe",
            "route_status": "ready",
        },
    )

    for _idx in range(3):
        gateway_module._record_yws_short_session("desktop", "dev-hot", lifetime_s=6.0)
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")

    assert reason == ""
    assert diag["client_short_sessions"] == 3
    assert diag["client_short_session_storm"] is True
    assert diag["dependency_recovery_allowed"] is True
    assert diag["dependency_recovery_reason"] == "client_short_session_storm"
    assert diag["route_dependency"]["reason"] == "fresh_lightweight_route_probe"
    assert gateway_module._YWS_GUARD_DIAG["dependency_recovery_allowed_total"] == 1
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    _clear_yws_guard_state()


def test_yws_guard_route_dependency_ignores_sync_backpressure_frame_degradation(monkeypatch) -> None:
    reliability = importlib.import_module("adaos.services.reliability")
    monkeypatch.setattr(
        reliability,
        "runtime_signal_snapshot",
        lambda: {
            "route": {
                "status": "ready",
                "summary": "hub route relay subscription installed",
                "details": {},
            }
        },
    )
    monkeypatch.setattr(
        reliability,
        "hub_root_protocol_snapshot",
        lambda *, now_ts=None: {
            "assessment": {"state": "nominal"},
            "route_runtime": {
                "active_tunnels": 2,
                "pending_tunnels": 0,
                "pending_events": 0,
                "pending_chunks": 0,
                "guardrail_active": False,
                "flows": {
                    "control": {"state": "active", "reason": "route_control_session_active"},
                    "frame": {
                        "state": "degraded",
                        "reason": "recent_error:sync_backpressure_late_drop",
                        "last_event": "sync_backpressure_late_drop",
                        "last_error": "route_sync_backpressure",
                    },
                },
            },
        },
    )

    dependency = gateway_module._yws_guard_route_dependency_snapshot(now_ts=time.time())

    assert dependency["ready"] is True
    assert dependency["reason"] == "route_signal_ready"
    assert dependency["frame_degraded_by_sync_shedding"] is True
    assert dependency["pressure"] == []


def test_yws_guard_scopes_reconnect_history_by_browser_session(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 10)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 10.0)

    gateway_module._record_yws_guard_attempt("desktop", "dev-hot", browser_session_id="tab-a")
    gateway_module._record_yws_guard_attempt("desktop", "dev-hot", browser_session_id="tab-a")
    gateway_module._record_yws_guard_attempt("desktop", "dev-hot", browser_session_id="tab-b")

    reason, diag = gateway_module._yws_guard_reject_reason(
        "desktop",
        "dev-hot",
        browser_session_id="tab-a",
    )

    assert reason == ""
    assert diag["client_open_15s"] == 2
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    _clear_yws_guard_state()


def test_yws_guard_rejects_client_backoff_even_without_active_yws() -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    client_key = gateway_module._yws_guard_quarantine_key("desktop", "dev-hot")
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL[client_key] = time.time() + 300.0

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")

    assert reason == "client_reconnect_backoff"
    assert diag["client_quarantine_cleared"] is False
    assert client_key in gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    _clear_yws_guard_state()


def test_yws_guard_scoped_replacement_keeps_client_backoff_quarantined(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS", True)
    client_key = gateway_module._yws_guard_client_history_key(
        "desktop",
        "dev-hot",
        browser_session_id="tab-a",
    )
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL[client_key] = time.time() + 300.0

    class _FakeWebSocket:
        query_params = {"dev": "dev-hot", "browser_session_id": "tab-a"}

        def __init__(self) -> None:
            self.closed: list[tuple[int, str]] = []

        async def close(self, code: int = 1000, reason: str | None = None) -> None:
            self.closed.append((code, str(reason or "")))

    stale = _FakeWebSocket()
    gateway_module._track_yws_connection("desktop", stale, device_id="dev-hot")

    closed = asyncio.run(
        gateway_module._close_existing_yws_client_connections(
            "desktop",
            "dev-hot",
            browser_session_id="tab-a",
        )
    )
    reason, diag = gateway_module._yws_guard_reject_reason(
        "desktop",
        "dev-hot",
        browser_session_id="tab-a",
    )

    assert closed == 1
    assert stale.closed == [(1012, "replaced_by_new_yws_session")]
    assert reason == "client_reconnect_backoff"
    assert diag["active_total"] == 0
    assert diag["dependency_recovery_allowed"] is False
    _clear_yws_guard_state()


def test_yws_guard_observes_webspace_reconnect_storm_without_quarantine(monkeypatch) -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_RECENT_OPEN_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 10.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 40.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_ESCALATION_WINDOW_S", 3600.0)
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    gateway_module._record_yws_guard_attempt("desktop", "dev-hot-a")
    gateway_module._record_yws_guard_attempt("desktop", "dev-hot-b")
    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot-c")
    assert reason == "webspace_reconnect_storm"
    assert diag["webspace_reconnect_storm"] is True
    assert diag["quarantine_ttl_s"] == 10.0
    assert diag["webspace_distinct_clients_10s"] == 2
    assert gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    assert gateway_module._YWS_GUARD_DIAG["last_webspace_reconnect_storm_webspace_id"] == "desktop"

    gateway_module._YWS_GUARD_QUARANTINE_UNTIL.clear()
    gateway_module._YWS_CLIENT_ATTEMPT_HISTORY.clear()
    gateway_module._record_yws_guard_attempt("desktop", "dev-hot-a")
    gateway_module._record_yws_guard_attempt("desktop", "dev-hot-b")
    reason2, diag2 = gateway_module._yws_guard_reject_reason("desktop", "dev-hot-c")
    assert reason2 == "webspace_reconnect_storm"
    assert diag2["webspace_reconnect_storm"] is True
    assert diag2["quarantine_ttl_s"] == 20.0
    assert gateway_module._YWS_GUARD_INCIDENTS
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()


def test_yws_guard_allows_rescue_connection_when_webspace_backoff_has_no_active_yws() -> None:
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    gateway_module._YWS_GUARD_DIAG.clear()
    webspace_key = gateway_module._yws_guard_quarantine_key("desktop")
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL[webspace_key] = time.time() + 300.0

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")

    assert reason == ""
    assert diag["webspace_quarantine_cleared"] is False
    assert webspace_key in gateway_module._YWS_GUARD_QUARANTINE_UNTIL
    _clear_yws_guard_state()


def test_acquire_yws_room_uses_cache_when_bootstrap_lags(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_MAX_S", 0.05)
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_POLL_S", 0.005)

    class _FakeRoom:
        pass

    room = _FakeRoom()
    original_rooms = gateway_module.y_server.rooms
    gateway_module.y_server.rooms = {}

    async def _fake_get_room(_name: str) -> object:
        await asyncio.sleep(0.2)
        return room

    async def _exercise() -> object:
        task = asyncio.create_task(gateway_module._acquire_yws_room("desktop", "dev-cache"))
        await asyncio.sleep(0.015)
        gateway_module.y_server.rooms["desktop"] = room
        return await task

    monkeypatch.setattr(gateway_module.y_server, "get_room", _fake_get_room)
    try:
        resolved = asyncio.run(_exercise())
    finally:
        gateway_module.y_server.rooms = original_rooms

    assert resolved is room


def test_acquire_yws_room_shares_one_room_across_devices_in_same_webspace(monkeypatch) -> None:
    """Browser identity selects a connection, never a private Webspace state."""
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_TIMEOUT_S", 0.0)
    rooms: dict[str, object] = {}

    async def _fake_get_room(name: str) -> object:
        await asyncio.sleep(0)
        return rooms.setdefault(name, object())

    async def _exercise() -> tuple[object, object, object]:
        first, second, other = await asyncio.gather(
            gateway_module._acquire_yws_room(
                "dev1-dev",
                "browser-device-a",
                yws_attempt_id="webrtc-yjs:page-a",
            ),
            gateway_module._acquire_yws_room(
                "dev1-dev",
                "browser-device-b",
                yws_attempt_id="webrtc-yjs:page-b",
            ),
            gateway_module._acquire_yws_room(
                "desktop",
                "browser-device-a",
                yws_attempt_id="webrtc-yjs:page-c",
            ),
        )
        return first, second, other

    monkeypatch.setattr(gateway_module.y_server, "get_room", _fake_get_room)

    first, second, other = asyncio.run(_exercise())

    assert first is second
    assert first is not other
    assert set(rooms) == {"dev1-dev", "desktop"}


def test_acquire_yws_room_leaves_bootstrap_running_after_wait_timeout(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_TIMEOUT_S", 0.01)
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_MAX_S", 0.01)
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_POLL_S", 0.005)

    state = {"completed": False, "cancelled": False}

    class _FakeRoom:
        pass

    async def _fake_get_room(_name: str) -> object:
        try:
            await asyncio.sleep(0.03)
            state["completed"] = True
            return _FakeRoom()
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def _exercise() -> None:
        try:
            await gateway_module._acquire_yws_room("desktop", "dev-timeout")
        except asyncio.TimeoutError:
            pass
        await asyncio.sleep(0.05)

    monkeypatch.setattr(gateway_module.y_server, "get_room", _fake_get_room)

    asyncio.run(_exercise())

    assert state == {"completed": True, "cancelled": False}


def test_yws_impl_cleans_up_after_first_message_timeout(monkeypatch) -> None:
    gateway_module._TRANSPORT_STATE["yws"].update(
        {
            "active_connections": 0,
            "open_total": 0,
            "close_total": 0,
            "last_open_at": 0.0,
            "last_close_at": 0.0,
        }
    )
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CLIENTS.clear()
    _clear_yws_guard_state()
    monkeypatch.setattr(gateway_module, "_YWS_ROOM_READY_TIMEOUT_S", 1.0)
    monkeypatch.setattr(gateway_module, "_YWS_FIRST_MESSAGE_TIMEOUT_S", 0.01)
    events: list[tuple[str, dict[str, object] | None]] = []

    class _FakeWebSocket:
        query_params = {"dev": "dev-first-timeout", "client_yws_attempt_id": "cyws-first-timeout"}
        close_code = None

        async def accept(self) -> None:
            return None

        async def send_bytes(self, _message: bytes) -> None:
            return None

        async def receive(self) -> dict[str, object]:
            await asyncio.sleep(0.05)
            return {"type": "websocket.receive", "bytes": b""}

    class _FakeRoom:
        async def serve(self, websocket) -> None:
            async for _message in websocket:
                raise AssertionError("the adapter should stop iteration before yielding a message")

    async def _fake_start_y_server() -> None:
        return None

    async def _fake_get_room(_name: str) -> object:
        return _FakeRoom()

    monkeypatch.setattr(gateway_module, "start_y_server", _fake_start_y_server)
    monkeypatch.setattr(gateway_module, "_publish_runtime_event", lambda topic, payload=None, source="yjs.gateway": events.append((topic, payload)))
    monkeypatch.setattr(gateway_module.y_server, "get_room", _fake_get_room)

    asyncio.run(gateway_module._yws_impl(_FakeWebSocket(), "desktop"))

    assert [topic for topic, _payload in events] == [
        "browser.session.changed",
        "browser.session.changed",
    ]
    assert events[0][1]["connection_state"] == "connected"
    assert events[1][1]["connection_state"] == "closed"
    assert events[0][1]["yjs_attempt_id"] == events[1][1]["yjs_attempt_id"]
    assert events[0][1]["client_yws_attempt_id"] == "cyws-first-timeout"
    assert events[1][1]["client_yws_attempt_id"] == "cyws-first-timeout"
    assert gateway_module._TRANSPORT_STATE["yws"]["active_connections"] == 0
    assert gateway_module._TRANSPORT_STATE["yws"]["open_total"] == 1
    assert gateway_module._TRANSPORT_STATE["yws"]["close_total"] == 1
    assert gateway_module._ACTIVE_YWS_CONNECTIONS == {}
    assert gateway_module._ACTIVE_YWS_CLIENTS == {}


def test_register_ws_event_subscriptions_installs_forwarder_once(monkeypatch) -> None:
    bus = _FakeBus()
    websocket = _FakeEventWebSocket()

    gateway_module._WS_EVENT_SUBSCRIBERS.clear()
    gateway_module._WS_EVENT_FORWARDER_INSTALLED = False
    monkeypatch.setattr(
        gateway_module,
        "get_agent_ctx",
        lambda: SimpleNamespace(bus=bus),
    )

    loop = asyncio.new_event_loop()
    try:
        added = gateway_module._register_ws_event_subscriptions(
            websocket,
            loop,
            ["core.update.status", "core.update.status"],
        )
        second = gateway_module._register_ws_event_subscriptions(
            websocket,
            loop,
            ["core.update.status"],
        )
    finally:
        loop.close()
        gateway_module._unregister_ws_event_subscriptions(websocket)
        gateway_module._WS_EVENT_SUBSCRIBERS.clear()
        gateway_module._WS_EVENT_FORWARDER_INSTALLED = False

    assert added == {"core.update.status"}
    assert second == set()
    assert [(prefix, getattr(handler, "__name__", "")) for prefix, handler in bus.subscriptions] == [
        ("*", "_forward_ws_bus_event")
    ]


def test_iter_initial_ws_event_messages_includes_hub_node_status(monkeypatch) -> None:
    bootstrap_module = types.ModuleType("adaos.services.bootstrap")
    bootstrap_module.load_config = lambda *args, **kwargs: SimpleNamespace(role="hub")
    bootstrap_module.is_ready = lambda *args, **kwargs: True
    monkeypatch.setitem(sys.modules, "adaos.services.bootstrap", bootstrap_module)
    from adaos.services.system_model import service as system_model_service

    monkeypatch.setattr(
        system_model_service,
        "current_node_status_push_payload",
        lambda: {
            "ready": True,
            "updated_at": 123.0,
            "heartbeat_interval_s": 5.0,
        },
    )
    monkeypatch.setattr(gateway_module.time, "time", lambda: 321.0)

    messages = gateway_module._iter_initial_ws_event_messages({"node.status"})

    assert messages == [
        {
            "ch": "events",
            "t": "evt",
            "kind": "node.status",
            "payload": {
                "ready": True,
                "updated_at": 123.0,
                "heartbeat_interval_s": 5.0,
            },
            "source": "node.status",
            "ts": 321.0,
        }
    ]


def test_iter_initial_ws_event_messages_includes_supervisor_raw_status(monkeypatch) -> None:
    from adaos.services import core_update as core_update_module

    monkeypatch.setattr(
        core_update_module,
        "read_public_update_status",
        lambda: {
            "ok": True,
            "status": {"state": "countdown", "phase": "scheduled"},
            "attempt": {"state": "planned"},
            "runtime": {"transition_mode": "warm_switch"},
            "_served_by": "supervisor",
        },
    )
    monkeypatch.setattr(gateway_module.time, "time", lambda: 654.0)

    messages = gateway_module._iter_initial_ws_event_messages({"supervisor.update.status.raw"})

    assert messages == [
        {
            "ch": "events",
            "t": "evt",
            "kind": "supervisor.update.status.raw",
            "payload": {
                "ok": True,
                "status": {"state": "countdown", "phase": "scheduled"},
                "attempt": {"state": "planned"},
                "runtime": {"transition_mode": "warm_switch"},
                "_served_by": "supervisor",
            },
            "source": "supervisor.update.status.raw",
            "ts": 654.0,
        }
    ]


def test_forward_ws_bus_event_delivers_core_update_status(monkeypatch) -> None:
    websocket = _FakeEventWebSocket()

    gateway_module._WS_EVENT_SUBSCRIBERS.clear()
    gateway_module._WS_EVENT_FORWARDER_INSTALLED = False

    loop = asyncio.new_event_loop()
    try:
        gateway_module._WS_EVENT_SUBSCRIBERS[id(websocket)] = {
            "websocket": websocket,
            "loop": loop,
            "topics": {"core.update.status"},
        }

        gateway_module._forward_ws_bus_event(
            SimpleNamespace(
                type="core.update.status",
                payload={"state": "countdown"},
                source="supervisor",
                ts=321.0,
            )
        )
        loop.run_until_complete(asyncio.sleep(0))
        loop.run_until_complete(asyncio.sleep(0))
    finally:
        loop.close()
        gateway_module._WS_EVENT_SUBSCRIBERS.clear()
        gateway_module._WS_EVENT_SEND_STATES.clear()

    assert websocket.messages == [
        {
            "ch": "events",
            "t": "evt",
            "kind": "core.update.status",
            "payload": {"state": "countdown"},
            "source": "supervisor",
            "ts": 321.0,
        }
    ]


def test_ws_event_send_queue_coalesces_hot_events(monkeypatch) -> None:
    websocket = _FakeEventWebSocket()
    monkeypatch.setattr(gateway_module, "_WS_EVENT_SEND_QUEUE_LIMIT", 2)
    gateway_module._WS_EVENT_SEND_STATES.clear()
    gateway_module._WS_EVENT_SEND_DIAG["coalesced_total"] = 0

    async def _run() -> None:
        gateway_module._enqueue_ws_event_message(
            websocket,
            {"ch": "events", "t": "evt", "kind": "node.status", "payload": {"seq": 1}, "source": "test", "ts": 1.0},
        )
        gateway_module._enqueue_ws_event_message(
            websocket,
            {"ch": "events", "t": "evt", "kind": "core.update.status", "payload": {"seq": 2}, "source": "test", "ts": 2.0},
        )
        gateway_module._enqueue_ws_event_message(
            websocket,
            {"ch": "events", "t": "evt", "kind": "node.status", "payload": {"seq": 3}, "source": "test", "ts": 3.0},
        )
        state = gateway_module._WS_EVENT_SEND_STATES[id(websocket)]
        queued = list(state["queue"])
        assert [item["payload"]["seq"] for item in queued] == [3, 2]
        assert int(gateway_module._WS_EVENT_SEND_DIAG["coalesced_total"]) >= 1
        gateway_module._drop_ws_event_send_state(websocket)

    asyncio.run(_run())
    gateway_module._WS_EVENT_SEND_STATES.clear()


def test_workspace_bootstrap_snapshot_keeps_sqlite_work_off_event_loop(monkeypatch) -> None:
    from adaos.services.yjs import gateway_ws as gateway_module

    started = threading.Event()
    release = threading.Event()

    def _slow_ensure(_webspace_id: str) -> None:
        started.set()
        assert release.wait(timeout=2.0)

    row = SimpleNamespace(
        effective_source_mode="workspace",
        current_scenario_overlay="web_desktop",
        has_current_scenario_overlay=True,
        home_scenario="web_desktop",
        effective_home_scenario="web_desktop",
        is_dev=False,
    )
    monkeypatch.setattr(gateway_module, "ensure_workspace", _slow_ensure)
    monkeypatch.setattr(gateway_module, "get_workspace", lambda _webspace_id: row)

    async def _run() -> None:
        task = asyncio.create_task(gateway_module._workspace_bootstrap_snapshot("desktop"))
        assert await asyncio.to_thread(started.wait, 1.0)
        loop_advanced = False

        async def _tick() -> None:
            nonlocal loop_advanced
            await asyncio.sleep(0)
            loop_advanced = True

        await _tick()
        assert loop_advanced is True
        assert task.done() is False
        release.set()
        snapshot = await task
        assert snapshot["current_scenario_overlay"] == "web_desktop"

    asyncio.run(_run())


def test_yws_guard_attempts_allow_single_client_reconnect_recovery(monkeypatch) -> None:
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_CLIENT_OPEN_15S", 3)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 2)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_COOLDOWN_S", 30.0)
    monkeypatch.setattr(gateway_module, "_YWS_GUARD_MAX_COOLDOWN_S", 30.0)
    gateway_module._YWS_OPEN_HISTORY.clear()
    gateway_module._YWS_CLIENT_OPEN_HISTORY.clear()
    gateway_module._YWS_ATTEMPT_HISTORY.clear()
    gateway_module._YWS_CLIENT_ATTEMPT_HISTORY.clear()
    gateway_module._YWS_GUARD_QUARANTINE_UNTIL.clear()
    gateway_module._YWS_GUARD_INCIDENTS.clear()
    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    gateway_module._ACTIVE_YWS_CONNECTIONS["desktop"] = [object()]

    for _ in range(3):
        gateway_module._record_yws_guard_attempt("desktop", "dev-hot")

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason == ""
    assert diag["client_open_15s"] == 3
    assert diag["client_reconnect_storm"] is True
    assert diag["webspace_distinct_clients_10s"] == 1
    assert diag["dependency_recovery_allowed"] is True
    assert diag["dependency_recovery_reason"] == "single_client_reconnect_storm_replacement"
    assert diag["quarantine_ttl_s"] is None
    assert not gateway_module._YWS_GUARD_QUARANTINE_UNTIL

    reason, diag = gateway_module._yws_guard_reject_reason("desktop", "dev-hot")
    assert reason == ""
    assert diag["dependency_recovery_allowed"] is True

    gateway_module._ACTIVE_YWS_CONNECTIONS.clear()
    _clear_yws_guard_state()
