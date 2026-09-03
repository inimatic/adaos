from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

DEVELOPMENT_ESCALATION_SCHEMA = "adaos.development_escalations.v1"
DEVELOPMENT_ESCALATION_FENCE = "adaos-development-escalation"
CORE_CAPABILITY_REQUEST_KIND = "core_capability_request"
CORE_IMPACT_CLASSES = {
    "blocker",
    "speed",
    "generalization",
    "contract_gap",
    "observability_gap",
    "lifecycle_gap",
    "policy_boundary",
    "compatibility_debt",
    "security_governance",
}

_FENCE_PATTERN = re.compile(
    rf"```{re.escape(DEVELOPMENT_ESCALATION_FENCE)}\s*\r?\n(?P<payload>.*?)\r?\n```",
    re.DOTALL,
)
_ENVELOPE_FIELDS = {"schema", "items"}
_ITEM_FIELDS = {
    "schema",
    "kind",
    "summary",
    "component_ref",
    "desired_contract",
    "impact",
    "motivation",
    "observed_limitation",
    "rejected_workarounds",
}
_WORKAROUND_FIELDS = {"approach", "reason"}


def _strict_json_object(pairs: Sequence[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON field: {key}")
        result[key] = value
    return result


def _bounded_text(
    value: Any,
    *,
    field: str,
    limit: int,
    required: bool = False,
) -> str:
    text = str(value or "").strip()
    if required and not text:
        raise ValueError(f"development escalation {field} is required")
    if len(text) > limit:
        raise ValueError(f"development escalation {field} exceeds {limit} characters")
    return text


def normalize_development_escalations(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, Mapping):
        raise TypeError("development escalation envelope must be an object")
    envelope = dict(value)
    if set(envelope) != _ENVELOPE_FIELDS:
        raise ValueError("development escalation envelope contains unsupported fields")
    if envelope.get("schema") != DEVELOPMENT_ESCALATION_SCHEMA:
        raise ValueError(
            f"development escalation schema must be {DEVELOPMENT_ESCALATION_SCHEMA}"
        )
    raw_items = envelope.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 8:
        raise ValueError("development escalation requires 1..8 items")

    normalized: list[dict[str, Any]] = []
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping):
            raise TypeError(f"development escalation item {index} must be an object")
        item = dict(raw_item)
        if set(item) - _ITEM_FIELDS:
            raise ValueError(
                f"development escalation item {index} contains unsupported fields"
            )
        if item.get("schema") not in (None, "adaos.development_escalation.v1"):
            raise ValueError(
                f"development escalation item {index} has an unsupported schema"
            )
        kind = _bounded_text(item.get("kind"), field="kind", limit=80, required=True)
        if kind != CORE_CAPABILITY_REQUEST_KIND:
            raise ValueError(f"unsupported development escalation kind: {kind}")
        impact = _bounded_text(
            item.get("impact") or "contract_gap",
            field="impact",
            limit=80,
            required=True,
        ).lower()
        if impact not in CORE_IMPACT_CLASSES:
            raise ValueError(f"unsupported development escalation impact: {impact}")
        component_ref = _bounded_text(
            item.get("component_ref"),
            field="component_ref",
            limit=200,
            required=True,
        )
        if not component_ref.startswith("core:"):
            raise ValueError("development escalation component_ref must start with core:")

        raw_workarounds = item.get("rejected_workarounds") or []
        if not isinstance(raw_workarounds, list) or len(raw_workarounds) > 8:
            raise ValueError("development escalation rejected_workarounds must contain at most 8 items")
        workarounds: list[dict[str, str]] = []
        for workaround_index, raw_workaround in enumerate(raw_workarounds):
            if not isinstance(raw_workaround, Mapping):
                raise TypeError(
                    f"development escalation workaround {workaround_index} must be an object"
                )
            workaround = dict(raw_workaround)
            if set(workaround) != _WORKAROUND_FIELDS:
                raise ValueError(
                    f"development escalation workaround {workaround_index} must contain approach and reason"
                )
            workarounds.append(
                {
                    "approach": _bounded_text(
                        workaround.get("approach"),
                        field="rejected_workarounds.approach",
                        limit=500,
                        required=True,
                    ),
                    "reason": _bounded_text(
                        workaround.get("reason"),
                        field="rejected_workarounds.reason",
                        limit=500,
                        required=True,
                    ),
                }
            )

        normalized.append(
            {
                "schema": "adaos.development_escalation.v1",
                "kind": kind,
                "summary": _bounded_text(
                    item.get("summary"), field="summary", limit=500, required=True
                ),
                "component_ref": component_ref,
                "desired_contract": _bounded_text(
                    item.get("desired_contract"),
                    field="desired_contract",
                    limit=2_000,
                    required=True,
                ),
                "impact": impact,
                "motivation": _bounded_text(
                    item.get("motivation"), field="motivation", limit=2_000
                ),
                "observed_limitation": _bounded_text(
                    item.get("observed_limitation"),
                    field="observed_limitation",
                    limit=2_000,
                    required=True,
                ),
                "rejected_workarounds": workarounds,
            }
        )
    return normalized


def parse_development_escalations(message: str) -> list[dict[str, Any]]:
    text = str(message or "")
    matches = list(_FENCE_PATTERN.finditer(text))
    marker_present = DEVELOPMENT_ESCALATION_FENCE in text
    if not matches:
        if marker_present:
            raise ValueError("development escalation fence is malformed")
        return []
    if len(matches) != 1:
        raise ValueError("exactly one development escalation fence is allowed")
    try:
        payload = json.loads(
            matches[0].group("payload"),
            object_pairs_hook=_strict_json_object,
        )
    except json.JSONDecodeError as exc:
        raise ValueError(f"development escalation JSON is invalid: {exc.msg}") from exc
    return normalize_development_escalations(payload)


__all__ = [
    "CORE_CAPABILITY_REQUEST_KIND",
    "CORE_IMPACT_CLASSES",
    "DEVELOPMENT_ESCALATION_FENCE",
    "DEVELOPMENT_ESCALATION_SCHEMA",
    "normalize_development_escalations",
    "parse_development_escalations",
]
