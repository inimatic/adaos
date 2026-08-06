from __future__ import annotations

from pathlib import Path

import pytest

from adaos.services import conversation_interactions, intent_mediation
from adaos.services.builder.workflow import BuilderWorkflowError, BuilderWorkflowService


PACKAGE_DIGEST = "sha256:" + "a" * 64
RELEASE_DIGEST = "sha256:" + "b" * 64


def _apply_evidence() -> dict[str, object]:
    return {
        "draft_ref": {"draft_id": "draft.empty", "revision": "UI-002"},
        "validation_evidence": [{"type": "test_run", "id": "test:automation-passed", "status": "passed"}],
        "approval": {
            "approval_id": "pa.builder.publish.empty",
            "actor_id": "user:owner",
            "actor_type": "user",
            "approved_at": "2026-08-05T00:00:00+00:00",
            "policy_evidence": [{"policy": "builder.publication", "decision": "allow"}],
        },
        "activation": {
            "operation_id": "activation.empty.020",
            "runtime_slot": "B",
            "health_receipt": {"status": "passed"},
        },
        "rollback": {
            "mode": "workspace_lock_restore",
            "operation_ref": "activation.empty.020:rollback",
        },
    }


def _service(tmp_path: Path, project_id: str = "empty_scenario") -> BuilderWorkflowService:
    skills = tmp_path / "skills"
    scenarios = tmp_path / "scenarios"
    project = scenarios / project_id
    skills.mkdir(parents=True, exist_ok=True)
    project.mkdir(parents=True, exist_ok=True)
    (project / "scenario.yaml").write_text(
        f"id: {project_id}\nversion: 0.1.0\nsupported_locales:\n  - en\n  - ru\n",
        encoding="utf-8",
    )
    revisions = project / "ui_revisions"
    revisions.mkdir(exist_ok=True)
    (revisions / "UI-001.json").write_text("{}\n", encoding="utf-8")
    (revisions / "current.txt").write_text("UI-001\n", encoding="utf-8")
    return BuilderWorkflowService(skills, scenarios, tmp_path / "state")


def _plan(
    service: BuilderWorkflowService,
    *,
    project_id: str = "empty_scenario",
    change_id: str = "CH-empty",
    lane: str = "prototype",
    affected_ref: str = "widget:root",
    parallel: bool = False,
) -> dict[str, object]:
    return service.transition(
        "scenario",
        project_id,
        "plan_change_set",
        metadata={
            "change_set_id": change_id,
            "request": "Create a safe empty-scenario flow.",
            "parallel": parallel,
            "affected_refs": [affected_ref],
            "source_message_ids": [f"message:{change_id}"],
            "issues": [
                {
                    "issue_id": f"issue-{change_id}",
                    "title": "Build the requested behavior",
                    "lane": lane,
                    "semantic_refs": [affected_ref],
                    "acceptance_criteria": ["The behavior is verified in Trial."],
                }
            ],
        },
    )["workflow"]


def test_empty_scenario_completes_dependent_cross_channel_flow(tmp_path: Path) -> None:
    service = _service(tmp_path)
    planned = _plan(service)
    revised = service.transition(
        "scenario",
        "empty_scenario",
        "prototype_revision_recorded",
        metadata={
            "revision": "UI-001",
            "object_type": "scenario",
            "change_id": "CH-empty",
            "evidence_refs": ["review:layout-approved"],
        },
    )["workflow"]
    assert revised["governed"]["state"] == "prototype_editing"

    interaction = service.conversation_interaction(
        "scenario",
        "empty_scenario",
        conversation_id="conversation:e2e",
        principal_id="user:local",
        command_context_id="webspace:dev-local",
    )
    presentations = [
        conversation_interactions.negotiate_presentation(
            interaction,
            conversation_interactions.standard_capability_profile(channel),
        )
        for channel in ("web", "telegram", "text")
    ]
    semantic = [
        (
            item["command"],
            item["risk"],
            item["confirmation_required"],
            item["target_ref"],
            item["expected_generation"],
        )
        for item in presentations[0]["actions"]
    ]
    assert all(
        [
            (
                item["command"],
                item["risk"],
                item["confirmation_required"],
                item["target_ref"],
                item["expected_generation"],
            )
            for item in presentation["actions"]
        ]
        == semantic
        for presentation in presentations[1:]
    )

    accept = next(
        item for item in presentations[0]["actions"] if item["command"] == "accept_prototype"
    )
    response = conversation_interactions.submit_response(
        interaction["interaction_id"],
        actor_id="user:local",
        expected_generation=interaction["generation"],
        idempotency_key="web:e2e:accept-prototype",
        values={"confirmed": True},
        action_token=accept["token"],
    )["response"]
    approved = service.invoke_interaction_response(
        "scenario",
        "empty_scenario",
        response,
        actor="user:local",
    )["workflow"]
    assert approved["governed"]["state"] == "automation_ready"

    running = service.transition(
        "scenario",
        "empty_scenario",
        "automation_started",
        metadata={
            "task_id": "RUN-empty-1",
            "originating_change_id": "CH-empty",
            "confirmed": True,
        },
    )["workflow"]
    assert running["governed"]["state"] == "automation_waiting"
    verified = service.transition(
        "scenario",
        "empty_scenario",
        "automation_completed",
        metadata={
            "task_id": "RUN-empty-1",
            "originating_change_id": "CH-empty",
            "version": "0.2.0",
            "snapshot_path": "workflow_snapshots/empty/automation",
            "evidence_refs": ["test:automation-passed"],
        },
    )["workflow"]
    assert verified["governed"]["state"] == "verification"
    checkpoint = service.transition(
        "scenario",
        "empty_scenario",
        "checkpoint_recorded",
        metadata={
            "change_id": "CH-empty",
            "package_digest": PACKAGE_DIGEST,
            "source_revision": "c" * 40,
            "evidence_refs": ["git:checkpoint"],
            "confirmed": True,
        },
    )["workflow"]
    assert checkpoint["governed"]["state"] == "trial_ready"
    waiting_trial = service.transition(
        "scenario",
        "empty_scenario",
        "candidate_preparation_started",
        metadata={"evidence_refs": ["trial:activation-accepted"], "confirmed": True},
    )["workflow"]
    assert waiting_trial["governed"]["state"] == "trial_waiting"
    trial = service.transition(
        "scenario",
        "empty_scenario",
        "candidate_prepared",
        metadata={
            "candidate_id": "candidate-empty-020",
            "release": "empty_scenario@0.2.0",
            "release_digest": RELEASE_DIGEST,
            "package_digest": PACKAGE_DIGEST,
            "base_release": "empty_scenario@0.1.0",
            "trial_workspace": "trial:empty_scenario:020",
            "evidence_refs": ["trial:activated"],
        },
    )["workflow"]
    assert trial["governed"]["state"] == "trial_review"
    with pytest.raises(BuilderWorkflowError, match="exact immutable candidate digest"):
        service.transition(
            "scenario",
            "empty_scenario",
            "candidate_accepted",
            metadata={
                "candidate_id": "candidate-empty-020",
                "candidate_digest": RELEASE_DIGEST,
            },
        )
    accepted = service.transition(
        "scenario",
        "empty_scenario",
        "candidate_accepted",
        metadata={
            "candidate_id": "candidate-empty-020",
            "candidate_digest": PACKAGE_DIGEST,
            "observations": ["user:accepted"],
        },
    )["workflow"]
    assert accepted["governed"]["state"] == "publication_ready"
    waiting_publication = service.transition(
        "scenario",
        "empty_scenario",
        "publication_started",
        metadata={"evidence_refs": ["registry:publication-accepted"]},
    )["workflow"]
    assert waiting_publication["governed"]["state"] == "publication_waiting"
    published = service.transition(
        "scenario",
        "empty_scenario",
        "publish",
        metadata={
            "candidate_id": "candidate-empty-020",
            "candidate_digest": PACKAGE_DIGEST,
            "version": "0.2.0",
            "release": "empty_scenario@0.2.0",
            "task_id": "RUN-empty-1",
            "evidence_refs": ["registry:published"],
            "apply_evidence": _apply_evidence(),
        },
    )["workflow"]

    assert published["governed"]["state"] == "published"
    assert published["change"]["status"] == "published"
    nodes = {item["kind"]: item for item in published["process"]["nodes"]}
    assert nodes["prototype"]["parent_ref"] == nodes["change"]["ref"]
    assert nodes["automation"]["parent_ref"] == nodes["prototype"]["ref"]
    assert nodes["verification"]["parent_ref"] == nodes["automation"]["ref"]
    assert nodes["trial"]["parent_ref"] == nodes["verification"]["ref"]
    assert nodes["publication"]["parent_ref"] == nodes["trial"]["ref"]
    assert nodes["workspace_installation"]["parent_ref"] == nodes["publication"]["ref"]
    assert nodes["publication"]["parent_ref"] == nodes["trial"]["ref"]
    assert {item["label"].split(":", 1)[0] for item in published["process"]["preview_options"]} == {
        "proto",
        "active",
        "public",
    }
    canonical_history = [
        item["canonical"]["command"]
        for item in published["history"]
        if item.get("canonical")
    ]
    assert canonical_history[-10:] == [
        "record_prototype_revision",
        "accept_prototype",
        "start_automation",
        "record_automation_success",
        "accept_verification",
        "start_trial",
        "record_trial_success",
        "accept_trial",
        "begin_publication",
        "record_publication_success",
    ]
    evidence_refs = {
        ref
        for run in published["change"]["runs"]
        for ref in run.get("evidence_refs") or []
    }
    assert {
        "review:layout-approved",
        "test:automation-passed",
        "git:checkpoint",
        "trial:activated",
        "registry:published",
    } <= evidence_refs


def test_protected_command_requires_explicit_confirmation_across_ingress(tmp_path: Path) -> None:
    button_service = _service(tmp_path / "button", "button_project")
    telegram_service = _service(tmp_path / "telegram", "telegram_project")
    text_service = _service(tmp_path / "text", "text_project")
    sdk_service = _service(tmp_path / "sdk", "sdk_project")
    _plan(button_service, project_id="button_project", change_id="CH-button")
    _plan(telegram_service, project_id="telegram_project", change_id="CH-telegram")
    _plan(text_service, project_id="text_project", change_id="CH-text")
    _plan(sdk_service, project_id="sdk_project", change_id="CH-sdk")

    button_interaction = button_service.conversation_interaction(
        "scenario",
        "button_project",
        conversation_id="conversation:button",
        principal_id="user:local",
        command_context_id="web:button",
    )
    presentation = conversation_interactions.negotiate_presentation(
        button_interaction,
        conversation_interactions.standard_capability_profile("web"),
    )
    token = next(
        item["token"] for item in presentation["actions"] if item["command"] == "accept_prototype"
    )
    button_response = conversation_interactions.submit_response(
        button_interaction["interaction_id"],
        actor_id="user:local",
        expected_generation=0,
        idempotency_key="button:accept",
        action_token=token,
        values={"confirmed": True},
        metadata={"io_type": "web"},
    )["response"]

    telegram_interaction = telegram_service.conversation_interaction(
        "scenario",
        "telegram_project",
        conversation_id="conversation:telegram",
        principal_id="user:local",
        command_context_id="telegram:button",
    )
    telegram_presentation = conversation_interactions.negotiate_presentation(
        telegram_interaction,
        conversation_interactions.standard_capability_profile("telegram"),
    )
    telegram_token = next(
        item["token"]
        for item in telegram_presentation["actions"]
        if item["command"] == "accept_prototype"
    )
    telegram_response = conversation_interactions.submit_action_token(
        telegram_token,
        actor_id="user:local",
        idempotency_key="telegram:button:accept",
        metadata={"io_type": "telegram"},
    )["response"]

    text_interaction = text_service.conversation_interaction(
        "scenario",
        "text_project",
        conversation_id="conversation:text",
        principal_id="user:local",
        command_context_id="telegram:text",
    )
    proposal = intent_mediation.propose_intent(
        "conversation:text",
        "telegram:message:1",
        "accept_prototype",
        explicit_interaction_id=text_interaction["interaction_id"],
    )
    assert proposal["disposition"] == "clarification_required"

    web_result = button_service.invoke_interaction_response(
        "scenario", "button_project", button_response, actor="user:local"
    )
    with pytest.raises(BuilderWorkflowError, match="explicit confirmed response"):
        telegram_service.invoke_interaction_response(
            "scenario", "telegram_project", telegram_response, actor="user:local"
        )
    with pytest.raises(intent_mediation.IntentMediationError, match="not committable"):
        intent_mediation.commit_proposal(
            proposal["proposal_id"],
            actor_id="user:local",
            idempotency_key="telegram:text:accept",
        )
    sdk_result = sdk_service.invoke_command(
        "scenario",
        "sdk_project",
        "accept_prototype",
        actor="user:local",
        idempotency_key="sdk:accept",
        input_value={"confirmed": True},
    )

    invocations = [
        web_result["invocation"],
        sdk_result["invocation"],
    ]
    assert [item["source"] for item in invocations] == ["web", "sdk"]
    assert {item["command"]["command_id"] for item in invocations} == {"accept_prototype"}
    assert {item["command"]["workflow_type"] for item in invocations} == {"builder.change"}
    assert {item["command"]["expected_generation"] for item in invocations} == {1}
    assert {item["risk"] for item in invocations} == {"isolated_write"}
    assert {item["target_ref"]["kind"] for item in invocations} == {"change"}
    assert {item["confirmation_required"] for item in invocations} == {True}
    assert all(
        item["workflow"]["governed"]["state"] == "automation_ready"
        for item in (web_result, sdk_result)
    )


def test_background_result_updates_originating_change_not_current_view(tmp_path: Path) -> None:
    service = _service(tmp_path)
    _plan(service, change_id="CH-background", affected_ref="widget:background")
    service.transition(
        "scenario", "empty_scenario", "stabilize_prototype", metadata={"confirmed": True}
    )
    service.transition(
        "scenario",
        "empty_scenario",
        "automation_started",
        metadata={
            "task_id": "RUN-background",
            "originating_change_id": "CH-background",
            "confirmed": True,
        },
    )
    current = _plan(
        service,
        change_id="CH-current-view",
        affected_ref="widget:current",
        parallel=True,
    )
    assert current["change"]["change_id"] == "CH-current-view"

    result = service.transition(
        "scenario",
        "empty_scenario",
        "automation_completed",
        metadata={
            "task_id": "RUN-background",
            "originating_change_id": "CH-background",
            "version": "0.2.0",
        },
    )

    assert result["updated_change_id"] == "CH-background"
    assert result["workflow"]["change"]["change_id"] == "CH-current-view"
    portfolio = result["workflow"]["change_portfolio"]
    assert portfolio["CH-background"]["automation"]["status"] == "completed"
    assert portfolio["CH-current-view"]["automation"]["status"] == "not_started"


def test_project_detects_indirect_shared_component_conflict(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first = _plan(service, change_id="CH-scenario", affected_ref="scenario:shopping-ui")
    second = _plan(
        service,
        change_id="CH-skill",
        affected_ref="skill:shopping-store",
        parallel=True,
    )
    configured = service.configure_project_dependencies(
        "scenario",
        "empty_scenario",
        [
            {
                "from_ref": "scenario:shopping-ui",
                "to_ref": "skill:shopping-store",
                "kind": "requires",
            }
        ],
        expected_project_generation=second["project"]["generation"],
    )["workflow"]

    assert first["project"]["conflicts"] == []
    assert configured["project"]["conflicts"] == [
        {
            "left_change_id": "CH-scenario",
            "right_change_id": "CH-skill",
            "affected_refs": ["skill:shopping-store"],
            "kind": "component_dependency",
        }
    ]
