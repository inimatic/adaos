from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

if "y_py" not in sys.modules:
    sys.modules["y_py"] = types.SimpleNamespace(YDoc=object)
if "ypy_websocket" not in sys.modules:
    ystore_mod = types.SimpleNamespace(BaseYStore=object, YDocNotFound=RuntimeError)
    sys.modules["ypy_websocket"] = types.SimpleNamespace(ystore=ystore_mod)
    sys.modules["ypy_websocket.ystore"] = ystore_mod

from adaos.services.scenario import projection_service as projection_service_module


@pytest.fixture(autouse=True)
def _reset_projection_runtime_diagnostics(monkeypatch: pytest.MonkeyPatch) -> None:
    # Projection tests that exercise core-owned persistence must not inherit a
    # skill ContextVar left by an earlier full-suite runtime task. Individual
    # skill-ownership tests override this explicitly after fixture setup.
    monkeypatch.setattr(projection_service_module, "get_current_skill", lambda: None)
    projection_service_module._PRIMARY_DOC_THROTTLE_NEXT_ALLOWED_AT.clear()
    projection_service_module._PRIMARY_DOC_GOVERNANCE_STATS.clear()
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    projection_service_module._YJS_PROJECTION_SOFT_OVERAGE_STATE.clear()
    projection_service_module._PROJECTION_RULE_MISS_STATS.clear()
    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_LOCAL_BRIDGE_ENABLED", False)
    yield
    projection_service_module._PRIMARY_DOC_THROTTLE_NEXT_ALLOWED_AT.clear()
    projection_service_module._PRIMARY_DOC_GOVERNANCE_STATS.clear()
    projection_service_module._YJS_PROJECTION_GUARD_STATS.clear()
    projection_service_module._YJS_PROJECTION_SOFT_OVERAGE_STATE.clear()
    projection_service_module._PROJECTION_RULE_MISS_STATS.clear()


def test_projection_service_records_missing_rule_for_skill_publish(monkeypatch, tmp_path) -> None:
    from adaos.services.skill.declarations import (
        clear_runtime_skill_declarations,
        load_runtime_skill_declarations,
    )

    projection_service_module.reset_projection_rule_miss_diagnostics()
    clear_runtime_skill_declarations("demo_skill")
    load_runtime_skill_declarations(
        "demo_skill",
        {
            "data_projections": [{"scope": "subnet", "slot": "declared.snapshot"}],
            "data_routes": [{"route": "stream", "receiver": "demo.events"}],
        },
        artifact_root=tmp_path,
    )
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="demo_skill"),
    )
    registry = SimpleNamespace(resolve_rule=lambda _scope, _slot: None, resolve=lambda _scope, _slot: [])
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=registry)

    asyncio.run(
        service.apply(
            "subnet",
            "demo.snapshot",
            {"ok": True},
            webspace_id="desktop",
        )
    )

    snapshot = projection_service_module.projection_rule_miss_snapshot(webspace_id="desktop")
    assert snapshot["attempt_total"] == 1
    assert snapshot["items"][0]["owner"] == "skill:demo_skill"
    assert snapshot["items"][0]["scope"] == "subnet"
    assert snapshot["items"][0]["slot"] == "demo.snapshot"
    assert snapshot["items"][0]["last_payload_bytes"] > 0
    assert snapshot["items"][0]["declarations_loaded"] is True
    assert snapshot["items"][0]["declared_projection_total"] == 1
    assert snapshot["items"][0]["declared_route_total"] == 1
    projection_service_module.reset_projection_rule_miss_diagnostics()
    clear_runtime_skill_declarations("demo_skill")


def test_projection_service_does_not_report_core_rule_miss(monkeypatch) -> None:
    projection_service_module.reset_projection_rule_miss_diagnostics()
    monkeypatch.setattr(projection_service_module, "get_current_skill", lambda: None)
    registry = SimpleNamespace(resolve_rule=lambda _scope, _slot: None, resolve=lambda _scope, _slot: [])
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=registry)

    asyncio.run(service.apply("subnet", "core.snapshot", {"ok": True}))

    assert projection_service_module.projection_rule_miss_snapshot()["attempt_total"] == 0


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


def _fake_run_detached_ydoc_mutation(
    state: dict[str, _FakeMap],
    calls: list[dict[str, object]] | None = None,
):
    async def _run(_ws: str, mutator, **kwargs):
        if calls is not None:
            calls.append(dict(kwargs))
        return mutator(_FakeDoc(state))

    return _run


async def _no_live_room(*_args, **_kwargs) -> dict[str, object]:
    return {
        "accepted": False,
        "applied": False,
        "changed": False,
        "reason": "room_not_ready",
    }


async def _allow_primary_doc_write(*_args, **_kwargs) -> bool:
    return True


def _fake_run_detached_with_update_callback(
    state: dict[str, _FakeMap],
    calls: list[dict[str, object]] | None = None,
    *,
    update_bytes: int = 96 * 1024,
):
    async def _run(_ws: str, mutator, **kwargs):
        if calls is not None:
            calls.append(dict(kwargs))
        result = mutator(_FakeDoc(state))
        callback = kwargs.get("write_update_callback")
        if callable(callback):
            callback(
                {
                    "webspace_id": "desktop",
                    "update_bytes": update_bytes,
                    "source": kwargs.get("write_source") or "projection_service",
                    "owner": kwargs.get("write_owner") or "skill:mediaserver",
                    "channel": kwargs.get("write_channel") or "projection.yjs.detached_worker",
                    "root_names": kwargs.get("load_mark_roots") or ["data"],
                    "live_room": False,
                    "persisted": True,
                }
            )
        return result

    return _run


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

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state),
    )

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


def test_projection_service_retries_live_room_handoff_after_detached_race(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    target = SimpleNamespace(backend="yjs", path="data/weather", webspace_id=None)
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=registry)
    live_calls = 0

    async def _live_room(_webspace_id, mutator, **_kwargs):
        nonlocal live_calls
        live_calls += 1
        if live_calls == 1:
            return {"applied": False, "reason": "room_not_ready"}
        doc = _FakeDoc(fake_state)
        with doc.begin_transaction() as txn:
            mutator(doc, txn)
        return {"applied": True, "reason": "applied"}

    async def _detached_race(*_args, **_kwargs):
        raise RuntimeError("sync_get_ydoc_live_room_requires_owner_handoff")

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _live_room)
    monkeypatch.setattr(projection_service_module, "run_detached_ydoc_mutation", _detached_race)

    asyncio.run(service.apply("runtime", "weather", {"city": "Moscow"}, webspace_id="desktop"))

    assert live_calls == 2
    assert fake_state["data"]["weather"] == {"city": "Moscow"}


def test_projection_service_uses_local_bridge_before_detached_fallback(monkeypatch) -> None:
    target = SimpleNamespace(backend="yjs", path="data/root_mgmnt", webspace_id=None)
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=registry)
    bridge_calls: list[tuple[str, str, object]] = []

    async def _bridge(webspace_id: str, path: str, value: object, **_kwargs):
        bridge_calls.append((webspace_id, path, value))
        return {"ok": True, "room_applied": True, "reason": "applied"}

    async def _detached(*_args, **_kwargs):
        raise AssertionError("detached fallback should not run after bridge apply")

    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_LOCAL_BRIDGE_ENABLED", True)
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(projection_service_module, "_try_local_projection_bridge", _bridge)
    monkeypatch.setattr(projection_service_module, "run_detached_ydoc_mutation", _detached)

    asyncio.run(service.apply("current_user", "root_mgmnt.snapshot", {"ok": True}, webspace_id="desktop-dev"))

    assert bridge_calls == [("desktop-dev", "data/root_mgmnt", {"ok": True})]


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

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state),
    )

    asyncio.run(service.apply("runtime", "weather", {"city": "Moscow"}, webspace_id="ws-test"))
    asyncio.run(service.apply("runtime", "weather", {"city": "Moscow"}, webspace_id="ws-test"))

    assert fake_root["weather"] == {"city": "Moscow"}
    assert len(fake_root.set_calls) == 1


def test_projection_service_reconciles_flat_mapping_projection_in_place(monkeypatch) -> None:
    fake_root = _FakeMap(
        {
            "media_indexer": {
                "status": {"value": "done"},
                "results": [{"title": "Gwen"}],
            }
        }
    )
    fake_state = {"data": fake_root}
    calls: list[tuple[object, str, object]] = []

    def _reconcile(y_map, txn, key: str, value: object) -> tuple[bool, str]:
        calls.append((txn, key, value))
        y_map.set(txn, key, value)
        return True, "diff"

    target = SimpleNamespace(backend="yjs", path="data/media_indexer", webspace_id=None)
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=registry)

    monkeypatch.setattr(projection_service_module, "set_map_value_if_changed", _reconcile)
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state),
    )

    next_value = {
        "status": {"value": "done"},
        "results": [{"title": "No Doubt"}],
    }
    asyncio.run(service.apply("runtime", "media_indexer.snapshot", next_value, webspace_id="desktop"))

    assert [(key, value) for _txn, key, value in calls] == [("media_indexer", next_value)]
    assert fake_root["media_indexer"]["results"] == [{"title": "No Doubt"}]


def test_projection_service_skips_identical_deep_yjs_update(monkeypatch) -> None:
    monkeypatch.setattr(projection_service_module, "_yjs_map_class", lambda: None)

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

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state),
    )

    asyncio.run(service.apply("runtime", "profile", {"theme": "dark"}, webspace_id="ws-test"))
    asyncio.run(service.apply("runtime", "profile", {"theme": "dark"}, webspace_id="ws-test"))

    assert fake_root["skills"]["profile"]["u1"]["settings"] == {"theme": "dark"}
    assert len(fake_root.set_calls) == 1


def test_projection_service_passes_target_root_to_detached_worker(monkeypatch) -> None:
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

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state, calls),
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


def test_nested_y_map_projection_reconciles_mapping_leaf_in_place(monkeypatch) -> None:
    class _FakeYMap(dict):
        def set(self, txn, key: str, value: object) -> None:  # noqa: ARG002
            self[key] = value

    root = _FakeYMap()
    calls: list[tuple[object, str, object]] = []

    def _reconcile(parent, txn, key: str, value: object) -> tuple[bool, str]:
        calls.append((parent, key, value))
        parent.set(txn, key, value)
        return True, "diff"

    monkeypatch.setattr(projection_service_module, "_yjs_map_class", lambda: _FakeYMap)
    monkeypatch.setattr(projection_service_module, "set_map_value_if_changed", _reconcile)

    payload = {"current": {"city": "Berlin"}, "status": "ok"}
    changed = projection_service_module._set_nested_y_map_path(
        root,
        _FakeTxn(),
        ["nodes", "hub", "weather"],
        payload,
    )

    assert changed is True
    assert [(key, value) for _parent, key, value in calls] == [("weather", payload)]
    assert root["nodes"]["hub"]["weather"] == payload


def test_projection_service_does_not_suppress_terminal_write_after_legacy_y_map_conversion(monkeypatch) -> None:
    fake_state = {
        "data": _FakeMap(
            {
                "nodes": {
                    "hub": {
                        "media_indexer": {
                            "status": {"value": "ready"},
                            "results": [{"title": "Gwen"}],
                        }
                    }
                }
            }
        )
    }
    live_calls: list[dict[str, object]] = []

    async def _submit_live_room_mutation(_ws: str, mutator, **kwargs) -> dict[str, object]:
        mutator(_FakeDoc(fake_state), _FakeTxn())
        callback = kwargs.get("update_callback")
        assert callable(callback)
        callback(
            {
                "update_bytes": 96 * 1024 if not live_calls else 512,
                "live_room": True,
            }
        )
        live_calls.append(dict(kwargs))
        return {"accepted": True, "applied": True, "changed": True, "reason": "applied"}

    target = SimpleNamespace(backend="yjs", path="data/media_indexer", webspace_id=None)
    registry = SimpleNamespace(
        resolve_rule=lambda _scope, _slot: SimpleNamespace(targets=[target], budget={}, route={}),
        resolve=lambda _scope, _slot: [target],
    )
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=registry)

    monkeypatch.setattr(projection_service_module, "_yjs_map_class", lambda: _FakeMap)
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _submit_live_room_mutation)
    monkeypatch.setattr(projection_service_module, "_local_node_id", lambda: "hub")
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="media_indexer_skill"),
    )

    asyncio.run(
        service.apply(
            "subnet",
            "media_indexer.snapshot",
            {"status": {"value": "searching"}, "results": []},
            webspace_id="desktop",
        )
    )
    asyncio.run(
        service.apply(
            "subnet",
            "media_indexer.snapshot",
            {"status": {"value": "done"}, "results": [{"title": "No Doubt"}]},
            webspace_id="desktop",
        )
    )

    assert len(live_calls) == 2
    assert fake_state["data"]["nodes"]["hub"]["media_indexer"]["status"]["value"] == "done"
    assert fake_state["data"]["nodes"]["hub"]["media_indexer"]["results"] == [{"title": "No Doubt"}]
    assert projection_service_module.yjs_projection_guard_snapshot(webspace_id="desktop")["total"] == 0


def test_projection_service_marks_skill_owner_in_write_metadata(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    metadata_calls: list[dict[str, object]] = []

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

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state, metadata_calls),
    )
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="infrastate_skill"),
    )

    asyncio.run(service.apply("subnet", "infrastate.snapshot", {"ok": True}, webspace_id="ws-test"))

    assert metadata_calls == [
        {
            "load_mark_roots": ["data"],
            "write_source": "projection_service",
            "write_owner": "skill:infrastate_skill",
            "write_channel": "projection.yjs.detached_worker",
            "governed": True,
            "write_update_callback": metadata_calls[0]["write_update_callback"],
        }
    ]
    assert callable(metadata_calls[0]["write_update_callback"])


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

    async def _submit_live(ws, mutator, **_kwargs):
        mutate_calls.append(ws)
        mutator(_FakeDoc(fake_state), _FakeTxn())
        return {"accepted": True, "applied": True, "changed": True, "reason": "applied"}

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _submit_live)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state, calls),
    )
    monkeypatch.setattr(
        projection_service_module,
        "get_current_skill",
        lambda: SimpleNamespace(name="infra_access_skill"),
    )

    asyncio.run(service.apply("subnet", "infra_access.snapshot", {"ok": True}, webspace_id="ws-test"))

    assert mutate_calls == ["ws-test"]
    assert calls == []


def test_projection_service_persists_changed_detached_fallback(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    persist_calls: list[str] = []

    target = SimpleNamespace(
        backend="yjs",
        path="data/infrastate/summary",
        webspace_id=None,
    )
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(
        ctx=SimpleNamespace(),
        registry=registry,
    )

    async def _persist(webspace_id: str) -> dict[str, object]:
        persist_calls.append(webspace_id)
        return {"ok": True, "webspace_id": webspace_id}

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(projection_service_module, "_govern_primary_doc_write", _allow_primary_doc_write)
    monkeypatch.setattr(
        projection_service_module,
        "_suppress_recent_projection_amplification",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_with_update_callback(fake_state, update_bytes=128),
    )
    monkeypatch.setattr(projection_service_module, "_persist_detached_projection_store", _persist)

    asyncio.run(
        service.apply(
            "runtime",
            "infrastate.summary",
            {"version": "0.1.622"},
            webspace_id="desktop",
        )
    )

    assert persist_calls == ["desktop"]


def test_projection_service_surfaces_detached_persistence_failure(monkeypatch) -> None:
    fake_state = {"data": _FakeMap()}
    target = SimpleNamespace(backend="yjs", path="data/weather", webspace_id=None)
    registry = SimpleNamespace(resolve=lambda scope, slot: [target])  # noqa: ARG005
    service = projection_service_module.ProjectionService(ctx=SimpleNamespace(), registry=registry)

    async def _fail_persist(_webspace_id: str) -> dict[str, object]:
        raise RuntimeError("disk unavailable")

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(projection_service_module, "_govern_primary_doc_write", _allow_primary_doc_write)
    monkeypatch.setattr(
        projection_service_module,
        "_suppress_recent_projection_amplification",
        lambda **_kwargs: None,
    )
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_with_update_callback(fake_state),
    )
    monkeypatch.setattr(projection_service_module, "_persist_detached_projection_store", _fail_persist)

    try:
        asyncio.run(service.apply("runtime", "weather", {"city": "Moscow"}, webspace_id="desktop"))
    except RuntimeError as exc:
        assert str(exc) == "disk unavailable"
    else:
        raise AssertionError("detached persistence failure must reach the caller")


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
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_with_update_callback(fake_state, calls),
    )
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
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_with_update_callback(fake_state, calls),
    )
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

    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state),
    )
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
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state, async_get_calls),
    )
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
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state),
    )
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


def test_projection_guard_allows_isolated_soft_overage_and_blocks_burst(monkeypatch) -> None:
    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_SOFT_OVERAGE_MAX_RATIO", 1.5)
    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_SOFT_OVERAGE_GRACE_TOTAL", 2)
    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_SOFT_OVERAGE_WINDOW_SEC", 60.0)
    payload = {"description": "x" * 540}
    kwargs = {
        "webspace_id": "desktop",
        "scope": "webspace",
        "slot": "infrastate.summary",
        "path": "data/infrastate/summary",
        "owner": "skill:infrastate_skill",
        "budget": {"max_payload_bytes": 512, "max_items": 10},
        "route": {"surface": "widget:infrastate_widget", "route": "yjs"},
    }

    first, first_guard = projection_service_module._guarded_projection_payload(payload, **kwargs)
    assert first == payload
    assert first_guard is None

    projection_service_module._guarded_projection_payload({"description": "healthy"}, **kwargs)
    second, second_guard = projection_service_module._guarded_projection_payload(payload, **kwargs)
    third, third_guard = projection_service_module._guarded_projection_payload(payload, **kwargs)
    degraded, burst_guard = projection_service_module._guarded_projection_payload(payload, **kwargs)

    assert second == payload
    assert second_guard is None
    assert third == payload
    assert third_guard is None
    assert degraded["error"] == "yjs_projection_payload_budget_exceeded"
    assert burst_guard is not None
    assert burst_guard["payload_overage"]["window_total"] == 3
    assert burst_guard["payload_overage"]["grace_total"] == 2
    assert burst_guard["payload_overage"]["hard_exceeded"] is False


def test_projection_guard_blocks_hard_payload_overage_immediately(monkeypatch) -> None:
    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_SOFT_OVERAGE_MAX_RATIO", 1.5)
    monkeypatch.setattr(projection_service_module, "_YJS_PROJECTION_SOFT_OVERAGE_GRACE_TOTAL", 2)
    payload = {"description": "x" * 800}

    degraded, guard = projection_service_module._guarded_projection_payload(
        payload,
        webspace_id="desktop",
        scope="webspace",
        slot="infrastate.summary",
        path="data/infrastate/summary",
        owner="skill:infrastate_skill",
        budget={"max_payload_bytes": 512, "max_items": 10},
        route={"surface": "widget:infrastate_widget", "route": "yjs"},
    )

    assert degraded["error"] == "yjs_projection_payload_budget_exceeded"
    assert guard is not None
    assert guard["payload_overage"]["hard_exceeded"] is True
    assert guard["payload_overage"]["hard_max_payload_bytes"] == 768


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

    async def _submit_live_room_mutation(_ws: str, mutator, **kwargs) -> dict[str, object]:
        mutator(_FakeDoc(fake_state), _FakeTxn())
        callback = kwargs.get("update_callback")
        assert callable(callback)
        callback({"update_bytes": 88_900, "live_room": True})
        return {"accepted": True, "applied": True, "changed": True, "reason": "applied"}

    monkeypatch.setattr(projection_service_module, "current_state_dir", lambda: tmp_path / "state")
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _submit_live_room_mutation)
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
    monkeypatch.setattr(projection_service_module, "submit_live_room_mutation", _no_live_room)
    monkeypatch.setattr(
        projection_service_module,
        "run_detached_ydoc_mutation",
        _fake_run_detached_ydoc_mutation(fake_state),
    )
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
    monkeypatch.setattr(
        projection_service_module,
        "_iter_persisted_yjs_projection_guard_events",
        lambda: (_ for _ in ()).throw(AssertionError("hot projection path read persisted diagnostics")),
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


def test_projection_uses_agent_context_node_id_without_reloading_config(monkeypatch) -> None:
    monkeypatch.setattr(
        projection_service_module,
        "_local_node_id",
        lambda: (_ for _ in ()).throw(AssertionError("node config reloaded")),
    )

    assert (
        projection_service_module._context_local_node_id(
            SimpleNamespace(config=SimpleNamespace(node_id="member-local"))
        )
        == "member-local"
    )


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
