from __future__ import annotations

import copy
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

from jsonschema import Draft202012Validator


ATTENTION_POLICY_SCHEMA = "adaos.conversation.attention_policy.v1"
ATTENTION_PLAN_SCHEMA = "adaos.conversation.attention_plan.v1"
_ATTENTION_ORDER = {"silent": 0, "normal": 1, "important": 2, "urgent": 3}


class ConversationAttentionError(ValueError):
    """Raised when an attention policy is incomplete or ambiguous."""


def _schema(name: str) -> dict[str, Any]:
    filename = name.removeprefix("adaos.")
    path = Path(__file__).resolve().parents[1] / "abi" / f"{filename}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _validate(name: str, value: Mapping[str, Any]) -> dict[str, Any]:
    record = copy.deepcopy(dict(value))
    errors = sorted(
        Draft202012Validator(_schema(name)).iter_errors(record),
        key=lambda item: list(item.absolute_path),
    )
    if errors:
        location = ".".join(str(item) for item in errors[0].absolute_path) or "$"
        raise ConversationAttentionError(
            f"{name} validation failed at {location}: {errors[0].message}"
        )
    return record


def default_attention_policy(
    *,
    policy_id: str = "attention.default",
    principal_scope: Sequence[str] = ("user",),
) -> dict[str, Any]:
    rules = {
        "accepted": ("update_status", False, "silent"),
        "started": ("update_status", False, "silent"),
        "progress": ("update_status", False, "silent"),
        "input_required": ("append_message", True, "important"),
        "resumed": ("update_status", False, "normal"),
        "terminal": ("append_message", True, "normal"),
        "cancelled": ("append_message", True, "normal"),
        "notification": ("append_message", True, "normal"),
    }
    return _validate(
        ATTENTION_POLICY_SCHEMA,
        {
            "schema": ATTENTION_POLICY_SCHEMA,
            "policy_id": str(policy_id or "attention.default"),
            "version": 1,
            "principal_scope": list(principal_scope),
            "channel_preferences": {
                "preferred": ["origin"],
                "allowed_fallbacks": ["web", "telegram", "text"],
            },
            "category_rules": {
                category: {
                    "disposition": disposition,
                    "notify": notify,
                    "minimum_attention": attention,
                }
                for category, (disposition, notify, attention) in rules.items()
            },
            "progress": {
                "coalesce": True,
                "minimum_interval_seconds": 2,
                "retain_evidence": True,
            },
            "quiet_hours": {
                "enabled": False,
                "start": "22:00",
                "end": "08:00",
                "timezone": "UTC",
                "urgent_bypass": True,
            },
            "escalation": {
                "input_required": "important",
                "failure": "important",
                "expiry": "important",
            },
        },
    )


def _in_quiet_hours(policy: Mapping[str, Any], *, now: str | None) -> bool:
    quiet = dict(policy.get("quiet_hours") or {})
    if not quiet.get("enabled"):
        return False
    try:
        current = datetime.fromisoformat(str(now).replace("Z", "+00:00")) if now else datetime.now(timezone.utc)
        current_minutes = current.hour * 60 + current.minute
        start_hour, start_minute = (int(item) for item in str(quiet["start"]).split(":"))
        end_hour, end_minute = (int(item) for item in str(quiet["end"]).split(":"))
    except (TypeError, ValueError, KeyError):
        return True
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    return start <= current_minutes < end if start < end else current_minutes >= start or current_minutes < end


def plan_attention(
    category: str,
    *,
    requested_attention: str = "normal",
    coalesce_key: str | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
    policy: Mapping[str, Any] | None = None,
    now: str | None = None,
) -> dict[str, Any]:
    selected = _validate(ATTENTION_POLICY_SCHEMA, policy or default_attention_policy())
    token = str(category or "notification").strip()
    rule = dict(selected["category_rules"].get(token) or {})
    if not rule:
        raise ConversationAttentionError(f"attention category is not governed: {token}")
    attention = max(
        (str(requested_attention or "normal"), str(rule["minimum_attention"])),
        key=lambda item: _ATTENTION_ORDER.get(item, -1),
    )
    escalation_reason: str | None = None
    escalation = dict(selected["escalation"])
    if token == "input_required":
        escalation_reason = "input_required"
    elif token == "terminal" and str(outcome or "success") in {"failure", "unknown"}:
        escalation_reason = "failure"
    elif str(reason_code or "") in {"expired", "reply_route_expired", "interaction_expired"}:
        escalation_reason = "expiry"
    if escalation_reason:
        escalated = str(escalation.get(escalation_reason) or "none")
        if escalated != "none" and _ATTENTION_ORDER[escalated] > _ATTENTION_ORDER[attention]:
            attention = escalated
    quiet = _in_quiet_hours(selected, now=now)
    urgent_bypass = bool(dict(selected["quiet_hours"]).get("urgent_bypass"))
    notify = bool(rule["notify"]) and not (quiet and not (attention == "urgent" and urgent_bypass))
    progress = dict(selected["progress"])
    should_coalesce = token == "progress" and bool(progress["coalesce"])
    return _validate(
        ATTENTION_PLAN_SCHEMA,
        {
            "schema": ATTENTION_PLAN_SCHEMA,
            "policy_id": selected["policy_id"],
            "policy_version": selected["version"],
            "category": token,
            "disposition": rule["disposition"],
            "attention": attention,
            "notify": notify,
            "quiet_hours_applied": quiet,
            "coalesce": should_coalesce,
            "coalesce_key": str(coalesce_key).strip() if coalesce_key else None,
            "retain_evidence": bool(progress["retain_evidence"]) if token == "progress" else True,
            "escalation_reason": escalation_reason,
        },
    )
