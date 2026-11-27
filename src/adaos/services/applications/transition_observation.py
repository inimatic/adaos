"""Bounded, read-only progress over the Core cutover journal; no data or effects."""
from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime
from threading import Lock
from typing import Any, Mapping

from adaos.domain.artifact_release import canonical_payload_digest
from .publication_readiness import PublicationPreconditionFailed, TEST_QUALIFICATION_BLOCKERS

_ACTIVE: Counter[tuple[str, str]] = Counter()
_LOCK = Lock()
_TASK_STATUS = {
    "planned": "input_required", "applying": "running", "reconciling": "running",
    "succeeded": "completed", "failed": "failed", "cancelled": "cancelled",
}


def transition_worker(operation_id: str, worker_id: str, delta: int) -> None:
    with _LOCK:
        key = (operation_id, worker_id)
        _ACTIVE[key] += delta
        if _ACTIVE[key] <= 0:
            del _ACTIVE[key]


def observe_transition(record: Mapping[str, Any]) -> dict[str, Any]:
    progress = record.get("progress") or {}
    intent = record.get("intent") or {}
    steps = list(intent.get("steps") or [])
    receipts = record.get("receipts") or {}
    state = "recovery_required"
    if record.get("aborted"):
        state = "cancelled"
    elif record.get("completed"):
        state = "succeeded"
    elif progress.get("state") == "applying" and progress.get("worker_pid") == os.getpid():
        with _LOCK:
            if _ACTIVE.get((str(record.get("operation_id")), str(progress.get("worker_id"))), 0):
                state = "applying"
    # An interrupted or legacy journal is not evidence of a live worker.
    # Another process may own it: report uncertainty, never start a new effect.
    return {
        "schema": "adaos.application.runtime_transition_progress.v1",
        "operation_id": record.get("operation_id"),
        "review_operation_id": (intent.get("publication_review") or {}).get("review_id"),
        "target_release_digest": (intent.get("target") or {}).get("release_digest"),
        "status": state,
        "phase": record.get("running_step") or state,
        "completed_steps": [step for step in steps if step in receipts],
        "steps": steps,
        "total_steps": len(steps),
        "updated_at": progress.get("updated_at"),
        "failure": progress.get("failure"),
        "runtime_fenced": state not in {"succeeded", "cancelled"},
        "retry_allowed": False,
        "next_action": "wait" if state == "applying" else (
            "review_exact_recovery" if state == "recovery_required" else "none"),
    }


def operation_task(operation: Mapping[str, Any], transition: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Adapt existing journal facts to the shared task-progress presentation ABI."""
    result = operation.get("result") or {}
    receipt = result.get("execution") or {}
    status = operation.get("status")
    execution = operation.get("execution_status") or receipt.get("status")
    task_status = _TASK_STATUS.get(status, "unknown")
    if execution == "blocked":
        task_status = "failed"
    elif execution in {"queued", "retrying"}:
        task_status = "queued"
    task = {"id": operation["operation_id"], "revision": operation.get("revision", 0),
            "status": task_status, "can_cancel": status == "planned" and task_status == "input_required"}
    for field in ("started_at", "updated_at"):
        if operation.get(field) and task_status != "input_required":
            task[field] = operation[field]
    if task_status == "input_required":
        plan = operation.get("plan") or operation.get("review") or {}
        if plan.get("conflicts"):
            task["error"] = "Execution is blocked by the listed component conflicts. The operation has not started."
        elif plan.get("can_apply") is False:
            task["error"] = "This plan cannot be applied. The operation has not started."
        else:
            task["message"] = "Waiting for confirmation. The operation has not started."
        return task
    failure = operation.get("execution_failure") or receipt.get("failure") or {}
    if status == "cancelled" and execution == "cancelled":
        task["status"] = "cancelled"
        task["message"] = "Stable acceptance was cancelled after verified recovery. The retained Beta can be reviewed again."
    elif failure.get("code") == "RecoveryRelocationInProgress":
        task["message" if execution == "retrying" else "error"] = (
            "Application recovery relocation is in progress. "
            + ("Waiting for storage admission; retrying automatically. " if execution == "retrying" else
               "The retained acceptance can continue after storage admission reopens. ")
            + ("No application data was changed." if failure.get("stage") == "preflight" else
               "Completed steps and the original decision are retained."))
    elif failure.get("code") == "WorkspaceSlotPackageConflict":
        task["status"] = "failed"
        task["error"] = (
            f"Stable acceptance is blocked: {failure['project_id']} uses "
            f"{failure['component_ref']} {failure['active_version']}, but this Beta requires "
            f"{failure.get('candidate_version') or 'an unavailable package'}. "
            "Align the dependency before accepting Stable. " + (
                "No application data was changed." if failure.get('stage') == 'preflight' else
                "Completed steps are retained."))
    elif (failure.get("stage") == "preflight" and failure.get("code") != "SharedSkillConflict"
            or failure.get("code") in TEST_QUALIFICATION_BLOCKERS):
        field = "message" if execution == "retrying" else "error"
        task[field] = (
            PublicationPreconditionFailed.MESSAGES.get(failure.get("code"),
                "Publication prerequisites could not be verified. No application data was changed.")
            + (" Retrying automatically." if execution == "retrying" else ""))
        if operation_task_action(operation):
            task[field] += " Open Dev tickets for this application to follow the Builder task."
            if failure.get("stage") != "preflight":
                task[field] += " Continue the exact Stable operation before replacing the Beta."
    elif failure.get("code") == "SharedSkillConflict":
        task["status"] = "failed"
        conflicts = failure.get("conflicts") or []
        providers = "; ".join(f"{item['skill']}" + (f" (Beta requires {item['candidate_version']}; active {item['active_version']})"
                              if item.get('candidate_version') and item.get('active_version') else '') +
                              f" ({', '.join(item['active_consumers'])})"
                              for item in conflicts)
        task["error"] = ("Stable acceptance is blocked by a shared dependency: " + providers +
            ". This Beta requires a different package from the one used by other applications. "
            "Prepare a compatible Beta before accepting it. " + (
                "Completed steps are retained." if transition and transition.get('completed_steps') else
                "No application data was moved."))
    elif failure.get("code") == "StableDataDiverged":
        task["status"] = "failed"
        task["error"] = ("Stable data changed after Beta started, and those changes are absent from its original snapshot. "
            "Acceptance stopped to protect the newer Stable data. Both data branches are retained; "
            "choose how to align them before accepting Stable.")
    elif failure.get("code") == "SourceCheckpointUnavailable":
        task["message" if execution == "retrying" else "error"] = (
            "The release service could not save the source checkpoint. Retrying automatically; completed steps will not be repeated."
            if execution == "retrying" else
            "The release service could not save the source checkpoint. Automatic retries have stopped; completed steps are retained.")
    elif failure.get("code") == "ReleaseServiceUnavailable":
        task["message" if execution == "retrying" else "error"] = (
            "Connection to the release service was interrupted. Retrying automatically; completed steps will not be repeated."
            if execution == "retrying" else
            "The release service is unavailable. Automatic retries have stopped; completed data and settings changes are retained.")
    elif execution == "blocked":
        task["error"] = ("The release service rejected the Stable verification request. The transition has stopped; completed steps are retained."
            if failure.get("code") == "ReleaseServiceRejected" else
            "Stable acceptance has stopped. Completed data and settings changes are retained; the exact operation needs diagnosis.")
    elif status == "unknown":
        task["message"] = "The outcome is unknown. Check this operation before retrying or recovering it."
    elif status == "failed":
        from .execution_outcome import execution_error
        error = operation.get("error") or execution_error(result) or {}
        task["error"] = error.get("message") or "The operation could not be completed."
    if transition:
        completed = list(transition.get("completed_steps") or [])
        total = int(transition.get("total_steps") or 0)
        if total:
            task["progress"] = {"completed": len(completed), "total": total}
        task["stage_id"] = transition.get("phase") or ""
        labels = {"protect_and_adopt_data": "Preserve and adopt data", "adopt_configuration": "Apply configuration",
                  "publish_verify": "Publish and verify Stable"}
        steps = list(transition.get("steps") or completed)
        task["stages"] = [{"id": step, "label": labels.get(step, step.replace("_", " ")),
            "status": "completed" if step in completed else "running" if step == task["stage_id"]
                and task["status"] == "running" else "failed" if step == task["stage_id"]
                and execution == "blocked" else "pending"} for step in steps]
    return task


def runtime_transition_task(transition: Mapping[str, Any]) -> dict[str, Any]:
    """Show the exact cutover when it has no matching user-facing operation."""
    applying = transition.get("status") == "applying"
    task = operation_task({
        "operation_id": transition["operation_id"],
        "status": "applying" if applying else "unknown",
        "updated_at": transition.get("updated_at"),
    }, transition)
    task["message"] = (
        "The Application transition is in progress."
        if applying else "Review the recorded transition before starting recovery."
    )
    return task


def operation_task_action(operation: Mapping[str, Any]) -> dict[str, str] | None:
    """Return the recovery action alongside the versioned task record."""
    receipt = (operation.get("result") or {}).get("execution") or {}
    failure = operation.get("execution_failure") or receipt.get("failure") or {}
    ticket_id = failure.get("development_ticket_id")
    if (failure.get("code") in TEST_QUALIFICATION_BLOCKERS and isinstance(ticket_id, str)
            and re.fullmatch(r"dticket\.[A-Za-z0-9_-]{1,100}", ticket_id)):
        return {"type": "open_dev_ticket", "ticket_id": ticket_id}
    return None


def observed_finalization_task(operation: Any, state: Any) -> dict[str, Any]:
    """Read one Finalize intent, independently of later Application operations."""
    import hashlib
    from .runtime_channel import ApplicationRuntimeChannel
    from .runtime_transition import ApplicationRuntimeTransition

    execution = operation.result.get('execution') or {}
    projection = {'operation_id': operation.operation_id, 'status': operation.status,
        'revision': operation.revision, 'started_at': operation.created_at,
        'updated_at': operation.updated_at, 'execution_status': execution.get('status'),
        'execution_failure': execution.get('failure')}
    progress = None
    candidate_id, release_digest = operation.plan.get('candidate_id'), operation.plan.get('release_digest')
    if candidate_id and release_digest and execution.get('effects_started') is not False:
        base = f"application-stable:{operation.application_id}:{candidate_id}"
        attempt = base + ':attempt:' + hashlib.sha256(operation.operation_id.encode()).hexdigest()
        runner = ApplicationRuntimeTransition(ApplicationRuntimeChannel(state, operation.application_id))
        record = runner.get(attempt) or runner.get(base)
        intent = (record or {}).get('intent') or {}
        target = intent.get('target') or {}
        review = intent.get('publication_review') or {}
        if (record and target.get('release_digest') == release_digest
                and (review.get('review_id') == operation.operation_id
                     or not review and record.get('operation_id') == base and _matches_original_cutover(record, operation))):
            progress = observe_transition(record)
    return operation_task(projection, progress)


def _matches_original_cutover(record: Mapping[str, Any], operation: Any) -> bool:
    """Match old base journals by the exact reviewed selections and creation time."""
    intent = record['intent']
    target = intent['target']
    expected = operation.plan.get('expected_selections') or []
    if (not expected or intent.get('expected') != sorted(expected, key=lambda item: item['webspace_id'])
            or target.get('application_id') != operation.application_id
            or (target.get('source'), target.get('runtime_root_ref')) != ('stable_installation', 'workspace')
            or record.get('fingerprint') != canonical_payload_digest(intent).removeprefix('sha256:')
            or operation.plan.get('recovered_adoption_digest') == canonical_payload_digest(record)):
        return False
    try:
        recorded_at = datetime.fromisoformat(target['updated_at'])
        started_at = datetime.fromisoformat(operation.created_at).replace(microsecond=0)
        updated_at = datetime.fromisoformat(operation.updated_at)
        return started_at <= recorded_at and (operation.status in {'applying', 'reconciling'} or recorded_at <= updated_at)
    except (KeyError, ValueError, TypeError):
        return False
