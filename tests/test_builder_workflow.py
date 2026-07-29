from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService, _replace_path


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


def test_atomic_replace_retries_transient_windows_lock(monkeypatch, tmp_path: Path) -> None:
    source = tmp_path / "source.json"
    target = tmp_path / "target.json"
    source.write_text("new", encoding="utf-8")
    target.write_text("old", encoding="utf-8")
    original_replace = Path.replace
    attempts = 0

    def flaky_replace(path: Path, destination: Path) -> Path:
        nonlocal attempts
        attempts += 1
        if path == source and attempts < 3:
            raise PermissionError("temporarily locked")
        return original_replace(path, destination)

    monkeypatch.setattr(Path, "replace", flaky_replace)

    _replace_path(source, target)

    assert attempts == 3
    assert target.read_text(encoding="utf-8") == "new"


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
    assert workflow["delivery"]["status"] == "published"
    assert workflow["capabilities"]["can_publish"] is False
    assert "workflow" not in json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))


def test_scenario_without_ui_revision_uses_current_content_not_manifest_version(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    (root / "ui_revisions" / "current.txt").unlink()
    (root / "ui_revisions" / "001.json").unlink()

    workflow = service.describe("scenario", "recipes")

    assert service.current_prototype_revision("scenario", "recipes") is None
    assert workflow["prototype"]["head_revision"] is None


def test_invalid_ui_revision_pointer_is_not_treated_as_a_revision(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    (root / "ui_revisions" / "current.txt").write_text("0.2.0\n", encoding="utf-8")

    workflow = service.describe("scenario", "recipes")

    assert service.current_prototype_revision("scenario", "recipes") is None
    assert workflow["prototype"]["head_revision"] is None


def test_change_set_routes_interface_work_through_prototype_first(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project

    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-favorites",
            "request": "Add a favorites section and preserve the existing shopping flow.",
            "source_message_ids": ["message-1"],
            "issues": [
                {
                    "issue_id": "favorites-layout",
                    "title": "Add the favorites section to the navigation",
                    "lane": "prototype",
                    "acceptance_criteria": ["Favorites is visible without hiding the shopping list."],
                },
                {
                    "issue_id": "favorites-storage",
                    "title": "Persist favorite recipes",
                    "lane": "automation",
                    "acceptance_criteria": ["Favorites survive a scenario restart."],
                },
            ],
        },
    )["workflow"]

    assert planned["active_phase"] == "prototype"
    assert planned["change_set"]["schema"] == "adaos.builder.change_set.v1"
    assert planned["change_set"]["route"] == "prototype_first"
    assert planned["change_set"]["gate"] == "prototype"
    assert planned["change_set"]["member_change_ids"] == ["CS-recipes-favorites"]
    assert planned["capabilities"]["can_plan_change_set"] is False

    approved = service.transition(
        "scenario",
        "recipes",
        "stabilize_prototype",
        metadata={"revision": "001"},
    )["workflow"]
    assert approved["change_set"]["status"] == "approved"
    assert approved["change_set"]["gate"] == "automation"
    assert approved["change_set"]["issues"][0]["status"] == "resolved"
    assert approved["change_set"]["issues"][1]["status"] == "open"


def test_change_set_projects_one_canonical_change_and_transition_runs(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project

    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        actor="builder.chat",
        metadata={
            "change_set_id": "CH-recipes-search",
            "run_id": "RUN-plan-search",
            "request": "Add fast recipe search.",
            "source_message_ids": ["message-search"],
            "issues": [
                {
                    "issue_id": "recipe-search",
                    "title": "Add fast recipe search",
                    "lane": "prototype",
                    "acceptance_criteria": ["Typing filters recipes immediately."],
                }
            ],
        },
    )["workflow"]

    assert planned["change"]["schema"] == "adaos.builder.change.v1"
    assert planned["change"]["change_id"] == "CH-recipes-search"
    assert planned["change"]["change_set_id"] == "CH-recipes-search"
    assert planned["change"]["project_ref"] == "scenario:recipes"
    assert planned["change_set"]["change_set_id"] == "CH-recipes-search"
    assert "change_id" not in planned["change_set"]
    assert len(planned["change"]["runs"]) == 1
    plan_run = planned["change"]["runs"][0]
    assert plan_run["schema"] == "adaos.builder.run.v1"
    assert plan_run["run_id"] == "RUN-plan-search"
    assert plan_run["change_id"] == "CH-recipes-search"
    assert plan_run["activity"] == "plan_change_set"
    assert plan_run["executor"] == "builder.chat"
    assert plan_run["status"] == "succeeded"

    approved = service.transition(
        "scenario",
        "recipes",
        "stabilize_prototype",
        actor="builder.ui",
        metadata={"revision": "001", "run_id": "RUN-approve-search"},
        expected_generation=planned["generation"],
    )["workflow"]

    assert approved["change"]["change_id"] == "CH-recipes-search"
    assert [item["run_id"] for item in approved["change"]["runs"]] == [
        "RUN-plan-search",
        "RUN-approve-search",
    ]
    assert approved["change_set"]["status"] == "approved"


def test_builder_transition_rejects_stale_generation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-stale",
            "request": "Change the recipe title.",
            "issues": [
                {
                    "issue_id": "title",
                    "title": "Change the recipe title",
                    "lane": "prototype",
                    "acceptance_criteria": ["The new title is visible."],
                }
            ],
        },
    )["workflow"]

    with pytest.raises(BuilderWorkflowError, match="stale Builder action generation"):
        service.transition(
            "scenario",
            "recipes",
            "stabilize_prototype",
            metadata={"revision": "001"},
            expected_generation=planned["generation"] - 1,
        )

    assert service.describe("scenario", "recipes")["change"]["status"] == "planned"


def test_context_packet_is_bounded_stable_and_persistable(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    (root / "scenario.yaml").write_text(
        "id: recipes\nversion: 0.1.0\ndepends:\n- recipe_store_skill\n",
        encoding="utf-8",
    )
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-context",
            "request": "Add a favorites filter.",
            "source_message_ids": ["message-context"],
            "issues": [
                {
                    "issue_id": "favorites-filter",
                    "title": "Add a favorites filter",
                    "lane": "prototype",
                    "acceptance_criteria": ["Only favorite recipes remain visible."],
                }
            ],
        },
    )

    first = service.build_context_packet(
        "scenario",
        "recipes",
        allowed_paths=["scenario.yaml", "webui.json"],
        instruction_refs=["docs/architecture/builder.md"],
    )
    second = service.build_context_packet(
        "scenario",
        "recipes",
        allowed_paths=["scenario.yaml", "webui.json"],
        instruction_refs=["docs/architecture/builder.md"],
        persist=True,
    )

    assert first["schema"] == "adaos.builder.context_packet.v1"
    assert first["digest"] == second["digest"]
    assert first["change"]["change_id"] == "CH-context"
    assert first["change"]["source_message_ids"] == ["message-context"]
    assert "transcript" not in first
    assert first["dependencies"] == ["recipe_store_skill"]
    assert first["allowed_paths"] == ["scenario.yaml", "webui.json"]
    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    assert persisted["workflow"]["context_packet"]["digest"] == first["digest"]
    assert persisted["workflow"]["change"]["context_packet_digest"] == first["digest"]


def test_workflow_rejects_divergent_change_compatibility_identities(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    legacy = {
        "request": "Keep ids consistent.",
        "route": "automation_direct",
        "gate": "automation",
        "status": "planned",
        "issues": [
            {
                "issue_id": "ids",
                "title": "Keep ids consistent",
                "lane": "automation",
                "status": "open",
                "acceptance_criteria": ["Divergent identities are rejected."],
            }
        ],
    }
    (root / "prompt_state.json").write_text(
        json.dumps(
            {
                "workflow": {
                    "change": {**legacy, "change_id": "CH-new", "change_set_id": "CH-new"},
                    "change_set": {**legacy, "change_set_id": "CH-old"},
                }
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(BuilderWorkflowError, match="identities diverge"):
        service.describe("scenario", "recipes")


def test_prototype_revision_is_recorded_without_approving_issues(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-layout",
            "request": "Adjust the recipe layout.",
            "issues": [
                {
                    "issue_id": "layout",
                    "title": "Adjust the recipe layout",
                    "lane": "prototype",
                    "acceptance_criteria": ["The revised layout is visible."],
                }
            ],
        },
    )
    (root / "ui_revisions" / "005.json").write_text("{}", encoding="utf-8")
    (root / "ui_revisions" / "current.txt").write_text("005\n", encoding="utf-8")

    recorded = service.transition(
        "scenario",
        "recipes",
        "prototype_revision_recorded",
        metadata={
            "object_type": "scenario",
            "revision": "005",
            "previous_revision": "001",
            "change_id": "change.layout.005",
        },
    )["workflow"]

    assert recorded["active_phase"] == "prototype"
    assert recorded["prototype"]["head_revision"] == "005"
    assert recorded["prototype"]["stable"] is False
    assert recorded["change_set"]["status"] == "in_progress"
    assert recorded["change_set"]["gate"] == "prototype"
    assert recorded["change_set"]["issues"][0]["status"] == "open"
    assert "change.layout.005" in recorded["change_set"]["member_change_ids"]
    assert recorded["history"][-1]["action"] == "prototype_revision_recorded"


def test_prototype_revision_cannot_be_recorded_during_automation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})

    with pytest.raises(BuilderWorkflowError, match="requires active prototype"):
        service.transition(
            "scenario",
            "recipes",
            "prototype_revision_recorded",
            metadata={"object_type": "scenario", "revision": "002"},
        )


def test_automation_followup_does_not_skip_pending_prototype_gate(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-layout",
            "request": "Add a favorites section.",
            "issues": [
                {
                    "issue_id": "layout",
                    "title": "Add favorites layout",
                    "lane": "prototype",
                    "acceptance_criteria": ["Favorites is visible."],
                }
            ],
        },
    )

    extended = service.transition(
        "scenario",
        "recipes",
        "change_issues_added",
        metadata={
            "change_set_id": "CS-layout",
            "change_id": "change-storage",
            "request": "Persist favorites.",
            "issues": [
                {
                    "issue_id": "storage",
                    "title": "Persist favorites",
                    "lane": "automation",
                    "acceptance_criteria": ["Favorites survive restart."],
                }
            ],
        },
    )["workflow"]

    assert extended["change_set"]["gate"] == "prototype"
    assert extended["change_set"]["route"] == "prototype_first"


def test_change_set_routes_functional_work_directly_to_automation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-sync",
            "request": "Synchronize shopping items with the store API.",
            "issues": [
                {
                    "title": "Implement store synchronization",
                    "lane": "automation",
                    "acceptance_criteria": ["A failed request leaves the local list unchanged."],
                }
            ],
        },
    )["workflow"]

    assert planned["active_phase"] == "prototype"
    assert planned["change_set"]["route"] == "automation_direct"
    assert planned["change_set"]["gate"] == "automation"

    started = service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "task.sync", "change_id": "change-sync-implementation"},
    )["workflow"]
    assert started["active_phase"] == "automation"
    assert started["change_set"]["status"] == "in_progress"
    assert started["change_set"]["member_change_ids"] == [
        "CS-recipes-sync",
        "change-sync-implementation",
    ]


@pytest.mark.parametrize("corrupted", ["???????? ??????", "Повреждённый \ufffd текст"])
def test_change_set_rejects_transport_corrupted_new_text(
    workflow_project: tuple[BuilderWorkflowService, Path],
    corrupted: str,
) -> None:
    service, _root = workflow_project

    with pytest.raises(BuilderWorkflowError, match="transport-corrupted"):
        service.transition(
            "scenario",
            "recipes",
            "plan_change_set",
            metadata={
                "change_set_id": "CS-corrupted",
                "request": corrupted,
                "issues": [
                    {
                        "title": "Valid title",
                        "lane": "prototype",
                        "acceptance_criteria": ["Valid criterion"],
                    }
                ],
            },
        )


def test_change_set_advances_through_automation_trial_and_publication(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-sync",
            "request": "Synchronize shopping items with the store API.",
            "issues": [
                {
                    "issue_id": "sync",
                    "title": "Implement store synchronization",
                    "lane": "automation",
                    "acceptance_criteria": ["Synchronization is covered by an integration test."],
                }
            ],
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "task.sync", "change_id": "change-sync-implementation"},
    )
    completed = service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.sync", "change_id": "change-sync-implementation"},
    )["workflow"]
    assert completed["change_set"]["status"] == "implemented"
    assert completed["change_set"]["gate"] == "trial"
    assert completed["change_set"]["issues"][0]["status"] == "resolved"

    checkpointed = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "checkpoint-sync",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )["workflow"]
    assert checkpointed["change_set"]["status"] == "checkpointed"
    assert "checkpoint-sync" in checkpointed["change_set"]["member_change_ids"]

    trial = service.transition(
        "scenario",
        "recipes",
        "candidate_prepared",
        metadata={
            "candidate_id": "candidate-sync",
            "release_digest": "sha256:" + "2" * 64,
            "package_digest": "sha256:" + "3" * 64,
        },
    )["workflow"]
    assert trial["change_set"]["status"] == "trial"

    service.transition(
        "scenario",
        "recipes",
        "candidate_accepted",
        metadata={"candidate_id": "candidate-sync"},
    )
    published = service.transition(
        "scenario",
        "recipes",
        "publish",
        metadata={"candidate_id": "candidate-sync", "version": "0.2.0"},
    )["workflow"]
    assert published["change_set"]["status"] == "published"
    assert published["change_set"]["gate"] == "complete"
    assert published["capabilities"]["can_plan_change_set"] is True


def test_active_change_set_requires_explicit_supersession(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    issue = {
        "title": "First change",
        "lane": "prototype",
        "acceptance_criteria": ["The first change is visible."],
    }
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={"change_set_id": "CS-1", "request": "First change", "issues": [issue]},
    )

    with pytest.raises(BuilderWorkflowError, match="supersedes_change_set_id"):
        service.transition(
            "scenario",
            "recipes",
            "plan_change_set",
            metadata={"change_set_id": "CS-2", "request": "Second change", "issues": [issue]},
        )

    superseded = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-2",
            "supersedes_change_set_id": "CS-1",
            "request": "Second change",
            "issues": [issue],
        },
    )["workflow"]
    assert superseded["change_set"]["change_set_id"] == "CS-2"


def test_followup_request_extends_active_change_set_and_invalidates_trial(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes",
            "request": "Synchronize shopping items.",
            "issues": [
                {
                    "issue_id": "sync",
                    "title": "Synchronize shopping items",
                    "lane": "automation",
                    "acceptance_criteria": ["Synchronization is transactional."],
                }
            ],
        },
    )
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})
    service.transition("scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"})
    service.transition(
        "scenario",
        "recipes",
        "candidate_prepared",
        metadata={
            "candidate_id": "candidate-1",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
        },
    )

    extended = service.transition(
        "scenario",
        "recipes",
        "change_issues_added",
        metadata={
            "change_set_id": "CS-recipes",
            "change_id": "change-layout-followup",
            "request": "Also show synchronization status next to each item.",
            "source_message_ids": ["message-2"],
            "issues": [
                {
                    "issue_id": "sync-status-layout",
                    "title": "Show synchronization status",
                    "lane": "prototype",
                    "acceptance_criteria": ["Every shopping item shows its synchronization state."],
                }
            ],
        },
    )["workflow"]

    assert extended["delivery"]["status"] == "stale"
    assert extended["change_set"]["route"] == "prototype_first"
    assert extended["change_set"]["gate"] == "prototype"
    assert extended["change_set"]["status"] == "changes_requested"
    assert extended["change_set"]["request_addenda"] == [
        "Also show synchronization status next to each item."
    ]
    assert extended["change_set"]["member_change_ids"][-1] == "change-layout-followup"


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
    assert completed["automation"]["snapshot_task_id"] == "task.1"
    assert completed["capabilities"]["can_prepare_candidate"] is True
    assert completed["capabilities"]["can_publish"] is False

    trial = service.transition(
        "scenario",
        "recipes",
        "candidate_prepared",
        metadata={
            "candidate_id": "recipes-0-1-1-abc",
            "release": "recipes@0.1.1",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
            "base_release": "recipes@0.1.0",
            "trial_workspace": "trials/recipes/workspace",
        },
    )["workflow"]
    assert trial["delivery"]["status"] == "trial"
    assert trial["capabilities"]["can_decide_candidate"] is True

    accepted = service.transition(
        "scenario",
        "recipes",
        "candidate_accepted",
        metadata={"candidate_id": "recipes-0-1-1-abc"},
    )["workflow"]
    assert accepted["delivery"]["status"] == "accepted"
    assert accepted["capabilities"]["can_publish"] is True

    published = service.transition(
        "scenario",
        "recipes",
        "publish",
        metadata={
            "version": "0.1.1",
            "task_id": "task.1",
            "candidate_id": "recipes-0-1-1-abc",
        },
    )["workflow"]
    assert published["active_phase"] == "automation"
    assert published["publication"]["current_version"] == "0.1.1"
    assert published["publication"]["status"] == "published"

    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    assert persisted["workflow_state"] == "automation"
    assert [item["action"] for item in persisted["workflow"]["history"]] == [
        "automation_started",
        "automation_completed",
        "candidate_prepared",
        "candidate_accepted",
        "publish",
    ]


def test_invalid_cross_phase_transition_is_rejected(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project

    with pytest.raises(BuilderWorkflowError, match="requires active automation"):
        service.transition("scenario", "recipes", "publish", metadata={"version": "0.1.1"})


def test_new_automation_iteration_reopens_a_terminal_result(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})
    service.transition(
        "scenario",
        "recipes",
        "automation_failed",
        metadata={"task_id": "task.1", "error": "schema mismatch"},
    )

    resumed = service.transition(
        "scenario",
        "recipes",
        "automation_iteration_started",
        metadata={"task_id": "task.2"},
    )["workflow"]

    assert resumed["active_phase"] == "automation"
    assert resumed["automation"]["status"] == "working"
    assert resumed["automation"]["iteration"] == 2
    assert resumed["automation"]["head_task_id"] == "task.2"
    assert resumed["automation"]["error"] is None


def test_return_to_prototype_uses_a_new_immutable_revision(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.1", "snapshot_path": "retained/automation"},
    )
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
    assert returned["capabilities"]["can_preview_automation"] is True


def test_failed_prototype_adaptation_restores_completed_automation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.1", "snapshot_path": "retained/automation"},
    )
    service.transition("scenario", "recipes", "request_return_to_prototype", metadata={"task_id": "task.2"})

    recovered = service.transition(
        "scenario",
        "recipes",
        "return_to_prototype_failed",
        metadata={"task_id": "task.2", "error": "unsafe binding remained"},
    )["workflow"]

    assert recovered["active_phase"] == "automation"
    assert recovered["automation"]["status"] == "completed"
    assert recovered["automation"]["adaptation_error"] == "unsafe binding remained"
    assert recovered["pending_transition"] is None
    assert recovered["capabilities"]["can_prepare_candidate"] is True
    assert recovered["capabilities"]["can_publish"] is False
    assert recovered["capabilities"]["can_return_to_prototype"] is True


def test_new_automation_work_invalidates_an_unpublished_candidate(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})
    service.transition("scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"})
    service.transition(
        "scenario",
        "recipes",
        "candidate_prepared",
        metadata={
            "candidate_id": "candidate-1",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
        },
    )

    reopened = service.transition(
        "scenario",
        "recipes",
        "automation_iteration_started",
        metadata={"task_id": "task.2"},
    )["workflow"]

    assert reopened["delivery"]["status"] == "stale"
    assert reopened["delivery"]["stale_reason"] == "automation_iteration_started"
    assert reopened["capabilities"]["can_publish"] is False


def test_new_checkpoint_supersedes_candidate_identity(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    first = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "change-1",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )["workflow"]
    second = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "change-2",
            "package_digest": "sha256:" + "2" * 64,
            "source_revision": "b" * 40,
        },
    )["workflow"]

    assert first["delivery"]["checkpoint_change_id"] == "change-1"
    assert second["delivery"]["status"] == "checkpoint"
    assert second["delivery"]["checkpoint_change_id"] == "change-2"
    assert second["delivery"]["candidate_id"] is None


def test_checkpoint_discards_candidate_stale_only_because_automation_changed(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition("scenario", "recipes", "automation_started", metadata={"task_id": "task.1"})
    service.transition("scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"})
    service.transition(
        "scenario",
        "recipes",
        "candidate_prepared",
        metadata={
            "candidate_id": "candidate-obsolete",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_iteration_started",
        metadata={"task_id": "task.2"},
    )
    service.transition("scenario", "recipes", "automation_completed", metadata={"task_id": "task.2"})

    checkpoint = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "change-new-result",
            "package_digest": "sha256:" + "3" * 64,
            "source_revision": "a" * 40,
        },
    )["workflow"]

    assert checkpoint["delivery"]["status"] == "checkpoint"
    assert checkpoint["delivery"]["replaces_candidate_id"] is None
    assert checkpoint["delivery"]["rebase_plan"] is None


def test_stale_candidate_rebase_plan_survives_automation_and_checkpoint(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "task.initial", "source_prototype_revision": "001"},
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.initial", "version": "0.1.0"},
    )
    service.transition(
        "scenario",
        "recipes",
        "candidate_prepared",
        metadata={
            "candidate_id": "candidate-stale",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "candidate_accepted",
        metadata={"candidate_id": "candidate-stale"},
    )
    stale = service.transition(
        "scenario",
        "recipes",
        "candidate_stale",
        metadata={
            "candidate_id": "candidate-stale",
            "rebase_plan": {
                "stale_reason": "base_release_moved",
                "target_base_release": "recipes@0.1.1",
            },
        },
    )["workflow"]
    assert stale["delivery"]["status"] == "stale"

    service.transition(
        "scenario",
        "recipes",
        "automation_iteration_started",
        metadata={"task_id": "task.reapply"},
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.reapply", "version": "0.1.2"},
    )
    checkpoint = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "checkpoint-reapply",
            "package_digest": "sha256:" + "3" * 64,
            "source_revision": "a" * 40,
        },
    )["workflow"]

    assert checkpoint["delivery"]["status"] == "checkpoint"
    assert checkpoint["delivery"]["replaces_candidate_id"] == "candidate-stale"
    assert checkpoint["delivery"]["rebase_plan"]["target_base_release"] == "recipes@0.1.1"


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
