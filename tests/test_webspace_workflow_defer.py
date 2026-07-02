from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

from adaos.services.scenario import webspace_runtime as webspace_runtime_module


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
