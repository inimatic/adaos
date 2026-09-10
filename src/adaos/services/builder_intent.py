"""Typed user-intent capture and deterministic Prototype Brief compilation."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Mapping

from jsonschema import Draft202012Validator


INTENT_SCHEMA = "adaos.builder.intent.v1"
PROTOTYPE_BRIEF_SCHEMA = "adaos.builder.prototype_brief.v1"

_ABI_ROOT = Path(__file__).resolve().parents[1] / "abi"
_REF_PATTERN = re.compile(
    r"(?:https?://[^\s<>()]+|(?:project|change|scenario|skill|modal):[A-Za-z0-9_.:/-]+)",
    flags=re.IGNORECASE,
)
_CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04ff]")
_AUTHORING_PATTERNS = (
    re.compile(
        r"\b(?:create|build|make|design)\b.{0,60}\b(?:applications?|apps?|pages?|screens?|interfaces?|prototypes?|scenarios?)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:show|produce|prepare)\b.{0,40}\b(?:first|initial)\s+(?:version|prototype)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:создай|создайте|сделай|сделайте|разработай|подготовь)\w*\b.{0,60}"
        r"\b(?:приложение|сценарий|интерфейс|прототип|экран|страницу)\b",
        flags=re.IGNORECASE,
    ),
)
_OPERATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "search",
        re.compile(r"\b(?:search|find|lookup|искать|найти|поиск)\w*\b", re.IGNORECASE),
    ),
    ("filter", re.compile(r"\b(?:filter|фильтр|фильтрац)\w*\b", re.IGNORECASE)),
    ("sort", re.compile(r"\b(?:sort|order by|сортир|упорядоч)\w*\b", re.IGNORECASE)),
    (
        "inspect",
        re.compile(
            r"\b(?:inspect|open|view|review|scan|browse|откр|просмотр|изуч)\w*\b|"
            r"\bпровер(?:ить|ять|яет|яют|ял|яла|яли|ь|ьте)\b",
            re.IGNORECASE,
        ),
    ),
    (
        "list",
        re.compile(
            r"\b(?:list|show|display|перечис|покаж|отобраз)\w*\b", re.IGNORECASE
        ),
    ),
    (
        "create",
        re.compile(
            r"\b(?:add|create|capture|record|добав|созда|запис)\w*\b", re.IGNORECASE
        ),
    ),
    (
        "assign",
        re.compile(
            r"\b(?:assign|delegate|поруч)\w*\b|\bназнач(?!енн|ени)\w*\b",
            re.IGNORECASE,
        ),
    ),
    (
        "transition",
        re.compile(
            r"\b(?:move|advance|transition|close|complete|finish|submit|approve|reject|cancel|"
            r"перемещ|перевод|закры|заверш|отправ|соглас|одобр|отклон|отмен)\w*\b|"
            r"\b(?:change|set|смен|меня)\w*\s+status\b|"
            r"\b(?:смен|меня)\w*\s+статус\w*\b",
            re.IGNORECASE,
        ),
    ),
    (
        "update",
        re.compile(
            r"\b(?:(?:edit(?:s|ed|ing)?|updat(?:e|es|ed|ing)|"
            r"chang(?:e|es|ed|ing)|mark(?:s|ed|ing)?)\b|"
            r"(?:редакт|измен|отмет)\w*\b)",
            re.IGNORECASE,
        ),
    ),
    ("archive", re.compile(r"\b(?:archive|архив)\w*\b", re.IGNORECASE)),
    ("delete", re.compile(r"\b(?:delete|remove|удал)\w*\b", re.IGNORECASE)),
)
_READ_OPERATIONS = {"list", "inspect", "search", "filter", "sort"}
_CAPTURE_ATTACHMENT_PATTERN = re.compile(
    r"\b(?:(?:attach|upload|add|capture)\w*.{0,80}(?:photos?|images?|pictures?|files?|attachments?|documents?)|"
    r"(?:photos?|images?|pictures?|files?|attachments?|documents?).{0,40}(?:attach|upload)\w*|"
    r"(?:\u043f\u0440\u0438\u043b\u043e\u0436|\u0437\u0430\u0433\u0440\u0443\u0437|\u0434\u043e\u0431\u0430\u0432)\w*.{0,80}"
    r"(?:\u0444\u043e\u0442\u043e|\u0438\u0437\u043e\u0431\u0440\u0430\u0436|\u0441\u043d\u0438\u043c\u043e\u043a|\u0444\u0430\u0439\u043b|\u0432\u043b\u043e\u0436\u0435\u043d|\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442)\w*|"
    r"(?:\u0444\u043e\u0442\u043e|\u0438\u0437\u043e\u0431\u0440\u0430\u0436|\u0441\u043d\u0438\u043c\u043e\u043a|\u0444\u0430\u0439\u043b|\u0432\u043b\u043e\u0436\u0435\u043d|\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442)\w*.{0,40}"
    r"(?:\u043f\u0440\u0438\u043b\u043e\u0436|\u0437\u0430\u0433\u0440\u0443\u0437)\w*)\b",
    re.IGNORECASE,
)
_REPEATED_COLLECTION_PATTERN = re.compile(
    r"\b(?:repeat(?:able|ed)?\s+(?:collections?|lists?|groups?|rows?|items?|entries)|"
    r"(?:collections?|lists?|groups?|rows?|items?|entries)\s+(?:that\s+)?(?:repeat|can\s+be\s+added)|"
    r"\u043f\u043e\u0432\u0442\u043e\u0440\u044f\u0435\u043c\w*\s+(?:\u0441\u043f\u0438\u0441\u043e\u043a|\u0433\u0440\u0443\u043f\u043f|\u0441\u0442\u0440\u043e\u043a|\u043f\u0443\u043d\u043a\u0442|\u044d\u043b\u0435\u043c\u0435\u043d\u0442)\w*|"
    r"(?:\u0441\u043f\u0438\u0441\u043e\u043a|\u0433\u0440\u0443\u043f\u043f\u0430)\w*\s+\u0441\s+\u0432\u043e\u0437\u043c\u043e\u0436\u043d\u043e\u0441\u0442\w*\s+\u0434\u043e\u0431\u0430\u0432\w*)\b",
    re.IGNORECASE,
)
_PER_ITEM_CAPTURE_PATTERN = re.compile(
    r"\b(?:each|every|per)\s+(?:item|entry|row|check)\w*\b|"
    r"\b(?:\u043a\u0430\u0436\u0434)\w*\s+(?:\u043f\u0443\u043d\u043a\u0442|\u044d\u043b\u0435\u043c\u0435\u043d\u0442|\u0441\u0442\u0440\u043e\u043a|\u043f\u0440\u043e\u0432\u0435\u0440\u043a)\w*\b",
    re.IGNORECASE,
)
_RESPONSIVE_PATTERN = re.compile(
    r"\b(?:mobile|compact|phone|tablet|desktop|wide|responsive|мобильн|компактн|телефон|планшет|десктоп|адаптив)\w*\b",
    re.IGNORECASE,
)
_ACCESSIBILITY_PATTERN = re.compile(
    r"\b(?:accessib|keyboard|screen reader|a11y|доступност|клавиатур|экранн\w+ диктор)\w*\b",
    re.IGNORECASE,
)
_WORKFLOW_STATES_PATTERNS = (
    re.compile(r"\bthrough\s+(?P<states>[^.!?]+)", re.IGNORECASE),
    re.compile(r"\b(?:через|по статусам)\s+(?P<states>[^.!?]+)", re.IGNORECASE),
)
_REPRESENTATIVE_STATE_SIGNAL_PATTERN = re.compile(
    r"\b(?:empty|no|none|without|unassigned|unfinished|incomplete|draft|completed?|"
    r"blocked|disabled|loading|offline|error|failed?|forbid|prevent|"
    r"пуст\w*|нет|без|неназнач\w*|не\s+назнач\w*|незаверш\w*|чернов\w*|"
    r"заверш\w*|заблокир\w*|недоступ\w*|загруз\w*|офлайн\w*|ошиб\w*|"
    r"неуспеш\w*|запрет\w*|нельзя)\b",
    re.IGNORECASE,
)


def _digest(value: Any) -> str:
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


@lru_cache(maxsize=2)
def _validator(filename: str) -> Draft202012Validator:
    schema = json.loads((_ABI_ROOT / filename).read_text(encoding="utf-8"))
    return Draft202012Validator(schema)


def _validate(filename: str, value: Mapping[str, Any]) -> None:
    _validator(filename).validate(dict(value))


def _locale(value: str | None, statement: str) -> str:
    token = str(value or "").strip().lower().replace("_", "-")
    if token:
        return token
    return "ru" if _CYRILLIC_PATTERN.search(statement) else "en"


def _knowledge(
    state: str,
    value: Any = None,
    *,
    evidence: list[str] | None = None,
    confidence: float | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {"state": state, "evidence": list(evidence or [])}
    if state == "known":
        result["value"] = copy.deepcopy(value)
    if confidence is not None:
        result["confidence"] = confidence
    return result


def _clauses(statement: str) -> list[tuple[str, int, int]]:
    result: list[tuple[str, int, int]] = []
    for match in re.finditer(r"[^.!?;\n]+(?:[.!?;]|$)", statement):
        raw = match.group(0)
        leading = len(raw) - len(raw.lstrip(" \t\r\n,;:-"))
        value = raw.strip(" \t\r\n,;:-.!?")
        if value:
            start = match.start() + leading
            result.append((value, start, start + len(value)))
    return result


def _authoring_spans(clause: str) -> list[tuple[int, int]]:
    spans = [
        match.span()
        for pattern in _AUTHORING_PATTERNS
        for match in pattern.finditer(clause)
    ]
    return sorted(spans)


def _overlaps_any(start: int, end: int, spans: list[tuple[int, int]]) -> bool:
    return any(start < span_end and end > span_start for span_start, span_end in spans)


def _without_spans(value: str, spans: list[tuple[int, int]]) -> str:
    if not spans:
        return value
    chars = list(value)
    for start, end in spans:
        chars[start:end] = " " * (end - start)
    return "".join(chars).strip(" \t\r\n,;:-.!?")


def _first_non_authoring_match(
    pattern: re.Pattern[str],
    clause: str,
    authoring_spans: list[tuple[int, int]],
) -> re.Match[str] | None:
    return next(
        (
            match
            for match in pattern.finditer(clause)
            if not _overlaps_any(match.start(), match.end(), authoring_spans)
        ),
        None,
    )


def _extract_operations(statement: str) -> tuple[list[dict[str, Any]], list[str]]:
    operations: list[dict[str, Any]] = []
    jobs: list[str] = []
    seen_operations: set[str] = set()
    for clause, clause_start, _clause_end in _clauses(statement):
        authoring_spans = _authoring_spans(clause)
        operation_exclusion_spans = [
            *authoring_spans,
            *(match.span() for match in _CAPTURE_ATTACHMENT_PATTERN.finditer(clause)),
        ]
        clause_operations = []
        for kind, pattern in _OPERATION_PATTERNS:
            match = _first_non_authoring_match(
                pattern, clause, operation_exclusion_spans
            )
            if match is not None:
                clause_operations.append((kind, match))
        if not clause_operations:
            continue
        job_statement = _without_spans(clause, authoring_spans)
        if job_statement:
            jobs.append(job_statement)
        for kind, match in clause_operations:
            if kind in seen_operations:
                continue
            seen_operations.add(kind)
            operation_start = clause_start + match.start()
            operation_end = clause_start + match.end()
            operations.append(
                {
                    "id": f"operation:{kind}",
                    "kind": kind,
                    "statement": match.group(0),
                    "target": _knowledge("unknown"),
                    "effect_scope": "read" if kind in _READ_OPERATIONS else "prototype",
                    "authority": "allowed" if kind in _READ_OPERATIONS else "unknown",
                    "evidence": [
                        f"intent.statement#char={operation_start}:{operation_end}"
                    ],
                }
            )
    return operations, jobs


def _extract_information_requirements(statement: str) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for match in _CAPTURE_ATTACHMENT_PATTERN.finditer(statement):
        requirements.append(
            {
                "id": f"information:{len(requirements) + 1:02d}",
                "kind": "attachment",
                "interaction": "capture",
                "statement": match.group(0).strip(),
                "evidence": [f"intent.statement#char={match.start()}:{match.end()}"],
                "confidence": 1.0,
            }
        )
    return requirements[:12]


def _extract_collection_requirements(statement: str) -> list[dict[str, Any]]:
    requirements: list[dict[str, Any]] = []
    for clause, start, end in _clauses(statement):
        if not _REPEATED_COLLECTION_PATTERN.search(clause):
            continue
        requirements.append(
            {
                "id": f"collection:{len(requirements) + 1:02d}",
                "kind": "repeated_collection",
                "interaction": (
                    "capture_each"
                    if _PER_ITEM_CAPTURE_PATTERN.search(clause)
                    else "present"
                ),
                "statement": clause,
                "evidence": [f"intent.statement#char={start}:{end}"],
                "confidence": 1.0,
            }
        )
    return requirements[:12]


def _extract_representative_states(statement: str) -> dict[str, Any]:
    for pattern in _WORKFLOW_STATES_PATTERNS:
        match = pattern.search(statement)
        if not match:
            continue
        raw = match.group("states")
        values = [
            item.strip(" \t\r\n,;:-")
            for item in re.split(r",|\band\b|\bи\b", raw, flags=re.IGNORECASE)
            if item.strip(" \t\r\n,;:-")
        ]
        if len(values) >= 2:
            return _knowledge(
                "known", values[:12], evidence=["intent.statement"], confidence=0.9
            )
    state_clauses = []
    for clause, _start, _end in _clauses(statement):
        value = _without_spans(clause, _authoring_spans(clause))
        if value and _REPRESENTATIVE_STATE_SIGNAL_PATTERN.search(value):
            state_clauses.append(value)
    if state_clauses:
        return _knowledge(
            "known",
            list(dict.fromkeys(state_clauses))[:12],
            evidence=["intent.statement"],
            confidence=0.8,
        )
    return _knowledge("unknown")


def capture_intent(
    statement: str,
    *,
    locale: str | None = None,
    source: Mapping[str, Any] | None = None,
    project_ref: str | None = None,
    change_ref: str | None = None,
    authority: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Capture the exact user statement without interpreting product semantics."""

    text = str(statement or "").strip()
    if not text:
        raise ValueError("Builder intent statement is required")
    source_value = {
        "kind": str(dict(source or {}).get("kind") or "chat").strip() or "chat"
    }
    for key in ("ref", "turn_id"):
        token = str(dict(source or {}).get(key) or "").strip()
        if token:
            source_value[key] = token
    authority_value = {
        "runtime_scope": "prototype",
        "external_effects": "unknown",
        "destructive_effects": "unknown",
        **dict(authority or {}),
    }
    unsigned: dict[str, Any] = {
        "schema": INTENT_SCHEMA,
        "statement": text,
        "locale": _locale(locale, text),
        "source": source_value,
        "scope": {
            "project_ref": str(project_ref or "").strip() or None,
            "change_ref": str(change_ref or "").strip() or None,
        },
        "explicit_refs": sorted(set(_REF_PATTERN.findall(text))),
        "authority": authority_value,
    }
    digest = _digest(unsigned)
    result = {
        **unsigned,
        "intent_id": f"intent:{digest.removeprefix('sha256:')[:24]}",
        "digest": digest,
    }
    _validate("builder.intent.v1.schema.json", result)
    return result


def compile_prototype_brief(intent: Mapping[str, Any] | str) -> dict[str, Any]:
    """Compile only explicit, deterministic facts and preserve unknown fields."""

    captured = (
        capture_intent(intent)
        if isinstance(intent, str)
        else copy.deepcopy(dict(intent))
    )
    _validate("builder.intent.v1.schema.json", captured)
    statement = str(captured["statement"])
    operations, job_statements = _extract_operations(statement)
    information_requirements = _extract_information_requirements(statement)
    collection_requirements = _extract_collection_requirements(statement)
    jobs = [
        {
            "id": f"job:{index:02d}",
            "statement": value,
            "evidence": ["intent.statement"],
            "confidence": 1.0,
        }
        for index, value in enumerate(job_statements, start=1)
    ]
    responsive = (
        _knowledge(
            "known",
            "explicitly requested",
            evidence=["intent.statement"],
            confidence=1.0,
        )
        if _RESPONSIVE_PATTERN.search(statement)
        else _knowledge("unknown")
    )
    accessibility = (
        _knowledge(
            "known",
            "explicitly requested",
            evidence=["intent.statement"],
            confidence=1.0,
        )
        if _ACCESSIBILITY_PATTERN.search(statement)
        else _knowledge("unknown")
    )
    unresolved = ["outcome", "actors", "entities", "content_volume"]
    if responsive["state"] == "unknown":
        unresolved.append("responsive")
    if accessibility["state"] == "unknown":
        unresolved.append("accessibility")
    if not operations:
        unresolved.append("operations")
    unsigned: dict[str, Any] = {
        "schema": PROTOTYPE_BRIEF_SCHEMA,
        "intent_ref": captured["intent_id"],
        "intent_digest": captured["digest"],
        "problem": _knowledge(
            "known", statement, evidence=["intent.statement"], confidence=1.0
        ),
        "outcome": _knowledge("unknown"),
        "actors": _knowledge("unknown"),
        "principal_jobs": jobs,
        "entities": _knowledge("unknown"),
        "information_requirements": information_requirements,
        "collection_requirements": collection_requirements,
        "operations": operations,
        "representative_states": _extract_representative_states(statement),
        "boundaries": {
            "data_effects": _knowledge(
                "known",
                "prototype_only",
                evidence=["intent.authority.runtime_scope"],
                confidence=1.0,
            ),
            "external_effects": _knowledge(
                "known",
                captured["authority"]["external_effects"],
                evidence=["intent.authority.external_effects"],
                confidence=1.0,
            ),
            "destructive_effects": _knowledge(
                "known",
                captured["authority"]["destructive_effects"],
                evidence=["intent.authority.destructive_effects"],
                confidence=1.0,
            ),
        },
        "constraints": {
            "locale": _knowledge(
                "known", captured["locale"], evidence=["intent.locale"], confidence=1.0
            ),
            "responsive": responsive,
            "accessibility": accessibility,
            "content_volume": _knowledge("unknown"),
        },
        "assumptions": [],
        "open_questions": [],
        "capability_gaps": [],
        "interpretation": {
            "mode": "deterministic_explicit",
            "confidence": 1.0 if operations else 0.5,
            "unresolved_fields": sorted(set(unresolved)),
        },
    }
    digest = _digest(unsigned)
    result = {
        **unsigned,
        "brief_id": f"brief:{digest.removeprefix('sha256:')[:24]}",
        "digest": digest,
    }
    _validate("builder.prototype_brief.v1.schema.json", result)
    return result


__all__ = [
    "INTENT_SCHEMA",
    "PROTOTYPE_BRIEF_SCHEMA",
    "capture_intent",
    "compile_prototype_brief",
]
