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
SKILL_INVOCATION_SCHEMA = "adaos.skill.invocation.v1"
ACTION_POLICY_SCHEMA = "adaos.conversation.action_policy.v1"

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

_ACTION_POLICY_RISK_BY_OUTPUT_RISK = {
    "none": "read",
    "low": "local_reversible",
    "medium": "isolated_write",
    "high": "workspace_activation",
    "destructive": "destructive",
}

_TASK_STATUS_BY_OUTPUT_KIND = {
    "clarification": "input_required",
    "confirmation": "input_required",
    "accepted": "submitted",
    "progress": "working",
    "result": "completed",
    "repair": "input_required",
    "refusal": "failed",
    "handoff": "unknown",
}

_AUTHORIZATION_REJECTION_MARKERS = (
    "actor_not_authorized",
    "missing_permission",
    "role_not_authorized",
    "unverified_principal",
    "invalid_principal",
    "principal_actor_mismatch",
)

_LEGACY_SIDE_EFFECT_POLICY = {
    "none": ("read", "none", "none"),
    "safe": ("read", "none", "none"),
    "read_only": ("read", "none", "none"),
    "readonly": ("read", "none", "none"),
    "ui_navigation": ("local_reversible", "none", "none"),
    "local_state_change": ("local_reversible", "reversible", "none"),
    "local_write": ("local_reversible", "reversible", "none"),
    "runtime_write": ("isolated_write", "reversible", "required"),
    "reversible": ("local_reversible", "reversible", "none"),
    "filesystem": ("isolated_write", "reversible", "required"),
    "skill_action": ("isolated_write", "reversible", "required"),
    "device_control": ("workspace_activation", "external", "required"),
    "network": ("workspace_activation", "external", "required"),
    "cross_node": ("workspace_activation", "external", "required"),
    "external": ("workspace_activation", "external", "required"),
    "external_io": ("workspace_activation", "external", "required"),
    "publication": ("publication", "external", "rich_review"),
    "destructive": ("destructive", "destructive", "rich_review"),
}


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


def _action_policy(
    *,
    risk_class: str = "read",
    side_effect: str = "none",
    confirmation: str = "none",
    required_capabilities: Sequence[str] = (),
    policy_refs: Sequence[str] = (),
) -> dict[str, Any]:
    return {
        "schema": "adaos.conversation.action_policy.v1",
        "risk_class": str(risk_class or "read").strip(),
        "side_effect": str(side_effect or "none").strip(),
        "confirmation": str(confirmation or "none").strip(),
        "required_capabilities": [
            str(item).strip() for item in required_capabilities if str(item).strip()
        ],
        "policy_refs": [str(item).strip() for item in policy_refs if str(item).strip()],
    }


def _input_context(
    *,
    channel: str = "text",
    modality: str = "text",
    actor_ref: Mapping[str, Any] | None = None,
    principal_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    context_ref: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "channel": str(channel or "text").strip(),
        "modality": str(modality or "text").strip(),
        "actor_ref": _deepcopy_mapping(actor_ref),
        "principal_ref": _deepcopy_mapping(principal_ref),
        "reply_route_ref": _deepcopy_mapping(reply_route_ref),
        "context_ref": _deepcopy_mapping(context_ref),
    }


def _provenance(
    *,
    source: str = "deterministic",
    package_ref: Mapping[str, Any] | None = None,
    package_digest: str | None = None,
    prompt_digest: str | None = None,
    context_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "source": str(source or "deterministic").strip(),
        "package_ref": _deepcopy_mapping(package_ref),
        "package_digest": str(package_digest).strip() if package_digest else None,
        "prompt_digest": str(prompt_digest).strip() if prompt_digest else None,
        "context_digest": str(context_digest).strip() if context_digest else None,
    }


def _trace(
    *,
    trace_id: str | None = None,
    span_id: str | None = None,
    parent_span_id: str | None = None,
    traceparent: str | None = None,
) -> dict[str, Any]:
    return {
        "trace_id": str(trace_id).strip() if trace_id is not None else None,
        "span_id": str(span_id).strip() if span_id is not None else None,
        "parent_span_id": (
            str(parent_span_id).strip() if parent_span_id is not None else None
        ),
        "traceparent": str(traceparent).strip() if traceparent is not None else None,
    }


def _output_provenance(
    *,
    source: str = "conversation",
    package_ref: Mapping[str, Any] | None = None,
    package_digest: str | None = None,
    source_ref: Mapping[str, Any] | None = None,
    source_digest: str | None = None,
) -> dict[str, Any]:
    return {
        "source": str(source or "conversation").strip(),
        "package_ref": _deepcopy_mapping(package_ref),
        "package_digest": str(package_digest).strip() if package_digest else None,
        "source_ref": _deepcopy_mapping(source_ref),
        "source_digest": str(source_digest).strip() if source_digest else None,
    }


def _output_reason(
    *,
    kind: str,
    summary: str,
    code: str | None = None,
    explanation: str | None = None,
    source: str = "conversation",
    retryable: bool | None = None,
) -> dict[str, Any]:
    selected_retryable = (
        bool(retryable)
        if retryable is not None
        else str(kind or "").strip() in {"clarification", "confirmation", "repair"}
    )
    return {
        "code": str(code).strip() if code is not None else str(kind or "").strip(),
        "explanation": (
            str(explanation).strip()
            if explanation is not None
            else str(summary or "").strip() or None
        ),
        "retryable": selected_retryable,
        "source": str(source or "conversation").strip(),
    }


def _output_lifecycle(
    *,
    kind: str,
    sequence: int = 1,
    update_kind: str = "snapshot",
    supersedes_output_id: str | None = None,
    terminal: bool | None = None,
    task_status: str | None = None,
) -> dict[str, Any]:
    output_kind = str(kind or "").strip()
    return {
        "sequence": int(sequence),
        "update_kind": str(update_kind or "snapshot").strip(),
        "supersedes_output_id": (
            str(supersedes_output_id).strip()
            if supersedes_output_id is not None
            else None
        ),
        "terminal": bool(terminal) if terminal is not None else output_kind in {"result", "refusal"},
        "task_status": str(
            task_status or _TASK_STATUS_BY_OUTPUT_KIND.get(output_kind, "none")
        ).strip(),
    }


def _content_parts(summary: str, parts: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if parts:
        return [copy.deepcopy(dict(item)) for item in parts]
    return [
        {
            "part_id": "part.1",
            "kind": "text",
            "text": str(summary or "").strip(),
            "data": None,
            "artifact_ref": None,
            "media_type": "text/plain",
            "sensitivity": "internal",
        }
    ]


def _normalize_output_action(action: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(action))
    command = str(record.get("command") or "").strip() or None
    action_id = str(record.get("action_id") or "").strip() or None
    risk_level = str(record.get("risk_level") or "none").strip()
    record.setdefault(
        "binding",
        {
            "kind": "workflow_command" if command else "none",
            "affordance_id": action_id,
            "workflow_command": command,
            "skill_operation": None,
        },
    )
    record.setdefault(
        "action_policy",
        _action_policy(
            risk_class=_ACTION_POLICY_RISK_BY_OUTPUT_RISK.get(risk_level, "read"),
            side_effect="reversible" if command else "none",
            confirmation="required" if bool(record.get("requires_confirmation")) else "none",
        ),
    )
    return record


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


def _schema_properties(schema_name: str) -> dict[str, Any]:
    properties = _abi_schema(schema_name).get("properties")
    return dict(properties) if isinstance(properties, Mapping) else {}


def _intent_act_supports_skill_action_policy() -> bool:
    semantic_acts = _schema_properties(INTENT_PROPOSAL_SCHEMA).get("semantic_acts")
    items = dict(semantic_acts.get("items") or {}) if isinstance(semantic_acts, Mapping) else {}
    properties = dict(items.get("properties") or {})
    skill = properties.get("skill_invocation")
    variants = []
    if isinstance(skill, Mapping):
        variants = list(skill.get("oneOf") or skill.get("anyOf") or [skill])
    for variant in variants:
        if not isinstance(variant, Mapping):
            continue
        variant_properties = dict(variant.get("properties") or {})
        if "action_policy" in variant_properties:
            return True
    return False


def _output_action_supports_policy() -> bool:
    actions = _schema_properties(CONVERSATION_OUTPUT_SCHEMA).get("actions")
    items = dict(actions.get("items") or {}) if isinstance(actions, Mapping) else {}
    properties = dict(items.get("properties") or {})
    return "binding" in properties or "action_policy" in properties


def validate_intent_proposal(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(INTENT_PROPOSAL_SCHEMA, value)


def validate_conversation_output(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(CONVERSATION_OUTPUT_SCHEMA, value)


def validate_response_envelope(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(RESPONSE_ENVELOPE_SCHEMA, value)


def validate_action_policy(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(ACTION_POLICY_SCHEMA, value)


def validate_skill_invocation(value: Mapping[str, Any]) -> dict[str, Any]:
    return _validate_schema(SKILL_INVOCATION_SCHEMA, value)


def action_policy(
    *,
    risk_class: str = "read",
    side_effect: str = "none",
    confirmation: str = "none",
    required_capabilities: Sequence[str] = (),
    policy_refs: Sequence[str] = (),
) -> dict[str, Any]:
    return validate_action_policy(
        {
            "schema": ACTION_POLICY_SCHEMA,
            "risk_class": str(risk_class or "read").strip(),
            "side_effect": str(side_effect or "none").strip(),
            "confirmation": str(confirmation or "none").strip(),
            "required_capabilities": list(dict.fromkeys(str(item) for item in required_capabilities)),
            "policy_refs": list(dict.fromkeys(str(item) for item in policy_refs)),
        }
    )


def action_policy_from_legacy_side_effect(value: str | None) -> dict[str, Any]:
    token = str(value or "none").strip().lower().replace("-", "_")
    risk_class, side_effect, confirmation = _LEGACY_SIDE_EFFECT_POLICY.get(
        token,
        ("workspace_activation", "external", "rich_review"),
    )
    return action_policy(
        risk_class=risk_class,
        side_effect=side_effect,
        confirmation=confirmation,
        policy_refs=(f"legacy-side-effect:{token or 'none'}",),
    )


def action_policy_from_workflow_risk(value: Mapping[str, Any] | str | None) -> dict[str, Any]:
    if isinstance(value, Mapping):
        return action_policy(
            risk_class=str(value.get("class") or "read"),
            side_effect=str(value.get("side_effect") or "none"),
            confirmation=str(value.get("confirmation") or "none"),
        )
    risk_class = str(value or "read").strip()
    defaults = {
        "read": ("none", "none"),
        "local_reversible": ("reversible", "none"),
        "isolated_write": ("reversible", "required"),
        "trial_activation": ("reversible", "required"),
        "workspace_activation": ("external", "required"),
        "publication": ("external", "rich_review"),
        "destructive": ("destructive", "rich_review"),
    }
    side_effect, confirmation = defaults.get(risk_class, ("external", "rich_review"))
    return action_policy(
        risk_class=risk_class if risk_class in defaults else "workspace_activation",
        side_effect=side_effect,
        confirmation=confirmation,
    )


def _trace_context(
    value: Mapping[str, Any] | None,
    *,
    seed: Mapping[str, Any],
) -> dict[str, Any]:
    supplied = dict(value or {})
    trace_id = str(supplied.get("trace_id") or "").strip() or hashlib.sha256(
        _canonical_trace_bytes({"trace": seed})
    ).hexdigest()[:32]
    span_id = str(supplied.get("span_id") or "").strip() or hashlib.sha256(
        _canonical_trace_bytes({"span": seed})
    ).hexdigest()[:16]
    parent_span_id = str(supplied.get("parent_span_id") or "").strip() or None
    traceparent = str(supplied.get("traceparent") or "").strip() or f"00-{trace_id}-{span_id}-01"
    return {
        "trace_id": trace_id,
        "span_id": span_id,
        "parent_span_id": parent_span_id,
        "traceparent": traceparent,
    }


def _canonical_trace_bytes(value: Mapping[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


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
        "skill_invocation": None,
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
    input_context: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    trace: Mapping[str, Any] | None = None,
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
    schema_properties = _schema_properties(INTENT_PROPOSAL_SCHEMA)
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
    if "input_context" in schema_properties:
        record["input_context"] = copy.deepcopy(
            dict(input_context) if isinstance(input_context, Mapping) else _input_context()
        )
    if "provenance" in schema_properties:
        record["provenance"] = copy.deepcopy(
            dict(provenance) if isinstance(provenance, Mapping) else _provenance()
        )
    if "trace" in schema_properties:
        record["trace"] = copy.deepcopy(dict(trace) if isinstance(trace, Mapping) else _trace())
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
    input_context: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    trace: Mapping[str, Any] | None = None,
    channel: str = "text",
    modality: str = "text",
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
    proposal_input_context = (
        copy.deepcopy(dict(input_context))
        if isinstance(input_context, Mapping)
        else _input_context(
            channel=channel,
            modality=modality,
            reply_route_ref=arguments["reply_route_ref"],
            context_ref=arguments["context_ref"],
        )
    )
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
        input_context=proposal_input_context,
        provenance=provenance,
        trace=trace,
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
    input_context: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    trace: Mapping[str, Any] | None = None,
    action_policy: Mapping[str, Any] | None = None,
    channel: str = "text",
    modality: str = "text",
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
    if _intent_act_supports_skill_action_policy():
        skill_invocation["action_policy"] = copy.deepcopy(
            dict(action_policy)
            if isinstance(action_policy, Mapping)
            else _action_policy(risk_class="read", side_effect="none", confirmation="none")
        )
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
        input_context=(
            copy.deepcopy(dict(input_context))
            if isinstance(input_context, Mapping)
            else _input_context(channel=channel, modality=modality)
        ),
        provenance=provenance,
        trace=trace,
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
            "action_policy": action_policy_from_workflow_risk(selected_risk),
            "provenance": copy.deepcopy(dict(record["provenance"])),
            "trace": copy.deepcopy(dict(record["trace"])),
        },
    }
    return validate_workflow_record(WORKFLOW_INVOCATION_SCHEMA, invocation)


def skill_invocation_from_intent_proposal(
    proposal: Mapping[str, Any],
    *,
    actor_id: str,
    idempotency_key: str | None = None,
    action_policy_value: Mapping[str, Any] | None = None,
    target_ref: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    record = validate_intent_proposal(proposal)
    if record["disposition"] != "proposed":
        raise ConversationalRuntimeError(
            f"intent proposal is not invocable: {record['disposition']}"
        )
    act = _single_act(record, "skill_invocation")
    declared = dict(act.get("skill_invocation") or {})
    skill_id = str(declared.get("skill_id") or "").strip()
    operation_id = str(declared.get("operation_id") or "").strip()
    if not skill_id or not operation_id:
        raise ConversationalRuntimeError(
            "skill intent proposal requires exact skill_id and operation_id"
        )
    selected_target = (
        copy.deepcopy(dict(target_ref))
        if isinstance(target_ref, Mapping)
        else _deepcopy_mapping(act.get("target_ref"))
        or _generic_ref("skill", skill_id)
    )
    selected_policy = validate_action_policy(
        action_policy_value
        if isinstance(action_policy_value, Mapping)
        else dict(declared.get("action_policy") or {})
    )
    timestamp = now or str(record.get("created_at") or _now())
    key = str(
        idempotency_key or f"intent:{record['proposal_id']}:{act['act_id']}"
    ).strip()
    seed = {
        "proposal_id": record["proposal_id"],
        "act_id": act["act_id"],
        "skill_id": skill_id,
        "operation_id": operation_id,
        "actor_id": actor_id,
    }
    invocation = {
        "schema": SKILL_INVOCATION_SCHEMA,
        "invocation_id": _stable_id("skill-invocation", seed),
        "conversation_id": record["conversation_id"],
        "proposal_ref": _generic_ref(
            "intent_proposal",
            str(record["proposal_id"]),
            schema=INTENT_PROPOSAL_SCHEMA,
        ),
        "actor_ref": _generic_ref("principal", actor_id),
        "target_ref": selected_target,
        "operation": {"skill_id": skill_id, "operation_id": operation_id},
        "input": copy.deepcopy(dict(declared.get("arguments") or {})),
        "action_policy": selected_policy,
        "idempotency_key": key,
        "trace": copy.deepcopy(dict(record["trace"])),
        "created_at": timestamp,
        "metadata": {
            "intent_act_id": act["act_id"],
            "source_message_id": record["source_message_id"],
            "model": copy.deepcopy(dict(record["model"])),
            "provenance": copy.deepcopy(dict(record["provenance"])),
        },
    }
    return validate_skill_invocation(invocation)


def build_conversation_output(
    *,
    output_id: str,
    conversation_id: str,
    kind: str,
    summary: str,
    audience: str = "user",
    risk_level: str = "none",
    reason: Mapping[str, Any] | None = None,
    content_parts: Sequence[Mapping[str, Any]] = (),
    details: Sequence[Mapping[str, Any]] = (),
    actions: Sequence[Mapping[str, Any]] = (),
    fields: Sequence[Mapping[str, Any]] = (),
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    correlation: Mapping[str, Any] | None = None,
    next_expected_input: Mapping[str, Any] | None = None,
    channel_constraints: Mapping[str, Any] | None = None,
    handoff_target: Mapping[str, Any] | None = None,
    lifecycle: Mapping[str, Any] | None = None,
    provenance: Mapping[str, Any] | None = None,
    trace: Mapping[str, Any] | None = None,
    response_envelope_ref: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    selected_kind = str(kind or "").strip()
    selected_summary = str(summary or "").strip()
    selected_risk = str(risk_level or "none").strip()
    schema_properties = _schema_properties(CONVERSATION_OUTPUT_SCHEMA)
    actions_support_policy = _output_action_supports_policy()
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
        "kind": selected_kind,
        "audience": str(audience or "user").strip(),
        "risk_level": selected_risk,
        "summary": selected_summary,
        "details": [copy.deepcopy(dict(item)) for item in details],
        "actions": [
            _normalize_output_action(item)
            if actions_support_policy
            else copy.deepcopy(dict(item))
            for item in actions
        ],
        "fields": [copy.deepcopy(dict(item)) for item in fields],
        "evidence_refs": [copy.deepcopy(dict(item)) for item in evidence_refs],
        "correlation": merged_correlation,
        "next_expected_input": merged_next_expected_input,
        "channel_constraints": merged_channel_constraints,
        "response_envelope_ref": _deepcopy_mapping(response_envelope_ref),
        "metadata": copy.deepcopy(dict(metadata or {})),
        "created_at": now or _now(),
    }
    if "reason" in schema_properties:
        record["reason"] = copy.deepcopy(
            dict(reason)
            if isinstance(reason, Mapping)
            else _output_reason(kind=selected_kind, summary=selected_summary)
        )
    if "content_parts" in schema_properties:
        record["content_parts"] = _content_parts(selected_summary, content_parts)
    if "handoff_target" in schema_properties:
        record["handoff_target"] = _deepcopy_mapping(handoff_target)
    if "lifecycle" in schema_properties:
        record["lifecycle"] = copy.deepcopy(
            dict(lifecycle)
            if isinstance(lifecycle, Mapping)
            else _output_lifecycle(kind=selected_kind)
        )
    if "provenance" in schema_properties:
        record["provenance"] = copy.deepcopy(
            dict(provenance)
            if isinstance(provenance, Mapping)
            else _output_provenance()
        )
    if "trace" in schema_properties:
        record["trace"] = copy.deepcopy(dict(trace) if isinstance(trace, Mapping) else _trace())
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
        reason=_output_reason(
            kind=output_kind,
            summary=summary,
            code=reason_code or output_kind,
            explanation=summary,
            source="workflow",
            retryable=output_kind in {"clarification", "confirmation", "repair"},
        ),
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
        lifecycle=_output_lifecycle(
            kind=output_kind,
            task_status=_TASK_STATUS_BY_OUTPUT_KIND.get(output_kind, "unknown"),
        ),
        provenance=_output_provenance(
            source="workflow",
            source_ref=_generic_ref("workflow_event", workflow_event_id)
            if workflow_event_id
            else None,
        ),
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
