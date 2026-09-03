from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any


DEVELOPMENT_FEEDBACK_OUTPUT_SCHEMA = "adaos.development_feedback_output.v1"
DEVELOPMENT_FEEDBACK_FENCE = "adaos-development-feedback"
_CATEGORIES = {
    "missing_capability",
    "ambiguous_contract",
    "conflicting_contract",
    "inefficient_contract",
    "insufficient_context",
    "observability_gap",
    "validation_gap",
    "policy_block",
}
_IMPACTS = {
    "blocker",
    "correctness",
    "reliability",
    "efficiency",
    "generalization",
    "comprehension",
    "observability",
    "policy",
}
_FENCE_PATTERN = re.compile(
    rf"```{re.escape(DEVELOPMENT_FEEDBACK_FENCE)}\s*\r?\n(?P<payload>.*?)\r?\n```",
    re.DOTALL,
)
_ENVELOPE_FIELDS = {"schema", "items"}
_ITEM_FIELDS = {
    "category",
    "summary",
    "blocking",
    "confidence",
    "impact",
    "target_refs",
    "details",
    "recommendation",
    "evidence_refs",
}


def _strict_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _text(value: Any, *, field: str, limit: int, required: bool = False) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"development feedback {field} is required")
    if len(text) > limit:
        raise ValueError(f"development feedback {field} exceeds {limit} characters")
    return text


def normalize_development_feedback(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("development feedback envelope must be an object")
    envelope = dict(value)
    if set(envelope) != _ENVELOPE_FIELDS:
        raise ValueError("development feedback envelope contains unsupported fields")
    if envelope.get("schema") != DEVELOPMENT_FEEDBACK_OUTPUT_SCHEMA:
        raise ValueError(f"development feedback schema must be {DEVELOPMENT_FEEDBACK_OUTPUT_SCHEMA}")
    raw_items = envelope.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 8:
        raise ValueError("development feedback requires 1..8 items")
    normalized: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise TypeError(f"development feedback item {index} must be an object")
        item = dict(raw_item)
        if set(item) - _ITEM_FIELDS:
            raise ValueError(f"development feedback item {index} contains unsupported fields")
        category = _text(item.get("category"), field="category", limit=80, required=True).lower()
        if category not in _CATEGORIES:
            raise ValueError(f"unsupported development feedback category: {category}")
        try:
            confidence = float(item.get("confidence", 1.0))
        except (TypeError, ValueError) as exc:
            raise ValueError("development feedback confidence must be numeric") from exc
        if not 0 <= confidence <= 1:
            raise ValueError("development feedback confidence must be between 0 and 1")
        impact = list(
            dict.fromkeys(
                _text(value, field="impact", limit=80).lower()
                for value in item.get("impact") or []
                if _text(value, field="impact", limit=80)
            )
        )
        if len(impact) > 8 or any(value not in _IMPACTS for value in impact):
            raise ValueError("development feedback impact contains unsupported values")
        target_refs = list(
            dict.fromkeys(
                _text(value, field="target_ref", limit=500, required=True)
                for value in item.get("target_refs") or []
            )
        )
        if len(target_refs) > 20 or any(":" not in value for value in target_refs):
            raise ValueError("development feedback target_refs are invalid")
        evidence_refs = []
        for raw_ref in item.get("evidence_refs") or []:
            if not isinstance(raw_ref, Mapping):
                raise TypeError("development feedback evidence refs must be objects")
            ref = dict(raw_ref)
            if set(ref) - {"type", "ref"}:
                raise ValueError("development feedback evidence ref contains unsupported fields")
            evidence_refs.append(
                {
                    "type": _text(ref.get("type"), field="evidence.type", limit=80, required=True),
                    "ref": _text(ref.get("ref"), field="evidence.ref", limit=1000, required=True),
                }
            )
        if len(evidence_refs) > 20:
            raise ValueError("development feedback has too many evidence refs")
        normalized.append(
            {
                "category": category,
                "summary": _text(item.get("summary"), field="summary", limit=1000, required=True),
                "blocking": bool(item.get("blocking")),
                "confidence": confidence,
                "impact": impact,
                "target_refs": target_refs,
                "details": _text(item.get("details"), field="details", limit=3000),
                "recommendation": _text(item.get("recommendation"), field="recommendation", limit=2000),
                "evidence_refs": evidence_refs,
            }
        )
    return normalized


def parse_development_feedback(message: str) -> list[dict[str, Any]]:
    text = str(message or "")
    matches = list(_FENCE_PATTERN.finditer(text))
    marker_present = DEVELOPMENT_FEEDBACK_FENCE in text
    if not matches:
        if marker_present:
            raise ValueError("development feedback fence is malformed")
        return []
    if len(matches) != 1:
        raise ValueError("exactly one development feedback fence is allowed")
    try:
        payload = json.loads(matches[0].group("payload"), object_pairs_hook=_strict_json_object)
    except json.JSONDecodeError as exc:
        raise ValueError(f"development feedback JSON is invalid: {exc.msg}") from exc
    return normalize_development_feedback(payload)


__all__ = [
    "DEVELOPMENT_FEEDBACK_FENCE",
    "DEVELOPMENT_FEEDBACK_OUTPUT_SCHEMA",
    "normalize_development_feedback",
    "parse_development_feedback",
]
