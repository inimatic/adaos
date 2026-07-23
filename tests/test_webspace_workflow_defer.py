from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import sys
import types
from types import SimpleNamespace

from adaos.services.scenario import webspace_runtime as webspace_runtime_module


def test_payload_only_materialize_updates_ready_materialization_from_resolved(monkeypatch) -> None:
    webspace_id = "payload-only-ready-materialization"
    request_id = "req-payload-only-ready"
    runtime = webspace_runtime_module.WebspaceScenarioRuntime(webspace_runtime_module.get_ctx())

    @asynccontextmanager
    async def _fake_open_readonly(_webspace_id: str):
        yield object()

    def _fake_collect(_ydoc, _webspace_id: str, **_kwargs):
        return webspace_runtime_module.WebspaceResolverInputs(
            webspace_id=webspace_id,
            scenario_id="prompt_engineer_scenario",
            source_mode="dev",
            metadata={
                "materialization": {
                    "required_branches": [
                        "ui.application",
                        "data.catalog",
                        "data.installed",
                        "data.desktop",
                        "data.webio",
                        "data.routing",
                        "registry.merged",
                    ]
                }
            },
            compatibility_cache_presence={
                "scenario_ui_application": True,
                "scenario_registry_entry": True,
                "scenario_catalog": True,
            },
        )

    def _fake_resolve(_inputs):
        return webspace_runtime_module.WebspaceResolverOutputs(
            webspace_id=webspace_id,
            scenario_id="prompt_engineer_scenario",
            source_mode="dev",
            application={
                "desktop": {"pageSchema": {"id": "prompt-page", "widgets": []}},
                "modals": {"apps_catalog": {}, "widgets_catalog": {}},
            },
            catalog={"apps": [], "widgets": []},
            registry={"modals": [], "widgets": []},
            installed={"apps": [], "widgets": []},
            desktop={"installed": {"apps": [], "widgets": []}},
            webio={},
            routing={"routes": {}},
            skill_decls=[],
        )

    monkeypatch.setattr(webspace_runtime_module, "_open_readonly_operational_ydoc", _fake_open_readonly)
    monkeypatch.setattr(runtime, "_collect_resolver_inputs_in_doc", _fake_collect)
    monkeypatch.setattr(runtime, "resolve_webspace", _fake_resolve)
    webspace_runtime_module._set_webspace_rebuild_status(
        webspace_id,
        status="running",
        pending=True,
        request_id=request_id,
        scenario_id="prompt_engineer_scenario",
    )
    try:
        entry = asyncio.run(
            runtime.materialize_webspace_payload_async(
                webspace_id,
                request_id=request_id,
                scenario_id="prompt_engineer_scenario",
            )
        )
        state = webspace_runtime_module.describe_webspace_rebuild_state(webspace_id)
    finally:
        webspace_runtime_module._WEBSPACE_REBUILD_STATUS.pop(webspace_id, None)

    assert entry.scenario_id == "prompt_engineer_scenario"
    assert state["materialization"]["ready"] is True
    assert state["materialization"]["readiness_state"] == "ready"
    assert state["materialization"]["missing_required_branches"] == []
    assert state["materialization"]["snapshot_source"] == "semantic_rebuild:payload_only"


def test_scenario_switch_rebuild_skips_workflow_sync_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_WORKFLOW_SYNC", raising=False)
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC", raising=False)
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "0")
    workflow_calls: list[tuple[str, str]] = []
    rebuild_kwargs: dict[str, object] = {}

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG001
        rebuild_kwargs.update(kwargs)
        self._last_rebuild_timings_ms = {"collect_inputs": 1.0, "resolve": 1.0, "apply": 1.0, "total": 3.0}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        self._last_rebuild_ydoc_timings_ms = {"total": 3.0}
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):
        workflow_calls.append((scenario_id, webspace_id))

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(
            reset_ystore_for_webspace=lambda _webspace_id: (_ for _ in ()).throw(
                AssertionError("ordinary scenario switch must preserve YStore")
            )
        ),
    )
    webspace_runtime_module._WORKFLOW_SYNC_TASKS.clear()
    webspace_runtime_module._WORKFLOW_SYNC_PENDING.clear()

    try:
        result = asyncio.run(
            webspace_runtime_module.rebuild_webspace_from_sources(
                "phase2-workflow-default-disabled",
                action="scenario_switch_rebuild",
                scenario_id="prompt_engineer_scenario",
                scenario_resolution="explicit",
                source_of_truth="scenario_switch",
                reseed_from_scenario=False,
            )
        )
    finally:
        webspace_runtime_module._WORKFLOW_SYNC_TASKS.clear()
        webspace_runtime_module._WORKFLOW_SYNC_PENDING.clear()

    assert result["accepted"] is True
    assert result["workflow_sync"]["skipped"] is True
    assert result["workflow_sync"]["reason"] == "workflow_sync_disabled_for_scenario_switch"
    assert result["timings_ms"]["workflow_sync_skipped"] == 0.0
    assert "workflow_sync" not in result["timings_ms"]
    assert "fresh_doc" not in rebuild_kwargs
    assert workflow_calls == []


def test_scenario_switch_rebuild_uses_payload_only_when_live_refresh_inline(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_WORKFLOW_SYNC", raising=False)
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC", raising=False)
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_PAYLOAD_ONLY_REBUILD", raising=False)
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_LIVE_ROOM_REFRESH", "0")
    materialize_calls: list[tuple[str, dict[str, object]]] = []
    rebuild_calls: list[tuple[str, dict[str, object]]] = []
    refresh_calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_refresh_projection(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_materialize(self, webspace_id: str, **kwargs):
        materialize_calls.append((webspace_id, dict(kwargs)))
        self._last_rebuild_timings_ms = {
            "collect_inputs": 1.0,
            "resolve": 2.0,
            "build_materialized_payload": 0.5,
            "to_registry_entry": 0.1,
            "total": 3.6,
        }
        self._last_rebuild_ydoc_timings_ms = {"payload_only": 0.0, "total": 3.6}
        self._last_resolver_debug = {"source": "loader:dev", "cache_hit": True}
        self._last_apply_summary = {"payload_only": True}
        self._last_materialized_payload = {
            "schema": "adaos.webspace.materialized_payload.v1",
            "scenario_id": "prompt_engineer_scenario",
            "application": {"desktop": {"pageSchema": {"id": "page"}}},
            "catalog": {"apps": [], "widgets": []},
            "registry": {"modals": [], "widgets": []},
            "installed": {"apps": [], "widgets": []},
            "desktop": {"installed": {"apps": [], "widgets": []}},
            "webio": {},
            "routing": {"routes": {}},
        }
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _unexpected_rebuild(self, webspace_id: str, **kwargs):
        rebuild_calls.append((webspace_id, dict(kwargs)))
        raise AssertionError("scenario switch should use payload-only materialization")

    async def _fake_live_refresh(webspace_id: str, **kwargs):
        refresh_calls.append((webspace_id, dict(kwargs)))
        return {
            "ok": True,
            "materialized_payload": {
                "apply_summary": {
                    "branch_count": 8,
                    "changed_branches": 8,
                    "unchanged_branches": 0,
                    "diff_applied_branches": 8,
                }
            },
        }

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh_projection)
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "materialize_webspace_payload_async",
        _fake_materialize,
    )
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _unexpected_rebuild)
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(refresh_live_webspace_effective_branches=_fake_live_refresh),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(reset_ystore_for_webspace=lambda _webspace_id: None),
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "phase2-payload-only-switch",
            action="scenario_switch_rebuild",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="scenario_switch",
            reseed_from_scenario=False,
        )
    )

    assert result["accepted"] is True
    assert result["payload_only_rebuild"] is True
    assert isinstance(result["materialization_identity"], dict)
    assert result["materialization_identity"]["scenario_id"] == "prompt_engineer_scenario"
    assert result["materialization_identity"]["key_hash"]
    assert materialize_calls == [
        (
            "phase2-payload-only-switch",
            {
                "scenario_id": "prompt_engineer_scenario",
                "materialization_identity": result["materialization_identity"],
                "isolate_process": False,
            },
        )
    ]
    assert rebuild_calls == []
    assert refresh_calls
    assert refresh_calls[-1][1]["materialized_payload"]["scenario_id"] == "prompt_engineer_scenario"
    assert refresh_calls[-1][1]["persist_repair"] is True
    assert refresh_calls[-1][1]["force_full_state_update"] is False
    assert result["apply_summary"]["changed_branches"] == 8
    assert result["apply_summary"]["diff_applied_branches"] == 8
    assert result["force_full_state_update"] is False


def test_scenario_switch_rebuild_can_skip_live_room_refresh_for_read_model(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_WORKFLOW_SYNC", raising=False)
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC", raising=False)
    monkeypatch.delenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_PAYLOAD_ONLY_REBUILD", raising=False)
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_LIVE_ROOM_REFRESH", "0")
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_SKIP_LIVE_ROOM_REFRESH", "1")
    materialize_calls: list[tuple[str, dict[str, object]]] = []
    refresh_calls: list[tuple[str, dict[str, object]]] = []

    async def _fake_refresh_projection(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_materialize(self, webspace_id: str, **kwargs):
        materialize_calls.append((webspace_id, dict(kwargs)))
        self._last_rebuild_timings_ms = {
            "collect_inputs": 1.0,
            "resolve": 2.0,
            "build_materialized_payload": 0.5,
            "to_registry_entry": 0.1,
            "total": 3.6,
        }
        self._last_rebuild_ydoc_timings_ms = {"payload_only": 0.0, "total": 3.6}
        self._last_resolver_debug = {"source": "loader:dev", "cache_hit": True}
        self._last_apply_summary = {"payload_only": True}
        self._last_materialized_payload = {
            "schema": "adaos.webspace.materialized_payload.v1",
            "webspace_id": webspace_id,
            "scenario_id": "prompt_engineer_scenario",
            "application": {"desktop": {"pageSchema": {"id": "page"}}, "modals": {"apps_catalog": {}, "widgets_catalog": {}}},
            "catalog": {"apps": [], "widgets": []},
            "registry": {"modals": [], "widgets": []},
            "installed": {"apps": [], "widgets": []},
            "desktop": {"installed": {"apps": [], "widgets": []}},
            "webio": {},
            "routing": {"routes": {}},
        }
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _unexpected_live_refresh(webspace_id: str, **kwargs):
        refresh_calls.append((webspace_id, dict(kwargs)))
        raise AssertionError("scenario switch read-model mode must not refresh live room")

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh_projection)
    monkeypatch.setattr(
        webspace_runtime_module.WebspaceScenarioRuntime,
        "materialize_webspace_payload_async",
        _fake_materialize,
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(refresh_live_webspace_effective_branches=_unexpected_live_refresh),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(reset_ystore_for_webspace=lambda _webspace_id: None),
    )

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "phase2-read-model-switch",
            action="scenario_switch_rebuild",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="scenario_switch",
            reseed_from_scenario=False,
        )
    )

    assert result["accepted"] is True
    assert result["payload_only_rebuild"] is True
    assert result["live_room_refresh"]["skipped"] is True
    assert result["live_room_refresh"]["reason"] == "scenario_switch_read_model_only"
    assert result["live_room_refresh"]["materialized_payload_available"] is True
    assert result["timings_ms"]["live_room_refresh_skipped"] == 0.0
    assert "live_room_refresh" not in result["timings_ms"]
    assert materialize_calls
    assert refresh_calls == []
    payload = webspace_runtime_module.get_webspace_rebuild_materialized_payload("phase2-read-model-switch")
    assert payload is not None
    assert payload["scenario_id"] == "prompt_engineer_scenario"


def test_scenario_switch_rebuild_can_defer_workflow_sync(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_WORKFLOW_SYNC_DEBOUNCE_S", "0")
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "0")
    workflow_calls: list[tuple[str, str]] = []

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG001
        self._last_rebuild_timings_ms = {"collect_inputs": 1.0, "resolve": 1.0, "apply": 1.0, "total": 3.0}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        self._last_rebuild_ydoc_timings_ms = {"total": 3.0}
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):
        workflow_calls.append((scenario_id, webspace_id))

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(reset_ystore_for_webspace=lambda _webspace_id: None),
    )
    webspace_runtime_module._WORKFLOW_SYNC_TASKS.clear()
    webspace_runtime_module._WORKFLOW_SYNC_PENDING.clear()

    async def _run() -> dict[str, object]:
        result = await webspace_runtime_module.rebuild_webspace_from_sources(
            "phase2-deferred-workflow",
            action="scenario_switch_rebuild",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="scenario_switch",
            reseed_from_scenario=False,
        )
        task = webspace_runtime_module._WORKFLOW_SYNC_TASKS.get("phase2-deferred-workflow")
        assert task is not None
        await task
        return result

    try:
        result = asyncio.run(_run())
    finally:
        webspace_runtime_module._WORKFLOW_SYNC_TASKS.clear()
        webspace_runtime_module._WORKFLOW_SYNC_PENDING.clear()

    assert result["accepted"] is True
    assert result["workflow_sync"]["deferred"] is True
    assert result["timings_ms"]["workflow_sync_deferred"] == 0.0
    assert "workflow_sync" not in result["timings_ms"]
    assert workflow_calls == [("prompt_engineer_scenario", "phase2-deferred-workflow")]


def test_scenario_switch_rebuild_can_defer_live_room_refresh(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_LIVE_ROOM_REFRESH", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_LIVE_ROOM_REFRESH_DEBOUNCE_S", "0")
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "1")
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_WORKFLOW_SYNC", "0")
    refresh_calls: list[tuple[str, str]] = []

    async def _fake_refresh(
        ctx,  # noqa: ARG001
        webspace_id: str,  # noqa: ARG001
        *,
        scenario_id: str | None = None,
        scenario_resolution: str | None = None,
    ) -> dict[str, object]:
        return {
            "attempted": True,
            "scenario_id": scenario_id,
            "scenario_resolution": scenario_resolution,
        }

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG001
        self._last_rebuild_timings_ms = {"collect_inputs": 1.0, "resolve": 1.0, "apply": 1.0, "total": 3.0}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        self._last_rebuild_ydoc_timings_ms = {"total": 3.0}
        return SimpleNamespace(scenario_id="prompt_engineer_scenario", apps=[], widgets=[])

    async def _fake_workflow_sync(self, scenario_id: str, webspace_id: str):  # noqa: ARG001
        return None

    async def _fake_live_refresh(webspace_id: str, *, reason: str):
        refresh_calls.append((webspace_id, reason))
        return {"ok": True}

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _fake_refresh)
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)
    monkeypatch.setattr(webspace_runtime_module.ScenarioWorkflowRuntime, "sync_workflow_for_webspace", _fake_workflow_sync)
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.gateway",
        types.SimpleNamespace(refresh_live_webspace_effective_branches=_fake_live_refresh),
    )
    monkeypatch.setitem(
        sys.modules,
        "adaos.services.yjs.store",
        types.SimpleNamespace(reset_ystore_for_webspace=lambda _webspace_id: None),
    )
    webspace_runtime_module._LIVE_ROOM_REFRESH_TASKS.clear()
    webspace_runtime_module._LIVE_ROOM_REFRESH_PENDING.clear()

    async def _run() -> dict[str, object]:
        result = await webspace_runtime_module.rebuild_webspace_from_sources(
            "phase2-deferred-live-room",
            action="scenario_switch_rebuild",
            scenario_id="prompt_engineer_scenario",
            scenario_resolution="explicit",
            source_of_truth="scenario_switch",
            reseed_from_scenario=False,
        )
        task = webspace_runtime_module._LIVE_ROOM_REFRESH_TASKS.get("phase2-deferred-live-room")
        assert task is not None
        await task
        return result

    try:
        result = asyncio.run(_run())
    finally:
        webspace_runtime_module._LIVE_ROOM_REFRESH_TASKS.clear()
        webspace_runtime_module._LIVE_ROOM_REFRESH_PENDING.clear()

    assert result["accepted"] is True
    assert result["live_room_refresh"]["deferred"] is True
    assert result["timings_ms"]["live_room_refresh_deferred"] == 0.0
    assert "live_room_refresh" not in result["timings_ms"]
    assert refresh_calls == [("phase2-deferred-live-room", "semantic_rebuild:scenario_switch_rebuild")]


def test_builder_revision_apply_does_not_use_scenario_switch_live_room_defer_flag(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_SCENARIO_SWITCH_DEFER_LIVE_ROOM_REFRESH", "1")
    monkeypatch.delenv("ADAOS_BUILDER_REVISION_DEFER_LIVE_ROOM_REFRESH", raising=False)

    assert webspace_runtime_module._defer_live_room_refresh_for_rebuild("scenario_switch_rebuild") is True
    assert webspace_runtime_module._defer_live_room_refresh_for_rebuild("builder_revision_apply") is False

    monkeypatch.setenv("ADAOS_BUILDER_REVISION_DEFER_LIVE_ROOM_REFRESH", "1")
    assert webspace_runtime_module._defer_live_room_refresh_for_rebuild("builder_revision_apply") is True


def test_builder_revision_apply_publishes_live_room_by_default(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_LIVE_ROOM_UPDATES", "0")
    monkeypatch.delenv("ADAOS_BUILDER_REVISION_LIVE_ROOM_UPDATES", raising=False)

    assert webspace_runtime_module._publish_live_room_for_rebuild("reload") is False
    assert webspace_runtime_module._publish_live_room_for_rebuild("builder_revision_apply") is True

    monkeypatch.setenv("ADAOS_BUILDER_REVISION_LIVE_ROOM_UPDATES", "0")
    assert webspace_runtime_module._publish_live_room_for_rebuild("builder_revision_apply") is False


def test_builder_revision_apply_skips_projection_refresh_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_BUILDER_REVISION_REFRESH_PROJECTION_RULES", raising=False)
    monkeypatch.setenv("ADAOS_WEBSPACE_REBUILD_REFRESH_LIVE_ROOM", "0")
    rebuild_kwargs: dict[str, object] = {}

    async def _unexpected_refresh(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("builder revision apply should not refresh projection rules by default")

    async def _fake_rebuild(self, webspace_id: str, **kwargs):  # noqa: ARG001
        rebuild_kwargs.update(kwargs)
        self._last_rebuild_timings_ms = {"collect_inputs": 1.0, "resolve": 1.0, "apply": 1.0, "total": 3.0}
        self._last_apply_summary = {"changed_branches": 1, "unchanged_branches": 0}
        self._last_rebuild_ydoc_timings_ms = {"total": 3.0}
        return SimpleNamespace(scenario_id="todo_list_5b9319fa", apps=[], widgets=[])

    monkeypatch.setattr(webspace_runtime_module, "_refresh_projection_rules_for_rebuild", _unexpected_refresh)
    monkeypatch.setattr(webspace_runtime_module, "_resolve_projection_refresh_space", lambda _webspace_id: "dev")
    monkeypatch.setattr(webspace_runtime_module.WebspaceScenarioRuntime, "rebuild_webspace_async", _fake_rebuild)

    result = asyncio.run(
        webspace_runtime_module.rebuild_webspace_from_sources(
            "desktop-dev",
            action="builder_revision_apply",
            scenario_id="todo_list_5b9319fa",
            scenario_resolution="explicit",
            source_of_truth="builder_revision",
            reseed_from_scenario=False,
        )
    )

    assert result["accepted"] is True
    assert result["projection_refresh"]["attempted"] is False
    assert result["projection_refresh"]["source"] == "skipped"
    assert result["projection_refresh"]["reason"] == "builder_revision_apply_reuses_existing_projection_rules"
    assert "projection_refresh_skipped" in result["timings_ms"]
    assert result["live_room_update_requested"] is True
    assert result["live_room_publish"] is False
    assert rebuild_kwargs["publish_live_room"] is False
    assert rebuild_kwargs["prefer_live_room"] is False
    assert rebuild_kwargs["fresh_doc"] is True
    assert rebuild_kwargs["replace_ystore_snapshot"] is True
