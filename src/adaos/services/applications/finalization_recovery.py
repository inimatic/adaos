"""Close a Finalize fence only from its exact, completed recovery evidence."""
from datetime import datetime
import hashlib

from adaos.domain.artifact_release import canonical_payload_digest
from .runtime_channel import ApplicationRuntimeChannel
from .runtime_transition import ApplicationRuntimeTransition


def settle_recovered_finalization(operations, operation):
    """Caller owns the Application operation lease; never infer success or replay."""
    plan = operation.plan
    if (operation.status not in {"unknown", "applying", "reconciling"}
            or plan.get("schema") != "adaos.application.lifecycle_plan.v1"
            or plan.get("action") != "promote_stable"
            or canonical_payload_digest(plan) != operation.plan_digest
            or operation.result.get("confirmation") != {"confirmed": True,
                "plan_digest": operation.plan_digest, "actor_ref": operation.actor_ref,
                "subnet_ref": operation.subnet_ref}):
        return None
    candidate_id = plan.get("candidate_id")
    base = f"application-stable:{operation.application_id}:{candidate_id}"
    # New attempts have a durable review identity. A later attempt must never
    # close merely because a previous attempt for the same Candidate was aborted.
    attempt = base + ":attempt:" + hashlib.sha256(operation.operation_id.encode()).hexdigest()
    channel = ApplicationRuntimeChannel(operations.state, operation.application_id)
    runner = ApplicationRuntimeTransition(channel)
    record = runner.get(attempt) or runner.get(base)
    if not record or not record.get("aborted") or record.get("completed"):
        return None
    intent = record.get("intent") or {}
    recovery = record.get("recovery") or {}
    steps = (recovery.get("intent") or {}).get("steps") or []
    receipts = recovery.get("receipts") or {}
    target = intent.get("target") or {}
    expected = intent.get("expected") or []
    review_id = (intent.get("publication_review") or {}).get("review_id")
    digest = canonical_payload_digest(record)
    if (review_id not in {None, operation.operation_id}
            or plan.get("recovered_adoption_digest") == digest
            or not recovery.get("completed_at") or recovery.get("running_step")
            or not steps or len(set(steps)) != len(steps)
            or any((receipts.get(step) or {}).get("ok") is not True for step in steps)
            or not intent.get("contract_digest") or not expected
            or record.get("fingerprint") != canonical_payload_digest(intent).removeprefix("sha256:")
            or steps != ["prove_no_stable_effects", "verify_source"]
            or (recovery.get("intent") or {}).get("source") != expected
            or target.get("application_id") != operation.application_id
            or target.get("source") != "stable_installation"
            or target.get("runtime_root_ref") != "workspace"
            or target.get("release_digest") != plan.get("release_digest")
            or expected != sorted(plan.get("expected_selections") or [], key=lambda s: s["webspace_id"])
            or any(item.get("application_id") != operation.application_id
                or item.get("runtime_root_ref") != f"trial:{candidate_id}"
                or item.get("release_digest") != plan.get("release_digest") for item in expected)):
        return None
    # Historical journals predate review_id. Their exact original selections
    # and ordering in time are required; a fresh review cannot inherit them.
    try:
        if not review_id and datetime.fromisoformat(operation.created_at).replace(microsecond=0) > datetime.fromisoformat(recovery["completed_at"]):
            return None
    except (ValueError, TypeError):
        return None
    execution = operation.result.get("execution") or {}
    return operations.service._transition_operation(operation, "cancelled", result={
        **operation.result, "ok": False, "status": "finalize_cancelled",
        "selected_beta_retained": True,
        "recovery": {"operation_id": record["operation_id"], "receipt_digest": digest,
                     "completed_at": recovery["completed_at"]},
        "execution": {"attempts": execution.get("attempts", 0), "status": "cancelled"},
    })
