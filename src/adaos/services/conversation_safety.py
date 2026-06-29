from __future__ import annotations

import re
from typing import Any, Mapping


SAFETY_SCHEMA = "adaos.conversation.retrieved_evidence_safety.v1"

_PATTERNS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    (
        "prompt_injection",
        "ignore_or_override_instructions",
        re.compile(
            r"\b(ignore|forget|override|bypass|disregard)\b.{0,80}\b(instruction|system|developer|policy|rules?)\b",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    (
        "prompt_injection",
        "russian_ignore_or_override_instructions",
        re.compile(
            r"(?:игнорируй|забудь|обойди|отмени|переопредели).{0,80}(?:инструкц|правил|политик|системн)",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    (
        "sensitive_information",
        "system_prompt_or_secret_request",
        re.compile(
            r"\b(system prompt|developer message|hidden prompt|secret|api key|token|password|credential)\b",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    (
        "sensitive_information",
        "russian_system_prompt_or_secret_request",
        re.compile(
            r"(?:системн(?:ый|ого)? промпт|скрыт(?:ый|ые) инструкц|секрет|ключ api|парол|токен)",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    (
        "excessive_agency",
        "unauthorized_tool_or_data_action",
        re.compile(
            r"\b(delete files?|run command|execute shell|exfiltrate|send (?:all )?data|disable safety|disable guard)\b",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
    (
        "excessive_agency",
        "russian_unauthorized_tool_or_data_action",
        re.compile(
            r"(?:удали файлы|выполни команд|запусти shell|отправь все данные|отключи защит|отключи safety)",
            re.IGNORECASE | re.UNICODE,
        ),
    ),
)


def inspect_retrieved_text(
    text: Any,
    *,
    source_ref: Mapping[str, Any] | None = None,
    max_excerpt_chars: int = 160,
) -> dict[str, Any]:
    value = str(text or "")
    flags: list[dict[str, Any]] = []
    for category, code, pattern in _PATTERNS:
        match = pattern.search(value)
        if not match:
            continue
        flags.append(
            {
                "category": category,
                "code": code,
                "excerpt": _excerpt(value, match.start(), match.end(), max_excerpt_chars=max_excerpt_chars),
            }
        )
    risk_level = "none"
    if any(item["category"] in {"sensitive_information", "excessive_agency"} for item in flags):
        risk_level = "high"
    elif flags:
        risk_level = "medium"
    return {
        "schema": SAFETY_SCHEMA,
        "trust_boundary": "retrieved_untrusted_evidence",
        "risk_level": risk_level,
        "flags": flags,
        "source_ref": dict(source_ref or {}),
    }


def is_high_risk(safety: Mapping[str, Any] | None) -> bool:
    return str((safety or {}).get("risk_level") or "").strip().lower() == "high"


def _excerpt(text: str, start: int, end: int, *, max_excerpt_chars: int) -> str:
    limit = max(32, min(int(max_excerpt_chars or 160), 500))
    context = max(8, limit // 4)
    left = max(0, start - context)
    right = min(len(text), end + context)
    excerpt = text[left:right].strip()
    if left > 0:
        excerpt = "..." + excerpt
    if right < len(text):
        excerpt += "..."
    if len(excerpt) > limit:
        excerpt = excerpt[: max(0, limit - 3)].rstrip() + "..."
    return excerpt
