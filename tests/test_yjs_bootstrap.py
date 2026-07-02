from __future__ import annotations

import asyncio
from types import SimpleNamespace

import y_py as Y

from adaos.services.yjs import bootstrap as bootstrap_module
from adaos.services.yjs.webspace import default_webspace_id


class _FakeStore:
    def __init__(self, apply_state=None, *, incremental_write_ok: bool = True) -> None:
        self._apply_state = apply_state
        self._incremental_write_ok = incremental_write_ok
        self.start_calls = 0
        self.apply_updates_calls = 0
        self.encode_calls = 0
        self.write_calls = 0
        self.write_kinds: list[str] = []
        self.write_notify: list[bool] = []
        self.encoded_state: dict[str, object] | None = None
        self._stored_doc = Y.YDoc()
        self._stored_doc_initialized = False

    def _ensure_stored_doc_initialized(self) -> None:
        if self._stored_doc_initialized:
            return
        if callable(self._apply_state):
            self._apply_state(self._stored_doc)
        self._stored_doc_initialized = True

    async def start(self) -> None:
        self.start_calls += 1

    async def apply_updates(self, ydoc: Y.YDoc) -> None:
        self.apply_updates_calls += 1
        self._ensure_stored_doc_initialized()
        update = Y.encode_state_as_update(self._stored_doc)
        if update:
            Y.apply_update(ydoc, update)

    def _capture_state(self, ydoc: Y.YDoc) -> dict[str, object]:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        registry_map = ydoc.get_map("registry")
        runtime_map = ydoc.get_map("runtime")
        return {
            "current_scenario": ui_map.get("current_scenario"),
            "ui_application": ui_map.get("application"),
            "ui_scenarios": ui_map.get("scenarios"),
            "data_catalog": data_map.get("catalog"),
            "data_installed": data_map.get("installed"),
            "data_scenarios": data_map.get("scenarios"),
            "registry_merged": registry_map.get("merged"),
            "registry_scenarios": registry_map.get("scenarios"),
            "runtime_bootstrap": runtime_map.get("bootstrap"),
        }

    async def write_update(self, update: bytes, *, update_kind: str = "raw", notify: bool = True) -> bool:
        self.write_calls += 1
        self.write_kinds.append(update_kind)
        self.write_notify.append(bool(notify))
        if not self._incremental_write_ok:
            raise RuntimeError("incremental write unavailable")
        self._ensure_stored_doc_initialized()
        if update:
            Y.apply_update(self._stored_doc, update)
        self.encoded_state = self._capture_state(self._stored_doc)
        return True

    async def encode_state_as_update(self, ydoc: Y.YDoc) -> None:
        self.encode_calls += 1
        self._stored_doc = ydoc
        self._stored_doc_initialized = True
        self.encoded_state = self._capture_state(ydoc)


def _assert_single_rebuild_nudge(
    emitted: list[tuple[str, dict[str, object], str]],
    *,
    scenario_id: str,
    webspace_id: str | None = None,
) -> None:
    assert len(emitted) == 1
    event_type, payload, source = emitted[0]
    assert event_type == "scenarios.synced"
    assert source == "yjs.bootstrap"
    assert payload["scenario_id"] == scenario_id
    assert payload["webspace_id"] == (webspace_id or default_webspace_id())
    assert payload["bootstrap_nudge"] is True


def test_bootstrap_propagates_apply_updates_cancellation(monkeypatch) -> None:
    class _CancelledStore(_FakeStore):
        async def apply_updates(self, ydoc: Y.YDoc) -> None:  # noqa: ARG002
            self.apply_updates_calls += 1
            raise asyncio.CancelledError()

    class _UnexpectedManager:
        def project_scenario_to_doc(self, *args, **kwargs) -> None:  # noqa: ARG002
            raise AssertionError("cancelled bootstrap must not project scenario data")

        async def sync_to_yjs_async(self, *args, **kwargs) -> None:  # noqa: ARG002
            raise AssertionError("cancelled bootstrap must not sync scenario data")

    store = _CancelledStore()
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _UnexpectedManager())

    try:
        asyncio.run(
            bootstrap_module.ensure_webspace_seeded_from_scenario(
                store,
                webspace_id="desktop",
                default_scenario_id="web_desktop",
            )
        )
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("CancelledError should propagate to the room bootstrap timeout")

    assert store.start_calls == 1
    assert store.apply_updates_calls == 1
    assert store.write_calls == 0
    assert store.encode_calls == 0


def test_bootstrap_reprojects_provided_doc_after_partial_apply_failure(monkeypatch) -> None:
    class _PanicAfterPartialApplyStore(_FakeStore):
        async def apply_updates(self, ydoc: Y.YDoc) -> None:
            self.apply_updates_calls += 1
            with ydoc.begin_transaction() as txn:
                ui_map = ydoc.get_map("ui")
                data_map = ydoc.get_map("data")
                ui_map.set(txn, "current_scenario", "todo_list")
                ui_map.set(txn, "application", {"modals": {"apps_catalog": {}, "widgets_catalog": {}}})
                data_map.set(txn, "catalog", {"apps": [], "widgets": []})
            raise RuntimeError("Couldn't get item's parent")

    class _ProjectingManager:
        def project_scenario_to_doc(self, ydoc: Y.YDoc, scenario_id: str, *, space: str = "workspace") -> None:
            with ydoc.begin_transaction() as txn:
                ui_map = ydoc.get_map("ui")
                data_map = ydoc.get_map("data")
                ui_map.set(txn, "current_scenario", scenario_id)
                ui_map.set(
                    txn,
                    "application",
                    {"desktop": {"pageSchema": {"id": "fresh-todo"}}, "modals": {"apps_catalog": {}, "widgets_catalog": {}}},
                )
                data_map.set(txn, "catalog", {"apps": [{"id": "fresh-app"}], "widgets": []})

    store = _PanicAfterPartialApplyStore()
    provided_doc = Y.YDoc()
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _ProjectingManager())
    monkeypatch.setattr(bootstrap_module, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(bootstrap_module, "emit", lambda *args, **kwargs: None)

    result = asyncio.run(
        bootstrap_module.ensure_webspace_seeded_from_scenario(
            store,
            webspace_id="desktop-dev",
            default_scenario_id="todo_list",
            space="dev",
            ydoc=provided_doc,
            prefer_default_scenario=True,
        )
    )

    assert result["mode"] == "scenario_projection"
    assert result["apply_updates_error"].startswith("RuntimeError:")
    assert result["apply_updates_discarded_partial_state"] is True
    assert store.write_calls == 1
    assert dict(provided_doc.get_map("ui").get("application") or {})["desktop"]["pageSchema"]["id"] == "fresh-todo"
    assert dict(provided_doc.get_map("data").get("catalog") or {})["apps"] == [{"id": "fresh-app"}]


def test_bootstrap_seed_fallback_projects_compat_seed_without_effective_writes(monkeypatch) -> None:
    class _FailingManager:
        async def sync_to_yjs_async(self, *args, **kwargs) -> None:  # noqa: ARG002
            raise FileNotFoundError("missing scenario payload")

    emitted: list[tuple[str, dict[str, object], str]] = []
    store = _FakeStore()

    bootstrap_module._BOOTSTRAP_REBUILD_NUDGE_LAST.clear()
    monkeypatch.setattr(bootstrap_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _FailingManager())
    monkeypatch.setattr(bootstrap_module, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(
        bootstrap_module,
        "emit",
        lambda bus, type_, payload, source: emitted.append((type_, dict(payload), source)),  # noqa: ARG005
    )

    asyncio.run(
        bootstrap_module.ensure_webspace_seeded_from_scenario(
            store,
            webspace_id=default_webspace_id(),
            default_scenario_id="web_desktop",
        )
    )

    assert store.start_calls == 1
    assert store.apply_updates_calls == 1
    assert store.write_calls == 2
    assert store.write_kinds == ["diff", "diff"]
    assert store.write_notify == [False, False]
    assert store.encode_calls == 0
    assert store.encoded_state is not None
    assert store.encoded_state["current_scenario"] == "web_desktop"
    assert store.encoded_state["ui_application"] is None
    assert store.encoded_state["data_catalog"] is None
    assert store.encoded_state["data_installed"] is None
    assert store.encoded_state["registry_merged"] is None
    runtime_bootstrap = dict(store.encoded_state["runtime_bootstrap"] or {})
    assert runtime_bootstrap["scenario_id"] == "web_desktop"
    assert runtime_bootstrap["state"] == "materializing"
    assert runtime_bootstrap["stage"] == "compatibility_fallback_projected"
    ui_scenarios = dict(store.encoded_state["ui_scenarios"] or {})
    data_scenarios = dict(store.encoded_state["data_scenarios"] or {})
    registry_scenarios = dict(store.encoded_state["registry_scenarios"] or {})
    assert ui_scenarios["node-1"]["web_desktop"]["application"]["desktop"]["pageSchema"]["id"] == "desktop"
    assert data_scenarios["node-1"]["web_desktop"]["catalog"]["apps"] == []
    assert registry_scenarios["node-1"]["web_desktop"] == {"widgets": [], "modals": []}
    _assert_single_rebuild_nudge(emitted, scenario_id="web_desktop")


def test_bootstrap_reuses_projected_seed_and_only_nudges_rebuild(monkeypatch) -> None:
    monkeypatch.setattr(bootstrap_module, "_local_node_id", lambda: "node-1")

    def _apply_state(ydoc: Y.YDoc) -> None:
        with ydoc.begin_transaction() as txn:
            ui_map = ydoc.get_map("ui")
            data_map = ydoc.get_map("data")
            registry_map = ydoc.get_map("registry")
            ui_map.set(txn, "current_scenario", "prompt_engineer_scenario")
            ui_map.set(
                txn,
                "scenarios",
                {
                    "node-1": {
                        "prompt_engineer_scenario": {
                            "application": {"desktop": {"pageSchema": {"id": "prompt-page"}}}
                        }
                    }
                },
            )
            data_map.set(
                txn,
                "scenarios",
                {
                    "node-1": {
                        "prompt_engineer_scenario": {"catalog": {"apps": [{"id": "prompt-app"}], "widgets": []}}
                    }
                },
            )
            registry_map.set(
                txn,
                "scenarios",
                {"node-1": {"prompt_engineer_scenario": {"modals": ["prompt-modal"], "widgets": []}}},
            )

    class _UnexpectedManager:
        async def sync_to_yjs_async(self, *args, **kwargs) -> None:  # noqa: ARG002
            raise AssertionError("should not project scenario again when projected seed already exists")

    emitted: list[tuple[str, dict[str, object], str]] = []
    store = _FakeStore(apply_state=_apply_state)

    bootstrap_module._BOOTSTRAP_REBUILD_NUDGE_LAST.clear()
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _UnexpectedManager())
    monkeypatch.setattr(bootstrap_module, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(
        bootstrap_module,
        "emit",
        lambda bus, type_, payload, source: emitted.append((type_, dict(payload), source)),  # noqa: ARG005
    )

    asyncio.run(
        bootstrap_module.ensure_webspace_seeded_from_scenario(
            store,
            webspace_id=default_webspace_id(),
            default_scenario_id="web_desktop",
        )
    )

    assert store.start_calls == 1
    assert store.apply_updates_calls == 1
    assert store.write_calls == 1
    assert store.encode_calls == 0
    assert store.encoded_state is not None
    runtime_bootstrap = dict(store.encoded_state["runtime_bootstrap"] or {})
    assert runtime_bootstrap["scenario_id"] == "prompt_engineer_scenario"
    assert runtime_bootstrap["state"] == "materializing"
    assert runtime_bootstrap["stage"] == "projected_seed_reuse"
    _assert_single_rebuild_nudge(emitted, scenario_id="prompt_engineer_scenario")


def test_bootstrap_prefers_current_pointer_when_projecting_missing_effective_ui(monkeypatch) -> None:
    def _apply_state(ydoc: Y.YDoc) -> None:
        with ydoc.begin_transaction() as txn:
            ydoc.get_map("ui").set(txn, "current_scenario", "prompt_engineer_scenario")

    captured: list[tuple[str, str, str, bool]] = []

    class _Manager:
        async def sync_to_yjs_async(
            self,
            scenario_id: str,
            webspace_id: str | None = None,
            *,
            space: str = "workspace",
            emit_event: bool = True,
        ) -> None:
            captured.append((scenario_id, str(webspace_id or ""), space, emit_event))

    store = _FakeStore(apply_state=_apply_state)
    monkeypatch.setattr(bootstrap_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _Manager())

    asyncio.run(
        bootstrap_module.ensure_webspace_seeded_from_scenario(
            store,
            webspace_id=default_webspace_id(),
            default_scenario_id="web_desktop",
            space="dev",
        )
    )

    assert store.start_calls == 1
    assert store.apply_updates_calls == 1
    assert store.write_calls == 1
    assert captured == [("prompt_engineer_scenario", default_webspace_id(), "dev", True)]


def test_bootstrap_can_prefer_manifest_home_over_stale_current_pointer(monkeypatch) -> None:
    def _apply_state(ydoc: Y.YDoc) -> None:
        with ydoc.begin_transaction() as txn:
            ui_map = ydoc.get_map("ui")
            data_map = ydoc.get_map("data")
            ui_map.set(txn, "current_scenario", "web_desktop")
            ui_map.set(
                txn,
                "application",
                {
                    "desktop": {"pageSchema": {"id": "desktop"}},
                    "modals": {
                        "apps_catalog": {"pageSchema": {"id": "apps_catalog"}},
                        "widgets_catalog": {"pageSchema": {"id": "widgets_catalog"}},
                    },
                },
            )
            data_map.set(txn, "catalog", {"apps": [], "widgets": []})

    captured: list[tuple[str, str, str, bool]] = []

    class _Manager:
        async def sync_to_yjs_async(
            self,
            scenario_id: str,
            webspace_id: str | None = None,
            *,
            space: str = "workspace",
            emit_event: bool = True,
        ) -> None:
            captured.append((scenario_id, str(webspace_id or ""), space, emit_event))

    store = _FakeStore(apply_state=_apply_state)
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _Manager())

    result = asyncio.run(
        bootstrap_module.ensure_webspace_seeded_from_scenario(
            store,
            webspace_id="desktop-dev",
            default_scenario_id="todo_list_5b9319fa",
            space="dev",
            prefer_default_scenario=True,
        )
    )

    assert result["scenario_id"] == "todo_list_5b9319fa"
    assert result["previous_scenario_id"] == "web_desktop"
    assert result["current_scenario_overridden"] is True
    assert captured == [("todo_list_5b9319fa", "desktop-dev", "dev", True)]


def test_bootstrap_projects_into_provided_ydoc_in_single_pass(monkeypatch) -> None:
    projected: list[tuple[str, str]] = []
    monkeypatch.setattr(bootstrap_module, "_local_node_id", lambda: "node-1")

    class _ProjectingManager:
        def project_scenario_to_doc(self, ydoc: Y.YDoc, scenario_id: str, *, space: str = "workspace") -> None:
            projected.append((scenario_id, space))
            with ydoc.begin_transaction() as txn:
                ui_map = ydoc.get_map("ui")
                data_map = ydoc.get_map("data")
                registry_map = ydoc.get_map("registry")
                ui_map.set(txn, "current_scenario", scenario_id)
                ui_map.set(
                    txn,
                    "scenarios",
                    {"node-1": {scenario_id: {"application": {"desktop": {"pageSchema": {"id": "prompt"}}}}}},
                )
                data_map.set(
                    txn,
                    "scenarios",
                    {"node-1": {scenario_id: {"catalog": {"apps": [{"id": "prompt"}], "widgets": []}}}},
                )
                registry_map.set(
                    txn,
                    "scenarios",
                    {"node-1": {scenario_id: {"widgets": [], "modals": ["prompt-modal"]}}},
                )

    emitted: list[tuple[str, dict[str, object], str]] = []
    store = _FakeStore()
    provided_doc = Y.YDoc()

    bootstrap_module._BOOTSTRAP_REBUILD_NUDGE_LAST.clear()
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _ProjectingManager())
    monkeypatch.setattr(bootstrap_module, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(
        bootstrap_module,
        "emit",
        lambda bus, type_, payload, source: emitted.append((type_, dict(payload), source)),  # noqa: ARG005
    )

    result = asyncio.run(
        bootstrap_module.ensure_webspace_seeded_from_scenario(
            store,
            webspace_id=default_webspace_id(),
            default_scenario_id="prompt_engineer_scenario",
            space="dev",
            ydoc=provided_doc,
        )
    )

    assert projected == [("prompt_engineer_scenario", "dev")]
    assert store.start_calls == 1
    assert store.apply_updates_calls == 1
    assert store.write_calls == 1
    assert store.encode_calls == 0
    assert result["used_provided_ydoc"] is True
    assert result["mode"] == "scenario_projection"
    assert result["persisted_via"] == "diff"
    assert provided_doc.get_map("ui").get("current_scenario") == "prompt_engineer_scenario"
    runtime_bootstrap = dict(provided_doc.get_map("runtime").get("bootstrap") or {})
    assert runtime_bootstrap["scenario_id"] == "prompt_engineer_scenario"
    assert runtime_bootstrap["state"] == "materializing"
    assert runtime_bootstrap["stage"] == "scenario_projected"
    assert runtime_bootstrap["ready"] is False
    _assert_single_rebuild_nudge(emitted, scenario_id="prompt_engineer_scenario")


def test_bootstrap_seed_fallback_uses_snapshot_when_incremental_write_fails(monkeypatch) -> None:
    class _FailingManager:
        async def sync_to_yjs_async(self, *args, **kwargs) -> None:  # noqa: ARG002
            raise FileNotFoundError("missing scenario payload")

    emitted: list[tuple[str, dict[str, object], str]] = []
    store = _FakeStore(incremental_write_ok=False)

    bootstrap_module._BOOTSTRAP_REBUILD_NUDGE_LAST.clear()
    monkeypatch.setattr(bootstrap_module, "_local_node_id", lambda: "node-1")
    monkeypatch.setattr(bootstrap_module, "_scenario_manager", lambda: _FailingManager())
    monkeypatch.setattr(bootstrap_module, "get_ctx", lambda: SimpleNamespace(bus=object()))
    monkeypatch.setattr(
        bootstrap_module,
        "emit",
        lambda bus, type_, payload, source: emitted.append((type_, dict(payload), source)),  # noqa: ARG005
    )

    asyncio.run(
        bootstrap_module.ensure_webspace_seeded_from_scenario(
            store,
            webspace_id=default_webspace_id(),
            default_scenario_id="web_desktop",
        )
    )

    assert store.write_calls == 2
    assert store.encode_calls == 2
    assert store.encoded_state is not None
    assert store.encoded_state["current_scenario"] == "web_desktop"
    _assert_single_rebuild_nudge(emitted, scenario_id="web_desktop")


def test_bootstrap_seed_if_empty_uses_configured_default_webspace(monkeypatch) -> None:
    captured: list[tuple[object, str]] = []
    fake_store = object()

    async def _fake_ensure(store, *, webspace_id: str, **kwargs) -> None:  # noqa: ANN001
        captured.append((store, webspace_id))

    monkeypatch.setattr(bootstrap_module, "default_webspace_id", lambda: "desktop-main")
    monkeypatch.setattr(bootstrap_module, "get_ystore_for_webspace", lambda webspace_id: (fake_store, webspace_id)[0] if webspace_id == "desktop-main" else None)
    monkeypatch.setattr(bootstrap_module, "ensure_webspace_seeded_from_scenario", _fake_ensure)

    asyncio.run(bootstrap_module.bootstrap_seed_if_empty(fake_store))  # type: ignore[arg-type]

    assert captured == [(fake_store, "desktop-main")]
