from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adaos.services.scenario.webspace_components import WebspaceProjectionService


def test_projection_service_describes_active_target() -> None:
    service = WebspaceProjectionService()
    operational = SimpleNamespace(
        webspace_id="ws-1",
        current_scenario="demo",
        effective_home_scenario="home",
        source_mode="dev",
    )
    registry = SimpleNamespace(
        snapshot=lambda: {
            "active_scenario_id": "demo",
            "active_space": "dev",
            "base_rule_count": 2,
            "scenario_rule_count": 3,
        }
    )

    result = service.describe(operational=operational, scenario_id=None, registry=registry)

    assert result["active_matches_target"] is True
    assert result["target_space"] == "dev"
    assert result["scenario_rule_count"] == 3


def test_projection_service_clears_stale_rules_after_load_failure() -> None:
    cleared: list[tuple[list[object], str, str]] = []

    class Registry:
        def load_from_scenario(self, scenario_id: str, *, space: str) -> int:
            raise ValueError("broken manifest")

        def replace_scenario_entries(self, entries, *, scenario_id: str, space: str) -> None:
            cleared.append((entries, scenario_id, space))

    result = WebspaceProjectionService().refresh_rules(
        registry=Registry(),
        scenario_id="demo",
        scenario_resolution="explicit",
        space="workspace",
    )

    assert result["rules_loaded"] == 0
    assert result["error"] == "ValueError: broken manifest"
    assert cleared == [([], "demo", "workspace")]


def test_projection_service_reports_timeout() -> None:
    async def slow() -> None:
        await asyncio.sleep(0.02)

    result = asyncio.run(WebspaceProjectionService().project(operation=slow, timeout_s=0.001))

    assert result == {"status": "timed_out"}
