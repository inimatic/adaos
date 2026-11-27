"""Close a Finalize fence only from exact recovery or preflight evidence."""
from datetime import datetime
import hashlib

from adaos.domain.application import RuntimeSelection
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.storage import mutation_lock
from adaos.services.artifact_pipeline.trial_activation import load_workspace_lock
from .runtime_channel import ApplicationRuntimeChannel, same_runtime_selections
from .runtime_transition import ApplicationRuntimeTransition


def _settle_publication_preflight(operations, operation, runner, base):
    """Recognize older one-attempt failures before publication or data adoption."""
    execution = operation.result.get("execution") or {}
    failure = execution.get("failure") or {}
    legacy_failure = (failure.get("code") == "FinalizationBlocked"
                      and "effects_started" not in execution and operation.recovery_reason == "ValueError")
    test_rejection = failure.get("code") == "CandidateTestsUnverified"
    plan = operation.plan
    already_settled = (operation.status == "failed" and operation.recovery_reason == "PublicationPreflightRejected"
                       and failure.get("stage") == "preflight" and execution.get("effects_started") is not False)
    if (not (operation.status == "unknown" or already_settled) or execution.get("status") != "blocked" or execution.get("attempts") != 1
            or not (legacy_failure or test_rejection) or "stable_release_digest" not in plan):
        return None

    application_id, candidate_id = operation.application_id, plan["candidate_id"]
    release_digest, baseline = plan["release_digest"], plan["stable_release_digest"]
    expected = tuple(RuntimeSelection.from_mapping(item) for item in plan.get("expected_selections") or ())
    if not expected or any((item.application_id, item.source, item.runtime_root_ref, item.release_digest)
            != (application_id, "local_trial", f"trial:{candidate_id}", release_digest) for item in expected):
        return None

    pipeline = operations.state / "artifact_pipeline"
    candidate_path = CandidateStore(pipeline / "candidates").path(candidate_id)
    promotion = pipeline / "promotions" / candidate_path.name
    workspace = operations.workspace / ".adaos"
    # The transition and publication writers take these leases in the same order.
    with (mutation_lock(runner.channel.path.with_suffix(".transition.lock"), timeout_s=.25),
          mutation_lock(promotion.with_suffix(".lock"), timeout_s=.25),
          mutation_lock(workspace / ".workspace-writer.lock", timeout_s=.25)):
        if runner.current(base) or runner.channel.pending_transitions() or promotion.exists():
            return None
        store = operations.store
        if not same_runtime_selections(store.list_application_runtime_selections(application_id), expected):
            return None
        if (store.get_channels(application_id).get("channels") or {}).get("stable") != baseline or baseline == release_digest:
            return None
        try:
            candidate, activation = operations._trial(application_id, candidate_id)
        except (FileNotFoundError, ValueError):
            return None
        app = store.get_application(application_id)
        if (app.publisher_ref != operation.subnet_ref or candidate.digest != plan.get("candidate_digest")
                or candidate.release_digest != release_digest or candidate.package_digest != plan.get("package_digest")
                or canonical_payload_digest(activation.get("runtime_binding") or {}) != plan.get("trial_binding_digest")):
            return None
        try:
            installation = store.get_installation(application_id)
        except FileNotFoundError:
            installation = None
        installed = installation.installed_release_digest if installation and installation.status == "active" else None
        lock = load_workspace_lock(workspace / "workspace.lock.json")
        slots = [slot.release_digest for slot in lock.slots if slot.project_id == app.legacy_project_id] if lock else []
        if installed != baseline or slots != ([baseline] if baseline else []):
            return None

        return operations.service._transition_operation(operation, "failed", recovery_reason="PublicationPreflightRejected",
            result={**operation.result, "execution": {**execution, "effects_started": False,
                                                      "failure": {**failure, "stage": "preflight"}}})


def settle_recovered_finalization(operations, operation):
    """Caller owns the Application operation lease; never infer success or replay."""
    plan = operation.plan
    if (operation.status not in {"unknown", "applying", "reconciling", "failed"}
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
    if not record:
        return _settle_publication_preflight(operations, operation, runner, base)
    if operation.status == "failed" or not record.get("aborted") or record.get("completed"):
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
