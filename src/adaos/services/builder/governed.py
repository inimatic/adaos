from __future__ import annotations

import copy
from functools import lru_cache
from typing import Any, Mapping

from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    WorkflowResolver,
    compile_definition,
    new_instance,
)


BUILDER_CHANGE_WORKFLOW_TYPE = "builder.change"
BUILDER_CHANGE_DEFINITION_VERSION = "1.0.0"

_INPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "confirmed": {"type": "boolean"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "legacy_action": {"type": "string"},
    },
    "additionalProperties": True,
}

_WAITING_STATES = {
    "automation_waiting": "Automation is running against an exact Change and source Prototype.",
    "prototype_derivation_waiting": "A safe Prototype derivation is running from retained Automation evidence.",
    "publication_waiting": "Publication is reconciling an externally visible result.",
    "reconciliation_required": "The last modifying outcome is unknown and must be observed before continuing.",
}

_STATES = (
    "ready",
    "prototype_editing",
    "prototype_review",
    "automation_ready",
    "automation_waiting",
    "verification",
    "prototype_derivation_waiting",
    "trial_ready",
    "trial_review",
    "publication_ready",
    "publication_waiting",
    "reconciliation_required",
    "published",
    "cancelled",
    "superseded",
)


def _transition(
    transition_id: str,
    source: str | list[str],
    target: str,
    command: str,
    *,
    risk: str = "isolated_write",
    side_effect: str = "reversible",
    confirmation: str = "none",
    activity: str | None = None,
    retry: str = "never",
    reconciliation: str = "not_applicable",
    async_mode: str = "terminal",
    aliases: list[str] | None = None,
) -> dict[str, Any]:
    recovery_required = activity is not None
    return {
        "schema": "adaos.workflow.transition.v1",
        "transition_id": transition_id,
        "source": source,
        "target": target,
        "trigger": {
            "kind": "command",
            "command": command,
            "input_schema": copy.deepcopy(_INPUT_SCHEMA),
        },
        "context": {"target_resolution": "instance", "command_context_required": True},
        "authority": {"actors": ["*"], "permissions": []},
        "guards": [{"id": "always", "params": {}, "reason_code": "builder_transition_blocked"}],
        "concurrency": {
            "conflict_scope": "builder_change",
            "requires_generation": True,
            "idempotency": "required",
        },
        "risk": {"class": risk, "side_effect": side_effect, "confirmation": confirmation},
        "effect": {
            "activity": activity,
            "transaction": "outbox" if recovery_required else "atomic",
            "retry": retry,
            "compensation": f"{activity}.compensate" if recovery_required and side_effect == "reversible" else None,
        },
        "recovery": {
            "timeout_seconds": 1800 if recovery_required else None,
            "heartbeat_seconds": 30 if recovery_required else None,
            "cancellation": "cooperative" if recovery_required else "not_applicable",
            "reconciliation": reconciliation,
        },
        "outcomes": {
            "success": target,
            "failure": source[0] if isinstance(source, list) else source,
            "input_required": source[0] if isinstance(source, list) else source,
            "cancelled": "cancelled",
            "unknown": "reconciliation_required" if recovery_required else source[0] if isinstance(source, list) else source,
        },
        "evidence": {"required": False, "minimum": 0},
        "approval": {"required": confirmation != "none", "policy_refs": []},
        "async_reply": {
            "mode": async_mode,
            "reply_route": "origin" if async_mode != "none" else "none",
        },
        "capability_requirements": {
            "required": [],
            "optional": ["buttons", "progress"],
            "fallback": "numbered_text",
        },
        "explanations": {
            "allowed": f"{command} is available for this Change.",
            "rejected": f"{command} is not available in the current Change state.",
            "completed": f"{command} was admitted for this Change.",
        },
        "events": {"emitted": [f"builder.change.{command}.admitted"], "outbox": recovery_required},
        "observability": {
            "audit_event": "builder.change.transition",
            "redaction": "policy",
            "metrics": ["builder_change_transition_total"],
            "trace": True,
        },
        "migration": {"introduced_in": BUILDER_CHANGE_DEFINITION_VERSION, "aliases": aliases or []},
    }


def builder_change_definition() -> dict[str, Any]:
    nonterminal = [state for state in _STATES if state not in {"published", "cancelled", "superseded"}]
    transitions = [
        _transition("plan_prototype", "ready", "prototype_editing", "plan_prototype_change", risk="local_reversible"),
        _transition("plan_automation", "ready", "automation_ready", "plan_automation_change", risk="local_reversible"),
        _transition("record_prototype_revision", "prototype_editing", "prototype_editing", "record_prototype_revision", risk="local_reversible"),
        _transition("record_prototype_experiment", "prototype_editing", "prototype_editing", "record_prototype_experiment", risk="local_reversible"),
        _transition("adopt_prototype_experiment", "prototype_editing", "prototype_editing", "adopt_prototype_experiment", risk="isolated_write", confirmation="rich_review"),
        _transition("discard_prototype_experiment", "prototype_editing", "prototype_editing", "discard_prototype_experiment", risk="local_reversible"),
        _transition("request_prototype_review", "prototype_editing", "prototype_review", "request_prototype_review", risk="read", side_effect="none"),
        _transition("accept_prototype", ["prototype_editing", "prototype_review"], "automation_ready", "accept_prototype"),
        _transition("revise_prototype", ["prototype_review", "verification", "trial_review"], "prototype_editing", "revise_prototype", risk="local_reversible"),
        _transition("extend_with_prototype", nonterminal, "prototype_editing", "extend_with_prototype_issues", risk="local_reversible"),
        _transition("extend_with_automation", [state for state in nonterminal if state != "prototype_editing"], "automation_ready", "extend_with_automation_issues", risk="local_reversible"),
        _transition("accept_review_constraint", nonterminal, "prototype_editing", "accept_review_constraint", risk="local_reversible"),
        _transition("start_automation", "automation_ready", "automation_waiting", "start_automation", activity="builder.codex.run", retry="never", reconciliation="required_on_unknown", async_mode="progress_and_terminal"),
        _transition("retry_automation", ["automation_ready", "automation_waiting", "verification"], "automation_waiting", "retry_automation", activity="builder.codex.run", retry="never", reconciliation="required_on_unknown", async_mode="progress_and_terminal"),
        _transition("automation_succeeded", "automation_waiting", "verification", "record_automation_success", risk="read", side_effect="none"),
        _transition("automation_failed", "automation_waiting", "automation_ready", "record_automation_failure", risk="read", side_effect="none"),
        _transition("automation_unknown", "automation_waiting", "reconciliation_required", "record_automation_unknown", risk="read", side_effect="none"),
        _transition("request_prototype_derivation", "verification", "prototype_derivation_waiting", "request_prototype_derivation", activity="builder.prototype.derive", retry="never", reconciliation="required_on_unknown", async_mode="progress_and_terminal"),
        _transition("prototype_derivation_succeeded", ["prototype_derivation_waiting", "verification"], "prototype_editing", "record_prototype_derivation_success", risk="read", side_effect="none"),
        _transition("prototype_derivation_failed", ["prototype_derivation_waiting", "automation_ready"], "verification", "record_prototype_derivation_failure", risk="read", side_effect="none"),
        _transition("prototype_derivation_unknown", "prototype_derivation_waiting", "reconciliation_required", "record_prototype_derivation_unknown", risk="read", side_effect="none"),
        _transition("accept_verification", "verification", "trial_ready", "accept_verification", risk="isolated_write"),
        _transition("start_trial", "trial_ready", "trial_review", "start_trial", risk="trial_activation", side_effect="external", activity="builder.trial.activate", retry="never", reconciliation="required_on_unknown", async_mode="progress_and_terminal"),
        _transition("prepare_trial_compatibility", ["verification", "trial_ready"], "trial_review", "prepare_trial_compatibility", risk="trial_activation", side_effect="external", activity="builder.trial.activate", retry="never", reconciliation="required_on_unknown", async_mode="progress_and_terminal", aliases=["candidate_prepared"]),
        _transition("accept_trial", "trial_review", "publication_ready", "accept_trial", risk="workspace_activation", side_effect="external", confirmation="required"),
        _transition("reject_trial", "trial_review", "automation_ready", "reject_trial", risk="local_reversible"),
        _transition("invalidate_candidate", ["trial_ready", "trial_review", "publication_ready"], "automation_ready", "invalidate_candidate", risk="local_reversible"),
        _transition("begin_publication", "publication_ready", "publication_waiting", "begin_publication", risk="publication", side_effect="external", confirmation="required", activity="builder.publication.publish", retry="never", reconciliation="required_on_unknown", async_mode="progress_and_terminal"),
        _transition("publish_compatibility", "publication_ready", "published", "publish_compatibility", risk="publication", side_effect="external", confirmation="required", activity="builder.publication.publish", retry="never", reconciliation="required_on_unknown", async_mode="terminal", aliases=["publish"]),
        _transition("publication_succeeded", "publication_waiting", "published", "record_publication_success", risk="read", side_effect="none"),
        _transition("publication_failed", "publication_waiting", "publication_ready", "record_publication_failure", risk="read", side_effect="none"),
        _transition("publication_unknown", "publication_waiting", "reconciliation_required", "record_publication_unknown", risk="read", side_effect="none"),
        _transition("reconcile_to_automation", "reconciliation_required", "automation_ready", "reconcile_automation", risk="read", side_effect="none"),
        _transition("reconcile_to_verification", "reconciliation_required", "verification", "reconcile_verification", risk="read", side_effect="none"),
        _transition("reconcile_to_publication", "reconciliation_required", "publication_ready", "reconcile_publication", risk="read", side_effect="none"),
    ]
    for state in nonterminal:
        transitions.append(_transition(f"cancel_from_{state}", state, "cancelled", f"cancel_from_{state}", risk="local_reversible"))
        transitions.append(_transition(f"supersede_from_{state}", state, "superseded", f"supersede_from_{state}", risk="local_reversible"))
    commands = sorted({item["trigger"]["command"] for item in transitions})
    return {
        "schema": "adaos.workflow.definition.v1",
        "workflow_type": BUILDER_CHANGE_WORKFLOW_TYPE,
        "definition_version": BUILDER_CHANGE_DEFINITION_VERSION,
        "aggregate_type": "builder.change",
        "initial_state": "ready",
        "states": [
            {
                "id": state,
                "label": state.replace("_", " ").title(),
                "terminal": state in {"published", "cancelled", "superseded"},
                **(
                    {"waiting": True, "wait_explanation": _WAITING_STATES[state]}
                    if state in _WAITING_STATES
                    else {}
                ),
            }
            for state in _STATES
        ],
        "commands": [
            {"id": command, "input_schema": copy.deepcopy(_INPUT_SCHEMA)}
            for command in commands
        ],
        "transitions": transitions,
        "subworkflows": [],
        "metadata": {
            "domain": "builder",
            "planes": ["change", "artifact_lineage", "run", "view"],
            "compatibility_projection": "prompt_state.workflow.v1",
        },
    }


@lru_cache(maxsize=1)
def compiled_builder_change_definition() -> CompiledWorkflowDefinition:
    return compile_definition(builder_change_definition())


def legacy_state(workflow: Mapping[str, Any]) -> str:
    change = dict(workflow.get("change") or workflow.get("change_set") or {})
    if not change or str(change.get("status") or "") in {"published", "rejected", "superseded"}:
        return "ready"
    publication = dict(workflow.get("publication") or {})
    delivery = dict(workflow.get("delivery") or {})
    automation = dict(workflow.get("automation") or {})
    prototype = dict(workflow.get("prototype") or {})
    if str(publication.get("status") or "") == "published":
        return "published"
    delivery_status = str(delivery.get("status") or "idle")
    if delivery_status == "accepted":
        return "publication_ready"
    if delivery_status == "trial":
        return "trial_review"
    if delivery_status == "checkpoint":
        return "trial_ready"
    if delivery_status in {"unknown", "reconciliation_required"}:
        return "reconciliation_required"
    automation_status = str(automation.get("status") or "not_started")
    if automation_status == "adapting":
        return "prototype_derivation_waiting"
    active_phase = str(workflow.get("active_phase") or "prototype")
    if active_phase == "automation":
        if automation_status == "working":
            return "automation_waiting"
        if automation_status == "completed":
            return "verification"
        return "automation_ready"
    if str(change.get("gate") or "") == "automation" or bool(prototype.get("stable")):
        return "automation_ready"
    return "prototype_editing"


def governed_instance(workflow: Mapping[str, Any], *, project_ref: str) -> dict[str, Any]:
    definition = compiled_builder_change_definition()
    change = dict(workflow.get("change") or workflow.get("change_set") or {})
    change_id = str(change.get("change_id") or change.get("change_set_id") or "ready").strip() or "ready"
    raw = workflow.get("governed")
    if isinstance(raw, Mapping):
        candidate = copy.deepcopy(dict(raw))
        if (
            candidate.get("workflow_type") == definition.workflow_type
            and candidate.get("definition_version") == definition.definition_version
            and candidate.get("instance_id") == f"change:{project_ref}:{change_id}"
        ):
            return candidate
    instance = new_instance(
        definition,
        f"change:{project_ref}:{change_id}",
        context={
            "target_ref": {"schema": "adaos.workflow.ref.v1", "kind": "change", "id": change_id},
            "project_ref": project_ref,
            "evidence_refs": list(change.get("source_message_ids") or [])[:100],
        },
    )
    instance["state"] = legacy_state(workflow)
    return instance


def canonical_command(action: str, workflow: Mapping[str, Any], metadata: Mapping[str, Any]) -> str | None:
    action = str(action or "").strip().lower()
    if action == "plan_change_set":
        issues = [item for item in metadata.get("issues") or [] if isinstance(item, Mapping)]
        return "plan_prototype_change" if any(str(item.get("lane") or "") == "prototype" for item in issues) else "plan_automation_change"
    if action == "change_issues_added":
        issues = [item for item in metadata.get("issues") or [] if isinstance(item, Mapping)]
        current = dict(workflow.get("change") or workflow.get("change_set") or {})
        prototype_pending = any(
            str(item.get("lane") or "") == "prototype"
            and str(item.get("status") or "open") not in {"resolved", "deferred"}
            for item in current.get("issues") or []
            if isinstance(item, Mapping)
        )
        return (
            "extend_with_prototype_issues"
            if prototype_pending or any(str(item.get("lane") or "") == "prototype" for item in issues)
            else "extend_with_automation_issues"
        )
    mapping = {
        "prototype_revision_recorded": "record_prototype_revision",
        "prototype_experiment_recorded": "record_prototype_experiment",
        "adopt_experiment": "adopt_prototype_experiment",
        "discard_experiment": "discard_prototype_experiment",
        "stabilize_prototype": "accept_prototype",
        "handoff_to_automation": "start_automation",
        "automation_started": "start_automation",
        "automation_iteration_started": "retry_automation",
        "automation_completed": "record_automation_success",
        "automation_failed": "record_automation_failure",
        "request_return_to_prototype": "request_prototype_derivation",
        "return_to_prototype": "record_prototype_derivation_success",
        "return_to_prototype_failed": "record_prototype_derivation_failure",
        "checkpoint_recorded": "accept_verification",
        "candidate_prepared": "prepare_trial_compatibility",
        "candidate_accepted": "accept_trial",
        "candidate_rejected": "reject_trial",
        "candidate_stale": "invalidate_candidate",
        "review_constraint_added": "accept_review_constraint",
        "publish": "publish_compatibility",
    }
    return mapping.get(action)


def legacy_action_for_command(command: str) -> str | None:
    """Return the bounded compatibility adapter for a canonical Builder command."""

    mapping = {
        "record_prototype_revision": "prototype_revision_recorded",
        "record_prototype_experiment": "prototype_experiment_recorded",
        "adopt_prototype_experiment": "adopt_experiment",
        "discard_prototype_experiment": "discard_experiment",
        "accept_prototype": "stabilize_prototype",
        "start_automation": "automation_started",
        "retry_automation": "automation_iteration_started",
        "record_automation_success": "automation_completed",
        "record_automation_failure": "automation_failed",
        "request_prototype_derivation": "request_return_to_prototype",
        "record_prototype_derivation_success": "return_to_prototype",
        "record_prototype_derivation_failure": "return_to_prototype_failed",
        "accept_verification": "checkpoint_recorded",
        "prepare_trial_compatibility": "candidate_prepared",
        "accept_trial": "candidate_accepted",
        "reject_trial": "candidate_rejected",
        "invalidate_candidate": "candidate_stale",
        "publish_compatibility": "publish",
    }
    return mapping.get(str(command or "").strip())


def admit_legacy_transition(
    workflow: Mapping[str, Any],
    action: str,
    metadata: Mapping[str, Any],
    *,
    project_ref: str,
    actor: str,
    idempotency_key: str,
    now: str,
) -> dict[str, Any] | None:
    command = canonical_command(action, workflow, metadata)
    if command is None:
        return None
    if action == "plan_change_set":
        change_id = str(metadata.get("change_id") or metadata.get("change_set_id") or "").strip()
        instance = new_instance(
            compiled_builder_change_definition(),
            f"change:{project_ref}:{change_id}",
            context={
                "target_ref": {"schema": "adaos.workflow.ref.v1", "kind": "change", "id": change_id},
                "project_ref": project_ref,
                "evidence_refs": [
                    str(item) for item in metadata.get("source_message_ids") or [] if str(item).strip()
                ][:100],
            },
            now=now,
        )
    else:
        instance = governed_instance(workflow, project_ref=project_ref)
    # The compatibility service already enforces the legacy confirmation gate.
    # Recording it explicitly prevents the canonical resolver from weakening it.
    input_value = {
        "confirmed": bool(
            metadata.get("confirmed")
            or command in {"accept_trial", "begin_publication", "publish_compatibility", "record_publication_success"}
        ),
        "evidence_refs": [str(item) for item in metadata.get("evidence_refs") or [] if str(item).strip()][:100],
        "legacy_action": str(action),
    }
    decision = WorkflowResolver().apply(
        compiled_builder_change_definition(),
        instance,
        command,
        input_value=input_value,
        actor=str(actor or "builder"),
        expected_generation=int(instance["generation"]),
        idempotency_key=idempotency_key,
        now=now,
    )
    return decision


def workflow_description(workflow: Mapping[str, Any], *, project_ref: str, actor: str = "user:local") -> dict[str, Any]:
    return WorkflowResolver().describe(
        compiled_builder_change_definition(),
        governed_instance(workflow, project_ref=project_ref),
        actor=actor,
    )
