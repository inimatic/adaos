"""Deterministic Builder lifecycle activities shared by Web, chat, and tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaos.sdk.builder import automation, workflow
from adaos.sdk.developer import projects


ACTIVITY_COMMANDS = frozenset(
    {
        "start_automation",
        "retry_automation",
        "request_prototype_derivation",
        "start_trial",
        "accept_trial",
        "reject_trial",
        "begin_publication",
    }
)


def _mapping(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, Mapping) else {}


def _implementation_brief(state: Mapping[str, Any]) -> str:
    change = _mapping(state.get("change") or state.get("change_set"))
    request = str(change.get("request") or "").strip()
    criteria = [
        str(criterion).strip()
        for issue in change.get("issues") or []
        if isinstance(issue, Mapping)
        for criterion in issue.get("acceptance_criteria") or []
        if str(criterion).strip()
    ]
    if not request:
        raise ValueError("Automation requires an active Change with an implementation brief")
    if not criteria:
        return request
    return request + "\n\nAcceptance criteria:\n- " + "\n- ".join(criteria[:50])


def _candidate_identity(state: Mapping[str, Any]) -> tuple[str, str]:
    delivery = _mapping(state.get("delivery"))
    candidate_id = str(delivery.get("candidate_id") or "").strip()
    digest = str(
        delivery.get("package_digest") or delivery.get("release_digest") or ""
    ).strip()
    if not candidate_id or not digest:
        raise ValueError("Builder lifecycle requires an exact candidate identity")
    return candidate_id, digest


def prepare_trial(
    object_type: str,
    object_id: str,
    *,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    state = workflow.get_state(object_type, object_id)
    delivery = _mapping(state.get("delivery"))
    automation_state = _mapping(state.get("automation"))
    change = _mapping(state.get("change") or state.get("change_set"))
    if str(automation_state.get("status") or "") != "completed":
        raise ValueError("Trial requires completed Automation")
    if str(delivery.get("status") or "") != "checkpoint":
        raise ValueError("Trial requires an exact Automation checkpoint")
    change_ids = list(
        dict.fromkeys(
            [
                *(
                    str(item).strip()
                    for item in change.get("member_change_ids") or []
                    if str(item).strip()
                ),
                str(delivery.get("checkpoint_change_id") or "").strip(),
            ]
        )
    )
    change_ids = [item for item in change_ids if item]
    started = workflow.transition(
        object_type,
        object_id,
        "candidate_preparation_started",
        actor=actor,
        metadata={
            "confirmed": True,
            "run_id": f"trial:{idempotency_key}",
            "idempotency_key": idempotency_key,
        },
    )
    validation_evidence = {
        "status": "passed",
        "validator": "builder.release.preflight",
        "checkpoint_package_digest": delivery.get("package_digest"),
        "checkpoint_source_revision": delivery.get("source_revision"),
        "automation_task_id": automation_state.get("head_task_id"),
        "change_id": change.get("change_id") or change.get("change_set_id"),
    }
    try:
        stale_candidate = str(delivery.get("replaces_candidate_id") or "").strip()
        if stale_candidate:
            result = projects.prepare_rebased_candidate(
                stale_candidate,
                object_type,
                object_id,
                validation_evidence=validation_evidence,
            )
        else:
            result = projects.prepare_candidate(
                object_type,
                object_id,
                change_ids=change_ids,
                validation_evidence=validation_evidence,
            )
    except Exception as exc:
        workflow.transition(
            object_type,
            object_id,
            "candidate_preparation_unknown",
            actor=actor,
            metadata={"error": str(exc), "idempotency_key": idempotency_key},
        )
        raise
    if not bool(result.get("ok", True)) or result.get("error"):
        failed = workflow.transition(
            object_type,
            object_id,
            "candidate_preparation_failed",
            actor=actor,
            metadata={
                "error": result.get("error")
                or result.get("status")
                or "trial_failed",
                "idempotency_key": idempotency_key,
            },
        )
        return {**dict(result), "ok": False, "workflow": failed.get("workflow")}
    candidate = _mapping(result.get("candidate"))
    release = _mapping(result.get("release"))
    candidate_id = str(candidate.get("candidate_id") or "").strip()
    release_digest = str(
        candidate.get("release_digest") or release.get("release_digest") or ""
    ).strip()
    package_digest = str(candidate.get("package_digest") or "").strip()
    if not candidate_id or not release_digest or not package_digest:
        workflow.transition(
            object_type,
            object_id,
            "candidate_preparation_unknown",
            actor=actor,
            metadata={
                "error": "candidate_identity_incomplete",
                "idempotency_key": idempotency_key,
            },
        )
        raise ValueError("Candidate preparation returned incomplete immutable identity")
    completed = workflow.transition(
        object_type,
        object_id,
        "candidate_prepared",
        actor=actor,
        metadata={
            "candidate_id": candidate_id,
            "release": f"{release.get('project_id')}@{release.get('version')}",
            "release_digest": release_digest,
            "package_digest": package_digest,
            "base_release": candidate.get("base_release"),
            "base_release_digest": candidate.get("base_release_digest"),
            "trial_workspace": result.get("trial_workspace"),
            "run_id": f"candidate:{candidate_id}:prepare",
            "idempotency_key": idempotency_key,
        },
    )
    return {**dict(result), "workflow": completed.get("workflow"), "started": started}


def decide_trial(
    object_type: str,
    object_id: str,
    *,
    accepted: bool,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    state = workflow.get_state(object_type, object_id)
    candidate_id, candidate_digest = _candidate_identity(state)
    decided = projects.decide_candidate(
        candidate_id,
        accepted=accepted,
        observations=[
            {
                "actor": actor,
                "decision": "accepted_for_publication"
                if accepted
                else "changes_requested",
                "idempotency_key": idempotency_key,
            }
        ],
    )
    transitioned = workflow.transition(
        object_type,
        object_id,
        "candidate_accepted" if accepted else "candidate_rejected",
        actor=actor,
        metadata={
            "candidate_id": candidate_id,
            "candidate_digest": candidate_digest,
            "observations": _mapping(decided.get("candidate")).get("trials") or [],
            "run_id": f"candidate:{candidate_id}:decision",
            "idempotency_key": idempotency_key,
        },
    )
    return {**dict(decided), "workflow": transitioned.get("workflow")}


def publish_candidate(
    object_type: str,
    object_id: str,
    *,
    actor: str,
    idempotency_key: str,
) -> dict[str, Any]:
    state = workflow.get_state(object_type, object_id)
    candidate_id, candidate_digest = _candidate_identity(state)
    automation_state = _mapping(state.get("automation"))
    workflow.transition(
        object_type,
        object_id,
        "publication_started",
        actor=actor,
        metadata={
            "confirmed": True,
            "run_id": f"candidate:{candidate_id}:publish",
            "idempotency_key": idempotency_key,
        },
    )
    try:
        result = projects.promote_candidate(
            candidate_id,
            permission_decision={
                "approved": True,
                "actor": actor,
                "actor_type": "user",
                "approval_id": f"candidate:{candidate_id}:publication",
            },
        )
    except Exception as exc:
        workflow.transition(
            object_type,
            object_id,
            "publication_unknown",
            actor=actor,
            metadata={"error": str(exc), "idempotency_key": idempotency_key},
        )
        raise
    status = str(result.get("status") or "").strip().lower()
    if status == "stale":
        workflow.transition(
            object_type,
            object_id,
            "publication_failed",
            actor=actor,
            metadata={"error": "candidate_stale", "idempotency_key": idempotency_key},
        )
        stale = workflow.transition(
            object_type,
            object_id,
            "candidate_stale",
            actor=actor,
            metadata={
                "candidate_id": candidate_id,
                "rebase_plan": result.get("rebase_plan"),
                "idempotency_key": idempotency_key,
            },
        )
        return {**dict(result), "workflow": stale.get("workflow")}
    if not bool(result.get("ok", True)) or result.get("error"):
        failed = workflow.transition(
            object_type,
            object_id,
            "publication_failed",
            actor=actor,
            metadata={
                "error": result.get("error") or status or "publication_failed",
                "idempotency_key": idempotency_key,
            },
        )
        return {**dict(result), "ok": False, "workflow": failed.get("workflow")}
    completed = workflow.transition(
        object_type,
        object_id,
        "publish",
        actor=actor,
        metadata={
            "version": result.get("version"),
            "release": result.get("release"),
            "candidate_id": candidate_id,
            "candidate_digest": candidate_digest,
            "task_id": automation_state.get("head_task_id"),
            "apply_evidence": result.get("apply_evidence"),
            "run_id": f"candidate:{candidate_id}:published",
            "idempotency_key": idempotency_key,
        },
    )
    return {**dict(result), "workflow": completed.get("workflow")}


def invoke_activity_command(
    command: str,
    object_type: str,
    object_id: str,
    *,
    actor: str,
    idempotency_key: str,
    input_value: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    token = str(command or "").strip()
    if token not in ACTIVITY_COMMANDS:
        raise ValueError(f"unsupported Builder lifecycle activity command: {token}")
    details = {**dict(metadata or {}), **dict(input_value or {})}
    webspace_id = str(details.get("webspace_id") or "desktop").strip() or "desktop"
    conversation_id = str(details.get("conversation_id") or "").strip() or None
    if token == "start_automation":
        state = workflow.get_state(object_type, object_id)
        change = _mapping(state.get("change") or state.get("change_set"))
        return automation.start(
            object_type=object_type,
            object_id=object_id,
            implementation_brief=str(details.get("implementation_brief") or "").strip()
            or _implementation_brief(state),
            webspace_id=webspace_id,
            conversation_id=conversation_id,
            change_set_id=str(
                change.get("change_id") or change.get("change_set_id") or ""
            ).strip()
            or None,
        )
    if token == "retry_automation":
        return automation.submit(
            str(
                details.get("implementation_brief")
                or "Continue the active Builder Change and resolve the remaining acceptance criteria."
            ),
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
            conversation_id=conversation_id,
        )
    if token == "request_prototype_derivation":
        return automation.return_to_prototype(
            object_type=object_type,
            object_id=object_id,
            webspace_id=webspace_id,
        )
    if token == "start_trial":
        return prepare_trial(
            object_type,
            object_id,
            actor=actor,
            idempotency_key=idempotency_key,
        )
    if token in {"accept_trial", "reject_trial"}:
        return decide_trial(
            object_type,
            object_id,
            accepted=token == "accept_trial",
            actor=actor,
            idempotency_key=idempotency_key,
        )
    return publish_candidate(
        object_type,
        object_id,
        actor=actor,
        idempotency_key=idempotency_key,
    )


__all__ = [
    "ACTIVITY_COMMANDS",
    "decide_trial",
    "invoke_activity_command",
    "prepare_trial",
    "publish_candidate",
]
