from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder.review import BuilderReviewService, compile_constraint
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


@pytest.fixture
def review_project(tmp_path: Path) -> tuple[BuilderReviewService, BuilderWorkflowService, Path]:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    root = scenarios / "recipes"
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
                        "layout": {"type": "stack", "areas": [{"id": "main", "role": "main"}]},
                        "widgets": [
                            {
                                "id": "recipe-form",
                                "type": "ui.form",
                                "area": "main",
                                "title": "Recipes",
                                "fields": [
                                    {"id": "recipe-name", "type": "text", "label": "Name"},
                                    {"id": "notes", "type": "textarea", "label": "Notes"},
                                ],
                            }
                        ],
                    }
                }
            }
        },
    }
    (root / "webui.json").write_text(json.dumps(webui, ensure_ascii=False), encoding="utf-8")
    revisions = root / "ui_revisions"
    revisions.mkdir()
    (revisions / "001.json").write_text("{}\n", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    workflow = BuilderWorkflowService(
        dev_skills_root=skills,
        dev_scenarios_root=scenarios,
        state_dir=tmp_path / "state",
    )
    workflow.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-recipes-review",
            "request": "Improve the recipe form.",
            "issues": [
                {
                    "issue_id": "recipe-label",
                    "title": "Use an explicit recipe label",
                    "lane": "prototype",
                    "acceptance_criteria": ["The recipe name label is explicit."],
                }
            ],
        },
    )
    return BuilderReviewService(workflow=workflow), workflow, root


def _review_anchor() -> dict:
    return {
        "schema": "adaos.builder.review_anchor.v1",
        "review_id": "review.recipe-name",
        "change_id": "CH-recipes-review",
        "artifact_ref": "scenario:recipes@ui_revision:001",
        "target_ref": "field:recipe-form:recipe-name",
        "comment": "Use the full label Recipe name.",
        "status": "accepted",
        "author_ref": "user:owner",
        "created_at": "2026-07-29T10:00:00+00:00",
    }


def test_review_constraint_is_persisted_and_verified_against_later_revision(
    review_project: tuple[BuilderReviewService, BuilderWorkflowService, Path],
) -> None:
    service, workflow, root = review_project

    registered = service.register_constraint(
        _review_anchor(),
        kind="label_equals",
        expected="Recipe name",
        source_revision="001",
    )

    constraint = registered["constraint"]
    assert constraint["status"] == "active"
    state = workflow.describe("scenario", "recipes")
    assert state["change"]["acceptance_constraints"][0]["review_id"] == "review.recipe-name"
    packet = workflow.build_context_packet("scenario", "recipes")
    assert packet["change"]["acceptance_constraints"][0]["constraint_id"] == constraint["constraint_id"]
    assert packet["budget"]["acceptance_constraint_count"] == 1

    first = service.evaluate_current("scenario", "recipes", revision="001")
    assert first["evaluations"][0]["status"] == "violated"
    assert first["evaluations"][0]["actual"] == "Name"

    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"][0]["fields"][0]["label"] = "Recipe name"
    (root / "webui.json").write_text(json.dumps(webui, ensure_ascii=False), encoding="utf-8")
    (root / "ui_revisions" / "002.json").write_text("{}\n", encoding="utf-8")
    (root / "ui_revisions" / "current.txt").write_text("002\n", encoding="utf-8")

    second = service.evaluate_current("scenario", "recipes", revision="002")
    assert second["evaluations"][0]["status"] == "satisfied"
    final_constraint = second["workflow"]["change"]["acceptance_constraints"][0]
    assert final_constraint["status"] == "satisfied"
    assert final_constraint["last_evaluation"]["revision"] == "002"


def test_review_constraint_requires_structured_supported_intent() -> None:
    with pytest.raises(BuilderWorkflowError, match="unsupported Review constraint kind"):
        compile_constraint(
            _review_anchor(),
            kind="make_it_better",
            expected=True,
            source_revision="001",
        )
