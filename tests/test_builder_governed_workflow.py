from __future__ import annotations

import copy
import io
import json
import shutil
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.domain.artifact_release import ArtifactSourceRef, WorkspaceLock
from adaos.services.artifact_pipeline import BuiltArtifactPackage, build_artifact_package
from adaos.services.builder.governed import (
    builder_change_definition,
    compiled_builder_change_definition,
)
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService
from adaos.services.governed_workflow import definition_review_report, export_statechart
from adaos.services.governed_workflow import workflow_definition_digest


ABI_ROOT = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi"


def _source_ref() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/builder_skill/",),
    )


def _build_builder_package(
    tmp_path: Path,
    definition: dict[str, object],
) -> tuple[BuiltArtifactPackage, Path]:
    version = str(definition["definition_version"])
    root = tmp_path / "builder_packages" / version
    root.mkdir(parents=True, exist_ok=True)
    (root / "skill.yaml").write_text(
        "name: builder_skill\n"
        f"version: {version}\n"
        "workflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )
    (root / "workflow.json").write_text(
        json.dumps(definition, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return (
        build_artifact_package(root, kind="skill", source_ref=_source_ref()),
        root,
    )


def _activate_builder_package(
    workspace: Path,
    built: BuiltArtifactPackage,
) -> None:
    target = workspace / "skills" / "builder_skill"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(built.archive_bytes)) as archive:
        archive.extract("skill.yaml", target)
        archive.extract("workflow.json", target)
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-08-04T00:00:00+00:00",
        components=(built.ref,),
    )
    lock_path = workspace / ".adaos" / "workspace.lock.json"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path.write_text(
        json.dumps(lock.to_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _service(tmp_path: Path) -> BuilderWorkflowService:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    root = scenarios / "recipes"
    skills.mkdir(exist_ok=True)
    root.mkdir(parents=True, exist_ok=True)
    (root / "scenario.yaml").write_text("id: recipes\nversion: 0.1.0\n", encoding="utf-8")
    revisions = root / "ui_revisions"
    revisions.mkdir(exist_ok=True)
    (revisions / "001.json").write_text("{}", encoding="utf-8")
    (revisions / "current.txt").write_text("001\n", encoding="utf-8")
    return BuilderWorkflowService(skills, scenarios, tmp_path / "state")


def _plan(service: BuilderWorkflowService, *, lane: str = "prototype") -> dict[str, object]:
    return service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-recipes",
            "request": "Add favorites to recipes.",
            "issues": [
                {
                    "issue_id": "favorites",
                    "title": "Add favorites",
                    "lane": lane,
                    "acceptance_criteria": ["Favorites can be selected."],
                }
            ],
        },
    )["workflow"]


def test_normative_builder_definition_is_compiled_and_explainable() -> None:
    compiled = compiled_builder_change_definition()
    report = definition_review_report(compiled)
    graph = export_statechart(compiled)

    assert builder_change_definition()["metadata"]["planes"] == [
        "change",
        "artifact_lineage",
        "run",
        "view",
    ]
    assert report["unreachable_states"] == []
    assert set(report["terminal_states"]) == {"cancelled", "published", "superseded"}
    assert {"prototype_editing", "automation_waiting", "trial_waiting", "trial_review", "publication_ready"} <= {
        item["id"] for item in graph["states"]
    }
    assert all(
        item.descriptor["authority"].get("roles") == ["registered"]
        for item in compiled.transitions
    )


def test_builder_projection_exposes_process_and_project_workflow_inspection(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    described = service.describe("scenario", "recipes")

    process = described["workflow_inspection"]["process"]
    project = described["workflow_inspection"]["project"]
    assert process["status"] == "admitted"
    assert process["validation"]["valid"] is True
    assert process["validation"]["metrics"]["transitions"] > 0
    assert process["binding"]["binding_digest"].startswith("sha256:")
    assert project["status"] == "not_declared"


def test_legacy_builder_instance_gets_digest_binding_without_losing_history(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _plan(service)
    state_path = service.dev_scenarios_root / "recipes" / "prompt_state.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    governed = state["workflow"]["governed"]
    original_history = list(governed["history"])
    governed.pop("definition_digest")
    state_path.write_text(json.dumps(state), encoding="utf-8")

    described = service.describe("scenario", "recipes")

    assert described["governed"]["history"] == original_history
    assert described["governed"]["definition_digest"] == workflow_definition_digest(
        compiled_builder_change_definition()
    )
    assert described["governed"]["context"]["legacy_definition_binding"]["status"] == "adopted"


def test_dev_builder_skill_workflow_is_runtime_authority(tmp_path: Path) -> None:
    service = _service(tmp_path)
    builder_skill = service.dev_skills_root / "builder_skill"
    builder_skill.mkdir()
    (builder_skill / "skill.yaml").write_text(
        "name: builder_skill\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )
    definition = builder_change_definition()
    definition["definition_version"] = "1.0.1"
    (builder_skill / "workflow.json").write_text(
        json.dumps(definition, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    described = service.describe("scenario", "recipes")

    assert described["governed"]["definition_version"] == "1.0.1"


def test_present_but_invalid_dev_builder_workflow_fails_closed(tmp_path: Path) -> None:
    service = _service(tmp_path)
    builder_skill = service.dev_skills_root / "builder_skill"
    builder_skill.mkdir()
    (builder_skill / "skill.yaml").write_text(
        "name: builder_skill\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\n",
        encoding="utf-8",
    )
    source = (
        Path(__file__).resolve().parents[1]
        / "src"
        / "adaos"
        / "services"
        / "builder"
        / "builder_change.workflow.json"
    )
    shutil.copyfile(source, builder_skill / "workflow.json")
    definition = json.loads((builder_skill / "workflow.json").read_text(encoding="utf-8"))
    definition["transitions"][0]["effect"]["activity"] = "builder.unregistered"
    (builder_skill / "workflow.json").write_text(json.dumps(definition), encoding="utf-8")

    with pytest.raises(BuilderWorkflowError, match="unregistered activity"):
        service.describe("scenario", "recipes")


def test_builder_package_cutover_requires_active_workspace_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    compatibility_service = _service(tmp_path)
    workspace = tmp_path / "workspace"
    monkeypatch.setenv("ADAOS_BUILDER_REQUIRE_ACTIVE_PACKAGE", "true")
    service = BuilderWorkflowService(
        compatibility_service.dev_skills_root,
        compatibility_service.dev_scenarios_root,
        compatibility_service.state_dir,
        workspace_root=workspace,
    )

    with pytest.raises(BuilderWorkflowError, match="active WorkspaceLock"):
        service.describe("scenario", "recipes")

    built, _source = _build_builder_package(tmp_path, builder_change_definition())
    _activate_builder_package(workspace, built)

    workflow = _plan(service)

    assert workflow["governed"]["package_digest"] == built.ref.digest
    assert (
        workflow["governed"]["binding_digest"]
        == built.ref.workflow_binding_digest
    )

    tampered = builder_change_definition()
    tampered["metadata"] = {**tampered["metadata"], "tampered": True}
    (workspace / "skills" / "builder_skill" / "workflow.json").write_text(
        json.dumps(tampered, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    restarted = BuilderWorkflowService(
        service.dev_skills_root,
        service.dev_scenarios_root,
        service.state_dir,
        workspace_root=workspace,
    )
    with pytest.raises(BuilderWorkflowError, match="differs from WorkspaceLock"):
        restarted.describe("scenario", "recipes")


def test_builder_package_cutover_migrates_restarts_and_rolls_back_in_flight_instance(
    tmp_path: Path,
) -> None:
    compatibility_service = _service(tmp_path)
    workspace = tmp_path / "workspace"
    target_definition = builder_change_definition()
    source_definition = copy.deepcopy(target_definition)
    source_definition["definition_version"] = "1.0.0"
    source_built, _source_root = _build_builder_package(tmp_path, source_definition)
    _activate_builder_package(workspace, source_built)
    service = BuilderWorkflowService(
        compatibility_service.dev_skills_root,
        compatibility_service.dev_scenarios_root,
        compatibility_service.state_dir,
        workspace_root=workspace,
        require_active_builder_package=True,
    )
    before = _plan(service)["governed"]

    target_built, _target_root = _build_builder_package(tmp_path, target_definition)
    migration = {
        "schema": "adaos.workflow.definition_migration.v1",
        "migration_id": "builder_change_1_0_to_current",
        "workflow_type": "builder.change",
        "from_definition_version": "1.0.0",
        "to_definition_version": target_definition["definition_version"],
        "allowed_source_states": ["prototype_editing"],
        "state_map": {"prototype_editing": "prototype_editing"},
        "context_set": {"builder_package_cutover": target_definition["definition_version"]},
        "context_remove": [],
        "authority": {
            "actors": ["user:local"],
            "permissions": ["workflow.definition.migrate"],
        },
        "explanation": "Move the active Builder change to its admitted package generation.",
    }

    migrated = service.migrate_in_flight_instance(
        "scenario",
        "recipes",
        source_definition=source_definition,
        target_definition=target_definition,
        migration=migration,
        expected_generation=before["generation"],
        idempotency_key="builder-cutover-current",
        target_package_digest=target_built.ref.digest,
        target_binding_digest=target_built.ref.workflow_binding_digest,
        now="2026-08-04T01:00:00+00:00",
    )
    replay = service.migrate_in_flight_instance(
        "scenario",
        "recipes",
        source_definition=source_definition,
        target_definition=target_definition,
        migration=migration,
        expected_generation=before["generation"],
        idempotency_key="builder-cutover-current",
        target_package_digest=target_built.ref.digest,
        target_binding_digest=target_built.ref.workflow_binding_digest,
        now="2026-08-04T01:00:00+00:00",
    )
    assert replay["idempotent_replay"] is True
    assert replay["instance"] == migrated["instance"]

    _activate_builder_package(workspace, target_built)
    restarted = BuilderWorkflowService(
        service.dev_skills_root,
        service.dev_scenarios_root,
        service.state_dir,
        workspace_root=workspace,
        require_active_builder_package=True,
    )
    after_restart = restarted.describe("scenario", "recipes")["governed"]
    assert after_restart == migrated["instance"]
    assert after_restart["definition_version"] == target_definition["definition_version"]
    assert after_restart["package_digest"] == target_built.ref.digest

    rolled_back = restarted.rollback_in_flight_migration(
        migrated["checkpoint_id"],
        now="2026-08-04T02:00:00+00:00",
    )
    assert rolled_back["instance"] == before
    assert restarted.rollback_in_flight_migration(
        migrated["checkpoint_id"],
        now="2026-08-04T02:00:00+00:00",
    )["idempotent_replay"] is True

    _activate_builder_package(workspace, source_built)
    restored = BuilderWorkflowService(
        service.dev_skills_root,
        service.dev_scenarios_root,
        service.state_dir,
        workspace_root=workspace,
        require_active_builder_package=True,
    ).describe("scenario", "recipes")["governed"]
    assert restored == before


def test_legacy_transition_is_admitted_and_persisted_by_canonical_statechart(tmp_path: Path) -> None:
    service = _service(tmp_path)

    planned = _plan(service)
    assert planned["governed"]["state"] == "prototype_editing"
    assert planned["workflow_description"]["state"] == "prototype_editing"

    approved = service.transition(
        "scenario", "recipes", "stabilize_prototype", metadata={"confirmed": True}
    )["workflow"]
    assert approved["governed"]["state"] == "automation_ready"

    running = service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "RUN-1", "confirmed": True},
    )["workflow"]
    assert running["governed"]["state"] == "automation_waiting"
    assert running["history"][-1]["canonical"]["command"] == "start_automation"

    restarted = _service(tmp_path).describe("scenario", "recipes")
    assert restarted["governed"] == running["governed"]
    assert restarted["workflow_description"]["state"] == "automation_waiting"


def test_process_projection_is_dependent_lineage_with_exact_preview_labels(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _plan(service, lane="automation")
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "RUN-1", "confirmed": True},
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "RUN-1", "version": "0.2.0", "snapshot_path": "snapshot"},
    )
    workflow = service.describe("scenario", "recipes")
    process = workflow["process"]

    schema = json.loads(
        (ABI_ROOT / "builder.process_projection.v1.schema.json").read_text(encoding="utf-8")
    )
    Draft202012Validator(schema).validate(process)
    by_kind = {item["kind"]: item for item in process["nodes"]}
    assert by_kind["prototype"]["parent_ref"] == by_kind["change"]["ref"]
    assert by_kind["automation"]["parent_ref"] == by_kind["prototype"]["ref"]
    assert by_kind["automation"]["source_ref"] == by_kind["prototype"]["ref"]
    assert {item["label"].split(":", 1)[0] for item in process["preview_options"]} == {
        "proto",
        "active",
    }


def test_view_selection_does_not_advance_canonical_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    planned = _plan(service)
    canonical_generation = planned["governed"]["generation"]

    updated = service.update_interaction_context(
        "scenario",
        "recipes",
        {"inspected_ref": "prototype:recipes:001", "preview_target": "proto:recipes:001"},
        expected_generation=planned["generation"],
    )["workflow"]

    assert updated["generation"] == planned["generation"] + 1
    assert updated["governed"]["generation"] == canonical_generation
    assert updated["governed"]["state"] == "prototype_editing"
    assert updated["process"]["preview_target"] == "proto:recipes:001"


def test_builder_actions_and_shared_interaction_are_bound_to_canonical_generation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = _plan(service)

    frame = service.interaction_frame("scenario", "recipes")
    approve = next(item for item in frame["actions"] if item["command"] == "builder.prototype.approve")
    assert approve["workflow_command"] == "accept_prototype"
    assert approve["workflow_generation"] == workflow["governed"]["generation"]

    interaction = service.conversation_interaction(
        "scenario",
        "recipes",
        conversation_id="conversation.builder.recipes",
        principal_id="user:local",
        command_context_id="webspace:dev1-dev",
    )
    by_command = {item["command"]: item for item in interaction["actions"]}
    assert "accept_prototype" in by_command
    assert by_command["accept_prototype"]["expected_generation"] == workflow["governed"]["generation"]
    assert by_command["accept_prototype"]["command_context_ref"]["id"] == "webspace:dev1-dev"


def test_compact_explanation_answers_state_reason_and_next_from_canonical_snapshot(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    workflow = _plan(service)

    explanation = service.compact_explanation("scenario", "recipes")
    frame = service.interaction_frame("scenario", "recipes")

    assert explanation["state"] == "prototype_editing"
    assert explanation["generation"] == workflow["governed"]["generation"]
    assert explanation["change_ref"] == "change:CH-recipes"
    assert "accept_prototype" in explanation["next_commands"]
    assert "Why:" in explanation["text"]
    assert "Next:" in explanation["text"]
    assert frame["message"] == explanation["text"]
    assert frame["status"]["workflow_state"] == explanation["state"]
    assert frame["status"]["next_commands"] == explanation["next_commands"]
