"""Deterministic Builder lifecycle activities shared by Web, chat, and tools."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaos.sdk import navigation
from adaos.sdk.builder import automation, preview, workflow
from adaos.sdk.developer import compositions, projects


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


def _published_candidate_matches(
    publication: Mapping[str, Any],
    *,
    candidate_id: str,
    candidate_digest: str,
) -> bool:
    if str(publication.get("status") or "").strip() != "published":
        return False
    release_record = _mapping(publication.get("release_record"))
    published_id = str(
        release_record.get("candidate_id") or publication.get("candidate_id") or ""
    ).strip()
    published_digest = str(
        release_record.get("package_digest")
        or release_record.get("release_digest")
        or publication.get("package_digest")
        or publication.get("release_digest")
        or ""
    ).strip()
    return published_id == candidate_id and published_digest == candidate_digest


def prepare_trial(
    object_type: str,
    object_id: str,
    *,
    actor: str,
    idempotency_key: str,
    source_webspace_id: str = "desktop",
    target_webspace_id: str | None = None,
    publication_project_ref: str | None = None,
) -> dict[str, Any]:
    state = workflow.get_state(object_type, object_id)
    delivery = _mapping(state.get("delivery"))
    automation_state = _mapping(state.get("automation"))
    change = _mapping(state.get("change") or state.get("change_set"))
    if str(automation_state.get("status") or "") != "completed":
        raise ValueError("Trial requires completed Automation")
    delivery_status = str(delivery.get("status") or "")
    if delivery_status not in {"checkpoint", "activating"}:
        raise ValueError("Trial requires an exact Automation checkpoint or resumable activation")
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
    started: dict[str, Any] | None = None
    if delivery_status == "checkpoint":
        started = workflow.transition(
            object_type,
            object_id,
            "candidate_preparation_started",
            actor=actor,
            metadata={
                "confirmed": True,
                "run_id": f"trial:{idempotency_key}",
                "idempotency_key": f"{idempotency_key}:start",
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
    source_webspace = str(source_webspace_id or "desktop").strip() or "desktop"
    trial_webspace = str(target_webspace_id or "").strip()
    if not trial_webspace:
        try:
            trial_webspace = preview.dev_webspace_id(source_webspace)
        except Exception:
            trial_webspace = ""
    trial_webspace = trial_webspace or source_webspace
    scope = navigation.runtime_scope()
    try:
        stale_candidate = str(delivery.get("replaces_candidate_id") or "").strip()
        project_ref = str(publication_project_ref or "").strip()
        if project_ref and not project_ref.startswith("project:"):
            raise ValueError("publication_project_ref must use project:<id>")
        if stale_candidate and project_ref:
            raise ValueError(
                "Project Trial rebase requires a fresh immutable project candidate"
            )
        if project_ref:
            result = compositions.prepare_candidate(
                project_ref.split(":", 1)[1],
                source_kind=object_type,
                source_name=object_id,
                source_revision=str(delivery.get("source_revision") or "").strip(),
                change_ids=change_ids,
                validation_evidence=validation_evidence,
                target_webspace_id=trial_webspace,
                target_space_kind="development",
                target_zone=str(scope.get("zone") or "").strip() or None,
                target_subnet_id=str(scope.get("subnet_id") or "").strip() or None,
                idempotency_key=idempotency_key,
            )
        elif stale_candidate:
            result = projects.prepare_rebased_candidate(
                stale_candidate,
                object_type,
                object_id,
                validation_evidence=validation_evidence,
                target_webspace_id=trial_webspace,
                target_space_kind="development",
                target_zone=str(scope.get("zone") or "").strip() or None,
                target_subnet_id=str(scope.get("subnet_id") or "").strip() or None,
                idempotency_key=idempotency_key,
            )
        else:
            result = projects.prepare_candidate(
                object_type,
                object_id,
                change_ids=change_ids,
                validation_evidence=validation_evidence,
                target_webspace_id=trial_webspace,
                target_space_kind="development",
                target_zone=str(scope.get("zone") or "").strip() or None,
                target_subnet_id=str(scope.get("subnet_id") or "").strip() or None,
                idempotency_key=idempotency_key,
            )
    except Exception as exc:
        workflow.transition(
            object_type,
            object_id,
            "candidate_preparation_unknown",
            actor=actor,
            metadata={
                "error": str(exc),
                "idempotency_key": f"{idempotency_key}:unknown",
            },
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
                "idempotency_key": f"{idempotency_key}:failure",
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
                "idempotency_key": f"{idempotency_key}:unknown",
            },
        )
        raise ValueError("Candidate preparation returned incomplete immutable identity")
    completed = workflow.transition(
        object_type,
        object_id,
        "candidate_prepared",
        actor=actor,
        metadata={
            "confirmed": True,
            "candidate_id": candidate_id,
            "release": f"{release.get('project_id')}@{release.get('version')}",
            "release_digest": release_digest,
            "package_digest": package_digest,
            "base_release": candidate.get("base_release"),
            "base_release_digest": candidate.get("base_release_digest"),
            "trial_workspace": result.get("trial_workspace"),
            "run_id": f"candidate:{candidate_id}:prepare",
            "idempotency_key": f"{idempotency_key}:success",
        },
    )
    completed_workflow = _mapping(completed.get("workflow"))
    activation = _mapping(result.get("trial_activation"))
    activation_target = _mapping(activation.get("target"))
    if object_type == "scenario" and activation:
        placed = workflow.record_project_placement(
            object_type,
            object_id,
            {
                "kind": "trial",
                "result_ref": {
                    "kind": "candidate",
                    "id": candidate_id,
                    "version": str(release.get("version") or "").strip(),
                    "digest": package_digest,
                },
                "target": {
                    "zone": activation_target.get("zone"),
                    "subnet_id": activation_target.get("subnet_id"),
                    "webspace_id": activation_target.get("webspace_id") or trial_webspace,
                    "space_kind": activation_target.get("space_kind") or "development",
                },
                "scenario_id": activation_target.get("scenario_id") or object_id,
                "data_mode": activation.get("data_mode") or "empty",
                "runtime_binding": activation.get("runtime_binding") or {},
                "trial_activation_ref": activation.get("activation_id"),
                "safety": activation.get("safety_evidence") or {},
            },
            expected_generation=int(completed_workflow.get("generation") or 0),
        )
        completed_workflow = _mapping(placed.get("workflow"))
    return {
        **dict(result),
        "workflow": completed_workflow,
        "started": started,
        "resumed": delivery_status == "activating",
    }


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
            "confirmed": True,
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
    delivery_state = _mapping(state.get("delivery"))
    publication_state = _mapping(state.get("publication"))
    publication_status = str(publication_state.get("status") or "not_started").strip()
    if _published_candidate_matches(
        publication_state,
        candidate_id=candidate_id,
        candidate_digest=candidate_digest,
    ):
        return {
            "ok": True,
            "status": "published",
            "duplicate": True,
            "workflow": workflow.get_state(object_type, object_id),
        }
    published_or_publishing_current = (
        publication_status == "publishing"
        and str(publication_state.get("candidate_id") or "").strip() == candidate_id
    )
    publication_attempt_key = idempotency_key
    if (
        publication_status == "unknown"
        and str(delivery_state.get("status") or "").strip() == "unknown"
        and str(delivery_state.get("candidate_id") or "").strip() == candidate_id
    ):
        reconciled = workflow.transition(
            object_type,
            object_id,
            "reconcile_publication",
            actor=actor,
            metadata={
                "evidence_refs": [f"candidate:{candidate_id}:idempotent-promotion-resume"],
                "run_id": f"candidate:{candidate_id}:reconcile-publication",
                "idempotency_key": f"{idempotency_key}:reconcile",
            },
        )
        reconciled_workflow = _mapping(reconciled.get("workflow"))
        reconciled_generation = int(
            reconciled_workflow.get("generation") or int(state.get("generation") or 0) + 1
        )
        publication_attempt_key = f"{idempotency_key}:resume:{reconciled_generation}"
    elif (
        publication_status == "ready"
        and publication_state.get("reconciled_at")
        and str(delivery_state.get("status") or "").strip() == "accepted"
        and str(delivery_state.get("candidate_id") or "").strip() == candidate_id
    ):
        publication_attempt_key = (
            f"{idempotency_key}:resume:{int(state.get('generation') or 0)}"
        )
    if not published_or_publishing_current:
        workflow.transition(
            object_type,
            object_id,
            "publication_started",
            actor=actor,
            metadata={
                "confirmed": True,
                "run_id": f"candidate:{candidate_id}:publish",
                "idempotency_key": f"{publication_attempt_key}:start",
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
            metadata={
                "error": str(exc),
                "idempotency_key": f"{publication_attempt_key}:unknown",
            },
        )
        raise
    status = str(result.get("status") or "").strip().lower()
    if status == "stale":
        workflow.transition(
            object_type,
            object_id,
            "publication_failed",
            actor=actor,
            metadata={
                "error": "candidate_stale",
                "idempotency_key": f"{publication_attempt_key}:failure",
            },
        )
        stale = workflow.transition(
            object_type,
            object_id,
            "candidate_stale",
            actor=actor,
            metadata={
                "candidate_id": candidate_id,
                "rebase_plan": result.get("rebase_plan"),
                "idempotency_key": f"{publication_attempt_key}:stale",
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
                "idempotency_key": f"{publication_attempt_key}:failure",
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
            "idempotency_key": f"{publication_attempt_key}:success",
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
            source_webspace_id=webspace_id,
            target_webspace_id=str(details.get("target_webspace_id") or "").strip()
            or None,
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
