from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Mapping, Sequence

from adaos.services import conversation_interactions, conversation_store
from adaos.services.conversational_runtime import (
    ConversationalRuntimeError,
    validate_intent_proposal,
)


INTENT_PROPOSAL_SCHEMA = "adaos.intent.proposal.v1"
_PENDING = {"created", "projected", "awaiting_input", "partially_answered", "validation_failed"}
_PROTECTED_RISKS = {"external", "destructive", "irreversible", "privileged", "publication", "release"}
_CONFIRMATION_AFFIRMATIVE = {
    "yes",
    "yes please",
    "confirm",
    "i confirm",
    "confirmed",
    "approve",
    "proceed",
    "да",
    "да подтверждаю",
    "подтверждаю",
    "согласен",
    "согласна",
    "продолжить",
}
_CONFIRMATION_NEGATIVE = {
    "no",
    "no thanks",
    "cancel",
    "reject",
    "do not confirm",
    "don t confirm",
    "i do not confirm",
    "i don t confirm",
    "нет",
    "нет спасибо",
    "отмена",
    "отменить",
    "отклонить",
    "не подтверждаю",
    "не согласен",
    "не согласна",
}
_CONFIRMATION_POSITIVE_MARKERS = {
    "yes", "confirm", "confirmed", "approve", "proceed",
    "да", "подтверждаю", "согласен", "согласна", "продолжить",
}
_CONFIRMATION_NEGATIVE_MARKERS = {
    "no", "not", "cancel", "reject", "нет", "не", "отмена", "отменить", "отклонить",
}
_ORDINALS = {
    "first": 0,
    "one": 0,
    "первый": 0,
    "первая": 0,
    "первое": 0,
    "один": 0,
    "второй": 1,
    "вторая": 1,
    "второе": 1,
    "second": 1,
    "two": 1,
    "третий": 2,
    "третья": 2,
    "третье": 2,
    "third": 2,
    "three": 2,
}


class IntentMediationError(ValueError):
    """Raised when informal text cannot be admitted as a governed response."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    try:
        return validate_intent_proposal(value)
    except ConversationalRuntimeError as exc:
        raise IntentMediationError(str(exc)) from exc


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"[^\w\-.:]+", " ", text.casefold(), flags=re.UNICODE).split())


def _segments(text: str) -> list[str]:
    parts = [item.strip() for item in re.split(r"[\r\n;]+", text) if item.strip()]
    return parts or [text.strip()]


def _confirmation_polarity(text: str) -> bool | str | None:
    """Classify only a bounded EN/RU confirmation vocabulary.

    Unrecognised phrases containing decision markers are ambiguous instead of
    being guessed.  This makes negation, mixed polarity, and same-utterance
    corrections fail closed; a caller can submit a versioned correction as a
    new intent proposal.
    """

    value = _normalized(text)
    if value in _CONFIRMATION_AFFIRMATIVE:
        return True
    if value in _CONFIRMATION_NEGATIVE:
        return False
    words = set(value.split())
    has_positive = bool(words & _CONFIRMATION_POSITIVE_MARKERS)
    has_negative = bool(words & _CONFIRMATION_NEGATIVE_MARKERS)
    if has_positive or has_negative:
        return "ambiguous"
    return None


def _pending(conversation_id: str, explicit_interaction_id: str | None) -> list[dict[str, Any]]:
    records = conversation_store.list_interactions(
        conversation_id=conversation_id,
        statuses=sorted(_PENDING),
    )
    if explicit_interaction_id:
        records = [item for item in records if item.get("interaction_id") == explicit_interaction_id]
    return records


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError as exc:
        raise IntentMediationError("dialog binding timestamp is invalid") from exc
    if parsed.tzinfo is None:
        raise IntentMediationError("dialog binding timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _voice_dialog_binding_reason(
    binding: Mapping[str, Any] | None,
    acts: Sequence[Mapping[str, Any]],
    *,
    source_message_id: str,
    now: str,
    actor_id: str | None = None,
) -> str | None:
    governed = [
        item
        for item in acts
        if str(item.get("kind") or "") in {"interaction_answer", "workflow_command"}
    ]
    if len(governed) != 1:
        return "voice_dialog_focus_ambiguous"
    if not isinstance(binding, Mapping):
        return "voice_dialog_binding_required"
    expected = governed[0]
    if str(binding.get("schema") or "") != "adaos.conversation.dialog_binding.v1":
        return "voice_dialog_binding_invalid"
    if str(binding.get("source_message_id") or "") != str(source_message_id or ""):
        return "voice_dialog_source_mismatch"
    if str(binding.get("interaction_id") or "") != str(expected.get("interaction_id") or ""):
        return "voice_dialog_interaction_mismatch"
    expected_generation = int(dict(expected.get("arguments") or {}).get("interaction_generation") or 0)
    binding_generation = binding.get("interaction_generation")
    if not isinstance(binding_generation, int) or binding_generation != expected_generation:
        return "voice_dialog_generation_mismatch"
    opened_at = _timestamp(str(binding.get("opened_at") or ""))
    expires_at = _timestamp(str(binding.get("expires_at") or ""))
    observed_at = _timestamp(now)
    if opened_at > observed_at:
        return "voice_dialog_not_open"
    if expires_at <= observed_at or expires_at <= opened_at:
        return "voice_dialog_expired"
    participant = binding.get("participant_ref")
    session = binding.get("session_ref")
    if not isinstance(participant, Mapping) or not str(participant.get("id") or "").strip():
        return "voice_dialog_participant_missing"
    if not isinstance(session, Mapping) or not str(session.get("id") or "").strip():
        return "voice_dialog_session_missing"
    if actor_id is not None and str(participant.get("id") or "") != str(actor_id or ""):
        return "voice_dialog_participant_mismatch"
    presentation = conversation_store.get_interaction_presentation(
        str(binding.get("presentation_id") or "")
    )
    if presentation is None:
        return "voice_dialog_presentation_unavailable"
    if (
        str(presentation.get("interaction_id") or "") != str(binding.get("interaction_id") or "")
        or presentation.get("interaction_generation") != binding_generation
    ):
        return "voice_dialog_presentation_mismatch"
    metadata = dict(presentation.get("metadata") or {})
    capabilities = dict(metadata.get("assurance_capabilities") or {})
    if (
        str(metadata.get("transport") or "") != "voice"
        or capabilities.get("bound_dialog_response") is not True
        or presentation.get("supported") is not True
    ):
        return "voice_dialog_presentation_not_admitted"
    action_id = str(dict(expected.get("arguments") or {}).get("action_id") or "")
    projected = next(
        (
            item
            for item in presentation.get("actions") or []
            if isinstance(item, Mapping) and str(item.get("action_id") or "") == action_id
        ),
        None,
    )
    if projected is None or projected.get("enabled") is not True:
        return "voice_dialog_action_not_admitted"
    return None


def _snapshot(interactions: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "interaction_id": item["interaction_id"],
            "interaction_generation": int(item["generation"]),
            "workflow_ref": copy.deepcopy(item.get("workflow_ref")),
            "actions": [
                {
                    "action_id": action["action_id"],
                    "command": action["command"],
                    "value": copy.deepcopy(action.get("value")),
                    "target_ref": copy.deepcopy(action.get("target_ref")),
                    "expected_generation": int(action["expected_generation"]),
                    "risk": action["risk"],
                    "confirmation_required": bool(action["confirmation_required"]),
                }
                for action in item.get("actions") or []
            ],
        }
        for item in interactions
    ]


def _action_candidates(
    segment: str,
    interactions: Sequence[Mapping[str, Any]],
) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    value = _normalized(segment)
    confirmation = _confirmation_polarity(segment)
    candidates: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for interaction in interactions:
        actions = [dict(item) for item in interaction.get("actions") or []]
        index: int | None = None
        if value.isdigit():
            index = int(value) - 1
        elif value in _ORDINALS:
            index = _ORDINALS[value]
        if index is not None and 0 <= index < len(actions):
            candidates.append((dict(interaction), actions[index]))
            continue
        spec = dict(interaction.get("input_spec") or {})
        if spec.get("kind") == "confirmation" and isinstance(confirmation, bool):
            wanted = confirmation
            for action in actions:
                if bool(action.get("value")) is wanted:
                    candidates.append((dict(interaction), action))
        for action in actions:
            aliases = {
                _normalized(str(action.get("action_id") or "")),
                _normalized(str(action.get("label") or "")),
                _normalized(str(action.get("command") or "")),
                _normalized(str(action.get("value") or "")),
            }
            aliases.discard("")
            if value in aliases:
                candidates.append((dict(interaction), action))
    unique: dict[tuple[str, str], tuple[dict[str, Any], dict[str, Any]]] = {}
    for interaction, action in candidates:
        unique[(str(interaction["interaction_id"]), str(action["action_id"]))] = (interaction, action)
    return list(unique.values())


def _looks_question(text: str) -> bool:
    value = _normalized(text)
    prefixes = (
        "what ", "why ", "how ", "when ", "where ", "which ",
        "что ", "почему ", "как ", "когда ", "где ", "какой ", "какая ", "какие ",
    )
    return text.rstrip().endswith("?") or value.startswith(prefixes)


def _non_command_kind(text: str) -> str:
    value = _normalized(text)
    if _looks_question(text):
        return "question"
    if re.search(r"\b(выбери|открой|переключись|select|switch|open)\b", value):
        return "context_selection"
    if re.search(r"\b(ошибка|дефект|не работает|добавь|хочу|нужно|bug|broken|add|implement|need|want)\b", value):
        return "new_issue"
    if re.search(r"\b(замечание|не нравится|предлагаю|feedback|review|suggest)\b", value):
        return "feedback"
    return "unrelated"


def _act(
    index: int,
    kind: str,
    text: str,
    *,
    interaction: Mapping[str, Any] | None = None,
    action: Mapping[str, Any] | None = None,
    confidence: float = 1.0,
) -> dict[str, Any]:
    return {
        "act_id": f"act.{index}",
        "kind": kind,
        "text": text,
        "target_ref": copy.deepcopy((action or {}).get("target_ref")),
        "interaction_id": str((interaction or {}).get("interaction_id")) if interaction else None,
        "command": str((action or {}).get("command")) if action else None,
        "skill_invocation": None,
        "arguments": {
            "action_id": (action or {}).get("action_id"),
            "value": copy.deepcopy((action or {}).get("value")),
            "interaction_generation": int((interaction or {}).get("generation") or 0),
            "expected_generation": int((action or {}).get("expected_generation") or 0),
        } if action else {},
        "confidence": confidence,
    }


def propose_intent(
    conversation_id: str,
    source_message_id: str,
    source_text: str,
    *,
    locale: str = "en",
    explicit_interaction_id: str | None = None,
    proposal_id: str | None = None,
    retention_class: str = "normal",
    redaction: str = "policy",
    supersedes_proposal_id: str | None = None,
    channel: str = "text",
    modality: str = "text",
    actor_ref: Mapping[str, Any] | None = None,
    principal_ref: Mapping[str, Any] | None = None,
    reply_route_ref: Mapping[str, Any] | None = None,
    context_ref: Mapping[str, Any] | None = None,
    dialog_binding: Mapping[str, Any] | None = None,
    package_ref: Mapping[str, Any] | None = None,
    package_digest: str | None = None,
    prompt_digest: str | None = None,
    trace: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    conversation = str(conversation_id or "").strip()
    message_id = str(source_message_id or "").strip()
    text = str(source_text or "").strip()
    if not conversation or not message_id or not text:
        raise IntentMediationError("conversation_id, source_message_id, and source_text are required")
    timestamp = now or _now()
    interactions = _pending(conversation, explicit_interaction_id)
    acts: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    ambiguity: dict[str, Any] | None = None
    protected: dict[str, Any] | None = None
    for index, segment in enumerate(_segments(text), start=1):
        confirmation = _confirmation_polarity(segment)
        confirmation_targets = [
            item
            for item in interactions
            if dict(item.get("input_spec") or {}).get("kind") == "confirmation"
        ]
        if confirmation == "ambiguous" and confirmation_targets:
            ambiguity = {
                "reason_code": "localized_confirmation_ambiguous",
                "candidates": [
                    {"interaction_id": item["interaction_id"], "action_id": None}
                    for item in confirmation_targets[:20]
                ],
            }
            acts.append(_act(index, "unrelated", segment, confidence=0.0))
            continue
        candidates = _action_candidates(segment, interactions)
        if len(candidates) == 1:
            interaction, action = candidates[0]
            risk = str(action.get("risk") or "read")
            if bool(action.get("confirmation_required")) or risk in _PROTECTED_RISKS:
                protected = {
                    "reason_code": "protected_action_requires_explicit_control",
                    "interaction_id": interaction["interaction_id"],
                    "action_id": action["action_id"],
                    "risk": risk,
                }
            acts.append(_act(index, "interaction_answer", segment, interaction=interaction, action=action))
        elif len(candidates) > 1:
            ambiguity = {
                "reason_code": "multiple_pending_targets",
                "candidates": [
                    {"interaction_id": item[0]["interaction_id"], "action_id": item[1]["action_id"]}
                    for item in candidates
                ],
            }
            alternatives.extend(
                {
                    "alternative_id": f"alternative.{len(alternatives) + offset + 1}",
                    "kind": "interaction_answer",
                    "confidence": 0.5,
                    "target_ref": copy.deepcopy(candidate[0].get("workflow_ref")),
                    "interaction_id": str(candidate[0]["interaction_id"]),
                    "action_id": str(candidate[1]["action_id"]),
                    "command": str(candidate[1].get("command") or "") or None,
                }
                for offset, candidate in enumerate(candidates)
            )
            acts.append(_act(index, "unrelated", segment, confidence=0.0))
        elif len(interactions) == 1 and dict(interactions[0].get("input_spec") or {}).get("kind") in {"text", "form"}:
            acts.append(_act(index, "interaction_answer", segment, interaction=interactions[0], confidence=1.0))
        else:
            kind = _non_command_kind(segment)
            free_text_targets = [
                item
                for item in interactions
                if dict(item.get("input_spec") or {}).get("kind") in {"text", "form", "confirmation"}
            ]
            if kind == "unrelated" and len(free_text_targets) > 1:
                ambiguity = {
                    "reason_code": "multiple_pending_targets",
                    "candidates": [
                        {"interaction_id": item["interaction_id"], "action_id": None}
                        for item in free_text_targets
                    ],
                }
                alternatives.extend(
                    {
                        "alternative_id": f"alternative.{len(alternatives) + offset + 1}",
                        "kind": "interaction_answer",
                        "confidence": 0.5,
                        "target_ref": copy.deepcopy(item.get("workflow_ref")),
                        "interaction_id": str(item["interaction_id"]),
                        "action_id": None,
                        "command": None,
                    }
                    for offset, item in enumerate(free_text_targets)
                )
            acts.append(_act(index, kind, segment, confidence=0.0 if ambiguity else 1.0))
    disposition = "proposed"
    clarification = None
    mutating = [item for item in acts if item["kind"] in {"interaction_answer", "workflow_command"}]
    if ambiguity:
        ambiguity.setdefault("prompt", None)
        disposition, clarification = "clarification_required", ambiguity
    elif protected:
        protected.setdefault("prompt", None)
        protected.setdefault("candidates", [])
        disposition, clarification = "clarification_required", protected
    elif str(modality or "").strip().lower() == "voice" and mutating:
        binding_reason = _voice_dialog_binding_reason(
            dialog_binding,
            mutating,
            source_message_id=message_id,
            now=timestamp,
        )
        if binding_reason:
            disposition = "clarification_required"
            clarification = {
                "reason_code": binding_reason,
                "prompt": None,
                "candidates": [
                    {
                        "interaction_id": item.get("interaction_id"),
                        "action_id": dict(item.get("arguments") or {}).get("action_id"),
                    }
                    for item in mutating[:20]
                ],
            }
    elif not mutating:
        disposition = "proposed"
    digest_input = json.dumps(
        {"conversation": conversation, "message": message_id, "text": text, "supersedes": supersedes_proposal_id},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    stable_id = "intent." + hashlib.sha256(digest_input.encode("utf-8")).hexdigest()[:32]
    record = _validate(
        {
            "schema": INTENT_PROPOSAL_SCHEMA,
            "proposal_id": str(proposal_id or stable_id).strip(),
            "conversation_id": conversation,
            "source_message_id": message_id,
            "source_text": text,
            "locale": str(locale or "en").strip(),
            "input_context": {
                "channel": str(channel or "text").strip(),
                "modality": str(modality or "text").strip(),
                "actor_ref": copy.deepcopy(dict(actor_ref)) if isinstance(actor_ref, Mapping) else None,
                "principal_ref": copy.deepcopy(dict(principal_ref)) if isinstance(principal_ref, Mapping) else None,
                "reply_route_ref": copy.deepcopy(dict(reply_route_ref)) if isinstance(reply_route_ref, Mapping) else None,
                "context_ref": copy.deepcopy(dict(context_ref)) if isinstance(context_ref, Mapping) else None,
            },
            "dialog_binding": (
                copy.deepcopy(dict(dialog_binding))
                if isinstance(dialog_binding, Mapping)
                else None
            ),
            "semantic_acts": acts,
            "alternatives": alternatives,
            "allowed_command_snapshot": _snapshot(interactions),
            "model": {"provider": "adaos", "name": "deterministic-intent-mediator", "version": "1.0.0"},
            "provenance": {
                "source": "deterministic",
                "package_ref": copy.deepcopy(dict(package_ref)) if isinstance(package_ref, Mapping) else None,
                "package_digest": str(package_digest).strip() if package_digest else None,
                "prompt_digest": str(prompt_digest).strip() if prompt_digest else None,
                "context_digest": "sha256:" + hashlib.sha256(
                    json.dumps(_snapshot(interactions), ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
                ).hexdigest(),
            },
            "trace": {
                "trace_id": str(dict(trace or {}).get("trace_id") or "") or None,
                "span_id": str(dict(trace or {}).get("span_id") or "") or None,
                "parent_span_id": str(dict(trace or {}).get("parent_span_id") or "") or None,
                "traceparent": str(dict(trace or {}).get("traceparent") or "") or None,
            },
            "disposition": disposition,
            "clarification": clarification,
            "supersedes_proposal_id": supersedes_proposal_id,
            "committed_response_ref": None,
            "retention": {"class": retention_class, "redaction": redaction},
            "created_at": timestamp,
            "updated_at": timestamp,
        }
    )
    stored = conversation_store.save_intent_proposal(record, create_only=True)
    if stored is None:
        raise IntentMediationError("durable conversation store is unavailable")
    return _validate(stored)


def commit_proposal(
    proposal_id: str,
    *,
    actor_id: str,
    idempotency_key: str,
    now: str | None = None,
) -> dict[str, Any]:
    proposal = conversation_store.get_intent_proposal(proposal_id)
    if proposal is None:
        raise IntentMediationError(f"intent proposal not found: {proposal_id}")
    proposal = _validate(proposal)
    if proposal["disposition"] != "proposed":
        raise IntentMediationError(f"intent proposal is not committable: {proposal['disposition']}")
    mutating = [
        item for item in proposal["semantic_acts"]
        if item["kind"] in {"interaction_answer", "workflow_command"}
    ]
    if len(mutating) != 1:
        raise IntentMediationError("intent proposal must identify exactly one governed response")
    act = mutating[0]
    interaction_id = str(act.get("interaction_id") or "")
    interaction = conversation_store.get_interaction(interaction_id)
    if interaction is None or interaction.get("status") not in _PENDING:
        raise IntentMediationError("pending interaction is no longer available")
    expected_generation = int(dict(act.get("arguments") or {}).get("interaction_generation") or 0)
    if int(interaction["generation"]) != expected_generation:
        raise IntentMediationError("intent proposal is stale")
    action_id = str(dict(act.get("arguments") or {}).get("action_id") or "") or None
    if action_id:
        action = next(
            (item for item in interaction.get("actions") or [] if item.get("action_id") == action_id),
            None,
        )
        if action is None or str(action.get("command")) != str(act.get("command")):
            raise IntentMediationError("proposed command is no longer allowed")
        if bool(action.get("confirmation_required")) or str(action.get("risk")) in _PROTECTED_RISKS:
            raise IntentMediationError("protected action requires an explicit control")
    modality = str(dict(proposal.get("input_context") or {}).get("modality") or "text")
    if modality == "voice":
        binding_reason = _voice_dialog_binding_reason(
            proposal.get("dialog_binding"),
            [act],
            source_message_id=str(proposal.get("source_message_id") or ""),
            now=now or _now(),
            actor_id=actor_id,
        )
        if binding_reason:
            raise IntentMediationError(binding_reason)
    result = conversation_interactions.submit_response(
        interaction_id,
        actor_id=actor_id,
        expected_generation=expected_generation,
        idempotency_key=idempotency_key,
        original_text=act["text"],
        proposed_action_id=action_id,
        intent_proposal={
            "schema": proposal["schema"],
            "proposal_id": proposal["proposal_id"],
            "act_id": act["act_id"],
            "model": proposal["model"],
            "modality": modality,
            "dialog_binding": copy.deepcopy(proposal.get("dialog_binding")),
        },
        now=now,
    )
    updated = copy.deepcopy(proposal)
    updated["disposition"] = "committed"
    updated["committed_response_ref"] = {
        "kind": "interaction_response",
        "id": result["response"]["response_id"],
    }
    updated["updated_at"] = now or _now()
    conversation_store.save_intent_proposal(_validate(updated))
    return {"proposal": updated, **result}


def correct_proposal(
    proposal_id: str,
    corrected_text: str,
    *,
    source_message_id: str | None = None,
    locale: str | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    previous = conversation_store.get_intent_proposal(proposal_id)
    if previous is None:
        raise IntentMediationError(f"intent proposal not found: {proposal_id}")
    previous = _validate(previous)
    timestamp = now or _now()
    corrected = copy.deepcopy(previous)
    corrected["disposition"] = "corrected"
    corrected["updated_at"] = timestamp
    conversation_store.save_intent_proposal(_validate(corrected))
    return propose_intent(
        previous["conversation_id"],
        source_message_id or f"{previous['source_message_id']}.correction.{uuid.uuid4().hex[:8]}",
        corrected_text,
        locale=locale or previous["locale"],
        retention_class=previous["retention"]["class"],
        redaction=previous["retention"]["redaction"],
        supersedes_proposal_id=previous["proposal_id"],
        now=timestamp,
    )


def interpretation_metrics(conversation_id: str) -> dict[str, Any]:
    proposals = conversation_store.list_intent_proposals(conversation_id, limit=1000)
    total = len(proposals)
    clarifications = sum(item.get("disposition") == "clarification_required" for item in proposals)
    corrected = sum(item.get("disposition") == "corrected" for item in proposals)
    committed = sum(item.get("disposition") == "committed" for item in proposals)
    return {
        "schema": "adaos.intent.metrics.v1",
        "conversation_id": conversation_id,
        "total": total,
        "committed": committed,
        "clarifications": clarifications,
        "corrections": corrected,
        "clarification_rate": clarifications / total if total else 0.0,
        "false_transition_proxy_rate": corrected / committed if committed else 0.0,
    }
