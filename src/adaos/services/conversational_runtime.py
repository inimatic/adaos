from __future__ import annotations

import copy
import hashlib
import json
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services.governed_workflow import validate_workflow_record, workflow_ref


INTENT_PROPOSAL_SCHEMA = "adaos.intent.proposal.v1"
CONVERSATION_OUTPUT_SCHEMA = "adaos.conversation.output.v1"
RESPONSE_ENVELOPE_SCHEMA = "adaos.conversation.response_envelope.v1"
WORKFLOW_COMMAND_SCHEMA = "adaos.workflow.command.v1"
WORKFLOW_INVOCATION_SCHEMA = "adaos.workflow.invocation.v1"

_CONVERSATION_RISK_BY_WORKFLOW_RISK = {
    "read": "none",
    "local_reversible": "low",
    "isolated_write": "medium",
    "trial_activation": "medium",
    "workspace_activation": "high",
    "publication": "high",
    "destructive": "destructive",
}

_RESPONSE_CATEGORY_BY_OUTPUT_KIND = {
    "clarification": "input_required",
    "confirmation": "input_required",
    "accepted": "accepted",
    "progress": "progress",
    "result": "terminal",
    "repair": "notification",
    "refusal": "notification",
    "handoff": "notification",
}

_AUTHORIZATION_REJECTION_MARKERS = (
    "actor_not_authorized",
    "missing_permission",
    "role_not_authorized",
    "unverified_principal",
    "invalid_principal",
    "principal_actor_mismatch",
)


class ConversationalRuntimeError(ValueError):
    """Raised when a conversational runtime record cannot satisfy the ABI."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _digest(value: Any) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _stable_id(prefix: str, value: Mapping[str, Any]) -> str:
    return f"{prefix}:{_digest(value).removeprefix('sha256:')[:32]}"


def _generic_ref(
    kind: str,
    identifier: str,
    *,
    schema: str | None = None,
    version: str | None = None,
    generation: int | None = None,
    digest: str | None = None,
) -> dict[str, Any]:
    return {
        "schema": schema,
        "kind": str(kind or "").strip(),
        "id": str(identifier or "").strip(),
        "version": str(version).strip() if version is not None else None,
        "generation": int(generation) if generation is not None else None,
        "digest": str(digest).strip() if digest is not None else None,
    }


@lru_cache(maxsize=16)
def _abi_schema(schema_name: str) -> dict[str, Any]:
    filename = schema_name.removeprefix("adaos.")
    path = Path(__file__).resolve().parents[1] / "abi" / f"{filename}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_schema(schema_name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    errors = sorted(
        Draft202012Validator(_abi_schema(schema_name)).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        first = errors[0]
        location = ".".join(str(item) for item in first.absolute_path) or "$"
        raise ConversationalRuntimeError(
            f"{schema_name} validation failed at {location}: {first.message}"
        )
    return record


def validate_intent_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(INTENT_PROPOSAL_SCHEMA, value)


def validate_conversation_output(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(CONVERSATION_OUTPUT_SCHEMA, value)


def validate_response_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(RESPONSE_ENVELOPE_SCHEMA, value)


def _deepcopy_mapping(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else None


def _normalize_workflow_ref(
    value: Mapping[str, Any] | None,
    *,
    fallback_kind: str | None = None,
) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    kind = str(value.get("kind") or fallback_kind or "").strip()
    identifier = str(value.get("id") or value.get("instance_id") or "").strip()
    if not kind or not identifier:
        return None
    generation = value.get("generation")
    return workflow_ref(
        kind,
        identifier,
        version=str(value.get("version")).strip() if value.get("version") is not None else None,
        generation=int(generation) if generation is not None else None,
        digest=str(value.get("digest")).strip() if value.get("digest") is not None else None,
    )


def _semantic_act(
    *,
    act_id: str,
    kind: str,
    text: str,
    target_ref: Mapping[str, Any] | None,
    interaction_id: str | None,
    command: str | None,
    arguments: Mapping[str, Any] | None,
    confidence: float,
    skill_invocation: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    act = {
        "act_id": str(act_id or "").strip(),
        "kind": kind,
        "text": str(text or "").strip(),
        "target_ref": _deepcopy_mapping(target_ref),
        "interaction_id": str(interaction_id).strip() if interaction_id is not None else None,
        "command": str(command).strip() if command is not None else None,
        "arguments": copy.deepcopy(dict(arguments or {})),
        "confidence": float(confidence),
    }
    if skill_invocation is not None:
        act["skill_invocation"] = copy.deepcopy(dict(skill_invocation))
    return act


def _proposal_record(
    *,
    conversation_id: str,
    source_message_id: str,
    source_text: str,
    locale: str,
    semantic_acts: Sequence[Mapping[str, Any]],
    alternatives: Sequence[Mapping[str, Any]] = (),
    allowed_command_snapshot: Sequence[Mapping[str, Any]] = (),
    model: Mapping[str, Any] | None = None,
    disposition: str = "proposed",
    clarification: Mapping[str, Any] | None = None,
    proposal_id: str | None = None,
    retention_class: str = "normal",
    redaction: str = "policy",
    supersedes_proposal_id: str | None = None,
    committed_response_ref: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    timestamp = now or _now()
    base = {
        "conversation_id": str(conversation_id or "").strip(),
        "source_message_id": str(source_message_id or "").strip(),
        "source_text": str(source_text or "").strip(),
        "acts": [dict(item) for item in semantic_acts],
        "supersedes": supersedes_proposal_id,
    }
    record = {
        "schema": INTENT_PROPOSAL_SCHEMA,
        "proposal_id": str(proposal_id or _stable_id("intent", base)).strip(),
        "conversation_id": base["conversation_id"],
        "source_message_id": base["source_message_id"],
        "source_text": base["source_text"],
        "locale": str(locale or "en").strip(),
        "semantic_acts": [copy.deepcopy(dict(item)) for item in semantic_acts],
        "alternatives": [copy.deepcopy(dict(item)) for item in alternatives],
        "allowed_command_snapshot": [
            copy.deepcopy(dict(item)) for item in allowed_command_snapshot
        ],
        "model": copy.deepcopy(
            dict(
                model
                or {
                    "provider": "adaos",
                    "name": "conversational-runtime",
                    "version": "1.0.0",
                }
            )
        ),
        "disposition": str(disposition or "proposed").strip(),
        "clarification": _deepcopy_mapping(clarification),
        "supersedes_proposal_id": (
            str(supersedes_proposal_id).strip()
            if supersedes_proposal_id is not None
            else None
        ),
        "committed_response_ref": _deepcopy_mapping(committed_response_ref),
        "retention": {"class": retention_class, "redaction": redaction},
        "created_at": timestamp,
        "updated_at": timestamp,
    }
    return validate_intent_proposal(record)


def build_workflow_intent_proposal(
    *,
    conversation_id: str,
    source_message_id: str,
    source_text: str,
    workflow_type: str,
    command_id: str,
    instance_ref: Mapping[str, Any],
    expected_generation: int | None = None,
    input_value: Mapping[str, Any] | None = None,
    target_ref: Mapping[str, Any] | None = None,
    interaction_id: str | None = None,
    action_id: str | None = None,
    context_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    risk: str = "read",
    confirmation_required: bool = False,
    confidence: float = 1.0,
    locale: str = "en",
    proposal_id: str | None = None,
    model: Mapping[str, Any] | None = None,
    disposition: str = "proposed",
    clarification: Mapping[str, Any] | None = None,
    alternatives: Sequence[Mapping[str, Any]] = (),
    allowed_command_snapshot: Sequence[Mapping[str, Any]] = (),
    retention_class: str = "normal",
    redaction: str = "policy",
    supersedes_proposal_id: str | None = None,
    committed_response_ref: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    instance = _normalize_workflow_ref(instance_ref, fallback_kind="workflow")
    if instance is None:
        raise ConversationalRuntimeError("workflow intent requires an exact instance_ref")
    generation = expected_generation
    if generation is None and instance.get("generation") is not None:
        generation = int(instance["generation"])
    if generation is None:
        raise ConversationalRuntimeError("workflow intent requires expected_generation")
    arguments = {
        "workflow_type": str(workflow_type or "").strip(),
        "instance_ref": instance,
        "expected_generation": int(generation),
        "input": copy.deepcopy(dict(input_value or {})),
        "action_id": str(action_id).strip() if action_id is not None else None,
        "context_ref": _normalize_workflow_ref(context_ref, fallback_kind="command_context"),
        "reply_route_ref": _normalize_workflow_ref(reply_route_ref, fallback_kind="reply_route"),
        "risk": str(risk or "read").strip(),
        "confirmation_required": bool(confirmation_required),
    }
    act = _semantic_act(
        act_id="act.1",
        kind="workflow_command",
        text=source_text,
        target_ref=target_ref or instance,
        interaction_id=interaction_id,
        command=command_id,
        arguments=arguments,
        confidence=confidence,
    )
    return _proposal_record(
        conversation_id=conversation_id,
        source_message_id=source_message_id,
        source_text=source_text,
        locale=locale,
        semantic_acts=(act,),
        alternatives=alternatives,
        allowed_command_snapshot=allowed_command_snapshot,
        model=model,
        disposition=disposition,
        clarification=clarification,
        proposal_id=proposal_id,
        retention_class=retention_class,
        redaction=redaction,
        supersedes_proposal_id=supersedes_proposal_id,
        committed_response_ref=committed_response_ref,
        now=now,
    )


def build_skill_intent_proposal(
    *,
    conversation_id: str,
    source_message_id: str,
    source_text: str,
    skill_id: str,
    operation_id: str,
    arguments: Mapping[str, Any] | None = None,
    target_ref: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
    locale: str = "en",
    proposal_id: str | None = None,
    model: Mapping[str, Any] | None = None,
    disposition: str = "proposed",
    clarification: Mapping[str, Any] | None = None,
    alternatives: Sequence[Mapping[str, Any]] = (),
    retention_class: str = "normal",
    redaction: str = "policy",
    supersedes_proposal_id: str | None = None,
    committed_response_ref: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    skill_invocation = {
        "skill_id": str(skill_id or "").strip(),
        "operation_id": str(operation_id or "").strip(),
        "arguments": copy.deepcopy(dict(arguments or {})),
    }
    act = _semantic_act(
        act_id="act.1",
        kind="skill_invocation",
        text=source_text,
        target_ref=target_ref or _generic_ref("skill", skill_invocation["skill_id"]),
        interaction_id=None,
        command=None,
        arguments=skill_invocation,
        confidence=confidence,
        skill_invocation=skill_invocation,
    )
    return _proposal_record(
        conversation_id=conversation_id,
        source_message_id=source_message_id,
        source_text=source_text,
        locale=locale,
        semantic_acts=(act,),
        alternatives=alternatives,
        allowed_command_snapshot=(),
        model=model,
        disposition=disposition,
        clarification=clarification,
        proposal_id=proposal_id,
        retention_class=retention_class,
        redaction=redaction,
        supersedes_proposal_id=supersedes_proposal_id,
        committed_response_ref=committed_response_ref,
        now=now,
    )


def _single_act(proposal: Mapping[str, Any], kind: str) -> dict[str, Any]:
    record = validate_intent_proposal(proposal)
    acts = [dict(item) for item in record["semantic_acts"] if item.get("kind") == kind]
    if len(acts) != 1:
        raise ConversationalRuntimeError(
            f"intent proposal must contain exactly one {kind} act"
        )
    return acts[0]


def workflow_invocation_from_intent_proposal(
    proposal: Mapping[str, Any],
    *,
    actor_id: str,
    idempotency_key: str | None = None,
    source: str = "intent",
    workflow_type: str | None = None,
    command_id: str | None = None,
    instance_ref: Mapping[str, Any] | None = None,
    expected_generation: int | None = None,
    input_value: Mapping[str, Any] | None = None,
    context_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    target_ref: Mapping[str, Any] | None = None,
    risk: str | None = None,
    confirmation_required: bool | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    record = validate_intent_proposal(proposal)
    if record["disposition"] != "proposed":
        raise ConversationalRuntimeError(
            f"intent proposal is not invocable: {record['disposition']}"
        )
    act = _single_act(record, "workflow_command")
    arguments = dict(act.get("arguments") or {})
    instance = _normalize_workflow_ref(
        instance_ref or arguments.get("instance_ref"),
        fallback_kind="workflow",
    )
    if instance is None or not instance.get("version") or instance.get("generation") is None:
        raise ConversationalRuntimeError(
            "workflow intent proposal requires exact instance_ref version and generation"
        )
    generation = expected_generation
    if generation is None:
        generation = arguments.get("expected_generation", instance.get("generation"))
    if generation is None:
        raise ConversationalRuntimeError("workflow intent proposal requires expected_generation")
    selected_command = str(command_id or act.get("command") or "").strip()
    if not selected_command:
        raise ConversationalRuntimeError("workflow intent proposal requires command")
    selected_workflow_type = str(workflow_type or arguments.get("workflow_type") or "").strip()
    if not selected_workflow_type:
        raise ConversationalRuntimeError("workflow intent proposal requires workflow_type")
    command_input = (
        copy.deepcopy(dict(input_value))
        if isinstance(input_value, Mapping)
        else copy.deepcopy(dict(arguments.get("input") or arguments.get("command_input") or {}))
    )
    selected_context_ref = _normalize_workflow_ref(
        context_ref or arguments.get("context_ref"),
        fallback_kind="command_context",
    )
    selected_reply_route_ref = _normalize_workflow_ref(
        reply_route_ref or arguments.get("reply_route_ref"),
        fallback_kind="reply_route",
    )
    selected_risk = str(risk or arguments.get("risk") or "read").strip()
    selected_confirmation = (
        bool(confirmation_required)
        if confirmation_required is not None
        else bool(arguments.get("confirmation_required"))
    )
    timestamp = now or str(record.get("created_at") or _now())
    key = str(
        idempotency_key
        or arguments.get("idempotency_key")
        or f"intent:{record['proposal_id']}:{act['act_id']}"
    ).strip()
    command = validate_workflow_record(
        WORKFLOW_COMMAND_SCHEMA,
        {
            "schema": WORKFLOW_COMMAND_SCHEMA,
            "command_id": selected_command,
            "workflow_type": selected_workflow_type,
            "instance_ref": instance,
            "actor_ref": workflow_ref("principal", actor_id),
            "expected_generation": int(generation),
            "idempotency_key": key,
            "input": command_input,
            "context_ref": selected_context_ref,
            "reply_route_ref": selected_reply_route_ref,
            "created_at": timestamp,
        },
    )
    invocation_seed = {
        "proposal_id": record["proposal_id"],
        "act_id": act["act_id"],
        "command": command,
        "actor_id": actor_id,
    }
    interaction_id = str(act.get("interaction_id") or "").strip()
    invocation = {
        "schema": WORKFLOW_INVOCATION_SCHEMA,
        "invocation_id": _stable_id("invocation:intent", invocation_seed),
        "source": str(source or "intent").strip(),
        "conversation_id": record["conversation_id"],
        "interaction_ref": workflow_ref("interaction", interaction_id) if interaction_id else None,
        "response_ref": None,
        "target_ref": (
            copy.deepcopy(dict(target_ref))
            if isinstance(target_ref, Mapping)
            else _deepcopy_mapping(act.get("target_ref"))
        ),
        "risk": selected_risk,
        "confirmation_required": selected_confirmation,
        "command": command,
        "created_at": timestamp,
        "metadata": {
            "intent_proposal_id": record["proposal_id"],
            "intent_act_id": act["act_id"],
            "source_message_id": record["source_message_id"],
            "model": copy.deepcopy(dict(record["model"])),
        },
    }
    return validate_workflow_record(WORKFLOW_INVOCATION_SCHEMA, invocation)


def build_conversation_output(
    *,
    output_id: str,
    conversation_id: str,
    kind: str,
    summary: str,
    audience: str = "user",
    risk_level: str = "none",
    details: Sequence[Mapping[str, Any]] = (),
    actions: Sequence[Mapping[str, Any]] = (),
    fields: Sequence[Mapping[str, Any]] = (),
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    correlation: Mapping[str, Any] | None = None,
    next_expected_input: Mapping[str, Any] | None = None,
    channel_constraints: Mapping[str, Any] | None = None,
    response_envelope_ref: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    merged_correlation = {
        "turn_trace_id": None,
        "intent_proposal_id": None,
        "interaction_id": None,
        "workflow_ref": None,
        "workflow_event_id": None,
        "command_id": None,
        "run_ref": None,
        "reply_route_ref": None,
        **copy.deepcopy(dict(correlation or {})),
    }
    merged_next_expected_input = {
        "kind": "none",
        "interaction_id": None,
        "fields": [],
        **copy.deepcopy(dict(next_expected_input or {})),
    }
    merged_channel_constraints = {
        "preferred": None,
        "fallbacks": [],
        "requires_rich_view": False,
        **copy.deepcopy(dict(channel_constraints or {})),
    }
    record = {
        "schema": CONVERSATION_OUTPUT_SCHEMA,
        "output_id": str(output_id or "").strip(),
        "conversation_id": str(conversation_id or "").strip(),
        "kind": str(kind or "").strip(),
        "audience": str(audience or "user").strip(),
        "risk_level": str(risk_level or "none").strip(),
        "summary": str(summary or "").strip(),
        "details": [copy.deepcopy(dict(item)) for item in details],
        "actions": [copy.deepcopy(dict(item)) for item in actions],
        "fields": [copy.deepcopy(dict(item)) for item in fields],
        "evidence_refs": [copy.deepcopy(dict(item)) for item in evidence_refs],
        "correlation": merged_correlation,
        "next_expected_input": merged_next_expected_input,
        "channel_constraints": merged_channel_constraints,
        "response_envelope_ref": _deepcopy_mapping(response_envelope_ref),
        "metadata": copy.deepcopy(dict(metadata or {})),
        "created_at": now or _now(),
    }
    return validate_conversation_output(record)


def _workflow_ref_from_instance(
    instance: Mapping[str, Any] | None,
    fallback: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if isinstance(instance, Mapping):
        instance_id = str(instance.get("instance_id") or "").strip()
        workflow_type = str(instance.get("workflow_type") or "").strip()
        if instance_id:
            return workflow_ref(
                "workflow",
                instance_id,
                version=str(instance.get("definition_version")).strip()
                if instance.get("definition_version") is not None
                else None,
                generation=int(instance.get("generation") or 0),
                digest=str(instance.get("definition_digest")).strip()
                if instance.get("definition_digest") is not None
                else None,
            )
        if workflow_type:
            return _generic_ref("workflow_type", workflow_type)
    return _normalize_workflow_ref(fallback, fallback_kind="workflow")


def _conversation_kind_for_execution(result: Mapping[str, Any], decision: Mapping[str, Any] | None) -> str:
    if not result.get("accepted"):
        reason = str(result.get("reason_code") or (decision or {}).get("reason_code") or "")
        return "refusal" if any(marker in reason for marker in _AUTHORIZATION_REJECTION_MARKERS) else "repair"
    for response in result.get("responses") or []:
        if isinstance(response, Mapping) and response.get("category") == "terminal":
            return "result"
    if isinstance(decision, Mapping) and decision.get("status") == "duplicate":
        return "accepted"
    return "accepted"


def _response_ref_from_execution_result(result: Mapping[str, Any]) -> dict[str, Any] | None:
    responses = [dict(item) for item in result.get("responses") or [] if isinstance(item, Mapping)]
    if not responses:
        return None
    return response_envelope_ref(responses[-1])


def _run_ref_from_commit(commit: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(commit, Mapping):
        return None
    for key, kind in (
        ("run_id", "run"),
        ("task_id", "task"),
        ("commit_id", "workflow_commit"),
        ("event_id", "workflow_event"),
    ):
        token = str(commit.get(key) or "").strip()
        if token:
            return _generic_ref(kind, token)
    return None


def conversation_output_from_workflow_execution(
    result: Mapping[str, Any],
    *,
    output_id: str | None = None,
    conversation_id: str | None = None,
    turn_trace_id: str | None = None,
    intent_proposal_id: str | None = None,
    response_envelope_ref_value: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    invocation = dict(result.get("invocation") or {})
    command = dict(invocation.get("command") or {})
    decision = dict(result.get("decision") or {}) if isinstance(result.get("decision"), Mapping) else None
    after = dict(decision.get("after") or {}) if isinstance(decision, Mapping) else {}
    event_records = [
        dict(item) for item in (decision or {}).get("event_records") or [] if isinstance(item, Mapping)
    ]
    workflow_event_id = str(event_records[0].get("event_id") or "").strip() if event_records else None
    selected_conversation_id = str(
        conversation_id or invocation.get("conversation_id") or result.get("conversation_id") or ""
    ).strip()
    if not selected_conversation_id:
        raise ConversationalRuntimeError("workflow execution output requires conversation_id")
    command_id = str(command.get("command_id") or (decision or {}).get("command") or "").strip() or None
    reason_code = str(result.get("reason_code") or (decision or {}).get("reason_code") or "").strip()
    summary = str((decision or {}).get("explanation") or "").strip()
    if not summary:
        summary = reason_code.replace("_", " ") if reason_code else "Command accepted."
    workflow_ref_value = _workflow_ref_from_instance(after, command.get("instance_ref"))
    interaction_ref = dict(invocation.get("interaction_ref") or {})
    interaction_id = str(interaction_ref.get("id") or "").strip() or None
    metadata = dict(invocation.get("metadata") or {})
    proposal_id = (
        str(intent_proposal_id).strip()
        if intent_proposal_id is not None
        else str(metadata.get("intent_proposal_id") or "").strip() or None
    )
    output_kind = _conversation_kind_for_execution(result, decision)
    risk = str(invocation.get("risk") or "read").strip()
    details = []
    if after:
        details.append({"label": "state", "value": after.get("state"), "sensitivity": "internal"})
        details.append(
            {"label": "generation", "value": after.get("generation"), "sensitivity": "internal"}
        )
    if reason_code:
        details.append({"label": "reason_code", "value": reason_code, "sensitivity": "internal"})
    response_ref = (
        _deepcopy_mapping(response_envelope_ref_value)
        if response_envelope_ref_value is not None
        else _response_ref_from_execution_result(result)
    )
    seed = {
        "invocation_id": invocation.get("invocation_id"),
        "command_id": command_id,
        "workflow_event_id": workflow_event_id,
        "status": result.get("status"),
        "reason_code": reason_code,
    }
    return build_conversation_output(
        output_id=output_id or _stable_id("conversation_output", seed),
        conversation_id=selected_conversation_id,
        kind=output_kind,
        summary=summary,
        risk_level=_CONVERSATION_RISK_BY_WORKFLOW_RISK.get(risk, "medium"),
        details=details,
        evidence_refs=[
            copy.deepcopy(dict(item))
            for record in event_records
            for item in record.get("evidence_refs") or []
            if isinstance(item, Mapping)
        ],
        correlation={
            "turn_trace_id": turn_trace_id or metadata.get("turn_trace_id"),
            "intent_proposal_id": proposal_id,
            "interaction_id": interaction_id,
            "workflow_ref": workflow_ref_value,
            "workflow_event_id": workflow_event_id,
            "command_id": command_id,
            "run_ref": _run_ref_from_commit(result.get("commit")),
            "reply_route_ref": _normalize_workflow_ref(
                command.get("reply_route_ref"),
                fallback_kind="reply_route",
            ),
        },
        response_envelope_ref=response_ref,
        metadata={
            "invocation_id": invocation.get("invocation_id"),
            "status": result.get("status"),
            "accepted": bool(result.get("accepted")),
            "transition_id": (decision or {}).get("transition_id"),
            "reason_code": reason_code or None,
        },
        now=now or str((decision or {}).get("decided_at") or _now()),
    )


def response_envelope_ref(envelope: Mapping[str, Any]) -> dict[str, Any]:
    record = validate_response_envelope(envelope)
    return _generic_ref(
        "response_envelope",
        str(record["envelope_id"]),
        schema=RESPONSE_ENVELOPE_SCHEMA,
        generation=int(record["sequence"]),
    )


def link_conversation_output_to_response_envelope(
    output: Mapping[str, Any],
    envelope: Mapping[str, Any],
) -> dict[str, Any]:
    record = validate_conversation_output(output)
    linked = copy.deepcopy(record)
    linked["response_envelope_ref"] = response_envelope_ref(envelope)
    return validate_conversation_output(linked)


def response_envelope_from_conversation_output(
    output: Mapping[str, Any],
    *,
    envelope_id: str | None = None,
    sequence: int = 1,
    category: str | None = None,
    reply_route_ids: Sequence[str] | None = None,
    sensitivity: str = "internal",
    attention: str = "normal",
    materialization_status: str = "pending",
    status: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    record = validate_conversation_output(output)
    timestamp = now or _now()
    output_correlation = dict(record["correlation"])
    route_ref = dict(output_correlation.get("reply_route_ref") or {})
    routes = list(
        dict.fromkeys(
            str(item).strip()
            for item in (
                list(reply_route_ids)
                if reply_route_ids is not None
                else [route_ref.get("id")] if route_ref.get("id") else []
            )
            if str(item).strip()
        )
    )
    selected_status = status or ("pending" if routes else "undeliverable")
    selected_category = category or _RESPONSE_CATEGORY_BY_OUTPUT_KIND.get(record["kind"], "notification")
    envelope_seed = {
        "conversation_id": record["conversation_id"],
        "output_id": record["output_id"],
        "category": selected_category,
    }
    data = {
        "semantic_output_id": record["output_id"],
        "kind": record["kind"],
        "details": copy.deepcopy(record["details"]),
        "actions": copy.deepcopy(record["actions"]),
        "fields": copy.deepcopy(record["fields"]),
        "evidence_refs": copy.deepcopy(record["evidence_refs"]),
        "next_expected_input": copy.deepcopy(record["next_expected_input"]),
        "metadata": copy.deepcopy(record.get("metadata") or {}),
    }
    envelope = {
        "schema": RESPONSE_ENVELOPE_SCHEMA,
        "envelope_id": str(envelope_id or _stable_id("response", envelope_seed)).strip(),
        "conversation_id": record["conversation_id"],
        "category": selected_category,
        "sequence": int(sequence),
        "correlation": {
            "workflow_ref": _deepcopy_mapping(output_correlation.get("workflow_ref")),
            "task_ref": _deepcopy_mapping(output_correlation.get("run_ref")),
            "interaction_ref": (
                workflow_ref("interaction", str(output_correlation["interaction_id"]))
                if output_correlation.get("interaction_id")
                else None
            ),
            "command_id": output_correlation.get("command_id"),
        },
        "payload": {"text": record["summary"], "data": data},
        "sensitivity": sensitivity,
        "attention": attention,
        "attention_plan": {
            "schema": "adaos.conversation.attention_plan.v1",
            "attention": attention,
            "reason": "semantic_output",
        },
        "coalesce_key": (
            f"conversation-output:{record['output_id']}"
            if selected_category == "progress"
            else None
        ),
        "terminal_key": (
            f"conversation-output:{record['output_id']}"
            if selected_category == "terminal"
            else None
        ),
        "reply_route_ids": routes,
        "materialization_status": materialization_status,
        "status": selected_status,
        "created_at": timestamp,
        "updated_at": timestamp,
        "materialized_at": None,
        "delivered_at": None,
        "acknowledged_at": None,
    }
    return validate_response_envelope(envelope)


__all__ = [
    "CONVERSATION_OUTPUT_SCHEMA",
    "INTENT_PROPOSAL_SCHEMA",
    "RESPONSE_ENVELOPE_SCHEMA",
    "ConversationalRuntimeError",
    "build_conversation_output",
    "build_skill_intent_proposal",
    "build_workflow_intent_proposal",
    "conversation_output_from_workflow_execution",
    "link_conversation_output_to_response_envelope",
    "response_envelope_from_conversation_output",
    "response_envelope_ref",
    "validate_conversation_output",
    "validate_intent_proposal",
    "validate_response_envelope",
    "workflow_invocation_from_intent_proposal",
]
