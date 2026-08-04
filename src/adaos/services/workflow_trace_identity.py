from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Mapping

from adaos.services.governed_workflow import (
    WORKFLOW_TRACE_IDENTITY_SCHEMA,
    validate_workflow_record,
    workflow_ref,
)


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )
    return f"sha256:{hashlib.sha256(raw).hexdigest()}"


def _mapping(value: Mapping[str, Any] | None) -> dict[str, Any]:
    return copy.deepcopy(dict(value or {}))


def _maybe_ref(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    record = _mapping(value)
    if not record:
        return None
    return record


def _string(value: Any) -> str | None:
    token = str(value or "").strip()
    return token or None


def _nested(mapping: Mapping[str, Any] | None, *path: str) -> Any:
    current: Any = mapping
    for key in path:
        if not isinstance(current, Mapping):
            return None
        current = current.get(key)
    return current


def _first_workflow_act(proposal: Mapping[str, Any]) -> dict[str, Any]:
    for item in proposal.get("semantic_acts") or []:
        if isinstance(item, Mapping) and item.get("kind") == "workflow_command":
            return dict(item)
    return {}


def _first_event_id(execution_result: Mapping[str, Any]) -> str | None:
    decision = execution_result.get("decision")
    if not isinstance(decision, Mapping):
        return None
    for item in decision.get("event_records") or []:
        if isinstance(item, Mapping):
            event_id = _string(item.get("event_id"))
            if event_id:
                return event_id
    return None


def _run_ref_from_execution(execution_result: Mapping[str, Any]) -> dict[str, Any] | None:
    commit = execution_result.get("commit")
    if not isinstance(commit, Mapping):
        return None
    for key in ("run_id", "task_id", "activity_attempt_id", "outbox_id"):
        token = _string(commit.get(key))
        if token:
            return workflow_ref("activity_run" if key == "activity_attempt_id" else "task", token)
    return None


def _add_mismatch(
    diagnostics: list[dict[str, str]],
    *,
    code: str,
    path: str,
    label: str,
    values: Mapping[str, Any],
) -> None:
    present = {key: value for key, value in values.items() if value not in (None, "", [])}
    normalized = {json.dumps(value, sort_keys=True, separators=(",", ":")) for value in present.values()}
    if len(normalized) <= 1:
        return
    diagnostics.append(
        {
            "code": code,
            "severity": "error",
            "path": path,
            "message": f"{label} differs across trace links",
        }
    )


def _workflow_identity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "kind": value.get("kind"),
        "id": value.get("id"),
        "version": value.get("version"),
        "digest": value.get("digest"),
    }


def _run_identity(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return {
        "kind": value.get("kind"),
        "id": value.get("id"),
        "version": value.get("version"),
        "generation": value.get("generation"),
        "digest": value.get("digest"),
    }


def workflow_trace_identity_report(
    *,
    turn_trace_id: str | None = None,
    intent_proposal: Mapping[str, Any] | None = None,
    interaction: Mapping[str, Any] | None = None,
    interaction_response: Mapping[str, Any] | None = None,
    invocation: Mapping[str, Any] | None = None,
    execution_result: Mapping[str, Any] | None = None,
    activity_run: Mapping[str, Any] | None = None,
    conversation_output: Mapping[str, Any] | None = None,
    response_envelope: Mapping[str, Any] | None = None,
    delivery_attempt: Mapping[str, Any] | None = None,
    trace_id: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    proposal = _mapping(intent_proposal)
    act = _first_workflow_act(proposal)
    interaction_record = _mapping(interaction)
    response = _mapping(interaction_response)
    invocation_record = _mapping(invocation or _nested(execution_result, "invocation"))
    command = _mapping(invocation_record.get("command") if invocation_record else {})
    result = _mapping(execution_result)
    decision_record = _mapping(result.get("decision") if result else None)
    event_record = next(
        (
            dict(item)
            for item in decision_record.get("event_records") or []
            if isinstance(item, Mapping)
        ),
        {},
    )
    output = _mapping(conversation_output)
    output_correlation = _mapping(output.get("correlation") if output else {})
    envelope = _mapping(response_envelope)
    envelope_correlation = _mapping(envelope.get("correlation") if envelope else {})
    attempt = _mapping(delivery_attempt)
    activity = _mapping(activity_run)

    diagnostics: list[dict[str, str]] = []

    proposal_id = _string(proposal.get("proposal_id"))
    interaction_id = _string(interaction_record.get("interaction_id"))
    response_id = _string(response.get("response_id"))
    invocation_id = _string(invocation_record.get("invocation_id"))
    output_id = _string(output.get("output_id"))
    envelope_id = _string(envelope.get("envelope_id"))
    attempt_id = _string(attempt.get("attempt_id"))
    activity_run_id = _string(activity.get("attempt_id")) or _string(
        _nested(result, "commit", "activity_attempt_id")
    )

    command_reply_route = _maybe_ref(command.get("reply_route_ref") if isinstance(command, Mapping) else None)
    output_reply_route = _maybe_ref(output_correlation.get("reply_route_ref"))
    envelope_routes = [
        _string(item) for item in envelope.get("reply_route_ids") or [] if _string(item)
    ]
    attempt_route_id = _string(attempt.get("route_id"))

    selected_conversation = _string(
        proposal.get("conversation_id")
        or invocation_record.get("conversation_id")
        or output.get("conversation_id")
        or envelope.get("conversation_id")
    )
    selected_turn_trace_id = _string(
        turn_trace_id
        or proposal.get("turn_trace_id")
        or interaction_record.get("turn_trace_id")
        or response.get("turn_trace_id")
        or invocation_record.get("turn_trace_id")
        or output_correlation.get("turn_trace_id")
        or envelope.get("turn_trace_id")
        or attempt.get("turn_trace_id")
    )
    selected_command_id = _string(
        act.get("command")
        or command.get("command_id")
        or _nested(result, "decision", "command")
        or output_correlation.get("command_id")
        or envelope_correlation.get("command_id")
    )
    selected_workflow_ref = _maybe_ref(
        output_correlation.get("workflow_ref")
        or envelope_correlation.get("workflow_ref")
        or command.get("instance_ref")
    )
    raw_run_ref = _maybe_ref(
        output_correlation.get("run_ref")
        or envelope_correlation.get("task_ref")
        or _run_ref_from_execution(result)
        or (workflow_ref("activity_run", activity_run_id) if activity_run_id else None)
    )
    selected_run_ref = (
        workflow_ref(
            "activity_run" if raw_run_ref.get("kind") == "activity_run" else "task",
            str(raw_run_ref["id"]),
            version=_string(raw_run_ref.get("version")),
            generation=(
                int(raw_run_ref["generation"])
                if raw_run_ref.get("generation") is not None
                else None
            ),
            digest=_string(raw_run_ref.get("digest")),
        )
        if raw_run_ref
        else None
    )
    selected_workflow_event_id = _string(
        _first_event_id(result) or output_correlation.get("workflow_event_id")
    )
    selected_reply_route_id = _string(
        (command_reply_route or {}).get("id")
        or (output_reply_route or {}).get("id")
        or (envelope_routes[0] if envelope_routes else None)
        or attempt_route_id
    )

    _add_mismatch(
        diagnostics,
        code="workflow.trace.turn_trace_mismatch",
        path="turn_trace_id",
        label="turn_trace_id",
        values={
            "intent_proposal": proposal.get("turn_trace_id"),
            "interaction": interaction_record.get("turn_trace_id"),
            "interaction_response": response.get("turn_trace_id"),
            "invocation": invocation_record.get("turn_trace_id"),
            "command": command.get("turn_trace_id"),
            "workflow_event": event_record.get("turn_trace_id"),
            "activity_run": activity.get("turn_trace_id")
            or _nested(activity, "effect_binding", "turn_trace_id"),
            "conversation_output": output_correlation.get("turn_trace_id"),
            "response_envelope": envelope.get("turn_trace_id"),
            "delivery_attempt": attempt.get("turn_trace_id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.context_mismatch",
        path="trace.trace_id",
        label="trace.trace_id",
        values={
            "intent_proposal": _nested(proposal, "trace", "trace_id"),
            "interaction": _nested(interaction_record, "trace", "trace_id"),
            "interaction_response": _nested(response, "trace", "trace_id"),
            "invocation": _nested(invocation_record, "trace", "trace_id"),
            "command": _nested(command, "trace", "trace_id"),
            "workflow_event": _nested(event_record, "trace", "trace_id"),
            "activity_run": _nested(activity, "trace", "trace_id")
            or _nested(activity, "effect_binding", "trace", "trace_id"),
            "conversation_output": _nested(output, "trace", "trace_id"),
            "response_envelope": _nested(envelope, "trace", "trace_id"),
            "delivery_attempt": _nested(attempt, "trace", "trace_id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.conversation_mismatch",
        path="conversation_id",
        label="conversation_id",
        values={
            "intent_proposal": proposal.get("conversation_id"),
            "invocation": invocation_record.get("conversation_id"),
            "conversation_output": output.get("conversation_id"),
            "response_envelope": envelope.get("conversation_id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.intent_proposal_mismatch",
        path="intent_proposal_id",
        label="intent_proposal_id",
        values={
            "intent_proposal": proposal_id,
            "invocation": _nested(invocation_record, "metadata", "intent_proposal_id"),
            "conversation_output": output_correlation.get("intent_proposal_id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.interaction_mismatch",
        path="interaction_id",
        label="interaction_id",
        values={
            "intent_proposal": act.get("interaction_id"),
            "interaction": interaction_id,
            "interaction_response": response.get("interaction_id"),
            "invocation": _nested(invocation_record, "interaction_ref", "id"),
            "conversation_output": output_correlation.get("interaction_id"),
            "response_envelope": _nested(envelope_correlation, "interaction_ref", "id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.interaction_response_mismatch",
        path="interaction_response_id",
        label="interaction_response_id",
        values={
            "interaction_response": response_id,
            "invocation": _nested(invocation_record, "response_ref", "id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.command_mismatch",
        path="command_id",
        label="command_id",
        values={
            "intent_proposal": act.get("command"),
            "invocation": command.get("command_id"),
            "decision": _nested(result, "decision", "command"),
            "conversation_output": output_correlation.get("command_id"),
            "response_envelope": envelope_correlation.get("command_id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.workflow_ref_mismatch",
        path="workflow_ref",
        label="workflow_ref",
        values={
            "invocation": _workflow_identity(command.get("instance_ref")),
            "conversation_output": _workflow_identity(output_correlation.get("workflow_ref")),
            "response_envelope": _workflow_identity(envelope_correlation.get("workflow_ref")),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.workflow_event_mismatch",
        path="workflow_event_id",
        label="workflow_event_id",
        values={
            "execution_result": _first_event_id(result),
            "conversation_output": output_correlation.get("workflow_event_id"),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.run_ref_mismatch",
        path="run_ref",
        label="run_ref",
        values={
            "execution_result": _run_identity(_run_ref_from_execution(result)),
            "conversation_output": _run_identity(output_correlation.get("run_ref")),
            "response_envelope": _run_identity(envelope_correlation.get("task_ref")),
        },
    )
    _add_mismatch(
        diagnostics,
        code="workflow.trace.response_envelope_mismatch",
        path="response_envelope_id",
        label="response_envelope_id",
        values={
            "conversation_output": _nested(output, "response_envelope_ref", "id"),
            "response_envelope": envelope_id,
            "delivery_attempt": attempt.get("envelope_id"),
        },
    )
    reply_route_values = {
        "invocation": _string((command_reply_route or {}).get("id")),
        "conversation_output": _string((output_reply_route or {}).get("id")),
        "delivery_attempt": attempt_route_id,
    }
    non_envelope_routes = {value for value in reply_route_values.values() if value}
    if len(non_envelope_routes) > 1 or (
        non_envelope_routes
        and envelope_routes
        and not non_envelope_routes.intersection(set(envelope_routes))
    ):
        diagnostics.append(
            {
                "code": "workflow.trace.reply_route_mismatch",
                "severity": "error",
                "path": "reply_route_id",
                "message": "reply_route_id differs across trace links",
            }
        )

    if response_id and not _nested(invocation_record, "response_ref", "id"):
        diagnostics.append(
            {
                "code": "workflow.trace.response_ref_missing",
                "severity": "warning",
                "path": "invocation.response_ref",
                "message": "interaction_response is present but invocation.response_ref is empty",
            }
        )

    seed = {
        "turn_trace_id": selected_turn_trace_id,
        "proposal_id": proposal_id,
        "interaction_id": interaction_id,
        "invocation_id": invocation_id,
        "output_id": output_id,
        "envelope_id": envelope_id,
        "attempt_id": attempt_id,
    }
    report = {
        "schema": WORKFLOW_TRACE_IDENTITY_SCHEMA,
        "trace_id": _string(trace_id)
        or _string(_nested(proposal, "trace", "trace_id"))
        or _string(_nested(invocation_record, "trace", "trace_id"))
        or "trace:" + _digest(seed).removeprefix("sha256:"),
        "valid": not any(item["severity"] == "error" for item in diagnostics),
        "generated_at": now or _now(),
        "conversation_id": selected_conversation,
        "turn_trace_id": selected_turn_trace_id,
        "intent_proposal_id": proposal_id
        or _string(_nested(invocation_record, "metadata", "intent_proposal_id"))
        or _string(output_correlation.get("intent_proposal_id")),
        "interaction_id": interaction_id
        or _string(response.get("interaction_id"))
        or _string(_nested(invocation_record, "interaction_ref", "id"))
        or _string(output_correlation.get("interaction_id"))
        or _string(_nested(envelope_correlation, "interaction_ref", "id")),
        "interaction_response_id": response_id or _string(_nested(invocation_record, "response_ref", "id")),
        "invocation_id": invocation_id,
        "command_id": selected_command_id,
        "workflow_ref": selected_workflow_ref,
        "workflow_event_id": selected_workflow_event_id,
        "activity_run_id": activity_run_id,
        "run_ref": selected_run_ref,
        "conversation_output_id": output_id,
        "response_envelope_id": envelope_id or _string(_nested(output, "response_envelope_ref", "id")),
        "delivery_attempt_id": attempt_id,
        "reply_route_id": selected_reply_route_id,
        "diagnostics": diagnostics,
        "links": {
            "intent_proposal_ref": workflow_ref("evidence", proposal_id) if proposal_id else None,
            "interaction_ref": workflow_ref("interaction", interaction_id) if interaction_id else None,
            "interaction_response_ref": (
                workflow_ref("interaction_response", response_id) if response_id else None
            ),
            "invocation_ref": workflow_ref("evidence", invocation_id) if invocation_id else None,
            "workflow_event_ref": (
                workflow_ref("evidence", selected_workflow_event_id)
                if selected_workflow_event_id
                else None
            ),
            "activity_run_ref": (
                workflow_ref("activity_run", activity_run_id)
                if activity_run_id
                else None
            ),
            "conversation_output_ref": workflow_ref("evidence", output_id) if output_id else None,
            "response_envelope_ref": (
                workflow_ref(
                    "evidence",
                    envelope_id or _string(_nested(output, "response_envelope_ref", "id")),
                )
                if envelope_id or _string(_nested(output, "response_envelope_ref", "id"))
                else None
            ),
            "delivery_attempt_ref": workflow_ref("evidence", attempt_id) if attempt_id else None,
            "reply_route_ref": (
                workflow_ref("reply_route", selected_reply_route_id)
                if selected_reply_route_id
                else None
            ),
        },
    }
    return validate_workflow_record(WORKFLOW_TRACE_IDENTITY_SCHEMA, report)


__all__ = [
    "WORKFLOW_TRACE_IDENTITY_SCHEMA",
    "workflow_trace_identity_report",
]
