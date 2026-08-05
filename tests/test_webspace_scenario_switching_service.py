from __future__ import annotations

import asyncio

import pytest

from adaos.services.scenario.webspace_components import (
    WebspaceScenarioSwitchingService,
    WebspaceTaskState,
)


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
