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
    "application_trace",
    "clarification_questions",
}
_APPLICATION_TRACE_FIELDS = {
    "schema",
    "contract_ref",
    "operation_id",
    "input_summary",
    "expected_behavior",
    "observed_behavior",
    "validation_result",
    "user_response",
    "trace_refs",
}


def _application_trace(value: Any) -> dict[str, Any] | None:
    if value in (None, {}):
        return None
    if not isinstance(value, Mapping):
        raise TypeError("development feedback application_trace must be an object")
    trace = dict(value)
    if set(trace) - _APPLICATION_TRACE_FIELDS:
        raise ValueError(
            "development feedback application_trace contains unsupported fields"
        )
    if trace.get("schema") != "adaos.development.application_trace.v1":
        raise ValueError(
            "development feedback application_trace schema is invalid"
        )
    contract_ref = _text(
        trace.get("contract_ref"),
        field="application_trace.contract_ref",
        limit=500,
        required=True,
    )
    if ":" not in contract_ref:
        raise ValueError(
            "development feedback application_trace contract_ref is invalid"
        )
    validation_result = _text(
        trace.get("validation_result"),
        field="application_trace.validation_result",
        limit=40,
        required=True,
    ).lower()
    if validation_result not in {"passed", "failed", "unknown", "not_run"}:
        raise ValueError(
            "development feedback application_trace validation_result is invalid"
        )
    trace_refs: list[dict[str, str]] = []
    for raw_ref in trace.get("trace_refs") or []:
        if not isinstance(raw_ref, Mapping):
            raise TypeError(
                "development feedback application_trace trace_refs must be objects"
            )
        ref = dict(raw_ref)
        if set(ref) - {"type", "ref"}:
            raise ValueError(
                "development feedback application_trace trace_ref contains unsupported fields"
            )
        trace_refs.append(
            {
                "type": _text(
                    ref.get("type"),
                    field="application_trace.trace_ref.type",
                    limit=80,
                    required=True,
                ),
                "ref": _text(
                    ref.get("ref"),
                    field="application_trace.trace_ref.ref",
                    limit=1000,
                    required=True,
                ),
            }
        )
    if len(trace_refs) > 12:
        raise ValueError(
            "development feedback application_trace has too many trace_refs"
        )
    return {
        "schema": "adaos.development.application_trace.v1",
        "contract_ref": contract_ref,
        "operation_id": _text(
            trace.get("operation_id"),
            field="application_trace.operation_id",
            limit=240,
            required=True,
        ),
        "input_summary": _text(
            trace.get("input_summary"),
            field="application_trace.input_summary",
            limit=1200,
            required=True,
        ),
        "expected_behavior": _text(
            trace.get("expected_behavior"),
            field="application_trace.expected_behavior",
            limit=2000,
            required=True,
        ),
        "observed_behavior": _text(
            trace.get("observed_behavior"),
            field="application_trace.observed_behavior",
            limit=3000,
            required=True,
        ),
        "validation_result": validation_result,
        "user_response": _text(
            trace.get("user_response"),
            field="application_trace.user_response",
            limit=1000,
        ),
        "trace_refs": trace_refs,
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


def development_feedback_model_rules() -> dict[str, Any]:
    """Expose parser vocabulary without a second hand-maintained prompt enum."""
    return {"category": sorted(_CATEGORIES), "impact": sorted(_IMPACTS),
            "max_items": 8, "max_target_refs": 20, "max_evidence_refs": 20,
            "target_refs": "Use kind:identifier, e.g. sdk:adaos.sdk.access.require. Fully qualified adaos.sdk symbols normalize to sdk: references; other bare names are invalid.",
            "text_limits": {"summary": 1000, "details": 3000, "recommendation": 2000},
            "clarification_questions": {
                "when": "Only blocking insufficient_context caused by a necessary user decision; never optional UX detail or an SDK/platform gap.",
                "max_questions": 8,
                "item": {"id": "stable simple identifier", "question": "one self-contained question (max 1000 chars)",
                         "reason": "why this decision is necessary (max 1000 chars)", "options": "optional list of up to 4 short suggested answers"},
                "safety": "Never ask for secret values or private production records. Ordinary implementation choices remain autonomous."}}


def normalize_clarification_questions(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 8:
        raise ValueError("clarification requires 1..8 questions")
    result = []
    for raw in value:
        if not isinstance(raw, Mapping) or set(raw) - {"id", "question", "reason", "options"}:
            raise ValueError("clarification question fields are invalid")
        identifier = raw.get("id")
        if not isinstance(identifier, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", identifier):
            raise ValueError("clarification question id is invalid")
        if any(item["id"] == identifier for item in result):
            raise ValueError("clarification question ids must be unique")
        options = raw.get("options", [])
        if (not isinstance(options, list) or len(options) > 4
                or any(not isinstance(item, str) or not item.strip() or len(item) > 240 for item in options)):
            raise ValueError("clarification options are invalid")
        fields = {}
        for key in ("question", "reason"):
            item = raw.get(key)
            if not isinstance(item, str) or not item.strip() or len(item) > 1000:
                raise ValueError(f"clarification {key} must be bounded non-empty text")
            fields[key] = item.strip()
        result.append({"id": identifier, **fields, "options": list(options)})
    return result


def _target_ref(value: Any) -> str:
    value = _text(value, field="target_ref", limit=500, required=True)
    if re.fullmatch(r"adaos\.sdk\.[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*", value, flags=re.ASCII):
        return _text("sdk:" + value, field="target_ref", limit=500, required=True)
    return value


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
                _target_ref(value)
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
        application_trace = _application_trace(item.get("application_trace"))
        questions = None
        if "clarification_questions" in item:
            if category != "insufficient_context" or item.get("blocking") is not True:
                raise ValueError("user clarification requires blocking insufficient_context")
            questions = normalize_clarification_questions(item["clarification_questions"])
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
                **({"clarification_questions": questions} if questions else {}),
                **(
                    {"application_trace": application_trace}
                    if application_trace
                    else {}
                ),
            }
        )
    questions = [question for item in normalized for question in item.get("clarification_questions", [])]
    if questions:
        normalize_clarification_questions(questions)
    return normalized


def required_user_questions(items: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """A platform blocker must not be disguised as an answerable user decision."""
    blocking = [item for item in items if item.get("blocking")]
    if not blocking or any(not item.get("clarification_questions") for item in blocking):
        return []
    return normalize_clarification_questions([
        question for item in blocking for question in item["clarification_questions"]
    ])


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
    "development_feedback_model_rules",
    "parse_development_feedback",
    "normalize_clarification_questions",
    "required_user_questions",
]
