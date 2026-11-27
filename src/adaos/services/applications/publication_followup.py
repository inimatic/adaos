"""Hand publication test blockers to the existing Builder Dev Ticket workflow."""

import hashlib
import logging
from pathlib import Path

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.development_tickets import DevelopmentTicketService
from adaos.services.mutation_lock import MutationLockTimeout, mutation_lock

from .publication_readiness import PublicationPreconditionFailed, TEST_QUALIFICATION_BLOCKERS


_LOG = logging.getLogger(__name__)


def selected_test_blocker(owner, store, application_id, candidate):
    """Read the latest Stable attempt for the selected immutable Beta."""
    operations = store.list_operations(application_id, actions={"promote_stable"},
        release_digest=candidate.release_digest, latest_only=True)
    if not operations:
        return None
    operation = operations[0]
    execution = operation.result.get("execution") or {}
    failure = execution.get("failure") or {}
    if (operation.status != "failed" or execution.get("status") != "blocked"
            or operation.plan.get("candidate_id") != candidate.candidate_id
            or failure.get("code") not in TEST_QUALIFICATION_BLOCKERS):
        return None
    from .test_acceptance import read_candidate_test_acceptance

    if failure.get("stage") == "preflight" and read_candidate_test_acceptance(owner, candidate):
        return None
    return operation


def reconcile_test_followups(operations, *, limit: int) -> list[dict[str, str]]:
    """Attach the recovery workflow to retained failures at either side of the effect boundary."""
    from .finalization_recovery import settle_recovered_finalization
    from .lifecycle_operations import PLAN_SCHEMA

    remaining = limit
    settled = []
    for recorded in operations.store.list_operations(
        statuses={"failed", "unknown"}, actions={"promote_stable"}, latest_only=True,
    ):
        if remaining <= 0:
            break
        execution = recorded.result.get("execution") or {}
        failure = execution.get("failure") or {}
        if (execution.get("status") != "blocked" or failure.get("code") not in TEST_QUALIFICATION_BLOCKERS
                or failure.get("development_ticket_id")):
            continue
        plan = recorded.plan
        if (plan.get("schema") != PLAN_SCHEMA or not plan.get("candidate_id")
                or not plan.get("release_digest") or not plan.get("target_version")
                or canonical_payload_digest(plan) != recorded.plan_digest
                or recorded.result.get("confirmation") != {"confirmed": True, "plan_digest": recorded.plan_digest,
                    "actor_ref": recorded.actor_ref, "subnet_ref": recorded.subnet_ref}):
            continue
        token = hashlib.sha256(recorded.application_id.encode()).hexdigest()
        try:
            with mutation_lock(operations.store.root / "operation-leases" / f"{token}.lock", timeout_s=.25):
                op = operations.store.get_operation(recorded.operation_id)
                if op.revision != recorded.revision:
                    continue
                if not any(s.source == "local_trial" and s.release_digest == plan["release_digest"]
                           and s.runtime_root_ref == f"trial:{plan['candidate_id']}"
                           for s in operations.store.list_application_runtime_selections(op.application_id)):
                    continue
                if execution.get("effects_started") is not False:
                    op = settle_recovered_finalization(operations, op) or op
                if op.status != recorded.status:
                    settled.append({"operation_id": op.operation_id, "status": op.status})
                    remaining -= 1
                    continue
                execution = op.result["execution"]
                failure = execution["failure"]
                project_id = operations.store.get_application(op.application_id).legacy_project_id
                ticket = record_test_qualification_ticket(operations.state, project_id=project_id,
                    application_id=op.application_id, version=plan["target_version"], operation_id=op.operation_id,
                    input_digest=plan["release_digest"], failure=failure,
                    effects_started=execution.get("effects_started") is not False, candidate_id=plan["candidate_id"])
                operations.service._transition_operation(op, op.status, recovery_reason=op.recovery_reason,
                    result={**op.result, "execution": {**execution, "failure": {**failure, "development_ticket_id": ticket}}})
                remaining -= 1
        except MutationLockTimeout:
            continue
        except (OSError, ValueError, RuntimeError):
            _LOG.warning("Could not reconcile Finalize test ticket operation=%s", recorded.operation_id, exc_info=True)
    return settled


def record_test_qualification_ticket(
    state: Path, *, project_id: str, application_id: str, version: str,
    operation_id: str, input_digest: str, failure: dict, effects_started: bool,
    candidate_id: str | None = None,
) -> str:
    reason = failure["code"]
    key = f"application-finalization:{application_id}:{candidate_id or input_digest}:{reason}"
    if effects_started:
        key += f":{operation_id}"
    summary = f"{project_id} {version}. {PublicationPreconditionFailed.MESSAGES[reason]}"
    if effects_started:
        summary += " Recover the exact Stable operation before replacing the selected Beta."
    evidence = [{"type": "application_operation" if candidate_id else "source_checkpoint",
                 "id": operation_id}]
    metadata = {"operation_id": operation_id, "reason": reason, "effects_started": effects_started}
    if candidate_id:
        metadata.update(candidate_id=candidate_id, release_digest=input_digest)
        evidence.append({"type": "candidate", "id": candidate_id})
    else:
        metadata["source_snapshot_digest"] = input_digest
    if failure.get("test_receipt_digest"):
        evidence.append({"type": "source_test_receipt", "id": failure["test_receipt_digest"]})
        metadata["failed_suites"] = failure["failed_suites"]
    service = DevelopmentTicketService(state_dir=state)
    signal = service.capture_signal(
        kind="development_request", summary=summary, source="applications.lifecycle.finalize",
        severity="high", blocking=True, dedup_key=key, owner_area="project",
        component_ref=f"project:{project_id}",
        origin_scope={"type": "runtime", "surface": "applications.lifecycle.finalize"},
        target_scope={"type": "project", "id": project_id, "project_id": project_id,
                      "project_ref": f"project:{project_id}", "application_id": application_id,
                      "version": version, "digest": input_digest, "source": "dev"},
        evidence_refs=evidence, metadata={"application_finalization": metadata},
    )["signal"]
    return service.ensure_ticket_for_signal(signal, kind="development_request",
        status="proposed", dedup_key=key)["ticket"]["ticket_id"]
