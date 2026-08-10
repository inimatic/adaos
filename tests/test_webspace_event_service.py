from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest

from adaos.services.scenario.webspace_components import WebspaceEventOperations, WebspaceEventService


def _operations() -> WebspaceEventOperations:
    return WebspaceEventOperations(
        default_webspace_id=lambda: "desktop",
        rebuild_webspace=AsyncMock(),
        schedule_skill_runtime_rebuild=Mock(),
        reload_publication_webspaces=AsyncMock(),
    )


def test_skill_event_defer_is_owned_by_event_service() -> None:
    operations = _operations()

    WebspaceEventService().skill_changed(
        {"defer_webspace_rebuild": True},
        operations,
        action="skill_update_sync",
        topic="skills.updated",
        allow_defer=True,
    )

    operations.schedule_skill_runtime_rebuild.assert_not_called()


@pytest.mark.asyncio
async def test_scenario_sync_maps_to_semantic_rebuild_contract() -> None:
    operations = _operations()

    await WebspaceEventService().scenarios_synced(
        {"webspace_id": "research", "scenario_id": "tlp_research"},
        operations,
    )

    operations.rebuild_webspace.assert_awaited_once_with(
        "research",
        action="scenario_projection_sync",
        scenario_id="tlp_research",
        scenario_resolution="projected_payload",
        source_of_truth="scenario_projection",
    )
