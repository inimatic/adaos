from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.project_aggregate import (
    BuilderProjectError,
    begin_mutation,
    finish_mutation,
)
from adaos.services.builder.workflow import BuilderWorkflowService
from adaos.services.builder.workflow import BuilderWorkflowError


ABI_ROOT = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi"


@pytest.fixture
def service(tmp_path: Path) -> BuilderWorkflowService:
    skills = tmp_path / "skills"
    root = tmp_path / "scenarios" / "recipes"
    skills.mkdir()
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    revisions = root / "ui_revisions"
    revisions.mkdir()
    (revisions / "001.json").write_text("{}", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    return BuilderWorkflowService(skills, tmp_path / "scenarios", tmp_path / "state")


def _plan(
    service: BuilderWorkflowService,
    change_id: str,
    semantic_ref: str,
    *,
    parallel: bool = False,
) -> dict[str, object]:
    return service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": change_id,
            "request": f"Implement {change_id}.",
            "parallel": parallel,
            "affected_refs": [semantic_ref],
            "issues": [
                {
                    "issue_id": f"issue-{change_id}",
                    "title": f"Implement {change_id}",
                    "lane": "prototype",
                    "semantic_refs": [semantic_ref],
                    "acceptance_criteria": [f"{change_id} is visible."],
                }
            ],
        },
    )["workflow"]


def test_project_aggregate_is_schema_valid_and_reference_oriented(service: BuilderWorkflowService) -> None:
    workflow = _plan(service, "CH-favorites", "widget:favorites")
    project = workflow["project"]
    schema = json.loads((ABI_ROOT / "builder.project.v1.schema.json").read_text(encoding="utf-8"))

    Draft202012Validator(schema).validate(project)
    assert project["project_ref"] == "scenario:recipes"
    assert project["identity"] == {
        "stable_id": "recipes",
        "kind": "scenario",
        "project_ref": "scenario:recipes",
        "title": "recipes",
        "description": None,
    }
    assert project["focus_by_context"]["default"] == "CH-favorites"
    assert project["changes"][0]["workflow_instance_ref"] == workflow["governed"]["instance_id"]
    assert project["changes"][0]["affected_refs"] == ["widget:favorites"]
    assert project["changes"][0]["issue_refs"] == ["issue:issue-CH-favorites"]
    assert project["change_edges"] == [
        {
            "from_ref": "change:CH-favorites",
            "to_ref": "issue:issue-CH-favorites",
            "relation": "contains_issue",
        }
    ]
    assert project["workflow_definition_version"] == "1.2.0"
    assert project["policy"]["risk_policy"]["fail_closed"] is True
    assert project["explanation"]["status"] == "active"
    assert "request" not in project["changes"][0]


def test_parallel_changes_are_preserved_and_focus_is_not_a_business_transition(
    service: BuilderWorkflowService,
) -> None:
    first = _plan(service, "CH-favorites", "widget:favorites")
    first_canonical = first["governed"]["generation"]
    second = _plan(service, "CH-search", "widget:search", parallel=True)

    assert {item["change_id"] for item in second["project"]["changes"]} == {
        "CH-favorites",
        "CH-search",
    }
    assert second["project"]["conflicts"] == []
    assert second["change"]["change_id"] == "CH-search"

    switched = service.focus_change(
        "scenario",
        "recipes",
        "CH-favorites",
        expected_view_generation=second["project"]["view_generation"],
    )["workflow"]
    assert switched["change"]["change_id"] == "CH-favorites"
    assert switched["governed"]["generation"] == first_canonical
    assert switched["project"]["generation"] == second["project"]["generation"]
    assert switched["project"]["view_generation"] == second["project"]["view_generation"] + 1


def test_change_portfolio_is_externalized_from_bounded_prompt_state(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "CH-favorites", "widget:favorites")
    _plan(service, "CH-search", "widget:search", parallel=True)

    raw_state = json.loads(
        service._state_path("scenario", "recipes").read_text(encoding="utf-8")
    )
    raw_workflow = raw_state["workflow"]
    external = raw_workflow["change_portfolio_external"]
    assert raw_workflow["change_portfolio"] == {}
    assert "change_set" not in raw_workflow
    assert external == {
        "schema": "adaos.builder.change_portfolio_external.v1",
        "change_ids": ["CH-favorites", "CH-search"],
    }
    for change_id in external["change_ids"]:
        assert service._portfolio_record_path("scenario", "recipes", change_id).is_file()

    # The persistence split is transparent to callers and survives a new
    # service instance. Prompt context remains bounded while the complete
    # project portfolio remains available to Builder workflow operations.
    restarted = BuilderWorkflowService(
        service.dev_skills_root,
        service.dev_scenarios_root,
        service.state_dir,
    )
    restored = restarted.describe("scenario", "recipes")
    assert set(restored["change_portfolio"]) == {"CH-favorites", "CH-search"}
    assert restored["change"]["change_id"] == "CH-search"


def test_conflict_index_and_mutation_admission_fail_closed(service: BuilderWorkflowService) -> None:
    _plan(service, "CH-label", "widget:recipe-title")
    second = _plan(service, "CH-layout", "widget:recipe-title", parallel=True)
    project = second["project"]

    assert project["conflicts"] == [
        {
            "left_change_id": "CH-label",
            "right_change_id": "CH-layout",
            "affected_refs": ["widget:recipe-title"],
            "kind": "direct",
        }
    ]
    summary = second["project_summary"]
    assert summary["open_change_count"] == 2
    assert summary["conflict_count"] == 1
    assert "stage" not in summary
    active = begin_mutation(
        project,
        "CH-label",
        expected_project_generation=project["generation"],
        expected_base_generation=0,
    )
    with pytest.raises(BuilderProjectError, match="conflicts with active mutation CH-label"):
        begin_mutation(
            active,
            "CH-layout",
            expected_project_generation=active["generation"],
            expected_base_generation=0,
        )
    released = finish_mutation(active, "CH-label", advance_base=True)
    label = next(item for item in released["changes"] if item["change_id"] == "CH-label")
    assert label["mutation_status"] == "idle"
    assert label["base_generation"] == 1


def test_unknown_change_scope_conflicts_at_project_boundary(service: BuilderWorkflowService) -> None:
    first = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-unknown-1",
            "request": "Change something.",
            "issues": [
                {
                    "issue_id": "unknown-1",
                    "title": "Unknown scope",
                    "lane": "automation",
                    "acceptance_criteria": ["The behavior changes."],
                }
            ],
        },
    )["workflow"]
    assert first["project"]["changes"][0]["affected_refs"] == ["scenario:recipes"]

    second = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-unknown-2",
            "request": "Change something else.",
            "parallel": True,
            "issues": [
                {
                    "issue_id": "unknown-2",
                    "title": "Another unknown scope",
                    "lane": "automation",
                    "acceptance_criteria": ["The other behavior changes."],
                }
            ],
        },
    )["workflow"]
    assert second["project"]["conflicts"][0]["affected_refs"] == ["scenario:recipes"]


def test_completed_mutation_advances_project_base_and_requires_explicit_rebase(
    service: BuilderWorkflowService,
) -> None:
    _plan(service, "CH-favorites", "widget:favorites")
    second = _plan(service, "CH-search", "widget:search", parallel=True)
    service.focus_change(
        "scenario",
        "recipes",
        "CH-favorites",
        expected_view_generation=second["project"]["view_generation"],
    )
    service.transition(
        "scenario", "recipes", "stabilize_prototype", metadata={"confirmed": True}
    )
    running = service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "RUN-favorites", "confirmed": True},
    )["workflow"]
    active = next(item for item in running["project"]["changes"] if item["change_id"] == "CH-favorites")
    assert active["mutation_status"] == "active"
    completed = service.transition(
        "scenario", "recipes", "automation_completed", metadata={"task_id": "RUN-favorites"}
    )["workflow"]
    assert completed["project"]["artifact_generation"] == 1

    service.focus_change(
        "scenario",
        "recipes",
        "CH-search",
        expected_view_generation=completed["project"]["view_generation"],
    )
    service.transition(
        "scenario", "recipes", "stabilize_prototype", metadata={"confirmed": True}
    )
    with pytest.raises(BuilderWorkflowError, match="explicit rebase is required"):
        service.transition(
            "scenario",
            "recipes",
            "automation_started",
            metadata={"task_id": "RUN-search", "confirmed": True},
        )

    rebased = service.rebase_change(
        "scenario",
        "recipes",
        "CH-search",
        expected_project_generation=service.describe("scenario", "recipes")["project"]["generation"],
        verified_unchanged_refs=["widget:search"],
    )["workflow"]
    search = next(item for item in rebased["project"]["changes"] if item["change_id"] == "CH-search")
    assert search["base_generation"] == 1
    resumed = service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "RUN-search", "confirmed": True},
    )["workflow"]
    assert resumed["governed"]["state"] == "automation_waiting"
