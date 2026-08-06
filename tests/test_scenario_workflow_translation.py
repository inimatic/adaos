from __future__ import annotations

import copy
from pathlib import Path

from adaos.services.scenario.workflow_translation import (
    inventory_scenario_workflows,
    shadow_compare_legacy_workflow,
    translate_legacy_scenario_workflow,
)


def _legacy() -> dict[str, object]:
    return {
        "initial_state": "draft",
        "states": {
            "draft": {
                "label": "Draft",
                "actions": [
                    {"id": "approve", "label": "Approve", "next_state": "done"}
                ],
            },
            "done": {"label": "Done", "actions": []},
        },
    }


def test_legacy_translation_is_deterministic_and_preserves_edges() -> None:
    first = translate_legacy_scenario_workflow(_legacy(), scenario_id="demo")
    second = translate_legacy_scenario_workflow(_legacy(), scenario_id="demo")

    assert first == second
    assert first["initial_state"] == "draft"
    assert first["transitions"][0]["source"] == "draft"
    assert first["transitions"][0]["target"] == "done"
    assert first["states"][1]["terminal"] is True


def test_shadow_comparison_reports_semantic_divergence() -> None:
    translated = translate_legacy_scenario_workflow(_legacy(), scenario_id="demo")
    matching = shadow_compare_legacy_workflow(
        _legacy(), translated, scenario_id="demo"
    )
    changed = copy.deepcopy(translated)
    changed["transitions"][0]["target"] = "draft"
    diverged = shadow_compare_legacy_workflow(
        _legacy(), changed, scenario_id="demo"
    )

    assert matching["status"] == "match"
    assert diverged["status"] == "diverged"


def test_inventory_separates_legacy_governed_and_no_workflow(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    governed = tmp_path / "governed"
    plain = tmp_path / "plain"
    for path in (legacy, governed, plain):
        path.mkdir()
    (legacy / "scenario.yaml").write_text(
        "id: legacy\nversion: 1.0.0\nworkflow:\n  initial_state: done\n  states:\n    done:\n      actions: []\n",
        encoding="utf-8",
    )
    (governed / "scenario.yaml").write_text(
        "id: governed\nversion: 1.0.0\nworkflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )
    (plain / "scenario.yaml").write_text(
        "id: plain\nversion: 1.0.0\n",
        encoding="utf-8",
    )

    inventory = inventory_scenario_workflows(tmp_path)

    assert inventory["counts"] == {
        "governed_manifest": 1,
        "legacy_inline": 1,
        "none": 1,
        "invalid": 0,
    }
