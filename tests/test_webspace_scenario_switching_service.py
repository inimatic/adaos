from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from adaos.services.scenario.webspace_components import (
    ScenarioSwitchOperations,
    WebspaceScenarioSwitchingService,
    WebspaceTaskState,
)


@pytest.mark.asyncio
async def test_async_ws_http_retries_share_cold_admission_without_cancelling_it(monkeypatch):
    service = WebspaceScenarioSwitchingService()
    entered, release = asyncio.Event(), asyncio.Event()
    calls = []

    async def admit(*_args, **kwargs):
        calls.append(kwargs["request_id"])
        entered.set()
        await release.wait()
        return {"accepted": True}

    monkeypatch.setattr(service, "_switch_impl", admit)
    options = dict(wait_for_rebuild=False, request_id="nav:shared-receipt")
    primary = asyncio.create_task(service.switch(None, "isolated-shared", "chat", **options))
    await entered.wait()
    retry = asyncio.create_task(service.switch(None, "isolated-shared", "chat", **options))
    await asyncio.sleep(0)
    assert calls == ["nav:shared-receipt"]
    with pytest.raises(ValueError, match="identity_conflict"):
        await service.switch(None, "isolated-shared", "other", **options)
    primary.cancel()
    with pytest.raises(asyncio.CancelledError):
        await primary
    release.set()
    assert (await retry)["accepted"] is True
    await asyncio.sleep(0)
    assert service._admissions == {}


def test_same_navigation_retry_joins_pending_atomic_projection_before_selector_changes():
    service = WebspaceScenarioSwitchingService()
    state = {"pending": True, "scenario_id": "chat", "request_id": "nav:one"}
    assert service.decide(current_scenario="builder", target_scenario="chat", rebuild_state=state,
        materialization_matches_target=False, request_id="nav:one").action == "join"
    assert service.decide(current_scenario="builder", target_scenario="chat", rebuild_state=state,
        materialization_matches_target=False, request_id="nav:two").action == "switch"


@pytest.mark.asyncio
async def test_synchronous_navigation_keeps_request_identity_and_rejects_slow_older_admission():
    service = WebspaceScenarioSwitchingService()
    entered, release = asyncio.Event(), asyncio.Event()
    describes = 0
    rebuilt = []
    row = SimpleNamespace(effective_kind="workspace", effective_source_mode="workspace", effective_home_scenario="home")

    async def describe(_webspace):
        nonlocal describes
        describes += 1
        if describes == 1:
            entered.set()
            await release.wait()
        return SimpleNamespace(current_scenario="home", effective_home_scenario="home")

    async def rebuild(_webspace, **kwargs):
        rebuilt.append(kwargs)
        return {"accepted": True}

    operations = SimpleNamespace(
        task_state=WebspaceTaskState(), log=Mock(),
        workspace_index=SimpleNamespace(get_workspace=lambda _: row,
            set_workspace_current_scenario_overlay=lambda *args, **kwargs: None),
        describe_operational_state=describe, describe_rebuild_state=lambda _: {},
        record_timing=lambda *args: None, scenario_switch_mode=lambda: "pointer_only",
        scenario_exists_for_switch=lambda *args, **kwargs: True,
        finalize_timing_map=lambda timings, **kwargs: dict(timings),
        copy_timing_map=lambda value: dict(value or {}), derive_phase_timings=lambda **kwargs: {},
        complete_scenario_switch_rebuild=rebuild,
    )
    old = asyncio.create_task(service.switch(operations, "isolated-atomic-admission", "old", request_id="nav:old"))
    await entered.wait()
    result = await service.switch(operations, "isolated-atomic-admission", "new", request_id="nav:new")
    release.set()
    with pytest.raises(RuntimeError, match="navigation_superseded"):
        await old
    assert result["accepted"] is True
    assert [item["request_id"] for item in rebuilt] == ["nav:new"]


def test_scenario_switching_service_normalizes_request() -> None:
    request = WebspaceScenarioSwitchingService().normalize_request(
        " ws-1 ",
        " demo ",
        set_home=None,
        wait_for_rebuild=False,
        request_id=" request-1 ",
        request_source=" ",
        request_client=None,
    )

    assert request.webspace_id == "ws-1"
    assert request.scenario_id == "demo"
    assert request.set_home is False
    assert request.wait_for_rebuild is False
    assert request.request_id == "request-1"
    assert request.request_source is None


@pytest.mark.parametrize("field", ["webspace", "scenario"])
def test_scenario_switching_service_requires_identifiers(field: str) -> None:
    webspace_id = "" if field == "webspace" else "ws-1"
    scenario_id = "" if field == "scenario" else "demo"

    with pytest.raises(ValueError):
        WebspaceScenarioSwitchingService().normalize_request(
            webspace_id,
            scenario_id,
            set_home=False,
            wait_for_rebuild=True,
            request_id=None,
            request_source=None,
            request_client=None,
        )


def test_scenario_switching_service_deduplicates_ready_target() -> None:
    decision = WebspaceScenarioSwitchingService().decide(
        current_scenario="demo",
        target_scenario="demo",
        rebuild_state={"pending": False, "status": "ready", "scenario_id": "demo"},
        materialization_matches_target=True,
    )

    assert decision.action == "skip"
    assert decision.reason == "already_current_ready"


def test_scenario_switching_service_trusts_persisted_materialization_after_restart() -> None:
    decision = WebspaceScenarioSwitchingService().decide(
        current_scenario="demo",
        target_scenario="demo",
        rebuild_state={"pending": False, "status": "idle"},
        materialization_matches_target=True,
    )

    assert decision.action == "skip"
    assert decision.reason == "already_current_ready"


def test_scenario_switching_service_joins_pending_target() -> None:
    decision = WebspaceScenarioSwitchingService().decide(
        current_scenario="demo",
        target_scenario="demo",
        rebuild_state={"pending": True, "status": "building", "scenario_id": "demo"},
        materialization_matches_target=False,
    )

    assert decision.action == "join"
    assert decision.reason == "already_pending_rebuild"


def test_scenario_switching_service_rebuilds_on_materialization_mismatch() -> None:
    decision = WebspaceScenarioSwitchingService().decide(
        current_scenario="demo",
        target_scenario="demo",
        rebuild_state={"pending": False, "status": "ready", "scenario_id": "demo"},
        materialization_matches_target=False,
    )

    assert decision.action == "switch"


@pytest.mark.asyncio
async def test_scenario_switching_service_owns_task_replacement_and_cleanup() -> None:
    service = WebspaceScenarioSwitchingService()
    state = WebspaceTaskState()
    blocker = asyncio.Event()
    cancelled: list[str] = []

    first = service.schedule_rebuild(
        task_state=state,
        webspace_id="desktop",
        scenario_id="one",
        operation=blocker.wait,
        on_cancel=lambda: cancelled.append("one"),
    )
    await asyncio.sleep(0)

    async def complete() -> None:
        return None

    second = service.schedule_rebuild(
        task_state=state,
        webspace_id="desktop",
        scenario_id="two",
        operation=complete,
    )
    with pytest.raises(asyncio.CancelledError):
        await first
    await second

    assert cancelled == ["one"]
    assert state.task_count(state.SCENARIO_SWITCH) == 0


@pytest.mark.asyncio
async def test_scenario_switching_service_bounds_background_rebuild() -> None:
    service = WebspaceScenarioSwitchingService()
    state = WebspaceTaskState()
    errors: list[Exception] = []

    async def blocked() -> None:
        await asyncio.Event().wait()

    task = service.schedule_rebuild(
        task_state=state,
        webspace_id="desktop",
        scenario_id="blocked",
        operation=blocked,
        timeout_s=0.01,
        on_error=errors.append,
    )
    await task

    assert len(errors) == 1
    assert isinstance(errors[0], asyncio.TimeoutError)
    assert state.task_count(state.SCENARIO_SWITCH) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("unavailable", [False, True])
async def test_scenario_switching_service_offloads_blocking_source_reads(unavailable) -> None:
    main_thread_id = threading.get_ident()
    workspace_thread_ids: list[int] = []
    scenario_thread_ids: list[int] = []
    row = SimpleNamespace(
        effective_kind="development",
        effective_source_mode="dev",
        effective_home_scenario="builder",
    )

    class WorkspaceIndex:
        @staticmethod
        def get_workspace(_webspace_id: str):
            workspace_thread_ids.append(threading.get_ident())
            return row

        @staticmethod
        def ensure_workspace(_webspace_id: str):
            raise AssertionError("existing workspace should be reused")

    async def describe_state(_webspace_id: str):
        return SimpleNamespace(
            current_scenario="builder",
            effective_home_scenario="builder",
        )

    def scenario_exists(_scenario_id: str, *, space: str) -> bool:
        assert space == "dev"
        scenario_thread_ids.append(threading.get_ident())
        if unavailable:
            from adaos.services.applications.trial_runtime import TrialRuntimeUnavailable
            raise TrialRuntimeUnavailable("Trial has expired")
        return False

    operations = ScenarioSwitchOperations(
        task_state=WebspaceTaskState(),
        log=SimpleNamespace(info=lambda *_args, **_kwargs: None, warning=lambda *_args, **_kwargs: None),
        workspace_index=WorkspaceIndex(),
        describe_operational_state=describe_state,
        describe_rebuild_state=lambda _webspace_id: {"pending": False, "status": "idle"},
        record_timing=lambda *_args, **_kwargs: None,
        materialization_scenario_from_rebuild_state=lambda _state: None,
        read_effective_materialization_scenario=lambda _webspace_id: asyncio.sleep(0, result=None),
        scenario_switch_mode=lambda: "pointer_only",
        copy_timing_map=lambda _value: None,
        derive_phase_timings=lambda **_kwargs: None,
        finalize_timing_map=lambda timings, **_kwargs: dict(timings),
        scenario_exists_for_switch=scenario_exists,
        set_rebuild_status=lambda _webspace_id, **fields: fields,
        set_rebuild_status_if_current=lambda _webspace_id, _request_id, **fields: fields,
        sync_webspace_listing_target=lambda _webspace_id: asyncio.sleep(0),
        schedule_scenario_switch_rebuild=lambda *_args, **_kwargs: None,
        complete_scenario_switch_rebuild=lambda *_args, **_kwargs: asyncio.sleep(0, result={}),
        set_map_value_if_changed=lambda *_args, **_kwargs: None,
        write_meta=lambda *_args, **_kwargs: None,
        async_get_ydoc=lambda *_args, **_kwargs: None,
        mutate_live_room=lambda *_args, **_kwargs: False,
    )

    result = await WebspaceScenarioSwitchingService().switch(
        operations,
        "builder-dev",
        "missing-scenario",
        wait_for_rebuild=False,
    )

    assert result["accepted"] is False
    assert result["error"] == ("scenario_runtime_unavailable" if unavailable else "scenario_not_found")
    if unavailable:
        assert result["error_detail"] == "TrialRuntimeUnavailable: Trial has expired"
    assert workspace_thread_ids and all(thread_id != main_thread_id for thread_id in workspace_thread_ids)
    assert scenario_thread_ids and all(thread_id != main_thread_id for thread_id in scenario_thread_ids)
