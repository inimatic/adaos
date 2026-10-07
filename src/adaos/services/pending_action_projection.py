from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


PROJECTION_SCHEMA = "adaos.pending_action.projection.v1"
_SCHEMA_PATH = Path(__file__).resolve().parents[1] / "abi" / "pending_action.projection.v1.schema.json"
_STATUS_MAP = {
    "created": "awaiting_decision",
    "projected": "awaiting_decision",
    "awaiting_input": "awaiting_decision",
    "partially_answered": "awaiting_decision",
    "answered": "decision_recorded",
    "validation_failed": "decision_rejected",
    "accepted": "dispatch_pending",
    "completed": "succeeded",
    "expired": "expired",
    "cancelled": "cancelled",
    "superseded": "superseded",
}
_OUTCOME_STATUSES = {"succeeded", "failed", "input_required", "outcome_unknown", "cancelled"}


class PendingActionProjectionError(ValueError):
    pass


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")


def _digest(value: Any) -> str:
    return f"sha256:{hashlib.sha256(_canonical(value)).hexdigest()}"


def _mapping(value: Any) -> dict[str, Any]:
    return copy.deepcopy(dict(value)) if isinstance(value, Mapping) else {}


def _validate(value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    schema = json.loads(_SCHEMA_PATH.read_text(encoding="utf-8"))
    errors = sorted(
        Draft202012Validator(schema).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise PendingActionProjectionError(f"invalid Pending Action projection at {location}: {errors[0].message}")
    return record


def _assurance(action: Mapping[str, Any]) -> dict[str, bool]:
    declared = _mapping(action.get("assurance"))
    if declared:
        return {
            "voice_permitted": bool(declared.get("voice_permitted")),
            "trusted_interface_required": bool(declared.get("trusted_interface_required")),
            "step_up_required": bool(declared.get("step_up_required")),
        }
    risk = str(action.get("risk") or "read").strip().lower()
    mutating = risk not in {"read", "none", "read_only"}
    step_up = risk in {"external", "destructive", "admin", "privileged", "registry"}
    return {
        "voice_permitted": not mutating and not bool(action.get("confirmation_required")),
        "trusted_interface_required": mutating or bool(action.get("confirmation_required")),
        "step_up_required": step_up,
    }


def project_pending_action(
    interaction: Mapping[str, Any],
    presentation: Mapping[str, Any],
    *,
    delivery_receipt: Mapping[str, Any] | None = None,
    decision_receipt: Mapping[str, Any] | None = None,
    execution_receipt: Mapping[str, Any] | None = None,
    outcome_receipt: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Project canonical Interaction/GWR state; never create an independent PA state machine."""

    semantic = _mapping(interaction)
    view = _mapping(presentation)
    interaction_id = str(semantic.get("interaction_id") or "").strip()
    generation = int(semantic.get("generation") or 0)
    if not interaction_id or view.get("interaction_id") != interaction_id:
        raise PendingActionProjectionError("presentation belongs to another interaction")
    if int(view.get("interaction_generation") or 0) != generation:
        raise PendingActionProjectionError("presentation generation is stale")
    if view.get("supported") is not True:
        raise PendingActionProjectionError("unsupported presentation cannot become a Pending Action")
    actions = [dict(item) for item in view.get("actions") or [] if isinstance(item, Mapping)]
    if not actions:
        raise PendingActionProjectionError("Pending Action requires at least one presented choice")
    choices: list[dict[str, Any]] = []
    for action in actions:
        token = str(action.get("token") or "").strip()
        action_id = str(action.get("action_id") or "").strip()
        if not token or not action_id:
            raise PendingActionProjectionError("every choice requires an exact action token")
        choices.append(
            {
                "id": action_id,
                "label": str(action.get("label") or "").strip(),
                "action_token": token,
                "command": str(action.get("command") or "").strip(),
                "risk": str(action.get("risk") or "read").strip(),
                "assurance": _assurance(action),
                "target_ref": _mapping(action.get("target_ref")) or None,
            }
        )
    metadata = _mapping(semantic.get("metadata"))
    outcome = _mapping(outcome_receipt)
    status = _STATUS_MAP.get(str(semantic.get("status") or ""), "awaiting_decision")
    observed_outcome = str(outcome.get("outcome") or outcome.get("status") or "").strip().lower()
    if observed_outcome in _OUTCOME_STATUSES:
        status = observed_outcome
    subject = {
        "task_ref": semantic.get("task_ref"),
        "workflow_ref": semantic.get("workflow_ref"),
        "targets": [item.get("target_ref") for item in choices],
    }
    participant_scopes = sorted(
        {
            str(scope).strip()
            for item in actions
            for scope in item.get("principal_scope") or []
            if str(scope).strip()
        }
    )
    owner = str(semantic.get("owner") or "").strip()
    message_receipts = _mapping(_mapping(view.get("metadata")).get("message_receipts"))
    receipt_values = [message_receipts.get("prompt")]
    receipt_values.extend(_mapping(message_receipts.get("actions")).values())
    catalog_digests = sorted(
        {
            str(_mapping(_mapping(item).get("catalog_ref")).get("catalog_digest"))
            for item in receipt_values
            if str(_mapping(_mapping(item).get("catalog_ref")).get("catalog_digest")).startswith("sha256:")
        }
    )
    record = {
        "schema": PROJECTION_SCHEMA,
        "projection_id": f"pending-action:{interaction_id}",
        "kind": str(metadata.get("pending_action_kind") or metadata.get("workflow_type") or "human_decision").strip(),
        "interaction_ref": {
            "id": interaction_id,
            "generation": generation,
            "presentation_id": str(view.get("presentation_id") or "").strip(),
        },
        "subject_digest": _digest(subject),
        "plan_digest": _digest(view.get("plan") or {}),
        "owner": owner,
        "participants": list(dict.fromkeys([owner, *participant_scopes])),
        "prompt": str(view.get("prompt") or semantic.get("prompt") or "").strip(),
        "locale_context": _mapping(semantic.get("locale_context")) or None,
        "choices": choices,
        "status": status,
        "deadline": semantic.get("expires_at"),
        "receipts": {
            "delivery": _mapping(delivery_receipt) or None,
            "decision": _mapping(decision_receipt) or None,
            "execution": _mapping(execution_receipt) or None,
            "outcome": outcome or None,
        },
        "evidence_refs": [
            _mapping(item)
            for item in list(metadata.get("evidence_refs") or [])[:20]
            if _mapping(item)
        ],
        "lineage": {
            "interaction_schema": str(semantic.get("schema") or "").strip(),
            "presentation_schema": str(view.get("schema") or "").strip(),
            "profile_id": str(view.get("profile_id") or "").strip(),
            "profile_version": int(view.get("profile_version") or 0),
            "presentation_locale": str(_mapping(view.get("metadata")).get("locale") or "").strip() or None,
            "message_catalog_digests": catalog_digests,
        },
        "created_at": str(semantic.get("created_at") or "").strip(),
        "updated_at": str(semantic.get("updated_at") or semantic.get("created_at") or "").strip(),
    }
    return _validate(record)


__all__ = ["PROJECTION_SCHEMA", "PendingActionProjectionError", "project_pending_action"]
