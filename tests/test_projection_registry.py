from __future__ import annotations

from adaos.services.scenario.projection_registry import ProjectionRegistry


def test_projection_registry_active_scenario_overrides_skill_defaults(monkeypatch) -> None:
    registry = ProjectionRegistry()
    registry.load_entries(
        [
            {
                "scope": "subnet",
                "slot": "weather.snapshot",
                "targets": [{"backend": "yjs", "path": "data/weather/default"}],
            }
        ]
    )

    monkeypatch.setattr(
        "adaos.services.scenario.projection_registry.read_manifest",
        lambda scenario_id, *, space="workspace": {
            "data_projections": [
                {
                    "scope": "subnet",
                    "slot": "weather.snapshot",
                    "targets": [{"backend": "yjs", "path": f"data/weather/{scenario_id}"}],
                }
            ]
        },
    )

    loaded = registry.load_from_scenario("storm_lab", space="dev")

    resolved = registry.resolve("subnet", "weather.snapshot")
    assert loaded == 1
    assert registry.active_scenario_id() == "storm_lab"
    assert registry.active_space() == "dev"
    assert len(resolved) == 1
    assert resolved[0].path == "data/weather/storm_lab"


def test_projection_registry_clears_stale_scenario_overrides(monkeypatch) -> None:
    registry = ProjectionRegistry()
    registry.load_entries(
        [
            {
                "scope": "subnet",
                "slot": "infrastate.snapshot",
                "targets": [{"backend": "yjs", "path": "data/infrastate/base"}],
            }
        ]
    )

    def _read_manifest(scenario_id: str, *, space: str = "workspace") -> dict[str, object]:
        if scenario_id == "with_override":
            return {
                "data_projections": [
                    {
                        "scope": "subnet",
                        "slot": "infrastate.snapshot",
                        "targets": [{"backend": "yjs", "path": "data/infrastate/override"}],
                    }
                ]
            }
        return {"data_projections": []}

    monkeypatch.setattr("adaos.services.scenario.projection_registry.read_manifest", _read_manifest)

    registry.load_from_scenario("with_override", space="workspace")
    overridden = registry.resolve("subnet", "infrastate.snapshot")
    registry.load_from_scenario("without_override", space="dev")
    restored = registry.resolve("subnet", "infrastate.snapshot")

    assert overridden[0].path == "data/infrastate/override"
    assert registry.active_scenario_id() == "without_override"
    assert registry.active_space() == "dev"
    assert restored[0].path == "data/infrastate/base"


def test_projection_registry_loads_yjs_route_budget_from_manifest() -> None:
    registry = ProjectionRegistry()

    loaded = registry.load_manifest(
        {
            "data_routes": [
                {
                    "surface": "widget:media",
                    "route": "yjs",
                    "projection_slot": "mediaserver.library",
                    "budget": {"max_payload_bytes": 8192, "max_items": 25},
                    "guard_visibility": {"degraded_state": "media library summary degraded"},
                }
            ],
            "data_projections": [
                {
                    "scope": "subnet",
                    "slot": "mediaserver.library",
                    "targets": [{"backend": "yjs", "path": "data/media/library"}],
                }
            ],
        }
    )

    rule = registry.resolve_rule("subnet", "mediaserver.library")
    assert loaded == 1
    assert rule is not None
    assert rule.budget == {"max_payload_bytes": 8192, "max_items": 25}
    assert rule.route["surface"] == "widget:media"
    assert rule.guard_visibility == {"degraded_state": "media library summary degraded"}


def test_projection_registry_replaces_skill_rules_without_leaving_stale_entries() -> None:
    registry = ProjectionRegistry()
    manifest = {
        "data_projections": [
            {
                "scope": "subnet",
                "slot": "shared.snapshot",
                "targets": [{"backend": "yjs", "path": "data/first"}],
            }
        ]
    }

    registry.replace_skill_manifest("first_skill", manifest)
    registry.replace_skill_manifest(
        "second_skill",
        {
            "data_projections": [
                {
                    "scope": "subnet",
                    "slot": "shared.snapshot",
                    "targets": [{"backend": "yjs", "path": "data/second"}],
                }
            ]
        },
    )
    assert registry.resolve("subnet", "shared.snapshot")[0].path == "data/second"

    registry.replace_skill_manifest("second_skill", {"data_projections": []})
    assert registry.resolve("subnet", "shared.snapshot")[0].path == "data/first"

    registry.replace_skill_manifest("first_skill", {"data_projections": []})
    assert registry.resolve_rule("subnet", "shared.snapshot") is None
