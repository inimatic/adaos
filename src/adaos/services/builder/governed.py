from __future__ import annotations

import copy
import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from adaos.services.governed_workflow import (
    CompiledWorkflowDefinition,
    WorkflowResolver,
    WorkflowResolutionError,
    compile_definition,
    new_instance,
    workflow_definition_digest,
    verified_workflow_principal,
)
from adaos.services.workflow_registry import platform_workflow_adapter_registry


BUILDER_CHANGE_WORKFLOW_TYPE = "builder.change"
BUILDER_CHANGE_DEFINITION_VERSION = "1.1.0"
_BUILDER_CHANGE_RESOURCE = Path(__file__).with_name("builder_change.workflow.json")


def builder_change_definition() -> dict[str, Any]:
    value = json.loads(_BUILDER_CHANGE_RESOURCE.read_text(encoding="utf-8"))
    if not isinstance(value, Mapping):
        raise ValueError("Builder compatibility workflow resource must contain an object")
    return copy.deepcopy(dict(value))


@lru_cache(maxsize=1)
def compiled_builder_change_definition() -> CompiledWorkflowDefinition:
    compiled = compile_definition(builder_change_definition())
    platform_workflow_adapter_registry().bind(compiled)
    return compiled


def legacy_state(workflow: Mapping[str, Any]) -> str:
    change = dict(workflow.get("change") or workflow.get("change_set") or {})
    if not change:
        return "ready"
    publication = dict(workflow.get("publication") or {})
    delivery = dict(workflow.get("delivery") or {})
    automation = dict(workflow.get("automation") or {})
    prototype = dict(workflow.get("prototype") or {})
    change_status = str(change.get("status") or "")
    if change_status == "published" or str(publication.get("status") or "") == "published":
        return "published"
    if change_status == "superseded":
        return "superseded"
    if change_status == "rejected":
        return "cancelled"
    delivery_status = str(delivery.get("status") or "idle")
    if delivery_status == "activating":
        return "trial_waiting"
    if delivery_status == "publication_waiting":
        return "publication_waiting"
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


def governed_instance(
    workflow: Mapping[str, Any],
    *,
    project_ref: str,
    definition: CompiledWorkflowDefinition | None = None,
    package_digest: str | None = None,
    binding_digest: str | None = None,
) -> dict[str, Any]:
    definition = definition or compiled_builder_change_definition()
    change = dict(workflow.get("change") or workflow.get("change_set") or {})
    change_id = str(change.get("change_id") or change.get("change_set_id") or "ready").strip() or "ready"
    raw = workflow.get("governed")
    if isinstance(raw, Mapping):
        candidate = copy.deepcopy(dict(raw))
        if candidate.get("instance_id") == f"change:{project_ref}:{change_id}":
            if candidate.get("workflow_type") != definition.workflow_type:
                raise WorkflowResolutionError("Builder workflow type migration is required")
            if candidate.get("definition_version") != definition.definition_version:
                raise WorkflowResolutionError(
                    "Builder workflow definition migration is required: "
                    f"{candidate.get('definition_version')} -> {definition.definition_version}"
                )
            expected_digest = workflow_definition_digest(definition)
            bound_digest = str(candidate.get("definition_digest") or "").strip()
            if bound_digest and bound_digest != expected_digest:
                raise WorkflowResolutionError(
                    "Builder workflow definition digest changed without a versioned migration"
                )
            if not bound_digest:
                candidate["definition_digest"] = expected_digest
                context = dict(candidate.get("context") or {})
                context["legacy_definition_binding"] = {
                    "status": "adopted",
                    "definition_version": definition.definition_version,
                    "definition_digest": expected_digest,
                }
                candidate["context"] = context
            for field, expected in (
                ("package_digest", package_digest),
                ("binding_digest", binding_digest),
            ):
                bound = str(candidate.get(field) or "").strip() or None
                if expected and bound and bound != expected:
                    raise WorkflowResolutionError(
                        f"Builder workflow {field} migration is required"
                    )
                if expected and not bound:
                    candidate[field] = expected
            return candidate
    instance = new_instance(
        definition,
        f"change:{project_ref}:{change_id}",
        context={
            "target_ref": {"schema": "adaos.workflow.ref.v1", "kind": "change", "id": change_id},
            "project_ref": project_ref,
            "evidence_refs": list(change.get("source_message_ids") or [])[:100],
        },
        package_digest=package_digest,
        binding_digest=binding_digest,
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
        "candidate_preparation_started": "start_trial",
        "candidate_prepared": "record_trial_success",
        "candidate_preparation_failed": "record_trial_failure",
        "candidate_preparation_unknown": "record_trial_unknown",
        "candidate_accepted": "accept_trial",
        "candidate_rejected": "reject_trial",
        "candidate_stale": "invalidate_candidate",
        "review_constraint_added": "accept_review_constraint",
        "publication_started": "begin_publication",
        "publish": "record_publication_success",
        "publication_failed": "record_publication_failure",
        "publication_unknown": "record_publication_unknown",
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
        "start_trial": "candidate_preparation_started",
        "record_trial_success": "candidate_prepared",
        "record_trial_failure": "candidate_preparation_failed",
        "record_trial_unknown": "candidate_preparation_unknown",
        "accept_trial": "candidate_accepted",
        "reject_trial": "candidate_rejected",
        "invalidate_candidate": "candidate_stale",
        "begin_publication": "publication_started",
        "record_publication_success": "publish",
        "record_publication_failure": "publication_failed",
        "record_publication_unknown": "publication_unknown",
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
    definition: CompiledWorkflowDefinition | None = None,
    package_digest: str | None = None,
    binding_digest: str | None = None,
) -> dict[str, Any] | None:
    definition = definition or compiled_builder_change_definition()
    command = canonical_command(action, workflow, metadata)
    if command is None:
        return None
    if action == "plan_change_set":
        change_id = str(metadata.get("change_id") or metadata.get("change_set_id") or "").strip()
        instance = new_instance(
            definition,
            f"change:{project_ref}:{change_id}",
            context={
                "target_ref": {"schema": "adaos.workflow.ref.v1", "kind": "change", "id": change_id},
                "project_ref": project_ref,
                "evidence_refs": [
                    str(item) for item in metadata.get("source_message_ids") or [] if str(item).strip()
                ][:100],
            },
            package_digest=package_digest,
            binding_digest=binding_digest,
            now=now,
        )
    else:
        instance = governed_instance(
            workflow,
            project_ref=project_ref,
            definition=definition,
            package_digest=package_digest,
            binding_digest=binding_digest,
        )
    # The compatibility service already enforces the legacy confirmation gate.
    # Recording it explicitly prevents the canonical resolver from weakening it.
    input_value = {
        "confirmed": bool(
            metadata.get("confirmed")
            or command in {"accept_trial", "begin_publication", "record_publication_success"}
        ),
        "evidence_refs": [str(item) for item in metadata.get("evidence_refs") or [] if str(item).strip()][:100],
        "legacy_action": str(action),
    }
    principal = verified_workflow_principal(
        str(actor or "builder"),
        authenticated=True,
        issuer="adaos.builder.compatibility",
    )
    decision = WorkflowResolver(require_verified_principal=True).apply(
        definition,
        instance,
        command,
        input_value=input_value,
        actor=str(actor or "builder"),
        principal=principal,
        expected_generation=int(instance["generation"]),
        idempotency_key=idempotency_key,
        now=now,
    )
    return decision


def workflow_description(
    workflow: Mapping[str, Any],
    *,
    project_ref: str,
    actor: str = "user:local",
    definition: CompiledWorkflowDefinition | None = None,
    authenticated: bool = True,
) -> dict[str, Any]:
    definition = definition or compiled_builder_change_definition()
    principal = verified_workflow_principal(
        actor,
        authenticated=authenticated,
        issuer="adaos.builder.projection",
    )
    return WorkflowResolver(require_verified_principal=True).describe(
        definition,
        governed_instance(workflow, project_ref=project_ref, definition=definition),
        actor=actor,
        principal=principal,
    )
