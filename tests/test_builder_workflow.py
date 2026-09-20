from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.governed import builder_change_definition
from adaos.services.builder.workflow import (
    BuilderWorkflowError,
    BuilderWorkflowService,
    _replace_path,
    _stable_digest,
)


ABI_ROOT = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi"


def _apply_evidence(*, draft_id: str = "draft.recipes") -> dict[str, object]:
    return {
        "draft_ref": {"draft_id": draft_id, "revision": "ui:001"},
        "validation_evidence": [
            {"type": "test_run", "id": "tests:passed", "status": "passed"}
        ],
        "approval": {
            "approval_id": "pa.builder.publish.1",
            "actor_id": "user:owner",
            "actor_type": "user",
            "approved_at": "2026-08-05T00:00:00+00:00",
            "policy_evidence": [{"policy": "builder.publish", "decision": "allow"}],
        },
        "activation": {
            "operation_id": "activate.1",
            "runtime_slot": "B",
            "health_receipt": {"status": "passed"},
        },
        "rollback": {"mode": "slot_switch", "operation_ref": "activate.1:rollback"},
    }


def _confirmed(metadata: dict[str, object] | None = None) -> dict[str, object]:
    return {**dict(metadata or {}), "confirmed": True}


def test_prototype_acceptance_request_is_scoped_to_user_facing_prototype_criteria() -> (
    None
):
    change = {
        "request": "Implement the application and prepare Trial.",
        "request_addenda": ["Set dryRun to true on every MCP dataSource."],
        "issues": [
            {
                "issue_id": "prototype-surface",
                "title": "Provide a usable responsive list",
                "lane": "prototype",
                "status": "open",
                "structural_status": "active",
                "acceptance_criteria": [
                    "The list is usable on wide and compact layouts.",
                    "The Prototype visibly and coherently satisfies: Set dryRun to true on every MCP dataSource, as required by the read-only data-source ABI.",
                ],
            },
            {
                "issue_id": "implementation",
                "title": "Persist saved records",
                "lane": "automation",
                "status": "open",
                "structural_status": "active",
                "acceptance_criteria": ["Saving persists after restart."],
            },
            {
                "issue_id": "deferred-prototype",
                "title": "Add a chart",
                "lane": "prototype",
                "status": "deferred",
                "structural_status": "active",
                "acceptance_criteria": ["A chart is visible."],
            },
        ],
    }

    request = BuilderWorkflowService._prototype_acceptance_request(change)

    assert request == "Provide a usable responsive list"
    assert "dryRun" not in request
    assert "persists after restart" not in request
    assert "chart" not in request


def _prepare_candidate(
    service: BuilderWorkflowService,
    metadata: dict[str, object],
) -> dict[str, object]:
    current = service.describe("scenario", "recipes")
    delivery = dict(current.get("delivery") or {})
    if delivery.get("status") != "checkpoint":
        service.transition(
            "scenario",
            "recipes",
            "checkpoint_recorded",
            metadata=_confirmed(
                {
                    "change_id": "checkpoint-before-trial",
                    "package_digest": str(
                        metadata.get("package_digest") or "sha256:" + "c" * 64
                    ),
                    "source_revision": "c" * 40,
                }
            ),
        )
    service.transition(
        "scenario",
        "recipes",
        "candidate_preparation_started",
        metadata=_confirmed({"activity_attempt_id": "trial-attempt:test"}),
    )
    return service.transition(
        "scenario", "recipes", "candidate_prepared", metadata=metadata
    )["workflow"]


def _publish_candidate(
    service: BuilderWorkflowService,
    metadata: dict[str, object],
) -> dict[str, object]:
    service.transition(
        "scenario",
        "recipes",
        "publication_started",
        metadata=_confirmed({"activity_attempt_id": "publication-attempt:test"}),
    )
    return service.transition("scenario", "recipes", "publish", metadata=metadata)[
        "workflow"
    ]


@pytest.fixture
def workflow_project(tmp_path: Path) -> tuple[BuilderWorkflowService, Path]:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    skills.mkdir()
    root = scenarios / "recipes"
    root.mkdir(parents=True)
    (root / "scenario.yaml").write_text(
        "id: recipes\nversion: 0.1.0\n", encoding="utf-8"
    )
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


def _write_json_yaml(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value), encoding="utf-8")


def test_component_acceptance_resolves_owning_project_domain_packs(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    project_root = Path(service.dev_projects_root) / "applications"
    _write_json_yaml(
        project_root / "project.yaml",
        {
            "id": "applications",
            "components": {"owned": [{"ref": "scenario:recipes"}]},
            "development": {"domain_packs": ["applications.compatibility.v1"]},
        },
    )

    assert service._target_domain_packs("scenario", "recipes") == (
        "applications.compatibility.v1",
    )


def test_component_acceptance_rejects_ambiguous_project_ownership(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    for project_id in ("applications", "applications_fork"):
        _write_json_yaml(
            Path(service.dev_projects_root) / project_id / "project.yaml",
            {
                "id": project_id,
                "components": {"owned": [{"ref": "scenario:recipes"}]},
                "development": {"domain_packs": ["applications.compatibility.v1"]},
            },
        )

    with pytest.raises(BuilderWorkflowError, match="owned by multiple DEV projects"):
        service._target_domain_packs("scenario", "recipes")


def _write_minimal_conversational_package(root: Path) -> None:
    (root / "scenario.yaml").write_text(
        "id: recipes\nversion: 0.1.0\nworkflow:\n  manifest: workflow.json\nconversational:\n  manifest: conversational/manifest.yaml\n",
        encoding="utf-8",
    )
    (root / "workflow.json").write_text(
        json.dumps(builder_change_definition()), encoding="utf-8"
    )
    conv = root / "conversational"
    _write_json_yaml(
        conv / "manifest.yaml",
        {
            "schema": "adaos.conversational.package_manifest.v1",
            "package_id": "recipes",
            "package_kind": "scenario",
            "owner_ref": {"kind": "scenario", "id": "recipes"},
            "version": "0.1.0",
            "workflow_refs": [
                {
                    "workflow_type": "builder.change",
                    "definition_ref": "../workflow.json",
                    "definition_version": "1.0.0",
                    "definition_digest": None,
                }
            ],
            "files": {
                "input": "input.yaml",
                "entities": "entities.yaml",
                "examples": "examples.yaml",
                "affordances": "affordances.yaml",
                "repair": "repair.yaml",
                "output": "output.yaml",
                "stories": [],
                "locales": ["locale.en.yaml"],
            },
            "locales": ["en"],
            "default_locale": "en",
            "privacy_defaults": {
                "source_scope": "scenario",
                "runtime_overlay_scope": "user",
                "public_promotion": "requires_review",
            },
            "compiled_outputs": [],
            "compatibility_aliases": [],
        },
    )
    _write_json_yaml(
        conv / "input.yaml",
        {
            "schema": "adaos.conversational.input.v1",
            "package_id": "recipes",
            "intents": [],
            "policy": {
                "default_confidence": 0.8,
                "abstain_below": 0.55,
                "protected_action_confirmation": True,
            },
        },
    )
    _write_json_yaml(
        conv / "entities.yaml",
        {
            "schema": "adaos.conversational.entities.v1",
            "package_id": "recipes",
            "entities": [],
        },
    )
    _write_json_yaml(
        conv / "examples.yaml",
        {
            "schema": "adaos.conversational.examples.v1",
            "package_id": "recipes",
            "examples": [],
            "hard_negatives": [],
        },
    )
    _write_json_yaml(
        conv / "affordances.yaml",
        {
            "schema": "adaos.conversational.affordances.v1",
            "package_id": "recipes",
            "affordances": [],
        },
    )
    _write_json_yaml(
        conv / "repair.yaml",
        {
            "schema": "adaos.conversational.repair.v1",
            "package_id": "recipes",
            "policies": [],
        },
    )
    _write_json_yaml(
        conv / "output.yaml",
        {
            "schema": "adaos.conversational.output.v1",
            "package_id": "recipes",
            "outputs": [],
        },
    )
    _write_json_yaml(
        conv / "locale.en.yaml",
        {
            "schema": "adaos.conversational.locale.v1",
            "package_id": "recipes",
            "locale": "en",
            "messages": {},
        },
    )


def test_atomic_replace_retries_transient_windows_lock(
    monkeypatch, tmp_path: Path
) -> None:
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
    assert "workflow" not in json.loads(
        (root / "prompt_state.json").read_text(encoding="utf-8")
    )


def test_development_summary_is_bounded_and_read_only(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    state = {
        "updated_at": "2026-09-07T12:00:00Z",
        "workflow": {
            "active_phase": "prototype",
            "prototype": {"status": "working", "stable": False},
        },
    }
    path = root / "prompt_state.json"
    path.write_text(json.dumps(state), encoding="utf-8")

    summary = service.development_summary("scenario", "recipes")

    assert summary == {
        "phase": "prototype",
        "status": "working",
        "revision": "001",
        "stable": False,
        "accepted": False,
        "publication_status": "not_started",
        "updated_at": "2026-09-07T12:00:00Z",
    }
    assert json.loads(path.read_text(encoding="utf-8")) == state


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


def test_interaction_frame_projects_risk_actions_and_independent_context(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-recipes-layout",
            "request": "Rename the recipe heading.",
            "issues": [
                {
                    "issue_id": "heading-label",
                    "title": "Rename the recipe heading",
                    "lane": "prototype",
                    "acceptance_criteria": ["The heading uses the approved label."],
                }
            ],
        },
    )["workflow"]

    updated = service.update_interaction_context(
        "scenario",
        "recipes",
        {
            "inspected_ref": "run:RUN-layout",
            "preview_target": "prototype:recipes:001",
        },
        expected_generation=planned["generation"],
    )
    frame = updated["interaction_frame"]

    schema = json.loads(
        (ABI_ROOT / "builder.interaction_frame.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    Draft202012Validator(schema).validate(frame)
    assert frame["context"]["conversation_focus"] == "change:CH-recipes-layout"
    assert frame["context"]["inspected_ref"] == "run:RUN-layout"
    assert frame["context"]["preview_target"] == "prototype:recipes:001"
    assert all(
        item["expected_generation"] == frame["generation"] for item in frame["actions"]
    )
    assert {item["risk"] for item in frame["actions"]} >= {
        "read",
        "local_reversible",
        "isolated_write",
    }
    assert all(
        item["risk_policy"]["risk_class"] == item["risk"] for item in frame["actions"]
    )
    assert (
        next(
            item
            for item in frame["actions"]
            if item["command"] == "builder.prototype.approve"
        )["risk_policy"]["inline_callback"]
        == "confirm"
    )


def test_conversation_interaction_uses_localized_shared_action_registry(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-conversation-surface",
            "request": "Уточнить интерфейс карточки.",
            "issues": [
                {
                    "issue_id": "card-layout",
                    "title": "Уточнить интерфейс карточки",
                    "lane": "prototype",
                    "acceptance_criteria": [
                        "Карточка соответствует согласованному прототипу."
                    ],
                }
            ],
        },
    )

    interaction = service.conversation_interaction(
        "scenario",
        "recipes",
        conversation_id="conversation.builder.surface",
        principal_id="skill:builder_skill",
        command_context_id="thread.builder.surface",
        locale="ru-RU",
    )

    commands = [item["command"] for item in interaction["actions"]]
    values = [item["value"] for item in interaction["actions"]]
    assert values[0] == "builder.change.extend"
    assert "builder.prototype.approve" in values
    assert commands[values.index("builder.prototype.approve")] == "accept_prototype"
    assert values[-4:] == [
        "builder.process.inspect",
        "builder.project.list",
        "builder.preview.link",
        "builder.help",
    ]
    assert interaction["locale_context"]["locale"] == "ru"
    assert interaction["actions"][0]["label_ref"] == "builder.action.change_extend"
    assert interaction["actions"][0]["label"] == "Добавить требование"


def test_dependent_surface_exposes_only_registered_canonical_continuation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-automation-bridge",
            "request": "Implement deterministic recipe export.",
            "issues": [
                {
                    "issue_id": "recipe-export",
                    "title": "Implement recipe export",
                    "lane": "automation",
                    "acceptance_criteria": ["Export is covered by a test."],
                }
            ],
        },
    )

    described = service.describe("scenario", "recipes")
    frame = service.interaction_frame("scenario", "recipes")
    continuation = next(
        item
        for item in frame["actions"]
        if item["command"] == "builder.implementation.start"
    )

    assert continuation["workflow_command"] == "start_automation"
    assert continuation["workflow_generation"] == described["governed"]["generation"]
    assert continuation["target_ref"] == "change:CH-automation-bridge"
    assert continuation["risk"] == "isolated_write"
    assert described["workflow_description"]["executor_readiness"]["blocked"] == 0


def test_interaction_context_rejects_stale_generation_without_mutation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    generation = service.describe("scenario", "recipes")["generation"]
    service.update_interaction_context(
        "scenario",
        "recipes",
        {"inspected_ref": "issue:first"},
        expected_generation=generation,
    )

    with pytest.raises(BuilderWorkflowError, match="stale Builder action generation"):
        service.update_interaction_context(
            "scenario",
            "recipes",
            {"preview_target": "prototype:recipes:001"},
            expected_generation=generation,
        )

    current = service.describe("scenario", "recipes")
    assert current["interaction"]["inspected_ref"] == "issue:first"
    assert current["interaction"]["preview_target"] is None


def test_issue_split_and_merge_preserve_structural_lineage(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-ambiguous-layout",
            "request": "Improve the recipe form and its saving behavior.",
            "issues": [
                {
                    "issue_id": "ambiguous",
                    "title": "Improve the form",
                    "lane": "prototype",
                    "acceptance_criteria": ["The resulting form is approved."],
                }
            ],
        },
    )["workflow"]
    split = service.transition(
        "scenario",
        "recipes",
        "change_issue_split",
        metadata={
            "change_set_id": "CH-ambiguous-layout",
            "issue_id": "ambiguous",
            "issues": [
                {
                    "issue_id": "layout",
                    "title": "Arrange form fields",
                    "lane": "prototype",
                    "acceptance_criteria": ["The field order is approved."],
                },
                {
                    "issue_id": "save",
                    "title": "Persist the recipe",
                    "lane": "automation",
                    "acceptance_criteria": ["Saving is covered by a functional test."],
                },
            ],
        },
        expected_generation=planned["generation"],
    )["workflow"]
    issues = {item["issue_id"]: item for item in split["change"]["issues"]}
    assert issues["ambiguous"]["structural_status"] == "split"
    assert issues["ambiguous"]["superseded_by_issue_ids"] == ["layout", "save"]
    assert issues["layout"]["derived_from_issue_ids"] == ["ambiguous"]
    assert split["change"]["gate"] == "prototype"

    merged = service.transition(
        "scenario",
        "recipes",
        "change_issues_merged",
        metadata={
            "change_set_id": "CH-ambiguous-layout",
            "issue_ids": ["layout", "save"],
            "issue": {
                "issue_id": "form-delivery",
                "title": "Deliver the approved recipe form",
                "lane": "prototype",
                "acceptance_criteria": ["The approved form saves a recipe."],
            },
        },
        expected_generation=split["generation"],
    )["workflow"]
    issues = {item["issue_id"]: item for item in merged["change"]["issues"]}
    assert issues["layout"]["structural_status"] == "merged"
    assert issues["save"]["superseded_by_issue_ids"] == ["form-delivery"]
    assert issues["form-delivery"]["derived_from_issue_ids"] == ["layout", "save"]
    assert (
        service.describe("scenario", "recipes")["change"]["issues"]
        == merged["change"]["issues"]
    )


def test_context_packet_bounds_conversation_memory_and_pending_action_refs(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-context",
            "request": "Refine the recipe card.",
            "issues": [
                {
                    "issue_id": "card",
                    "title": "Refine the recipe card",
                    "lane": "prototype",
                    "acceptance_criteria": ["The card is easier to scan."],
                }
            ],
        },
    )
    from adaos.services.builder.repair import BuilderRepairService

    BuilderRepairService(state_dir=service.state_dir).report(
        project_id="recipes",
        signal_type="test_failure",
        summary="Recipe card regression failed",
        context={"test": "test_recipe_card", "artifact_id": "recipes"},
    )

    packet = service.build_context_packet(
        "scenario",
        "recipes",
        conversation_context={
            "schema": "adaos.context.packet.v1",
            "conversation_id": "conv.builder.recipes",
            "thread_id": "thread.recipes",
            "messages": [
                {
                    "id": "message-1",
                    "seq": 1,
                    "role": "user",
                    "text": "Keep the card compact.",
                    "source_ref": {
                        "type": "conversation_message",
                        "message_id": "message-1",
                    },
                    "secret_transport_field": "must not cross the Builder boundary",
                }
            ],
            "segments": [],
            "memory": [
                {
                    "id": "memory-1",
                    "scope": "conversation",
                    "owner": "skill:builder_skill",
                    "text": "The user prefers compact cards.",
                    "visibility": "owner_only",
                }
            ],
            "diagnostics": {"fallbacks": ["fts_unavailable"], "raw_debug": "omit me"},
            "raw_transcript": "must not cross the Builder boundary",
        },
        pending_action_refs=[
            {
                "id": "pending-1",
                "kind": "builder.prototype.review",
                "status": "pending",
                "domain_ref": {"object_type": "scenario", "object_id": "recipes"},
                "payload": {"secret": "omit me"},
                "allowed_actions": ["approve", "reject"],
            }
        ],
        persist=True,
    )

    assert packet["conversation"]["messages"][0]["text"] == "Keep the card compact."
    assert "secret_transport_field" not in packet["conversation"]["messages"][0]
    assert "raw_transcript" not in packet["conversation"]
    assert packet["conversation"]["memory"][0]["id"] == "memory-1"
    assert packet["pending_actions"][0]["id"] == "pending-1"
    assert "payload" not in packet["pending_actions"][0]
    assert packet["budget"]["conversation_message_count"] == 1
    assert packet["budget"]["pending_action_ref_count"] == 1
    assert packet["facets"]["workflow_definition"]["status"] == "missing"
    assert (
        packet["facets"]["workflow_definition"]["inspection_status"] == "not_declared"
    )
    assert packet["facets"]["repair_context"]["active_count"] == 1
    assert (
        packet["facets"]["repair_context"]["tasks"][0]["signal_type"] == "test_failure"
    )
    assert packet["budget"]["active_repair_count"] == 1
    assert (
        service.describe("scenario", "recipes")["context_packet"]["digest"]
        == packet["digest"]
    )


def test_context_packet_surfaces_valid_conversational_static_report(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    _write_minimal_conversational_package(root)
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-conversation-valid",
            "request": "Admit the conversation package.",
            "issues": [
                {
                    "issue_id": "conversation",
                    "title": "Admit the conversation package",
                    "lane": "prototype",
                    "acceptance_criteria": ["The package passes admission."],
                }
            ],
        },
    )

    packet = service.build_context_packet("scenario", "recipes")

    workflow_facet = packet["facets"]["workflow_definition"]
    conversational_facet = packet["facets"]["conversational_definition"]
    assert conversational_facet["status"] == "present"
    assert conversational_facet["valid"] is True
    assert conversational_facet["diagnostics"] == []
    assert conversational_facet["package_digest"].startswith("sha256:")
    assert (
        conversational_facet["static_report"]["schema"]
        == "adaos.workflow.static_report.v1"
    )
    assert (
        conversational_facet["static_report"]["definition_digest"]
        == workflow_facet["definition_digest"]
    )
    assert conversational_facet["static_report"]["coverage"]["states_declared"]


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
                    "acceptance_criteria": [
                        "Favorites is visible without hiding the shopping list."
                    ],
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
        metadata=_confirmed({"revision": "001"}),
    )["workflow"]
    assert approved["change_set"]["status"] == "approved"
    assert approved["change_set"]["gate"] == "automation"
    assert approved["change_set"]["issues"][0]["status"] == "resolved"
    assert approved["change_set"]["issues"][1]["status"] == "open"


def test_specification_delta_is_canonical_scoped_and_not_implicitly_accepted(
    workflow_project,
):
    service, root = workflow_project
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-spec",
            "request": "Create an editor",
            "source_message_ids": ["m1"],
            "issues": [
                {
                    "issue_id": "i1",
                    "title": "Editor",
                    "lane": "prototype",
                    "acceptance_criteria": ["Editor visible"],
                }
            ],
        },
    )["workflow"]
    delta = {
        "operations": [
            {
                "requirement_id": "editor",
                "operation": "add",
                "stage": "prototype",
                "text": "Open editor",
                "acceptance_criteria": ["Editor visible"],
                "issue_ids": ["i1"],
                "source_message_ids": ["m1"],
            }
        ]
    }
    saved = service.save_specification_delta(
        "scenario",
        "recipes",
        delta,
        change_id="CS-spec",
        expected_generation=planned["generation"],
        actor="user:owner",
    )
    current = service.describe("scenario", "recipes")
    assert current["change"]["specification_delta"] == saved["delta"]
    assert current["specification"]["delta"]["digest"] == saved["delta"]["digest"]
    assert current["application_specification"]["prototype"]["requirements"] == {}
    packet = service.build_context_packet("scenario", "recipes", persist=False)
    assert packet["change"]["specification_delta"]["digest"] == saved["delta"]["digest"]
    with pytest.raises(BuilderWorkflowError, match="stale"):
        service.save_specification_delta(
            "scenario",
            "recipes",
            delta,
            change_id="CS-spec",
            expected_generation=planned["generation"],
            actor="user:owner",
        )
    with pytest.raises(BuilderWorkflowError, match="another Change"):
        service.save_specification_delta(
            "scenario",
            "recipes",
            delta,
            change_id="other",
            expected_generation=current["generation"],
            actor="user:owner",
        )
    approved = service.transition(
        "scenario",
        "recipes",
        "stabilize_prototype",
        metadata=_confirmed({"revision": "001"}),
    )["workflow"]
    # A compatibility stabilization without an exact acceptance receipt is not spec acceptance.
    assert approved["application_specification"]["prototype"]["requirements"] == {}
    with pytest.raises(BuilderWorkflowError, match="editable Prototype"):
        service.save_specification_delta(
            "scenario",
            "recipes",
            delta,
            change_id="CS-spec",
            expected_generation=approved["generation"],
            actor="user:owner",
        )


def test_strict_prototype_acceptance_requires_current_behavior_and_visual_evidence(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "recipes",
                        "layout": {
                            "version": 2,
                            "pattern": "document",
                            "density": "comfortable",
                            "regions": [
                                {
                                    "id": "main",
                                    "role": "main",
                                    "presentation": {
                                        "wide": "pane",
                                        "compact": "stack",
                                    },
                                }
                            ],
                        },
                        "widgets": [],
                    }
                }
            }
        },
    }
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-strict-prototype",
            "request": "Show the recipe workspace.",
            "prototype_acceptance_required": True,
            "issues": [
                {
                    "issue_id": "prototype-layout",
                    "title": "Create the prototype layout",
                    "lane": "prototype",
                    "acceptance_criteria": ["The workspace is visible."],
                }
            ],
        },
    )["workflow"]

    with pytest.raises(BuilderWorkflowError, match="acceptance evidence is required"):
        service.transition(
            "scenario",
            "recipes",
            "stabilize_prototype",
            metadata=_confirmed({"revision": "001"}),
        )

    saved = service.save_specification_delta(
        "scenario",
        "recipes",
        {
            "operations": [
                {
                    "stage": "prototype",
                    "operation": "add",
                    "requirement_id": "workspace.visible",
                    "text": "The workspace is visible",
                    "acceptance_criteria": ["The workspace is visible"],
                    "issue_ids": ["prototype-layout"],
                }
            ]
        },
        change_id="CH-strict-prototype",
        expected_generation=planned["generation"],
        actor="user:owner",
    )
    planned = saved["workflow"]

    accepted = service.accept_prototype(
        "scenario",
        "recipes",
        reviewer={"id": "agent:codex", "kind": "agent", "delegated_by": "user:owner"},
        behavior_checks=[
            {
                "id": "render.ready",
                "status": "passed",
                "evidence_refs": ["test:prototype-render"],
            }
        ],
        visual_checks=[
            {
                "breakpoint": "compact",
                "viewport": {"width": 390, "height": 844},
                "status": "passed",
                "evidence_ref": "screenshot:.tmp/recipes-compact.png",
            },
            {
                "breakpoint": "wide",
                "viewport": {"width": 1440, "height": 900},
                "status": "passed",
                "evidence_ref": "screenshot:.tmp/recipes-wide.png",
            },
        ],
        expected_generation=planned["generation"],
    )

    workflow = accepted["workflow"]
    assert workflow["prototype"]["acceptance"]["revision"] == "001"
    assert workflow["change_set"]["gate"] == "automation"

    requirement = workflow["application_specification"]["prototype"]["requirements"][
        "workspace.visible"
    ]
    assert requirement["evidence_ref"] == accepted["acceptance"]["acceptance_id"]
    assert workflow["application_specification"]["automation"]["requirements"] == {}

    started = service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"task_id": "task.acceptance-refresh", "confirmed": True},
        expected_generation=workflow["generation"],
    )["workflow"]
    workflow = service.transition(
        "scenario",
        "recipes",
        "automation_failed",
        metadata={"task_id": "task.acceptance-refresh", "error": "fixture failure"},
        expected_generation=started["generation"],
    )["workflow"]
    assert workflow["active_phase"] == "automation"

    reaffirmed = service.accept_prototype(
        "scenario",
        "recipes",
        reviewer={"id": "agent:codex", "kind": "agent", "delegated_by": "user:owner"},
        behavior_checks=[
            {
                "id": "render.ready",
                "status": "passed",
                "evidence_refs": ["test:prototype-render-reaffirmed"],
            }
        ],
        visual_checks=[
            {
                "breakpoint": "compact",
                "viewport": {"width": 390, "height": 844},
                "status": "passed",
                "evidence_ref": "screenshot:.tmp/recipes-compact-reaffirmed.png",
            },
            {
                "breakpoint": "wide",
                "viewport": {"width": 1440, "height": 900},
                "status": "passed",
                "evidence_ref": "screenshot:.tmp/recipes-wide-reaffirmed.png",
            },
        ],
        expected_generation=workflow["generation"],
    )
    assert reaffirmed["workflow"]["governed"]["state"] == "automation_ready"
    assert reaffirmed["workflow"]["governed"]["history"][-2]["command"] == (
        "accept_review_constraint"
    )
    assert (
        reaffirmed["workflow"]["governed"]["history"][-1]["command"]
        == "accept_prototype"
    )

    webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"] = [
        {
            "id": "changed",
            "type": "ui.text",
            "area": "main",
            "inputs": {"text": "Changed"},
        }
    ]
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    with pytest.raises(BuilderWorkflowError, match="stale"):
        service.transition("scenario", "recipes", "automation_started")


@pytest.mark.parametrize("with_records", [False, True])
def test_prototype_acceptance_loads_and_binds_declared_locale_assets(
    workflow_project: tuple[BuilderWorkflowService, Path],
    monkeypatch: pytest.MonkeyPatch,
    with_records: bool,
) -> None:
    from adaos.services.ui_capabilities import evaluate_ui_request

    service, root = workflow_project
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "resources": {
                    "recipes.i18n.en": {
                        "kind": "data",
                        "role": "i18n",
                        "locale": "en",
                        "path": "assets/i18n/en.json",
                    },
                    "recipes.i18n.ru": {
                        "kind": "data",
                        "role": "i18n",
                        "locale": "ru",
                        "path": "assets/i18n/ru.json",
                    },
                },
                "desktop": {
                    "pageSchema": {
                        "id": "recipes",
                        "layout": {
                            "version": 2,
                            "pattern": "document",
                            "density": "comfortable",
                            "regions": [
                                {
                                    "id": "main",
                                    "role": "main",
                                    "presentation": {
                                        "wide": "pane",
                                        "compact": "stack",
                                    },
                                }
                            ],
                        },
                        "widgets": [],
                    }
                },
            }
        },
    }
    snapshots = []
    if with_records:
        for name in ("entries", "categories"):
            resource_type = f"prototype.{name}"
            webui["ui"]["application"]["desktop"]["pageSchema"]["widgets"].append(
                {
                    "id": name,
                    "type": "ui.table",
                    "area": "main",
                    "dataSource": {
                        "kind": "resourceQuery",
                        "resourceType": resource_type,
                    },
                    "inputs": {"columns": [{"key": "title", "label": "Title"}]},
                    "actions": [
                        {
                            "on": "click:edit",
                            "type": "resourceOperation",
                            "target": resource_type,
                            "params": {
                                "operation_id": "update",
                                "record_id": "$event.id",
                                "payload": {"title": "Edited"},
                            },
                        }
                    ],
                }
            )
            snapshots.append(
                {
                    "resource_type": resource_type,
                    "generation": 0,
                    "record_count": 1,
                    "records": [{"id": name, "title": name}],
                    **{
                        key: "sha256:" + "a" * 64
                        for key in (
                            "definition_digest",
                            "bundle_digest",
                            "records_digest",
                        )
                    },
                }
            )
    monkeypatch.setattr(
        BuilderWorkflowService,
        "_prototype_resource_snapshots",
        lambda self, **kwargs: list(snapshots),
    )
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    locales = root / "assets" / "i18n"
    locales.mkdir(parents=True)
    (locales / "en.json").write_text(
        json.dumps({"recipes.title": "Recipes"}), encoding="utf-8"
    )
    (locales / "ru.json").write_text(
        json.dumps({"recipes.title": "Рецепты"}, ensure_ascii=False),
        encoding="utf-8",
    )
    observed: dict[str, object] = {}

    def evaluate(*args: object, **kwargs: object) -> dict[str, object]:
        observed["locale_dictionaries"] = kwargs.get("locale_dictionaries")
        observed["prototype_resources"] = kwargs.get("prototype_resources")
        result = evaluate_ui_request(*args, **kwargs)
        if with_records:
            assert result["qualification"]["requirements"]["prototype_resource"] is True
        return result

    monkeypatch.setattr(
        "adaos.services.builder.prototype_acceptance.evaluate_ui_request",
        evaluate,
    )
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-localized-prototype",
            "request": "List entries and edit a selected entry."
            if with_records
            else "Show the recipe workspace.",
            "prototype_acceptance_required": True,
            "issues": [
                {
                    "issue_id": "localized-prototype",
                    "title": "Provide the localized prototype",
                    "lane": "prototype",
                    "acceptance_criteria": [
                        "English and Russian resources are present."
                    ],
                }
            ],
        },
    )["workflow"]
    accepted = service.accept_prototype(
        "scenario",
        "recipes",
        reviewer={"id": "agent:codex", "kind": "agent", "delegated_by": "user:owner"},
        behavior_checks=[
            {
                "id": "render.ready",
                "status": "passed",
                "evidence_refs": ["test:prototype-render"],
            }
        ],
        visual_checks=[
            {
                "breakpoint": "compact",
                "viewport": {"width": 390, "height": 844},
                "status": "passed",
                "evidence_ref": "screenshot:recipes-compact.png",
            },
            {
                "breakpoint": "wide",
                "viewport": {"width": 1440, "height": 900},
                "status": "passed",
                "evidence_ref": "screenshot:recipes-wide.png",
            },
        ],
        expected_generation=planned["generation"],
    )

    assert observed["locale_dictionaries"] == {
        "en": {"recipes.title": "Recipes"},
        "ru": {"recipes.title": "Рецепты"},
    }
    assert observed["prototype_resources"] == snapshots
    resources = accepted["workflow"]["prototype"]["acceptance"]["prototype_resources"]
    assert resources[-1]["resource_type"] == "prototype.locale_dictionaries"
    assert len(resources) == len(snapshots) + 1
    history_acceptance = accepted["workflow"]["history"][-1]["metadata"]["acceptance"]
    assert set(history_acceptance) == {"acceptance_id", "revision", "digest"}

    (locales / "ru.json").write_text(
        json.dumps({"recipes.title": "Каталог рецептов"}, ensure_ascii=False),
        encoding="utf-8",
    )
    with pytest.raises(BuilderWorkflowError, match="stale: prototype_resources"):
        service.transition("scenario", "recipes", "automation_started")


@pytest.mark.parametrize(
    "owner_ref,owned,accepted",
    [
        ("scenario:recipes", True, True),
        ("project:catalog", True, True),
        ("project:catalog", False, False),
        ("scenario:another", True, False),
    ],
)
def test_prototype_resource_snapshot_resolves_only_manifest_proven_application_ownership(
    workflow_project,
    monkeypatch,
    owner_ref,
    owned,
    accepted,
):
    from adaos.services.resources.prototype import PrototypeResourceService

    service, root = workflow_project
    project_root = service.dev_projects_root / "catalog"
    project_root.mkdir(parents=True)
    (project_root / "project.yaml").write_text(
        json.dumps(
            {
                "id": "catalog",
                "components": {
                    "owned": [
                        {"ref": "scenario:recipes" if owned else "scenario:another"}
                    ],
                    "dependencies": [{"ref": "scenario:recipes"}],
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        PrototypeResourceService,
        "definition",
        lambda self, ref: {"metadata": {"project_ref": owner_ref}},
    )
    calls = []
    monkeypatch.setattr(
        PrototypeResourceService,
        "acceptance_snapshots",
        lambda self, **kwargs: calls.append(kwargs) or [],
    )
    inputs = dict(
        object_type="scenario",
        object_id="recipes",
        change_id="change",
        revision="001",
        webui={
            "source": {"kind": "resourceQuery", "resourceType": "prototype.example"}
        },
        webui_digest="digest",
    )
    if accepted:
        service._prototype_resource_snapshots(**inputs)
        assert calls == [
            {
                "project_ref": owner_ref,
                "change_id": "change",
                "revision": "001",
                "webui_digest": "digest",
                "resource_types": ["prototype.example"],
            }
        ]
    else:
        with pytest.raises(BuilderWorkflowError, match="owner"):
            service._prototype_resource_snapshots(**inputs)
        assert not calls


def test_optional_prototype_acceptance_is_preserved_for_automation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    webui = {
        "schema": "adaos.webui.v1",
        "ui": {
            "application": {
                "desktop": {
                    "pageSchema": {
                        "id": "recipes",
                        "layout": {
                            "version": 2,
                            "pattern": "document",
                            "density": "comfortable",
                            "regions": [
                                {
                                    "id": "main",
                                    "role": "main",
                                    "presentation": {
                                        "wide": "pane",
                                        "compact": "stack",
                                    },
                                }
                            ],
                        },
                        "widgets": [],
                    }
                }
            }
        },
    }
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    planned = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-optional-acceptance",
            "request": "Show the recipe workspace.",
            "issues": [
                {
                    "issue_id": "prototype-layout",
                    "title": "Show the recipe workspace",
                    "lane": "prototype",
                    "acceptance_criteria": ["The workspace is visible."],
                }
            ],
        },
    )["workflow"]

    accepted = service.accept_prototype(
        "scenario",
        "recipes",
        reviewer={"id": "agent:codex", "kind": "agent", "delegated_by": "user:owner"},
        behavior_checks=[
            {
                "id": "render.ready",
                "status": "passed",
                "evidence_refs": ["test:prototype-render"],
            }
        ],
        visual_checks=[
            {
                "breakpoint": "compact",
                "viewport": {"width": 390, "height": 844},
                "status": "passed",
                "evidence_ref": "screenshot:.tmp/recipes-compact.png",
            },
            {
                "breakpoint": "wide",
                "viewport": {"width": 1440, "height": 900},
                "status": "passed",
                "evidence_ref": "screenshot:.tmp/recipes-wide.png",
            },
        ],
        expected_generation=planned["generation"],
    )

    assert accepted["workflow"]["prototype"]["acceptance_required"] is False
    admitted = service.require_current_prototype_acceptance("scenario", "recipes")
    assert admitted is not None
    assert admitted["digest"] == accepted["acceptance"]["digest"]


def test_automation_followup_reuses_immutable_acceptance_after_source_changes(
    workflow_project: tuple[BuilderWorkflowService, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, _root = workflow_project
    acceptance = {
        "acceptance_id": "acceptance:recipes:004",
        "project_ref": "scenario:recipes",
        "change_id": "CH-recipes-followup",
        "revision": "004",
        "webui_digest": "sha256:" + "1" * 64,
        "decision": "accepted",
        "digest": "sha256:" + "2" * 64,
    }
    monkeypatch.setattr(
        "adaos.services.builder.prototype_acceptance.admit_prototype_acceptance",
        lambda value, **_kwargs: dict(value),
    )
    workflow = {
        "active_phase": "automation",
        "prototype": {
            "head_revision": "004",
            "stable": True,
            "acceptance_required": False,
            "acceptance": acceptance,
        },
        "automation": {"status": "completed"},
        "change": {
            "change_id": "CH-recipes-followup",
            "change_set_id": "CH-recipes-followup",
            "request": "Continue the accepted implementation.",
        },
    }
    monkeypatch.setattr(BuilderWorkflowService, "describe", lambda *_args: workflow)

    admitted = service.require_current_prototype_acceptance("scenario", "recipes")

    assert admitted is not None
    assert admitted["digest"] == acceptance["digest"]


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
        metadata=_confirmed({"revision": "001", "run_id": "RUN-approve-search"}),
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


def test_large_context_packet_is_externalized_and_hydrated(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    packet_body = {
        "schema": "adaos.builder.context_packet.v1",
        "payload": "x" * (400 * 1024),
    }
    packet = {
        **packet_body,
        "digest": _stable_digest(packet_body),
        "built_at": "2026-09-08T00:00:00+00:00",
    }
    service._write_state(
        "scenario",
        "recipes",
        {"workflow": {"context_packet": packet}},
    )

    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    workflow = persisted["workflow"]
    assert workflow["context_packet"] is None
    assert workflow["context_packet_external"] == {
        "schema": "adaos.builder.context_packet_external.v1",
        "digest": packet["digest"],
    }
    assert (
        service._read_state("scenario", "recipes")["workflow"]["context_packet"]
        == packet
    )

    packet_path = service._context_packet_path("scenario", "recipes", packet["digest"])
    tampered = {**packet, "payload": "y" + packet["payload"][1:]}
    packet_path.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(BuilderWorkflowError, match="identity differs"):
        service._read_state("scenario", "recipes")


def test_change_run_ledger_is_externalized_and_hydrated(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    runs = [
        {
            "schema": "adaos.builder.run.v1",
            "run_id": f"run-{index:03d}",
            "status": "succeeded",
            "evidence_refs": ["evidence:" + ("x" * 2048)],
        }
        for index in range(75)
    ]
    change = {
        "schema": "adaos.builder.change.v1",
        "change_id": "CH-large-run-ledger",
        "runs": runs,
    }

    service._write_state(
        "scenario",
        "recipes",
        {"workflow": {"change": change}},
    )

    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    stored_change = persisted["workflow"]["change"]
    assert stored_change["runs"] == []
    assert stored_change["runs_external"] == {
        "schema": "adaos.builder.change_runs_external.v1",
        "digest": _stable_digest({"runs": runs}),
        "count": len(runs),
    }
    assert (
        service._read_state("scenario", "recipes")["workflow"]["change"]["runs"] == runs
    )

    ledger_path = service._change_run_ledger_path(
        "scenario",
        "recipes",
        change["change_id"],
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["runs"][0]["status"] = "failed"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(BuilderWorkflowError, match="identity differs"):
        service._read_state("scenario", "recipes")


def test_governed_ledger_is_externalized_and_hydrated(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-governed-ledger",
            "request": "Build the accepted prototype.",
            "source_message_ids": ["message-1"],
            "issues": [
                {
                    "issue_id": "issue-governed-ledger",
                    "title": "Preserve the governed workflow ledger.",
                    "lane": "prototype",
                    "acceptance_criteria": ["The ledger remains readable."],
                }
            ],
        },
    )
    state = service._read_state("scenario", "recipes")
    governed = state["workflow"]["governed"]
    history = [dict(governed["history"][0]) for _ in range(120)]
    idempotency = [dict(governed["idempotency"][0]) for _ in range(120)]
    governed["history"] = history
    governed["idempotency"] = idempotency

    service._write_state("scenario", "recipes", state)

    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    workflow = persisted["workflow"]
    assert workflow["governed"]["history"] == []
    assert workflow["governed"]["idempotency"] == []
    assert workflow["governed_ledger_external"] == {
        "schema": "adaos.builder.governed_ledger_external.v1",
        "digest": _stable_digest({"history": history, "idempotency": idempotency}),
        "history_count": len(history),
        "idempotency_count": len(idempotency),
    }
    hydrated = service._read_state("scenario", "recipes")["workflow"]["governed"]
    assert hydrated["history"] == history
    assert hydrated["idempotency"] == idempotency

    ledger_path = service._governed_ledger_path(
        "scenario",
        "recipes",
        "CH-governed-ledger",
    )
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["history"][0]["transition_id"] = "tampered"
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    with pytest.raises(BuilderWorkflowError, match="identity differs"):
        service._read_state("scenario", "recipes")


def test_context_packet_execution_scope_excludes_unrelated_change_history(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    (root / "scenario.yaml").write_text(
        "id: recipes\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-scoped",
            "request": "Old broad implementation request.",
            "source_message_ids": ["dticket.old", "dticket.current"],
            "issues": [
                {
                    "issue_id": "automation-followup-dticket.old",
                    "title": "Old issue",
                    "lane": "automation",
                    "acceptance_criteria": ["Old behavior changes."],
                    "source_message_ids": ["dticket.old"],
                },
                {
                    "issue_id": "automation-followup-dticket.current",
                    "title": "Current issue",
                    "lane": "automation",
                    "acceptance_criteria": ["Current behavior changes."],
                    "source_message_ids": ["dticket.current"],
                },
            ],
        },
    )

    packet = service.build_context_packet(
        "scenario",
        "recipes",
        execution_scope={
            "source_message_ids": ["dticket.current"],
            "repair_ids": ["repair.current"],
            "intent": "Apply only the current repair package.",
        },
    )

    assert packet["change"]["intent"] == "Apply only the current repair package."
    assert packet["change"]["source_message_ids"] == ["dticket.current"]
    assert [item["issue_id"] for item in packet["change"]["issues"]] == [
        "automation-followup-dticket.current"
    ]
    assert packet["change"]["request_addenda"] == []
    assert packet["execution_scope"] == {
        "source_message_ids": ["dticket.current"],
        "repair_ids": ["repair.current"],
        "issue_scope": "change",
        "active": True,
    }
    assert packet["budget"]["issue_count"] == 1


def test_context_packet_current_iteration_does_not_replay_change_portfolio(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CH-iteration",
            "request": "Old broad request.",
            "source_message_ids": ["message.old"],
            "issues": [
                {
                    "issue_id": "CH-older:I01",
                    "title": "Historical accepted work",
                    "lane": "automation",
                    "status": "resolved",
                    "acceptance_criteria": ["Do not replay this old criterion."],
                    "source_message_ids": ["message.old"],
                }
            ],
        },
    )

    packet = service.build_context_packet(
        "scenario",
        "recipes",
        execution_phase="automation",
        execution_scope={
            "intent": "Validate the current source only.",
            "issue_scope": "current_iteration",
        },
    )

    assert packet["change"]["change_id"] == "CH-iteration"
    assert packet["change"]["intent"] == "Validate the current source only."
    assert packet["change"]["issues"] == []
    assert packet["change"]["source_message_ids"] == []
    assert packet["change"]["request_addenda"] == []
    assert packet["facets"]["constraints"]["issue_acceptance"] == []
    assert packet["execution_scope"] == {
        "source_message_ids": [],
        "repair_ids": [],
        "issue_scope": "current_iteration",
        "active": True,
    }
    assert packet["budget"]["issue_count"] == 0


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
                    "change": {
                        **legacy,
                        "change_id": "CH-new",
                        "change_set_id": "CH-new",
                    },
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
    service.transition(
        "scenario", "recipes", "automation_started", metadata={"task_id": "task.1"}
    )

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
                    "acceptance_criteria": [
                        "A failed request leaves the local list unchanged."
                    ],
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
        metadata=_confirmed(
            {"task_id": "task.sync", "change_id": "change-sync-implementation"}
        ),
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
    service, root = workflow_project
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
                    "acceptance_criteria": [
                        "Synchronization is covered by an integration test."
                    ],
                }
            ],
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed(
            {"task_id": "task.sync", "change_id": "change-sync-implementation"}
        ),
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
    task_runs = [
        item for item in completed["change"]["runs"] if item["run_id"] == "task.sync"
    ]
    assert len(task_runs) == 1
    assert task_runs[0]["status"] == "succeeded"
    assert task_runs[0]["activity"] == "automation_completed"
    assert not any(
        item["activity"] == "automation_started" and item["status"] == "running"
        for item in completed["change"]["runs"]
    )

    checkpointed = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata=_confirmed(
            {
                "change_id": "checkpoint-sync",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "a" * 40,
            }
        ),
    )["workflow"]
    assert checkpointed["change_set"]["status"] == "checkpointed"
    assert "checkpoint-sync" in checkpointed["change_set"]["member_change_ids"]

    trial = _prepare_candidate(
        service,
        {
            "candidate_id": "candidate-sync",
            "release_digest": "sha256:" + "2" * 64,
            "package_digest": "sha256:" + "3" * 64,
        },
    )
    assert trial["change_set"]["status"] == "trial"
    Draft202012Validator(
        json.loads(
            (ABI_ROOT / "builder.process_projection.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
    ).validate(trial["process"])
    trial_preview = next(
        item for item in trial["process"]["preview_options"] if item["kind"] == "trial"
    )
    assert trial_preview["label"] == "trial:recipes:candidate-sync"
    trial_action = next(
        item
        for item in service.interaction_frame("scenario", "recipes")["actions"]
        if item["command"] == "builder.preview.trial"
    )
    assert trial_action["target_ref"] == trial_preview["label"]

    service.transition(
        "scenario",
        "recipes",
        "candidate_accepted",
        metadata={
            "candidate_id": "candidate-sync",
            "candidate_digest": "sha256:" + "3" * 64,
        },
    )
    published = _publish_candidate(
        service,
        {
            "candidate_id": "candidate-sync",
            "candidate_digest": "sha256:" + "3" * 64,
            "version": "0.2.0",
            "apply_evidence": _apply_evidence(),
        },
    )
    assert published["change_set"]["status"] == "published"
    assert published["change_set"]["gate"] == "complete"
    assert published["governed"]["state"] == "published"
    assert published["capabilities"]["can_plan_change_set"] is True
    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    persisted["workflow"].pop("governed", None)
    (root / "prompt_state.json").write_text(
        json.dumps(persisted, ensure_ascii=False),
        encoding="utf-8",
    )
    hydrated = service.describe("scenario", "recipes")
    assert hydrated["governed"]["state"] == "published"
    explanation = service.compact_explanation("scenario", "recipes")
    assert explanation["state"] == "published"
    assert explanation["next_commands"] == [
        "builder.publication.place",
        "builder.process.inspect",
        "builder.change.plan",
        "builder.project.list",
        "builder.help",
    ]
    assert explanation["installed"] is True
    frame = service.interaction_frame("scenario", "recipes")
    assert frame["message"] == explanation["text"]
    assert [item["command"] for item in frame["actions"]] == [
        "builder.publication.place",
        "builder.process.inspect",
        "builder.change.plan",
        "builder.project.list",
        "builder.help",
    ]
    process_by_kind = {item["kind"]: item for item in hydrated["process"]["nodes"]}
    assert process_by_kind["trial"]["status"] == "accepted"
    process_ru = service.process_explanation("scenario", "recipes", locale="ru")
    assert "Прототип:" in process_ru["text"]
    assert "зафиксировано" in process_ru["text"]
    assert "Апробация:" in process_ru["text"]
    assert "принято" in process_ru["text"]

    placed = service.record_project_placement(
        "scenario",
        "recipes",
        {
            "kind": "stable",
            "result_ref": {
                "kind": "release",
                "id": "scenario:recipes",
                "version": "0.2.0",
            },
            "target": {"webspace_id": "desktop", "space_kind": "workspace"},
            "scenario_id": "recipes",
        },
        expected_generation=hydrated["generation"],
    )["workflow"]
    placed_frame = service.interaction_frame("scenario", "recipes", locale="ru")
    assert placed["project"]["stable_placement_ref"]
    assert placed_frame["actions"][0]["command"] == "builder.publication.open"
    assert "builder.preview.link" not in {
        item["command"] for item in placed_frame["actions"]
    }
    assert any(item["kind"] == "placement" for item in placed["process"]["nodes"])


@pytest.mark.parametrize(
    "changed",
    [
        None,
        "candidate",
        "digest",
        "target",
        "runtime",
        "activation",
        "safety",
        "status",
    ],
)
@pytest.mark.parametrize("data_mode", ["empty", "snapshot"])
def test_placement_replay_is_noop_only_for_exact_result(
    workflow_project, changed, data_mode
):
    service, root = workflow_project
    generation = service.describe("scenario", "recipes")["generation"]
    placement = {
        "kind": "trial",
        "result_ref": {
            "kind": "candidate",
            "id": "candidate-a",
            "digest": "sha256:" + "a" * 64,
        },
        "target": {"webspace_id": "desktop-dev", "space_kind": "development"},
        "runtime_binding": {
            "kind": "isolated_trial_workspace",
            "path": "trials/candidate-a",
        },
        "trial_activation_ref": "activation-a",
        "safety": {"status": "verified"},
        "data_mode": data_mode,
    }
    first = service.record_project_placement(
        "scenario", "recipes", placement, expected_generation=generation
    )
    persisted = (root / "prompt_state.json").read_bytes()
    events = []
    service.event_sink = events.append
    if changed == "candidate":
        placement["result_ref"]["id"] = "candidate-b"
    elif changed == "digest":
        placement["result_ref"]["digest"] = "sha256:" + "b" * 64
    elif changed == "target":
        placement["target"]["webspace_id"] = "different-dev"
    elif changed == "runtime":
        placement["runtime_binding"]["path"] = "trials/candidate-b"
    elif changed == "activation":
        placement["trial_activation_ref"] = "activation-b"
    elif changed == "safety":
        placement["safety"] = {}
    elif changed == "status":
        placement["status"] = "detached"
    if changed:
        with pytest.raises(
            BuilderWorkflowError, match="stale Builder workflow generation"
        ):
            service.record_project_placement(
                "scenario", "recipes", placement, expected_generation=generation
            )
    else:
        placement["updated_at"] = "2026-09-15T00:00:00+00:00"
        replay = service.record_project_placement(
            "scenario", "recipes", placement, expected_generation=generation
        )
        assert replay["duplicate"] is True
        assert replay["placement"] == first["placement"]
        assert replay["workflow"]["generation"] == first["workflow"]["generation"]
    assert (root / "prompt_state.json").read_bytes() == persisted
    assert events == []


def test_builder_text_continuation_is_durable_and_generation_bound(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    interaction = service.conversation_input_interaction(
        "scenario",
        "recipes",
        surface_command="builder.change.plan",
        conversation_id="conversation.builder.input",
        principal_id="skill:builder_skill",
        command_context_id="thread.builder.input",
        locale="ru",
    )

    assert interaction["input_spec"]["kind"] == "text"
    assert interaction["input_spec"]["required_fields"] == ["text"]
    assert interaction["actions"] == []
    assert interaction["metadata"]["continuation"] == {
        "surface_command": "builder.change.plan",
        "expected_generation": 0,
        "workflow_generation": 0,
    }
    assert (
        interaction["prompt"]
        == "Опишите, что нужно изменить. Строитель разложит запрос на Issues и Change."
    )


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
        metadata={
            "change_set_id": "CS-1",
            "request": "First change",
            "issues": [issue],
        },
    )
    first_packet = service.build_context_packet("scenario", "recipes", persist=True)

    with pytest.raises(BuilderWorkflowError, match="supersedes_change_set_id"):
        service.transition(
            "scenario",
            "recipes",
            "plan_change_set",
            metadata={
                "change_set_id": "CS-2",
                "request": "Second change",
                "issues": [issue],
            },
        )

    transition = service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-2",
            "supersedes_change_set_id": "CS-1",
            "request": "Second change",
            "issues": [issue],
        },
    )
    superseded = transition["workflow"]
    assert transition["updated_change_id"] == "CS-2"
    assert superseded["change_set"]["change_set_id"] == "CS-2"
    assert superseded["change"]["supersedes_change_id"] == "CS-1"
    assert superseded["change_set"]["supersedes_change_set_id"] == "CS-1"
    assert superseded.get("context_packet") is None

    second_packet = service.build_context_packet("scenario", "recipes", persist=True)
    assert second_packet["change"]["change_id"] == "CS-2"
    assert second_packet["change"]["intent"] == "Second change"
    assert second_packet["digest"] != first_packet["digest"]


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
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed({"task_id": "task.1"}),
    )
    service.transition(
        "scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"}
    )
    _prepare_candidate(
        service,
        {
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
                    "acceptance_criteria": [
                        "Every shopping item shows its synchronization state."
                    ],
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
        metadata=_confirmed(
            {"source_prototype_revision": "UI 001", "task_id": "task.1"}
        ),
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
    assert completed["capabilities"]["can_prepare_candidate"] is False
    assert completed["capabilities"]["can_publish"] is False

    trial = _prepare_candidate(
        service,
        {
            "candidate_id": "recipes-0-1-1-abc",
            "release": "recipes@0.1.1",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
            "base_release": "recipes@0.1.0",
            "trial_workspace": "trials/recipes/workspace",
            "permission_decision": {
                "approved": True,
                "actor": "user:owner",
                "approval_id": "approval:recipes-trial",
            },
        },
    )
    assert trial["delivery"]["status"] == "trial"
    assert trial["delivery"]["permission_decision"] == {
        "approved": True,
        "actor": "user:owner",
        "approval_id": "approval:recipes-trial",
    }
    assert trial["capabilities"]["can_decide_candidate"] is True

    accepted = service.transition(
        "scenario",
        "recipes",
        "candidate_accepted",
        metadata={
            "candidate_id": "recipes-0-1-1-abc",
            "candidate_digest": "sha256:" + "2" * 64,
        },
    )["workflow"]
    assert accepted["delivery"]["status"] == "accepted"
    assert accepted["capabilities"]["can_publish"] is True

    published = _publish_candidate(
        service,
        {
            "version": "0.1.1",
            "task_id": "task.1",
            "candidate_id": "recipes-0-1-1-abc",
            "candidate_digest": "sha256:" + "2" * 64,
            "apply_evidence": _apply_evidence(),
        },
    )
    assert published["active_phase"] == "automation"
    assert published["publication"]["current_version"] == "0.1.1"
    assert published["publication"]["status"] == "published"
    release_record = published["publication"]["release_record"]
    assert release_record["approval"]["actor_id"] == "user:owner"
    assert release_record["activation"]["runtime_slot"] == "B"
    Draft202012Validator(
        json.loads(
            (ABI_ROOT / "builder.applied_release.v1.schema.json").read_text(
                encoding="utf-8"
            )
        )
    ).validate(release_record)

    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    assert persisted["workflow_state"] == "automation"
    assert [item["action"] for item in persisted["workflow"]["history"]] == [
        "automation_started",
        "automation_completed",
        "checkpoint_recorded",
        "candidate_preparation_started",
        "candidate_prepared",
        "candidate_accepted",
        "publication_started",
        "publish",
    ]


def test_invalid_cross_phase_transition_is_rejected(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project

    with pytest.raises(BuilderWorkflowError, match="requires active automation"):
        service.transition(
            "scenario", "recipes", "publish", metadata={"version": "0.1.1"}
        )


def test_unknown_trial_outcome_is_not_projected_as_retryable_before_reconciliation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-trial-recovery",
            "request": "Prepare the exact implementation for an isolated Trial.",
            "issues": [
                {
                    "issue_id": "I-trial-recovery",
                    "title": "Prepare the exact Trial candidate",
                    "lane": "automation",
                    "status": "resolved",
                    "acceptance_criteria": ["The checkpoint enters an isolated Trial."],
                }
            ],
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed({"task_id": "task.trial-recovery"}),
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.trial-recovery", "version": "0.1.1"},
    )
    service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata=_confirmed(
            {
                "change_id": "checkpoint-trial-recovery",
                "package_digest": "sha256:" + "a" * 64,
                "source_revision": "a" * 40,
            }
        ),
    )
    service.transition(
        "scenario",
        "recipes",
        "candidate_preparation_started",
        metadata=_confirmed(
            {
                "activity_attempt_id": "trial-attempt:unknown",
                "idempotency_key": "trial:stable-release-digest",
            }
        ),
    )
    unknown = service.transition(
        "scenario",
        "recipes",
        "candidate_preparation_unknown",
        metadata={"error": "remote read rejected before activation"},
    )["workflow"]

    assert unknown["delivery"]["status"] == "unknown"
    assert unknown["capabilities"]["can_prepare_candidate"] is False

    recovered = service.transition(
        "scenario",
        "recipes",
        "reconcile_verification",
        metadata={"evidence_refs": ["diagnostic:remote-read-no-write"]},
    )["workflow"]

    assert recovered["delivery"]["status"] == "idle"
    assert recovered["governed"]["state"] == "verification"
    assert recovered["capabilities"]["can_prepare_candidate"] is False

    readmitted = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata=_confirmed(
            {
                "change_id": "checkpoint-trial-recovery",
                "package_digest": "sha256:" + "a" * 64,
                "source_revision": "a" * 40,
            }
        ),
    )["workflow"]

    assert readmitted["delivery"]["status"] == "checkpoint"
    assert readmitted["governed"]["state"] == "trial_ready"
    assert readmitted["capabilities"]["can_prepare_candidate"] is True

    before_generation = readmitted["generation"]
    before_project_generation = readmitted["project"]["generation"]
    duplicate = service.transition(
        "scenario",
        "recipes",
        "candidate_preparation_started",
        metadata=_confirmed(
            {
                "activity_attempt_id": "trial-attempt:retried-after-reconciliation",
                "idempotency_key": "trial:stable-release-digest",
            }
        ),
    )

    assert duplicate["duplicate"] is True
    assert duplicate["workflow"]["generation"] == before_generation
    assert duplicate["workflow"]["project"]["generation"] == before_project_generation
    assert duplicate["workflow"]["delivery"]["status"] == "checkpoint"
    assert duplicate["workflow"]["governed"]["state"] == "trial_ready"

    # A pre-fix process could persist only the compatibility half of that
    # duplicate transition. Re-admitting an already observed external result
    # with a distinct reconciliation key repairs the projections without
    # repeating Trial activation.
    persisted = json.loads((root / "prompt_state.json").read_text(encoding="utf-8"))
    persisted["workflow"]["delivery"]["status"] = "activating"
    (root / "prompt_state.json").write_text(
        json.dumps(persisted, ensure_ascii=False),
        encoding="utf-8",
    )
    aligned = service.transition(
        "scenario",
        "recipes",
        "candidate_preparation_started",
        metadata=_confirmed(
            {
                "idempotency_key": "trial:stable-release-digest:waiting-reconcile",
                "reconciliation": "external_trial_result_observed",
            }
        ),
    )["workflow"]
    assert aligned["delivery"]["status"] == "activating"
    assert aligned["governed"]["state"] == "trial_waiting"


def test_unknown_publication_requires_explicit_evidenced_reconciliation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-publication-recovery",
            "request": "Publish the verified recipe change.",
            "issues": [
                {
                    "issue_id": "I-publication-recovery",
                    "title": "Publish the verified recipe change",
                    "lane": "automation",
                    "status": "resolved",
                    "acceptance_criteria": [
                        "The accepted candidate is published once."
                    ],
                }
            ],
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed({"task_id": "task.recovery"}),
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.recovery", "version": "0.1.1"},
    )
    _prepare_candidate(
        service,
        {
            "candidate_id": "recipes-0-1-1-recovery",
            "release": "recipes@0.1.1",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "candidate_accepted",
        metadata={
            "candidate_id": "recipes-0-1-1-recovery",
            "candidate_digest": "sha256:" + "2" * 64,
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "publication_started",
        metadata=_confirmed(),
    )
    unknown = service.transition(
        "scenario",
        "recipes",
        "publication_unknown",
        metadata={"error": "activation result needs reconciliation"},
    )["workflow"]

    assert unknown["governed"]["state"] == "reconciliation_required"
    assert unknown["delivery"]["status"] == "unknown"
    recovered = service.transition(
        "scenario",
        "recipes",
        "reconcile_publication",
        metadata={
            "evidence_refs": [
                "activation:failed-and-rolled-back",
                "activation-recovery:admitted",
            ],
            "idempotency_key": "reconcile-publication:1",
        },
    )["workflow"]

    assert recovered["governed"]["state"] == "publication_ready"
    assert recovered["delivery"]["status"] == "accepted"
    assert recovered["publication"]["status"] == "ready"
    assert recovered["reconciliation_history"][-1]["previous_error"] == (
        "activation result needs reconciliation"
    )
    resumed = service.transition(
        "scenario",
        "recipes",
        "publication_started",
        metadata=_confirmed({"idempotency_key": "publish-after-recovery:1"}),
    )["workflow"]
    assert resumed["governed"]["state"] == "publication_waiting"
    assert resumed["delivery"]["status"] == "publication_waiting"

    # Model a crash after the compatibility mutation was durable but before
    # the canonical transition result was persisted.  A new, explicitly
    # versioned attempt must repair only that local projection.
    state_path = _root / "prompt_state.json"
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    persisted["workflow"]["governed"] = {
        **persisted["workflow"]["governed"],
        "state": "publication_ready",
    }
    state_path.write_text(json.dumps(persisted), encoding="utf-8")
    repaired = service.transition(
        "scenario",
        "recipes",
        "publication_started",
        metadata=_confirmed({"idempotency_key": "publish-after-recovery:2"}),
    )["workflow"]
    assert repaired["governed"]["state"] == "publication_waiting"
    assert repaired["delivery"]["status"] == "publication_waiting"


def test_new_automation_iteration_reopens_a_terminal_result(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario", "recipes", "automation_started", metadata={"task_id": "task.1"}
    )
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
    service.transition(
        "scenario", "recipes", "automation_started", metadata={"task_id": "task.1"}
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.1", "snapshot_path": "retained/automation"},
    )
    service.transition(
        "scenario",
        "recipes",
        "request_return_to_prototype",
        metadata={"task_id": "task.2"},
    )

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
    assert (root / "ui_revisions" / "current.txt").read_text(
        encoding="utf-8"
    ).strip() == "002"
    assert returned["active_phase"] == "prototype"
    assert returned["prototype"]["status"] == "working"
    assert returned["automation"]["status"] == "frozen"
    assert returned["prototype"]["derived_from_automation_task"] == "task.2"
    assert returned["capabilities"]["can_preview_automation"] is True


def test_return_to_prototype_marks_checkpoint_delivery_stale(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed({"task_id": "task.1"}),
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.1", "snapshot_path": "retained/automation"},
    )
    service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "implementation-checkpoint",
            "package_digest": "sha256:" + "a" * 64,
            "source_revision": "source-revision",
        },
    )

    returned = service.transition(
        "scenario",
        "recipes",
        "return_to_prototype",
        metadata={"revision": "002", "task_id": "task.2"},
    )["workflow"]

    assert returned["delivery"]["status"] == "stale"
    assert returned["delivery"]["stale_reason"] == "returned_to_prototype"


def test_failed_prototype_adaptation_restores_completed_automation(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario", "recipes", "automation_started", metadata={"task_id": "task.1"}
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.1", "snapshot_path": "retained/automation"},
    )
    service.transition(
        "scenario",
        "recipes",
        "request_return_to_prototype",
        metadata={"task_id": "task.2"},
    )

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
    assert recovered["capabilities"]["can_prepare_candidate"] is False
    assert recovered["capabilities"]["can_publish"] is False
    assert recovered["capabilities"]["can_return_to_prototype"] is True


def test_checkpoint_reconciliation_reenters_failed_automation_without_new_iteration(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    started = service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed({"task_id": "task.1"}),
    )["workflow"]
    service.transition(
        "scenario",
        "recipes",
        "automation_failed",
        metadata={"task_id": "task.1", "error": "Forge checkpoint rejected"},
    )

    recovered = service.transition(
        "scenario",
        "recipes",
        "automation_iteration_started",
        metadata={"task_id": "task.1", "reconciliation": True},
    )["workflow"]
    completed = service.transition(
        "scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"}
    )["workflow"]

    assert recovered["automation"]["iteration"] == started["automation"]["iteration"]
    assert recovered["automation"]["status"] == "working"
    assert completed["automation"]["status"] == "completed"


def test_new_automation_work_invalidates_an_unpublished_candidate(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed({"task_id": "task.1"}),
    )
    service.transition(
        "scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"}
    )
    _prepare_candidate(
        service,
        {
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
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed({"task_id": "task.1"}),
    )
    service.transition(
        "scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"}
    )
    _prepare_candidate(
        service,
        {
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
    service.transition(
        "scenario", "recipes", "automation_completed", metadata={"task_id": "task.2"}
    )

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


def test_checkpoint_rejects_same_artifact_version_with_different_bytes(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "change-1",
            "checkpoint_ref": "scenario:recipes",
            "version": "0.1.0",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )
    with pytest.raises(
        BuilderWorkflowError, match="artifact version already maps to different bytes"
    ):
        service.transition(
            "scenario",
            "recipes",
            "checkpoint_recorded",
            metadata={
                "change_id": "change-2",
                "checkpoint_ref": "scenario:recipes",
                "version": "0.1.0",
                "package_digest": "sha256:" + "2" * 64,
                "source_revision": "b" * 40,
            },
        )


def test_checkpoint_versions_are_scoped_to_artifact_identity(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    first = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "change-scenario",
            "checkpoint_ref": "scenario:recipes",
            "version": "0.1.0",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )["workflow"]
    second = service.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "change_id": "change-project",
            "checkpoint_ref": "project:recipes",
            "version": "0.1.0",
            "package_digest": "sha256:" + "2" * 64,
            "source_revision": "b" * 40,
        },
    )["workflow"]

    assert first["checkpoint_versions"]["scenario:recipes@0.1.0"]["package_digest"] == (
        "sha256:" + "1" * 64
    )
    assert second["checkpoint_versions"]["project:recipes@0.1.0"]["package_digest"] == (
        "sha256:" + "2" * 64
    )
    assert second["delivery"]["checkpoint_ref"] == "project:recipes"


def test_stale_candidate_rebase_plan_survives_automation_and_checkpoint(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, _root = workflow_project
    service.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata=_confirmed(
            {"task_id": "task.initial", "source_prototype_revision": "001"}
        ),
    )
    service.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.initial", "version": "0.1.0"},
    )
    _prepare_candidate(
        service,
        {
            "candidate_id": "candidate-stale",
            "release_digest": "sha256:" + "1" * 64,
            "package_digest": "sha256:" + "2" * 64,
        },
    )
    service.transition(
        "scenario",
        "recipes",
        "candidate_accepted",
        metadata={
            "candidate_id": "candidate-stale",
            "candidate_digest": "sha256:" + "2" * 64,
        },
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
    assert (
        checkpoint["delivery"]["rebase_plan"]["target_base_release"] == "recipes@0.1.1"
    )


def test_archived_project_cannot_transition(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project
    (root / "prompt_state.json").write_text(
        json.dumps({"archived": True}), encoding="utf-8"
    )

    with pytest.raises(BuilderWorkflowError, match="archived projects"):
        service.transition("scenario", "recipes", "stabilize_prototype")


def test_only_latest_automation_snapshot_is_retained(
    workflow_project: tuple[BuilderWorkflowService, Path],
) -> None:
    service, root = workflow_project

    first = service.snapshot_current_automation("scenario", "recipes", task_id="task.1")
    first_path = Path(first["path"])
    assert (
        json.loads((first_path / "snapshot.json").read_text(encoding="utf-8"))[
            "task_id"
        ]
        == "task.1"
    )

    webui = json.loads((root / "webui.json").read_text(encoding="utf-8"))
    webui["ui"]["application"]["desktop"]["pageSchema"]["title"] = "Automated v2"
    (root / "webui.json").write_text(json.dumps(webui), encoding="utf-8")
    second = service.snapshot_current_automation(
        "scenario", "recipes", task_id="task.2"
    )

    assert second["path"] == first["path"]
    assert (
        json.loads((first_path / "snapshot.json").read_text(encoding="utf-8"))[
            "task_id"
        ]
        == "task.2"
    )
    retained = json.loads((first_path / "webui.json").read_text(encoding="utf-8"))
    assert (
        retained["ui"]["application"]["desktop"]["pageSchema"]["title"]
        == "Automated v2"
    )
    assert not first_path.with_name(".automation.previous").exists()


def test_automation_snapshot_uses_project_owned_skill_ui_when_scenario_has_none(
    tmp_path: Path,
) -> None:
    scenarios = tmp_path / "scenarios"
    scenario = scenarios / "metrics"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        "id: metrics\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    skills = tmp_path / "skills"
    skill = skills / "metrics_skill"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: metrics_skill\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (skill / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"modals": {}}}),
        encoding="utf-8",
    )
    projects = tmp_path / "projects"
    project = projects / "metrics_project"
    project.mkdir(parents=True)
    (project / "project.yaml").write_text(
        "schema: adaos.project.v1\nid: metrics_project\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    service = BuilderWorkflowService(
        dev_skills_root=skills,
        dev_scenarios_root=scenarios,
        dev_projects_root=projects,
        state_dir=tmp_path / "state",
    )

    result = service.snapshot_current_automation(
        "scenario",
        "metrics",
        task_id="task.project-ui",
        project_ref="project:metrics_project",
        component_refs=["skill:metrics_skill"],
    )

    snapshot = Path(result["path"])
    assert result["schema"] == "adaos.builder.automation_snapshot.v2"
    assert result["object_id"] == "metrics"
    assert result["project_ref"] == "project:metrics_project"
    assert result["components"] == [
        {
            "ref": "skill:metrics_skill",
            "files": [
                "components/skills/metrics_skill/skill.yaml",
                "components/skills/metrics_skill/webui.json",
            ],
        }
    ]
    assert (snapshot / "project" / "project.yaml").is_file()
    assert (
        snapshot / "components" / "skills" / "metrics_skill" / "webui.json"
    ).is_file()


def test_automation_snapshot_rejects_descriptorless_scenario_without_owned_ui(
    tmp_path: Path,
) -> None:
    scenario = tmp_path / "scenarios" / "empty"
    scenario.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        "id: empty\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    service = BuilderWorkflowService(
        dev_skills_root=tmp_path / "skills",
        dev_scenarios_root=tmp_path / "scenarios",
        state_dir=tmp_path / "state",
    )

    with pytest.raises(
        BuilderWorkflowError, match="owned components provide webui.json"
    ):
        service.snapshot_current_automation("scenario", "empty")
