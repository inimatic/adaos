from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


@pytest.fixture
def workflow_project(tmp_path: Path) -> tuple[BuilderWorkflowService, Path]:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    skills.mkdir()
    root = scenarios / "recipes"
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    (root / "webui.json").write_text(
        json.dumps(
            {
                "schema": "adaos.webui.v1",
                "ui": {"application": {"desktop": {"pageSchema": {"id": "recipes"}}}},
            }
        ),
        encoding="utf-8",
    )
    revision_dir = root / "ui_revisions"
    revision_dir.mkdir()
    (revision_dir / "001.json").write_text("{}", encoding="utf-8")
    (revision_dir / "current.txt").write_text("001\n", encoding="utf-8")
    return BuilderWorkflowService(
        dev_skills_root=skills,
        dev_scenarios_root=scenarios,
        state_dir=tmp_path / "state",
    ), root


def test_workflow_migrates_legacy_state_without_mutating_it(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    (root / "prompt_state.json").write_text(
        json.dumps({"workflow_state": "publication", "archived": False}),
        encoding="utf-8",
    )

    workflow = service.describe("scenario", "recipes")

    assert workflow["active_phase"] == "automation"
    assert workflow["prototype"]["status"] == "frozen"
    assert workflow["automation"]["status"] == "completed"
    assert workflow["publication"]["status"] == "published"
    assert workflow["capabilities"]["can_publish"] is True
    assert "workflow" not in json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))


def test_only_active_phase_is_mutable_and_publication_is_a_snapshot(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project

    handed_off = service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"source_prototype_revision": "UI 001", "task_id": "task.1"},
    )["workflow"]
    assert handed_off["active_phase"] == "automation"
    assert handed_off["prototype"]["status"] == "frozen"
    assert handed_off["capabilities"]["can_edit_prototype"] is False

    completed = service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.1", "version": "0.1.0"},
    )["workflow"]
    assert completed["automation"]["status"] == "completed"
    assert completed["capabilities"]["can_publish"] is True

    published = service.transition(
        "scenario",
        "recipes",
        "publish",
        metadata={"version": "0.1.1", "task_id": "task.1"},
    )["workflow"]
    assert published["active_phase"] == "automation"
    assert published["publication"]["current_version"] == "0.1.1"
    assert published["publication"]["status"] == "published"

    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    assert persisted["workflow_state"] == "automation"
    assert [item["action"] for item in persisted["workflow"]["history"]] == [
        "automation_started",
        "automation_completed",
        "publish",
    ]


def test_invalid_cross_phase_transition_is_rejected(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project

    with pytest.raises(BuilderWorkflowError, match="requires active automation"):
        service.transition("scenario", "recipes", "publish", metadata={"version": "0.1.1"})


def test_return_to_prototype_uses_a_new_immutable_revision(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})
    service.transition("scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"})
    service.transition("scenario", "recipes", "request_return_to_prototype", metadata={"task_id": "task.2"})

    snapshot = service.snapshot_current_prototype(
        "scenario",
        "recipes",
        source_task_id="task.2",
    )
    returned = service.transition(
        "scenario",
        "recipes",
        "return_to_prototype",
        metadata={"revision": snapshot["revision"], "task_id": "task.2"},
    )["workflow"]

    assert snapshot["revision"] == "002"
    assert (root / "ui_revisions" / "002.json").is_file()
    assert (root / "ui_revisions" / "current.txt").read_text(encoding="utf-8").strip() == "002"
    assert returned["active_phase"] == "prototype"
    assert returned["prototype"]["status"] == "working"
    assert returned["automation"]["status"] == "frozen"
    assert returned["prototype"]["derived_from_automation_task"] == "task.2"


def test_archived_project_cannot_transition(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    (root / "prompt_state.json").write_text(json.dumps({"archived": True}), encoding="utf-8")

    with pytest.raises(BuilderWorkflowError, match="archived projects"):
        service.transition("scenario", "recipes", "stabilize_prototype")


def test_only_latest_automation_snapshot_is_retained(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project

    first = service.snapshot_current_automation("scenario", "recipes", task_id="task.1")
    first_path = Path(first["path"])
    assert json.loads((first_path / "snapshot.json").read_text(encoding="utf-8"))["task_id"] == "task.1"

    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    webui["ui"]["application"]["desktop"]["pageSchema"]["title"] = "Automated v2"
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    second = service.snapshot_current_automation("scenario", "recipes", task_id="task.2")

    assert second["path"] == first["path"]
    assert json.loads((first_path / "snapshot.json").read_text(encoding="utf-8"))["task_id"] == "task.2"
    retained = json.loads((first_path / "webui.json").read_text(encoding="utf-8"))
    assert retained["ui"]["application"]["desktop"]["pageSchema"]["title"] == "Automated v2"
    assert not first_path.with_name(".automation.previous").exists()
