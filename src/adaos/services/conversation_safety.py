from __future__ import annotations

import re
import json
from typing import Any, Mapping


SAFETY_SCHEMA = "adaos.conversation.retrieved_evidence_safety.v1"
ACTION_RISK_SCHEMA = "adaos.conversation.action_risk.v1"

_ACTION_RISK_ORDER = {
    "safe": 0,
    "ui_navigation": 1,
    "local_write": 2,
    "filesystem": 3,
    "network": 3,
    "cross_node": 4,
    "device_control": 4,
    "credential": 5,
}
_ACTION_RISK_APPROVAL_CLASSES = {"filesystem", "network", "cross_node", "device_control", "credential"}

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


def classify_action_risk(action: Mapping[str, Any] | str | None) -> dict[str, Any]:
    if action is None:
        action_map: dict[str, Any] = {}
    elif isinstance(action, str):
        action_map = {"tool": action}
    else:
        action_map = dict(action)
    text = _action_text(action_map)
    candidates: list[tuple[str, str]] = []
    explicit = str(
        action_map.get("risk_class")
        or action_map.get("side_effect_class")
        or action_map.get("effect_class")
        or ""
    ).strip()
    if explicit:
        candidates.append((_normalize_action_risk(explicit), "explicit_class"))
    if _contains_any(text, ("credential", "secret", "password", "token", "api key", "api_key", "ключ api", "парол", "секрет")):
        candidates.append(("credential", "credential_keyword"))
    if _contains_any(text, ("device", "relay", "gpio", "camera", "microphone", "lock", "unlock", "термостат", "реле", "замок")):
        candidates.append(("device_control", "device_keyword"))
    if _contains_any(text, ("cross_node", "remote node", "subnet", "federated", "target_node", "member node", "другая нода", "подсеть")):
        candidates.append(("cross_node", "cross_node_keyword"))
    if _contains_any(text, ("http://", "https://", "webhook", "telegram", "email", "network", "internet", "fetch", "post ", "send data")):
        candidates.append(("network", "network_keyword"))
    if _contains_any(text, ("file", "path", "write_file", "delete_file", "rm ", "remove-item", "filesystem", "workspace", "файл")):
        candidates.append(("filesystem", "filesystem_keyword"))
    if _contains_any(text, ("local_write", "memory.remember", "conversation_store", "sqlite", "draft", "patch")):
        candidates.append(("local_write", "local_write_keyword"))
    if _contains_any(text, ("ui_navigation", "open modal", "show widget", "toast", "browser view")):
        candidates.append(("ui_navigation", "ui_keyword"))

    if not candidates:
        candidates.append(("safe", "default"))
    risk_class = max((item[0] for item in candidates), key=lambda item: _ACTION_RISK_ORDER.get(item, 0))
    approval_required = risk_class in _ACTION_RISK_APPROVAL_CLASSES
    return {
        "schema": ACTION_RISK_SCHEMA,
        "risk_class": risk_class,
        "approval_required": approval_required,
        "mandatory_review": approval_required,
        "reasons": [{"risk_class": risk, "reason": reason} for risk, reason in candidates],
    }


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


def _normalize_action_risk(value: str) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "none": "safe",
        "read_only": "safe",
        "readonly": "safe",
        "ui": "ui_navigation",
        "navigation": "ui_navigation",
        "write": "local_write",
        "local": "local_write",
        "fs": "filesystem",
        "file_system": "filesystem",
        "device": "device_control",
        "credentials": "credential",
        "secret": "credential",
        "remote": "cross_node",
        "federated": "cross_node",
    }
    token = aliases.get(token, token)
    return token if token in _ACTION_RISK_ORDER else "safe"


def _action_text(action: Mapping[str, Any]) -> str:
    try:
        encoded = json.dumps(dict(action), ensure_ascii=False, sort_keys=True)
    except Exception:
        encoded = str(action)
    return encoded.lower()


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(needle.lower() in text for needle in needles)
