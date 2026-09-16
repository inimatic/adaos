"""Typed model completion, before candidate validation or repair is allowed."""

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from adaos.domain.development_escalations import parse_development_escalations
from adaos.domain.development_feedback import (
    DEVELOPMENT_FEEDBACK_OUTPUT_SCHEMA,
    normalize_clarification_questions,
    parse_development_feedback,
    quarantine_invalid_nonblocking_feedback,
)


OUTCOME_SCHEMA = {
    "type": "object",
    "description": "Final assistant response only, after normal tool-enabled work or a necessary stop. This schema does not disable tools or working messages.",
    "additionalProperties": False,
    "properties": {
        "status": {"type": "string", "enum": ["completed", "needs_input", "blocked"]},
        "report": {"type": "string"},
        "questions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "id": {"type": "string"},
                    "question": {"type": "string"},
                    "reason": {"type": "string"},
                    "options": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["id", "question", "reason", "options"],
            },
        },
    },
    "required": ["status", "report", "questions"],
}

OUTCOME_INSTRUCTION = """## Required task outcome

The runner requests a structured FINAL response, not a JSON-only working turn.
Use the available tools normally to inspect and edit admitted source. Working
messages and tool calls remain enabled; only the last assistant message must
match the outcome schema. Do not stop or ask to switch modes merely because a
final response format is configured. Attempt the relevant admitted tool before
claiming that tool work is unavailable; report an actual tool failure as a
platform blocker, not as a missing user permission that was already granted.
Choose `completed` only when
the candidate is ready for independent validation, `needs_input` when a necessary
user decision is unanswered, or `blocked` for a platform/contract blocker.
`completed` is not acceptance and must not claim unexecuted checks passed.
Put your full result summary and any development feedback/escalation envelope
in `report`. For `needs_input`, return 1..8 self-contained `questions`, each with
a stable id, question, reason and optional suggestions (`options`, [] if none).
Do not rely on a conversational question, a file or an unavailable interactive
tool to suspend this non-interactive run. End with `needs_input`; the orchestrator
persists the questions, waits for answers and explicitly resumes the same Change.
Do not guess an answer or proceed with implementation while awaiting it.
For other statuses, return questions: []. A `blocked` report must include the
supplied blocking development feedback or authorized escalation contract.
Never ask for secrets or installed production data. Already recorded explicit
answers resolve the corresponding earlier requests; do not ask them again.
"""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate outcome field: {key}")
        result[key] = value
    return result


def outcome_message(raw: str) -> str:
    """Adapt the typed outcome to the existing governed feedback lifecycle.

    The raw structured response remains in the executor evidence. This adapter
    preserves the existing feedback identity/owner/binding implementation.
    """
    value = json.loads(raw, object_pairs_hook=_unique_object)
    errors = list(Draft202012Validator(OUTCOME_SCHEMA).iter_errors(value))
    if errors:
        raise ValueError("invalid Automation outcome shape")
    status, report = value["status"], value["report"]
    if not report.strip():
        raise ValueError("Automation outcome report is empty")
    try:
        feedback = parse_development_feedback(report)
    except (TypeError, ValueError) as exc:
        quarantined = (
            quarantine_invalid_nonblocking_feedback(report, reason=str(exc))
            if status == "completed"
            else None
        )
        if quarantined is None:
            raise
        report = quarantined
        feedback = parse_development_feedback(report)
    escalations = parse_development_escalations(report)
    blocking = any(item["blocking"] for item in feedback)
    if status == "needs_input":
        questions = normalize_clarification_questions(value["questions"])
        if feedback or escalations:
            raise ValueError("needs_input must not duplicate feedback or disguise a platform blocker")
        envelope = {"schema": DEVELOPMENT_FEEDBACK_OUTPUT_SCHEMA, "items": [{
            "category": "insufficient_context", "blocking": True,
            "summary": "A required user decision is pending",
            "impact": ["blocker"], "clarification_questions": questions,
        }]}
        return report + "\n\n```adaos-development-feedback\n" + json.dumps(envelope, ensure_ascii=False) + "\n```"
    if value["questions"] or any(item.get("clarification_questions") for item in feedback):
        raise ValueError("user questions require needs_input status")
    if status == "completed" and (blocking or escalations):
        raise ValueError("completed outcome contains a blocker")
    if status == "blocked" and not (blocking or escalations):
        raise ValueError("blocked outcome requires governed feedback")
    return report
