from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


@pytest.fixture
def service(tmp_path: Path) -> BuilderWorkflowService:
    skills = tmp_path / "skills"
    root = tmp_path / "scenarios" / "recipes"
    skills.mkdir()
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "recipes-page",
                        "layout": {"type": "stack", "responsive": {"compact": "single-column"}},
                        "widgets": [
                            {"id": "recipe-title", "type": "ui.text", "title": "Recipes"},
                            {"id": "recipe-list", "type": "ui.list", "title": "Recipe list"},
                        ],
                    }
                }
            }
        },
    }
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    revisions = root / "ui_revisions"
    revisions.mkdir()
    (revisions / "001.json").write_text("{}", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    return BuilderWorkflowService(skills, tmp_path / "scenarios", tmp_path / "state")


def _plan(service: BuilderWorkflowService, target_ref: str) -> None:
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-layout",
            "request": "Move the recipe title before the list.",
            "issues": [
                {
                    "issue_id": "layout",
                    "title": "Move the recipe title",
                    "lane": "prototype",
                    "semantic_refs": [target_ref],
                    "acceptance_criteria": ["The title is before the list."],
                }
            ],
        },
    )


def test_spatial_context_reports_structure_abi_constraints_data_and_authority(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "widget:recipe-title")
    required = ["target_structure", "abi", "constraints", "data_policy", "execution_authority"]
    packet = service.build_context_packet(
        "scenario",
        "recipes",
        required_facets=required,
        enforce_context_coverage=True,
    )

    assert packet["coverage"] == {
        "required": required,
        "present": required,
        "missing": [],
        "ambiguous": [],
        "ready": True,
    }
    target = packet["facets"]["target_structure"]["resolved"][0]
    assert target["target_ref"] == "widget:recipe-title"
    assert target["siblings"] == ["recipe-title", "recipe-list"]
    assert target["order"] == 0
    assert packet["facets"]["abi"]["definition_ref"] == "abi:webui.v1.schema.json"
    assert packet["facets"]["data_policy"]["selected_mode"] == "mock"


def test_missing_semantic_target_fails_before_model_submission(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "widget:missing")
    with pytest.raises(BuilderWorkflowError, match="missing:target_structure"):
        service.build_context_packet(
            "scenario",
            "recipes",
            required_facets=["target_structure", "abi"],
            enforce_context_coverage=True,
        )

    report = service.build_context_packet(
        "scenario",
        "recipes",
        required_facets=["target_structure", "abi"],
        enforce_context_coverage=False,
    )
    assert report["coverage"]["ready"] is False
    assert report["coverage"]["missing"] == ["target_structure"]


def test_context_packet_digest_covers_purpose_facets_and_review_constraints(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "widget:recipe-title")
    iteration = service.build_context_packet(
        "scenario",
        "recipes",
        run_purpose="iteration",
        required_facets=["target_structure"],
    )
    experiment = service.build_context_packet(
        "scenario",
        "recipes",
        run_purpose="experiment",
        required_facets=["target_structure"],
    )
    assert iteration["run"]["purpose"] == "iteration"
    assert experiment["run"]["purpose"] == "experiment"
    assert iteration["digest"] != experiment["digest"]

