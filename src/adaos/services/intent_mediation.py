from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator

from adaos.services import conversation_interactions, conversation_store


INTENT_PROPOSAL_SCHEMA = "adaos.intent.proposal.v1"
_PENDING = {"created", "projected", "awaiting_input", "partially_answered", "validation_failed"}
_PROTECTED_RISKS = {"external", "destructive", "irreversible", "privileged", "publication", "release"}
_YES = {"yes", "y", "ok", "okay", "confirm", "confirmed", "да", "д", "ок", "подтверждаю"}
_NO = {"no", "n", "cancel", "reject", "нет", "н", "отмена", "отменить", "отклонить"}
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


def _schema() -> dict[str, Any]:
    path = Path(__file__).resolve().parents[1] / "abi" / "intent.proposal.v1.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    errors = sorted(
        Draft202012Validator(_schema()).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise IntentMediationError(
            f"{INTENT_PROPOSAL_SCHEMA} validation failed at {location}: {errors[0].message}"
        )
    return record


def _normalized(text: str) -> str:
    return " ".join(re.sub(r"[^\w\-.:]+", " ", text.casefold(), flags=re.UNICODE).split())


def _segments(text: str) -> list[str]:
    parts = [item.strip() for item in re.split(r"[\r\n;]+", text) if item.strip()]
    return parts or [text.strip()]


def _pending(conversation_id: str, explicit_interaction_id: str | None) -> list[dict[str, Any]]:
    records = conversation_store.list_interactions(
        conversation_id=conversation_id,
        statuses=sorted(_PENDING),
    )
    if explicit_interaction_id:
        records = [item for item in records if item.get("interaction_id") == explicit_interaction_id]
    return records


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
        if spec.get("kind") == "confirmation" and value in _YES | _NO:
            wanted = value in _YES
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
            alternatives.extend(ambiguity["candidates"])
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
                alternatives.extend(ambiguity["candidates"])
            acts.append(_act(index, kind, segment, confidence=0.0 if ambiguity else 1.0))
    disposition = "proposed"
    clarification = None
    mutating = [item for item in acts if item["kind"] in {"interaction_answer", "workflow_command"}]
    if ambiguity:
        disposition, clarification = "clarification_required", ambiguity
    elif protected:
        disposition, clarification = "clarification_required", protected
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
            "semantic_acts": acts,
            "alternatives": alternatives,
            "allowed_command_snapshot": _snapshot(interactions),
            "model": {"provider": "adaos", "name": "deterministic-intent-mediator", "version": "1.0.0"},
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
