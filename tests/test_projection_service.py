from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace
from contextlib import asynccontextmanager

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.scenario import projection_service as projection_service_module


def test_projection_service_apply_sync_waits_for_async_apply(monkeypatch) -> None:
    calls: list[tuple[str, str, object, str | None, str | None]] = []

    async def _apply(self, scope, slot, value, *, user_id=None, webspace_id=None):  # noqa: ARG001
        await asyncio.sleep(0)
        calls.append((scope, slot, value, user_id, webspace_id))

    monkeypatch.setattr(projection_service_module.ProjectionService, "apply", _apply)
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=SimpleNamespace())

    service.apply_sync(
        "subnet",
        "infra.status",
        {"value": "OK"},
        user_id="operator",
        webspace_id="desktop",
    )

    assert calls == [("subnet", "infra.status", {"value": "OK"}, "operator", "desktop")]


def test_projection_service_apply_sync_rejects_active_event_loop() -> None:
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=SimpleNamespace())

    async def _call() -> None:
        try:
            service.apply_sync("subnet", "infra.status", {"value": "OK"})
        except RuntimeError as exc:
            assert "await ProjectionService.apply()" in str(exc)
            return
        raise AssertionError("apply_sync must reject an active event-loop thread")

    asyncio.run(_call())


class _FakeTxn:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeMap(dict):
    def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
        self[key] = value


class _FakeDoc:
    def __init__(self, state: dict[str, _FakeMap]) -> None:
        self._state = state

    def get_map(self, name: str) -> _FakeMap:
        return self._state.setdefault(name, _FakeMap())

    def begin_transaction(self) -> _FakeTxn:
        return _FakeTxn()


class _FakeAsyncDoc:
    def __init__(self, state: dict[str, _FakeMap]) -> None:
        self._state = state

    async def __aenter__(self) -> _FakeDoc:
        return _FakeDoc(self._state)

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def _fake_async_get_ydoc(state: dict[str, _FakeMap], calls: list[dict[str, object]] | None = None):
    def _factory(_ws: str, **kwargs) -> _FakeAsyncDoc:
        if calls is not None:
            calls.append(dict(kwargs))
        return _FakeAsyncDoc(state)

    return _factory


class _FakeAsyncDocWithUpdateCallback(_FakeAsyncDoc):
    def __init__(self, state: dict[str, _FakeMap], kwargs: dict[str, object]) -> None:
        super().__init__(state)
        self._kwargs = kwargs

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        callback = self._kwargs.get("write_update_callback")
        if callable(callback):
            callback(
                {
                    "webspace_id": "desktop",
                    "update_bytes": 96 * 1024,
                    "source": "projection_service",
                    "owner": "skill:mediaserver",
                    "channel": "projection.yjs",
                    "root_names": ["data"],
                    "live_room": False,
                    "persisted": True,
                }
            )
        return False


def _fake_async_get_ydoc_with_update_callback(
    state: dict[str, _FakeMap],
    calls: list[dict[str, object]] | None = None,
):
    def _factory(_ws: str, **kwargs) -> _FakeAsyncDocWithUpdateCallback:
        if calls is not None:
            calls.append(dict(kwargs))
        return _FakeAsyncDocWithUpdateCallback(state, dict(kwargs))

    return _factory


@asynccontextmanager
async def _fake_ystore_write_metadata(**kwargs):
    yield kwargs


def test_projection_service_merges_deep_yjs_paths_without_overwriting_siblings(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}

    target = SimpleNamespace(
        backend="yjs",
        path="data/skills/profile/{user_id}/settings",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state))

    asyncio.run(
        service.apply(
            "current_user",
            "profile.settings",
            {"theme": "dark"},
            user_id="u1",
            webspace_id="ws-test",
        )
    )
    asyncio.run(
        service.apply(
            "current_user",
            "profile.settings",
            {"theme": "light"},
            user_id="u2",
            webspace_id="ws-test",
        )
    )

    assert fake_state["data"]["skills"]["profile"]["u1"]["settings"] == {"theme": "dark"}
    assert fake_state["data"]["skills"]["profile"]["u2"]["settings"] == {"theme": "light"}


def test_projection_service_skips_identical_flat_yjs_update(monkeypatch) -> None:
    class _CountingMap(_FakeMap):
        def __init__(self) -> None:
            super().__init__()
            self.set_calls: list[tuple[str, object]] = []

        def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
            self.set_calls.append((key, value))
            super().set(txn, key, value)

    fake_root = _CountingMap()
    fake_state = {"data": fake_root}

    target = SimpleNamespace(
        backend="yjs",
        path="data/weather",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state))

    asyncio.run(service.apply("runtime", "weather", {"city": "Moscow"}, webspace_id="ws-test"))
    asyncio.run(service.apply("runtime", "weather", {"city": "Moscow"}, webspace_id="ws-test"))

    assert fake_root["weather"] == {"city": "Moscow"}
    assert len(fake_root.set_calls) == 1


def test_projection_service_skips_identical_deep_yjs_update(monkeypatch) -> None:
    class _CountingMap(_FakeMap):
        def __init__(self) -> None:
            super().__init__()
            self.set_calls: list[tuple[str, object]] = []

        def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
            self.set_calls.append((key, value))
            super().set(txn, key, value)

    fake_root = _CountingMap()
    fake_state = {"data": fake_root}

    target = SimpleNamespace(
        backend="yjs",
        path="data/skills/profile/u1/settings",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state))

    asyncio.run(service.apply("runtime", "profile", {"theme": "dark"}, webspace_id="ws-test"))
    asyncio.run(service.apply("runtime", "profile", {"theme": "dark"}, webspace_id="ws-test"))

    assert fake_root["skills"]["profile"]["u1"]["settings"] == {"theme": "dark"}
    assert len(fake_root.set_calls) == 1


def test_projection_service_passes_target_root_to_async_get_ydoc(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    calls: list[dict[str, object]] = []

    target = SimpleNamespace(
        backend="yjs",
        path="data/weather",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(
        projection_service_module,
        "async_get_ydoc",
        _fake_async_get_ydoc(fake_state, calls),
    )

    asyncio.run(service.apply("runtime", "weather", {"city": "Moscow"}, webspace_id="ws-test"))

    assert len(calls) == 1
    assert calls[0]["load_mark_roots"] == ["data"]
    assert calls[0]["governed"] is True
    assert callable(calls[0]["write_update_callback"])


def test_merge_nested_path_clones_y_like_arrays_before_rewriting_node_scoped_roots() -> None:
    class _JsonOnlyArray:
        def __init__(self, values: list[object]) -> None:
            self._values = list(values)

        def to_json(self) -> str:
            import json as _json

            return _json.dumps(self._values)

    existing = {
        "member-1": {
            "weather": {
                "history": _JsonOnlyArray([{"city": "Berlin"}]),
            },
        },
    }

    changed, merged = projection_service_module._merge_nested_path(
        existing,
        ["member-1", "voice_chat"],
        {"messages": [{"text": "hello"}]},
    )

    assert changed is True
    assert merged["member-1"]["weather"]["history"] == [{"city": "Berlin"}]
    assert merged["member-1"]["voice_chat"] == {"messages": [{"text": "hello"}]}


def test_nested_y_map_projection_updates_leaf_after_legacy_branch_conversion(monkeypatch) -> None:
    class _FakeYMap(dict):
        def __init__(self, initial=None) -> None:
            super().__init__(initial or {})
            self.set_calls: list[tuple[str, object]] = []

        def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
            self.set_calls.append((key, value))
            self[key] = value

    monkeypatch.setattr(projection_service_module, "_yjs_map_class", lambda: _FakeYMap)

    root = _FakeYMap(
        {
            "nodes": {
                "hub": {
                    "media": {"library": {"count": 1520}},
                    "infrastate": {"summary": {"ok": True}},
                }
            }
        }
    )

    changed = projection_service_module._set_nested_y_map_path(
        root,
        _FakeTxn(),
        ["nodes", "hub", "media", "library_summary"],
        {"count": 1534, "items": []},
    )

    assert changed is True
    assert isinstance(root["nodes"], _FakeYMap)
    assert isinstance(root["nodes"]["hub"], _FakeYMap)
    assert isinstance(root["nodes"]["hub"]["media"], _FakeYMap)
    assert root["nodes"]["hub"]["media"]["library"]["count"] == 1520
    assert root["nodes"]["hub"]["infrastate"]["summary"]["ok"] is True
    root_set_calls = len(root.set_calls)

    changed = projection_service_module._set_nested_y_map_path(
        root,
        _FakeTxn(),
        ["nodes", "hub", "media", "library_summary"],
        {"count": 1535, "items": []},
    )

    assert changed is True
    assert len(root.set_calls) == root_set_calls
    assert root["nodes"]["hub"]["media"]["library_summary"]["count"] == 1535


def test_projection_service_marks_skill_owner_in_write_metadata(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    metadata_calls: list[dict[str, object]] = []

    @asynccontextmanager
    async def _capture_metadata(**kwargs):
        metadata_calls.append(dict(kwargs))
        yield kwargs

    target = SimpleNamespace(
        backend="yjs",
        path="data/weather",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state))
    monkeypatch.setattr(projection_service_module, "ystore_write_metadata", _capture_metadata)
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )

    asyncio.run(service.apply("subnet", "infrastate.snapshot", {"ok": True}, webspace_id="ws-test"))

    assert metadata_calls == [
        {
            "root_names": ["data"],
            "source": "projection_service",
            "owner": "skill:infrastate_skill",
            "channel": "projection.yjs",
            "governed": True,
        }
    ]


def test_projection_service_uses_live_room_fast_path_for_skill_owned_writes(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    calls: list[dict[str, object]] = []
    mutate_calls: list[str] = []

    target = SimpleNamespace(
        backend="yjs",
        path="data/weather",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(
        projection_service_module,
        "mutate_live_room",
        lambda ws, _mutator, **_kwargs: mutate_calls.append(ws) or True,
    )
    monkeypatch.setattr(
        projection_service_module,
        "async_get_ydoc",
        _fake_async_get_ydoc(fake_state, calls),
    )
    monkeypatch.setattr(projection_service_module, "ystore_write_metadata", _fake_ystore_write_metadata)
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="infra_access_skill"),
    )

    asyncio.run(service.apply("subnet", "infra_access.snapshot", {"ok": True}, webspace_id="ws-test"))

    assert mutate_calls == ["ws-test"]
    assert calls == []


def test_projection_service_compacts_inline_after_detached_write_amplification(monkeypatch, tmp_path) -> None:
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    fake_state = {"data": _FakeMap()}
    calls: list[dict[str, object]] = []
    compaction_calls: list[dict[str, object]] = []

    async def _capture_compaction(webspace_id: str, **kwargs) -> dict[str, object]:
        compaction_calls.append({"webspace_id": webspace_id, **dict(kwargs)})
        return {
            "requested": True,
            "executed": True,
            "compacted": True,
            "released_replay_bytes": 98304,
            "malloc_trimmed": True,
        }

    target = SimpleNamespace(
        backend="yjs",
        path="data/media/library_summary",
        webspace_id=None,
    )
    rule = SimpleNamespace(
        targets=[target],
        budget={"max_payload_bytes": 65536, "max_items": 1000},
        route={"surface": "widget:media", "route": "yjs", "projection_slot": "mediaserver.library_summary"},
    )
    registry = SimpleNamespace(
        resolve_rule=lambda scope, slot: rule,  # noqa: ARG005
        resolve=lambda scope, slot: [target],  # noqa: ARG005
    )
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "current_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(
        projection_service_module,
        "async_get_ydoc",
        _fake_async_get_ydoc_with_update_callback(fake_state, calls),
    )
    monkeypatch.setattr(projection_service_module, "ystore_write_metadata", _fake_ystore_write_metadata)
    monkeypatch.setattr(projection_service_module, "_compact_projection_amplification_store", _capture_compaction)
    monkeypatch.setattr(
        projection_service_module,
        "_request_projection_amplification_compaction",
        lambda _ws: (_ for _ in ()).throw(AssertionError("detached path should compact inline")),
    )
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="mediaserver"),
    )

    asyncio.run(
        service.apply(
            "subnet",
            "mediaserver.library_summary",
            {"ok": True, "count": 1534, "items": [{"id": "all", "count": 1534}]},
            webspace_id="desktop",
        )
    )

    assert len(calls) == 1
    assert compaction_calls == [
        {
            "webspace_id": "desktop",
            "mode": "inline_after_detached_write",
            "delay_sec": 0.0,
        }
    ]
    snapshot = projection_service_module.yjs_projection_guard_snapshot(webspace_id="desktop")
    assert snapshot["totals"]["guarded"] == 1
    item = snapshot["items"][0]
    assert item["owner"] == "skill:mediaserver"
    assert item["recovery"]["mode"] == "inline_after_detached_write"
    assert item["recovery"]["deferred_until"] == "detached_writer_flush_complete"


def test_projection_service_suppresses_recent_amplified_projection(monkeypatch, tmp_path) -> None:
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    fake_state = {"data": _FakeMap()}
    calls: list[dict[str, object]] = []

    async def _capture_compaction(_webspace_id: str, **_kwargs) -> dict[str, object]:
        return {
            "requested": True,
            "executed": True,
            "compacted": True,
            "released_replay_bytes": 98304,
            "malloc_trimmed": True,
        }

    target = SimpleNamespace(
        backend="yjs",
        path="data/media/library_summary",
        webspace_id=None,
    )
    rule = SimpleNamespace(
        targets=[target],
        budget={"max_payload_bytes": 65536, "max_items": 1000},
        route={"surface": "widget:media", "route": "yjs", "projection_slot": "mediaserver.library_summary"},
    )
    registry = SimpleNamespace(
        resolve_rule=lambda scope, slot: rule,  # noqa: ARG005
        resolve=lambda scope, slot: [target],  # noqa: ARG005
    )
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "current_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_AMPLIFICATION_SUPPRESS_SEC", 300.0)
    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(
        projection_service_module,
        "async_get_ydoc",
        _fake_async_get_ydoc_with_update_callback(fake_state, calls),
    )
    monkeypatch.setattr(projection_service_module, "ystore_write_metadata", _fake_ystore_write_metadata)
    monkeypatch.setattr(projection_service_module, "_compact_projection_amplification_store", _capture_compaction)
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="mediaserver"),
    )

    payload = {"ok": True, "count": 1534, "items": []}
    asyncio.run(service.apply("subnet", "mediaserver.library_summary", payload, webspace_id="desktop"))
    asyncio.run(service.apply("subnet", "mediaserver.library_summary", payload, webspace_id="desktop"))

    assert len(calls) == 1
    snapshot = projection_service_module.yjs_projection_guard_snapshot(webspace_id="desktop")
    item = snapshot["items"][0]
    assert item["owner"] == "skill:mediaserver"
    assert item["suppressed_total"] == 1
    assert item["last_suppressed_reason"] == "recent_projection_write_amplification"
    assert item["suppressed_until"] > item["last_at"]


def test_projection_service_throttles_skill_owned_primary_doc_writes_when_policy_is_critical(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    throttle_calls: list[dict[str, object]] = []

    async def _capture_govern(**kwargs):
        throttle_calls.append(dict(kwargs))
        return True

    target = SimpleNamespace(
        backend="yjs",
        path="data/infrastate",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state))
    monkeypatch.setattr(
        projection_service_module,
        "_yjs_primary_doc_policy_state",
        lambda **_kwargs: {"policy_state": "throttle", "observed_state": "critical"},
    )
    monkeypatch.setattr(projection_service_module, "_govern_primary_doc_write", _capture_govern)
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )

    asyncio.run(service.apply("subnet", "infrastate.snapshot", {"ok": True}, webspace_id="ws-test"))

    assert len(throttle_calls) == 1
    call = throttle_calls[0]
    assert call["webspace_id"] == "ws-test"
    assert call["path"] == "data/nodes/hub/infrastate"
    assert call["owner"] == "skill:infrastate_skill"
    policy = call["policy"]
    assert policy["policy_state"] == "throttle"
    assert policy["observed_state"] == "critical"
    assert policy["route"] == {
        "kind": "yjs_projection",
        "surface": "subnet.infrastate.snapshot",
        "backend": "yjs",
        "path": "data/nodes/hub/infrastate",
        "root": "data",
    }
    assert policy["projection"]["scope"] == "subnet"
    assert policy["projection"]["slot"] == "infrastate.snapshot"


def test_projection_service_blocks_skill_owned_primary_doc_writes_when_policy_requires(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    calls: list[dict[str, object]] = []

    async def _capture_govern(**kwargs):
        calls.append(dict(kwargs))
        return False

    target = SimpleNamespace(
        backend="yjs",
        path="data/infrastate",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    async_get_calls: list[dict[str, object]] = []
    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state, async_get_calls))
    monkeypatch.setattr(
        projection_service_module,
        "_yjs_primary_doc_policy_state",
        lambda **_kwargs: {"policy_state": "block", "observed_state": "critical", "blocked_roots": ["data"]},
    )
    monkeypatch.setattr(projection_service_module, "_govern_primary_doc_write", _capture_govern)
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )

    asyncio.run(service.apply("subnet", "infrastate.snapshot", {"ok": True}, webspace_id="ws-test"))

    assert len(calls) == 1
    call = calls[0]
    assert call["webspace_id"] == "ws-test"
    assert call["path"] == "data/nodes/hub/infrastate"
    assert call["owner"] == "skill:infrastate_skill"
    policy = call["policy"]
    assert policy["policy_state"] == "block"
    assert policy["observed_state"] == "critical"
    assert policy["blocked_roots"] == ["data"]
    assert policy["route"]["kind"] == "yjs_projection"
    assert policy["route"]["surface"] == "subnet.infrastate.snapshot"
    assert policy["projection"]["slot"] == "infrastate.snapshot"
    assert async_get_calls == []
    assert fake_state["data"] == {}


def test_projection_service_degrades_oversized_yjs_projection_before_write(monkeypatch, tmp_path) -> None:
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    fake_state = {"data": _FakeMap()}
    target = SimpleNamespace(
        backend="yjs",
        path="data/media/library",
        webspace_id=None,
    )
    rule = SimpleNamespace(
        targets=[target],
        budget={"max_payload_bytes": 512, "max_items": 10},
        route={"surface": "widget:media", "route": "yjs", "projection_slot": "mediaserver.library"},
    )
    registry = SimpleNamespace(
        resolve_rule=lambda scope, slot: rule,  # noqa: ARG005
        resolve=lambda scope, slot: [target],  # noqa: ARG005
    )
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "current_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state))
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="mediaserver"),
    )

    payload = {
        "ok": True,
        "items": [{"name": f"file-{idx}.mp4", "size_bytes": idx} for idx in range(40)],
        "count": 40,
        "total_bytes": 780,
        "summary": {"title": "Media Server", "value": 40},
    }

    asyncio.run(service.apply("subnet", "mediaserver.library", payload, webspace_id="desktop"))

    projected = fake_state["data"]["nodes"]["hub"]["media"]["library"]
    assert projected["ok"] is False
    assert projected["state"] == "degraded"
    assert projected["error"] == "yjs_projection_payload_budget_exceeded"
    assert "items" not in projected
    assert projected["guard"]["owner"] == "skill:mediaserver"
    assert projected["guard"]["slot"] == "mediaserver.library"
    assert projected["guard"]["max_list_items"] == 40
    assert projected["preserved"]["count"] == 40

    snapshot = projection_service_module.yjs_projection_guard_snapshot(
        webspace_id="desktop",
        owner="skill:mediaserver",
    )
    assert snapshot["webspace_id"] == "desktop"
    assert snapshot["owner"] == "skill:mediaserver"
    assert snapshot["total"] == 1
    assert snapshot["totals"]["guarded"] == 1
    assert snapshot["items"][0]["slot"] == "mediaserver.library"


def test_projection_guard_snapshot_reads_persisted_cli_process_events(monkeypatch, tmp_path) -> None:
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    monkeypatch.setattr(projection_service_module, "current_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(projection_service_module.time, "time", lambda: 1778055331.0)

    projection_service_module._record_yjs_projection_guard_event(
        webspace_id="desktop",
        owner="skill:mediaserver",
        scope="subnet",
        slot="mediaserver.library",
        path="data/nodes/hub/media/library",
        root_name="data",
        reason="yjs_projection_payload_budget_exceeded",
        payload_bytes=402482,
        projected_bytes=402482,
        degraded_bytes=26969,
        max_payload_bytes=262144,
        max_items=1000,
        collection_metrics={
            "max_list_items": 1520,
            "max_list_path": "items",
            "list_item_total": 1643,
            "mapping_key_total": 8404,
        },
        route={"surface": "widget:media", "projection_slot": "mediaserver.library"},
    )

    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()

    snapshot = projection_service_module.yjs_projection_guard_snapshot(webspace_id="desktop", limit=20)

    assert snapshot["total"] == 1
    assert snapshot["totals"]["guarded"] == 1
    item = snapshot["items"][0]
    assert item["owner"] == "skill:mediaserver"
    assert item["slot"] == "mediaserver.library"
    assert item["payload_bytes"] == 402482
    assert item["max_list_items"] == 1520
    assert item["last_at"] == 1778055331.0


def test_projection_service_records_post_write_yjs_amplification(monkeypatch, tmp_path) -> None:
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    projection_service_module._PRIMARY_DOC_GOVERNANCE_STATS.clear()
    from adaos.services.yjs import governance as yjs_governance
    from adaos.services.yjs import owner_guard

    with yjs_governance._LOCK:
        yjs_governance._STATS.clear()
    with owner_guard._LOCK:
        owner_guard._DECISIONS.clear()
        owner_guard._QUARANTINES.clear()
        owner_guard._QUARANTINE_INCIDENTS.clear()
        owner_guard._QUARANTINE_TOTAL = 0
        owner_guard._DENIED_TOTAL = 0

    fake_state = {"data": _FakeMap()}
    target = SimpleNamespace(
        backend="yjs",
        path="data/media/library",
        webspace_id=None,
    )
    registry = SimpleNamespace(
        resolve_rule=lambda scope, slot: SimpleNamespace(targets=[target], budget={}, route={}),  # noqa: ARG005
        resolve=lambda scope, slot: [target],  # noqa: ARG005
    )
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    def _mutate_live_room(_ws: str, mutator, **kwargs) -> bool:
        mutator(_FakeDoc(fake_state), _FakeTxn())
        callback = kwargs.get("update_callback")
        assert callable(callback)
        callback({"update_bytes": 88_900})
        return True

    monkeypatch.setattr(projection_service_module, "current_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(projection_service_module, "mutate_live_room", _mutate_live_room)
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(
        projection_service_module,
        "_request_projection_amplification_compaction",
        lambda webspace_id: {
            "action": "ystore_runtime_compaction",
            "requested": True,
            "reason": "projection_write_amplification",
            "webspace_id": webspace_id,
        },
    )
    monkeypatch.setattr(owner_guard, "admit_owner_work", lambda **_kwargs: {"allowed": True})
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="mediaserver"),
    )

    asyncio.run(
        service.apply(
            "runtime",
            "mediaserver.library",
            {"ok": True, "count": 1527, "items": []},
            webspace_id="desktop",
        )
    )

    snapshot = projection_service_module.yjs_projection_guard_snapshot(
        webspace_id="desktop",
        owner="skill:mediaserver",
    )

    assert snapshot["total"] == 1
    item = snapshot["items"][0]
    assert item["reason"] == "yjs_projection_write_amplification"
    assert item["path"] == "data/media/library"
    assert item["payload_bytes"] < 2048
    assert item["update_bytes"] == 88_900
    assert item["amplification_ratio"] >= 8.0
    assert item["recovery"]["requested"] is True
    assert item["recovery"]["reason"] == "projection_write_amplification"

    governance = projection_service_module.primary_doc_governance_snapshot(
        webspace_id="desktop",
        owner="skill:mediaserver",
    )
    assert governance["last_policy_state"] == "warn"
    assert governance["last_reason"] == "yjs_projection_write_amplification"
    assert governance["last_update_bytes"] == 88_900
    assert governance["last_route"]["kind"] == "yjs_projection"
    assert governance["last_projection"]["update_bytes"] == 88_900
    assert governance["last_recovery"]["requested"] is True


def test_projection_governance_attributes_sibling_write_amplification_suspect(monkeypatch, tmp_path) -> None:
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    projection_service_module._PRIMARY_DOC_GOVERNANCE_STATS.clear()
    from adaos.services.yjs import governance as yjs_governance
    from adaos.services.yjs import owner_guard

    with yjs_governance._LOCK:
        yjs_governance._STATS.clear()
    with owner_guard._LOCK:
        owner_guard._DECISIONS.clear()
        owner_guard._QUARANTINES.clear()
        owner_guard._QUARANTINE_INCIDENTS.clear()
        owner_guard._QUARANTINE_TOTAL = 0
        owner_guard._DENIED_TOTAL = 0

    fake_state = {"data": _FakeMap()}
    target = SimpleNamespace(
        backend="yjs",
        path="data/infrastate/summary",
        webspace_id=None,
    )
    registry = SimpleNamespace(
        resolve_rule=lambda scope, slot: SimpleNamespace(targets=[target], budget={}, route={}),  # noqa: ARG005
        resolve=lambda scope, slot: [target],  # noqa: ARG005
    )
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    monkeypatch.setattr(projection_service_module, "current_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(projection_service_module, "mutate_live_room", lambda _ws, _mutator, **_kwargs: False)
    monkeypatch.setattr(projection_service_module, "async_get_ydoc", _fake_async_get_ydoc(fake_state))
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(owner_guard, "admit_owner_work", lambda **_kwargs: {"allowed": True})
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )

    projection_service_module._record_yjs_projection_guard_event(
        webspace_id="desktop",
        owner="skill:mediaserver",
        scope="subnet",
        slot="mediaserver.library",
        path="data/nodes/hub/media/library",
        root_name="data",
        reason="yjs_projection_payload_budget_exceeded",
        payload_bytes=402482,
        projected_bytes=402482,
        degraded_bytes=26969,
        max_payload_bytes=262144,
        max_items=1000,
        collection_metrics={
            "max_list_items": 1520,
            "max_list_path": "items",
            "list_item_total": 1643,
            "mapping_key_total": 8404,
        },
        route={"projection_slot": "mediaserver.library"},
    )

    asyncio.run(service.apply("subnet", "infrastate.summary", {"ok": True}, webspace_id="desktop"))

    snapshot = projection_service_module.primary_doc_governance_snapshot(
        webspace_id="desktop",
        owner="skill:infrastate_skill",
    )

    suspects = snapshot["last_write_amplification_suspects"]
    assert suspects[0]["owner"] == "skill:mediaserver"
    assert suspects[0]["slot"] == "mediaserver.library"
    assert suspects[0]["path"] == "data/nodes/hub/media/library"
    assert suspects[0]["payload_bytes"] == 402482
    assert snapshot["last_amplified_branch_owner"] == "skill:mediaserver"


def test_projection_service_governance_snapshot_tracks_throttle_and_block_events(monkeypatch) -> None:
    projection_service_module._PRIMARY_DOC_GOVERNANCE_STATS.clear()
    from adaos.services.yjs import governance as yjs_governance
    from adaos.services.yjs import owner_guard

    with yjs_governance._LOCK:
        yjs_governance._STATS.clear()
    with owner_guard._LOCK:
        owner_guard._DECISIONS.clear()
        owner_guard._QUARANTINES.clear()
        owner_guard._QUARANTINE_INCIDENTS.clear()
        owner_guard._QUARANTINE_TOTAL = 0
        owner_guard._DENIED_TOTAL = 0
    monkeypatch.setattr(owner_guard, "admit_owner_work", lambda **_kwargs: {"allowed": True})

    monkeypatch.setattr(projection_service_module.time, "time", lambda: 1778055331.0)

    asyncio.run(
        projection_service_module._govern_primary_doc_write(
            policy={
                "policy_state": "throttle",
                "reason": "write_amplification",
                "route": {"kind": "yjs_projection", "surface": "subnet.infrastate.snapshot"},
                "projection": {"scope": "subnet", "slot": "infrastate.snapshot", "root": "data"},
            },
            webspace_id="desktop",
            path="data/infrastate",
            owner="skill:infrastate_skill",
        )
    )
    asyncio.run(
        projection_service_module._govern_primary_doc_write(
            policy={
                "policy_state": "block",
                "reason": "write_amplification_blocked",
                "blocked_roots": ["data"],
                "route": {"kind": "yjs_projection", "surface": "subnet.infrastate.snapshot"},
                "projection": {"scope": "subnet", "slot": "infrastate.snapshot", "root": "data"},
            },
            webspace_id="desktop",
            path="data/infrastate",
            owner="skill:infrastate_skill",
        )
    )

    snapshot = projection_service_module.primary_doc_governance_snapshot(
        webspace_id="desktop",
        owner="skill:infrastate_skill",
    )

    assert snapshot["throttled_total"] == 1
    assert snapshot["blocked_total"] == 1
    assert snapshot["last_policy_state"] == "block"
    assert snapshot["last_reason"] == "write_amplification_blocked"
    assert snapshot["last_path"] == "data/infrastate"
    assert snapshot["last_blocked_roots"] == ["data"]
    assert snapshot["last_route"]["kind"] == "yjs_projection"
    assert snapshot["last_route"]["surface"] == "subnet.infrastate.snapshot"
    assert snapshot["last_projection"]["slot"] == "infrastate.snapshot"
