from __future__ import annotations

import asyncio
import threading
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


def test_projection_service_resolves_missing_rebuild_target() -> None:
    calls: list[tuple[str, str]] = []

    class Registry:
        def load_from_scenario(self, scenario_id: str, *, space: str) -> int:
            calls.append((scenario_id, space))
            return 4

    async def resolve_target(webspace_id: str, scenario_id: str | None):
        assert webspace_id == "desktop"
        assert scenario_id is None
        return SimpleNamespace(), "home", "manifest_home"

    result = asyncio.run(
        WebspaceProjectionService().refresh_for_rebuild(
            registry=Registry(),
            webspace_id="desktop",
            scenario_id=None,
            scenario_resolution=None,
            resolve_target=resolve_target,
            resolve_space=lambda _webspace_id: "dev",
        )
    )

    assert result["scenario_id"] == "home"
    assert result["scenario_resolution"] == "manifest_home"
    assert result["rules_loaded"] == 4
    assert calls == [("home", "dev")]


def test_projection_service_offloads_source_and_rule_loading() -> None:
    main_thread_id = threading.get_ident()
    calls: list[tuple[str, int]] = []

    class Registry:
        def load_from_scenario(self, _scenario_id: str, *, space: str) -> int:
            calls.append((f"rules:{space}", threading.get_ident()))
            return 1

    def resolve_space(_webspace_id: str) -> str:
        calls.append(("space", threading.get_ident()))
        return "dev"

    result = asyncio.run(
        WebspaceProjectionService().refresh_for_rebuild(
            registry=Registry(),
            webspace_id="desktop-dev",
            scenario_id="demo",
            scenario_resolution="explicit",
            resolve_target=lambda *_args: None,
            resolve_space=resolve_space,
        )
    )

    assert result["rules_loaded"] == 1
    assert [name for name, _thread_id in calls] == ["space", "rules:dev"]
    assert all(thread_id != main_thread_id for _name, thread_id in calls)
