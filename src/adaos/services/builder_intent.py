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
        r"\b(?:show|produce|prepare|make|build)\b.{0,40}\b(?:first|initial)\s+"
        r"(?:version|prototype)\b(?:\s+(?:usable|useful|practical|working))?(?:\s+now)?",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:создай|создайте|сделай|сделайте|разработай|подготовь)\w*\b.{0,60}"
        r"\b(?:приложение|сценарий|интерфейс|прототип|экран|страницу)\b",
        flags=re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:покаж|сделай|сделайте|подготовь)\w*\b.{0,40}"
        r"\b(?:перв\w*|рабоч\w*|содержательн\w*|пригодн\w*)\b.{0,24}"
        r"\b(?:верси\w*|прототип\w*)\b",
        flags=re.IGNORECASE,
    ),
)
_OPERATION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "search",
        re.compile(
            r"\b(?:search|lookup|искать|поиск)\w*\b",
            re.IGNORECASE,
        ),
    ),
    ("filter", re.compile(r"\b(?:filter|фильтр|фильтрац)\w*\b", re.IGNORECASE)),
    ("sort", re.compile(r"\b(?:sort|order by|сортир|упорядоч)\w*\b", re.IGNORECASE)),
    (
        "inspect",
        re.compile(
            r"\b(?:inspect|open|view|review|see|scan|browse|find|найти|наход|"
            r"откр|просмотр|просматр|изуч)\w*\b|"
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
            r"\b(?:add|create|capture|record|добав|созда|запис(?:а|ы)|запиш)\w*\b",
            re.IGNORECASE,
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
            r"(?:редактир|измен|отмет|перенос)\w*\b)",
            re.IGNORECASE,
        ),
    ),
    ("archive", re.compile(r"\b(?:archive|архив)\w*\b", re.IGNORECASE)),
    ("delete", re.compile(r"\b(?:delete|remove|удал)\w*\b", re.IGNORECASE)),
)
_READ_OPERATIONS = {"list", "inspect", "search", "filter", "sort"}
_CAPTURE_ATTACHMENT_PATTERN = re.compile(
    r"\b(?:(?:attach|upload)\w*[^.!?;\n]{0,80}(?:photos?|images?|pictures?|files?|attachments?|documents?)|"
    r"(?:add|capture)\w*\s+(?:(?!(?:to|about|for|of)\b)\w+\s+){0,3}(?:photos?|images?|pictures?|files?|attachments?|documents?)|"
    r"(?:photos?|images?|pictures?|files?|attachments?|documents?).{0,40}(?:attach|upload)\w*|"
    r"добав\w*\s+(?:\w+\s+)?(?:(?:\w+,\s*){0,3}\w+\s+и\s+)?(?:фото\w*|изображени\w*|снимок|файл\w*|вложени\w*|документ\w*)|"
    r"(?:прилож(?:ить|и|ите)|прикреп\w*|загруз(?:ить|и|ите|им|ят|ишь|ит)|загруж(?:ать|ает|ают|ай|айте))\b[^.!?;\n]{0,80}"
    r"(?:\u0444\u043e\u0442\u043e|\u0438\u0437\u043e\u0431\u0440\u0430\u0436|\u0441\u043d\u0438\u043c\u043e\u043a|\u0444\u0430\u0439\u043b|\u0432\u043b\u043e\u0436\u0435\u043d|\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442)\w*|"
    r"(?:\u0444\u043e\u0442\u043e|\u0438\u0437\u043e\u0431\u0440\u0430\u0436|\u0441\u043d\u0438\u043c\u043e\u043a|\u0444\u0430\u0439\u043b|\u0432\u043b\u043e\u0436\u0435\u043d|\u0434\u043e\u043a\u0443\u043c\u0435\u043d\u0442)\w*.{0,40}"
    r"(?:прилож(?:ить|и|ите)|прикреп\w*|загруз(?:ить|и|ите|им|ят|ишь|ит)|загруж(?:ать|ает|ают|ай|айте)))\b",
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
    r"unfilled|understaffed|fully\s+staffed|blocked|disabled|loading|offline|error|failed?|forbid|prevent|overdue|late|"
    r"conflict\w*|unavailable|busy|"
    r"пуст\w*|нет|без|неназнач\w*|не\s+назнач\w*|незаверш\w*|чернов\w*|"
    r"заверш\w*|заблокир\w*|недоступ\w*|загруз\w*|офлайн\w*|ошиб\w*|"
    r"неуспеш\w*|запрет\w*|нельзя|просроч\w*|конфликт\w*|занят\w*)\b",
    re.IGNORECASE,
)
_NON_STATE_CONTINUITY_PATTERN = re.compile(
    r"\bwithout\s+(?:losing|leaving|closing|hiding|resetting)\b|"
    r"\b(?:keep|preserve|retain)\w*\s+(?:the\s+)?(?:queue|list|context|selection)\b|"
    r"\b(?:\u043d\u0435\s+\u0442\u0435\u0440\u044f|\u0431\u0435\u0437\s+\u043f\u043e\u0442\u0435\u0440\u0438|\u0441\u043e\u0445\u0440\u0430\u043d)\w*.{0,24}"
    r"(?:\u043e\u0447\u0435\u0440\u0435\u0434|\u0441\u043f\u0438\u0441\u043e\u043a|\u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442|\u0432\u044b\u0431\u043e\u0440)\w*\b",
    re.IGNORECASE,
)
_JOB_SEPARATOR_PATTERN = re.compile(
    r"[,;:]|\b(?:and\s+then|then|and|or|but|while|"
    r"и\s+затем|затем|и|или|либо|а\s+затем|а)\b",
    re.IGNORECASE,
)

_EXCLUSION_END_PATTERN = re.compile(
    r"\b(?:не\s+(?:нужн\w*|требу\w*)|not\s+(?:needed|required|necessary))\s*$", re.IGNORECASE
)


def _explicit_exclusions(statement: str) -> tuple[str, list[dict[str, Any]]]:
    """Keep explicit scope exclusions as evidence, not required implementation work."""
    chars = list(statement)
    exclusions = []
    for clause, start, end in _clauses(statement):
        if not _EXCLUSION_END_PATTERN.search(clause):
            continue
        contrasts = list(re.finditer(r"\b(?:but|но)\s+", clause, re.IGNORECASE))
        if contrasts:
            offset = contrasts[-1].end()
            start += offset
            clause = clause[offset:]
        exclusions.append({"id": f"exclusion:{len(exclusions) + 1:02d}", "statement": clause,
                           "evidence": [f"intent.statement#char={start}:{end}"], "confidence": 1.0})
        chars[start:end] = " " * (end - start)
    return "".join(chars), exclusions


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


def _operation_mentions(
    clause: str, exclusion_spans: list[tuple[int, int]]
) -> list[tuple[str, re.Match[str]]]:
    candidates: list[tuple[int, int, int, str, re.Match[str]]] = []
    for priority, (kind, pattern) in enumerate(_OPERATION_PATTERNS):
        for match in pattern.finditer(clause):
            if _overlaps_any(match.start(), match.end(), exclusion_spans):
                continue
            candidates.append((match.start(), match.end(), priority, kind, match))
    selected: list[tuple[int, int, int, str, re.Match[str]]] = []
    for candidate in sorted(candidates, key=lambda item: (item[0], item[2], -item[1])):
        if any(
            candidate[0] < existing[1] and candidate[1] > existing[0]
            for existing in selected
        ):
            continue
        selected.append(candidate)
    mentions: list[tuple[str, re.Match[str]]] = []
    for _start, _end, _priority, kind, match in sorted(
        selected, key=lambda item: item[0]
    ):
        if (kind == "create" and match.group(0).lower() in {"record", "records"}
            and mentions and mentions[-1][0] in _READ_OPERATIONS
            and not _JOB_SEPARATOR_PATTERN.search(clause, mentions[-1][1].end(), match.start())):
            continue
        if mentions and mentions[-1][0] == kind:
            previous = mentions[-1][1]
            if not _JOB_SEPARATOR_PATTERN.search(
                clause, previous.end(), match.start()
            ):
                continue
        mentions.append((kind, match))
    return mentions


def _trim_job_span(clause: str, start: int, end: int) -> tuple[int, int]:
    discard = " \t\r\n,;:-.!?"
    while start < end and clause[start] in discard:
        start += 1
    while end > start and clause[end - 1] in discard:
        end -= 1
    return start, end


def _atomic_job_spans(
    clause: str,
    mentions: list[tuple[str, re.Match[str]]],
    authoring_spans: list[tuple[int, int]],
) -> list[tuple[str, int, int]]:
    if not mentions:
        return []
    first_mention_start = mentions[0][1].start()
    segment_start = max(
        (
            end
            for start, end in authoring_spans
            if start <= first_mention_start and end <= first_mention_start
        ),
        default=0,
    )
    boundaries: list[tuple[int, int]] = []
    for (_previous_kind, previous), (_kind, current) in zip(
        mentions, mentions[1:]
    ):
        separators = list(
            _JOB_SEPARATOR_PATTERN.finditer(clause, previous.end(), current.start())
        )
        if separators:
            separator = separators[-1]
            boundaries.append((separator.start(), separator.end()))
        else:
            boundaries.append((current.start(), current.start()))

    result: list[tuple[str, int, int]] = []
    for index, (_kind, mention) in enumerate(mentions):
        segment_end = boundaries[index][0] if index < len(boundaries) else len(clause)
        start, end = _trim_job_span(clause, segment_start, segment_end)
        if start >= end or not (start <= mention.start() < end):
            start, end = mention.start(), mention.end()
        result.append((clause[start:end], start, end))
        if index < len(boundaries):
            segment_start = boundaries[index][1]
    return result


def _extract_operations(
    statement: str,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    operations: list[dict[str, Any]] = []
    jobs: list[dict[str, Any]] = []
    seen_operations: set[tuple[str, str]] = set()
    for clause, clause_start, _clause_end in _clauses(statement):
        authoring_spans = _authoring_spans(clause)
        operation_exclusion_spans = [
            *authoring_spans,
            *(match.span() for match in _CAPTURE_ATTACHMENT_PATTERN.finditer(clause)),
        ]
        mentions = _operation_mentions(clause, operation_exclusion_spans)
        if not mentions:
            continue
        job_spans = _atomic_job_spans(clause, mentions, authoring_spans)
        for job_statement, job_start, job_end in job_spans:
            jobs.append(
                {
                    "id": f"job:{len(jobs) + 1:02d}",
                    "statement": job_statement,
                    "evidence": [
                        f"intent.statement#char={clause_start + job_start}:"
                        f"{clause_start + job_end}"
                    ],
                    "confidence": 0.95,
                }
            )
        for (kind, match), (job_statement, job_start, job_end) in zip(mentions, job_spans, strict=True):
            identity = (kind, job_statement)
            if identity in seen_operations:
                continue
            seen_operations.add(identity)
            kind_count = sum(item["kind"] == kind for item in operations)
            operation_start = clause_start + job_start
            operation_end = clause_start + job_end
            operations.append(
                {
                    "id": f"operation:{kind}" + (f":{kind_count + 1}" if kind_count else ""),
                    "kind": kind,
                    "statement": job_statement,
                    "source_clause": clause,
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


def _extract_residual_requirements(statement: str) -> list[dict[str, Any]]:
    """Keep explicit clauses that deterministic interpretation cannot classify.

    These are evidence-bearing requirements, not inferred domain concepts. A
    later schema-constrained stage may classify them, but cannot silently drop
    them while producing the Prototype.
    """

    requirements: list[dict[str, Any]] = []
    for clause, clause_start, _clause_end in _clauses(statement):
        authoring_spans = _authoring_spans(clause)
        value = _without_spans(clause, authoring_spans)
        if not value:
            continue
        value_start = clause.find(value)
        if value_start < 0:
            continue
        if _operation_mentions(
            clause,
            [
                *authoring_spans,
                *(match.span() for match in _CAPTURE_ATTACHMENT_PATTERN.finditer(clause)),
            ],
        ):
            continue
        if _CAPTURE_ATTACHMENT_PATTERN.search(value):
            continue
        if _REPEATED_COLLECTION_PATTERN.search(value):
            continue
        if _REPRESENTATIVE_STATE_SIGNAL_PATTERN.search(value):
            continue
        start = clause_start + value_start
        end = start + len(value)
        requirements.append(
            {
                "id": f"residual:{len(requirements) + 1:02d}",
                "statement": value,
                "evidence": [f"intent.statement#char={start}:{end}"],
                "confidence": 1.0,
            }
        )
    return requirements[:16]


def _extract_representative_states(
    statement: str, jobs: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    values: list[str] = []
    seen: set[str] = set()

    def append(value: str) -> None:
        normalized = str(value or "").strip(" \t\r\n,;:-.!?")
        key = normalized.casefold()
        if normalized and key not in seen:
            seen.add(key)
            values.append(normalized)

    for pattern in _WORKFLOW_STATES_PATTERNS:
        for match in pattern.finditer(statement):
            raw = match.group("states")
            states = [
                item.strip(" \t\r\n,;:-")
                for item in re.split(r",|\band\b|\bи\b", raw, flags=re.IGNORECASE)
                if item.strip(" \t\r\n,;:-")
            ]
            if len(states) >= 2:
                for state in states:
                    append(state)

    state_jobs = [
        str(item.get("statement") or "")
        for item in jobs or []
        if _REPRESENTATIVE_STATE_SIGNAL_PATTERN.search(
            str(item.get("statement") or "")
        )
        and not _NON_STATE_CONTINUITY_PATTERN.search(
            str(item.get("statement") or "")
        )
        and not any(
            pattern.search(str(item.get("statement") or ""))
            for pattern in _WORKFLOW_STATES_PATTERNS
        )
    ]
    for state_job in state_jobs:
        append(state_job)

    for clause, _start, _end in _clauses(statement):
        value = _without_spans(clause, _authoring_spans(clause))
        if not value or not _REPRESENTATIVE_STATE_SIGNAL_PATTERN.search(value):
            continue
        if any(state_job in value for state_job in state_jobs):
            continue
        if any(pattern.search(value) for pattern in _WORKFLOW_STATES_PATTERNS):
            continue
        signals = list(_REPRESENTATIVE_STATE_SIGNAL_PATTERN.finditer(value))
        split_values: list[str] = []
        if len(signals) > 1:
            for signal, following in zip(signals, signals[1:]):
                separators = list(
                    _JOB_SEPARATOR_PATTERN.finditer(
                        value, signal.end(), following.start()
                    )
                )
                if separators:
                    split_values.append(value[signal.start() : separators[-1].start()])
            if split_values:
                split_values.append(value[signals[-1].start() :])
        for state_value in split_values or [value]:
            append(state_value)

    if values:
        return _knowledge(
            "known",
            values[:12],
            evidence=["intent.statement"],
            confidence=0.9 if len(values) >= 2 else 0.8,
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
    statement, exclusions = _explicit_exclusions(statement)
    operations, jobs = _extract_operations(statement)
    information_requirements = _extract_information_requirements(statement)
    collection_requirements = _extract_collection_requirements(statement)
    residual_requirements = _extract_residual_requirements(statement)
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
            "known", str(captured["statement"]), evidence=["intent.statement"], confidence=1.0
        ),
        "outcome": _knowledge("unknown"),
        "actors": _knowledge("unknown"),
        "principal_jobs": jobs,
        "residual_requirements": residual_requirements,
        "exclusions": exclusions,
        "entities": _knowledge("unknown"),
        "information_requirements": information_requirements,
        "collection_requirements": collection_requirements,
        "operations": operations,
        "representative_states": _extract_representative_states(statement, jobs),
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


def _brief_evidence(brief: Mapping[str, Any], values: Any) -> list[str]:
    brief_ref = str(brief.get("brief_id") or "").strip()
    return [
        value if value.startswith("brief:") else f"{brief_ref}/{value}"
        for raw in values or []
        if (value := str(raw or "").strip())
    ]


def _stable_requirement_id(prefix: str, item: Mapping[str, Any]) -> str:
    identity = {
        key: item.get(key)
        for key in ("kind", "interaction", "statement")
        if item.get(key) is not None
    }
    return f"{prefix}:{_digest(identity).removeprefix('sha256:')[:12]}"


def _merge_requirement_list(
    briefs: list[dict[str, Any]], key: str, prefix: str
) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for brief in briefs:
        for raw in brief.get(key) or []:
            if not isinstance(raw, Mapping):
                continue
            item = copy.deepcopy(dict(raw))
            stable_id = _stable_requirement_id(prefix, item)
            item["id"] = stable_id
            item["evidence"] = _brief_evidence(brief, item.get("evidence"))
            existing = merged.get(stable_id)
            if existing:
                item["evidence"] = list(
                    dict.fromkeys(
                        [
                            *list(existing.get("evidence") or []),
                            *list(item.get("evidence") or []),
                        ]
                    )
                )
                item["confidence"] = max(
                    float(existing.get("confidence") or 0),
                    float(item.get("confidence") or 0),
                )
            merged[stable_id] = item
    return list(merged.values())


def _merge_known_field(
    briefs: list[dict[str, Any]], path: tuple[str, ...]
) -> dict[str, Any]:
    selected: tuple[dict[str, Any], Mapping[str, Any]] | None = None
    for brief in briefs:
        value: Any = brief
        for key in path:
            value = value.get(key) if isinstance(value, Mapping) else None
        if isinstance(value, Mapping) and value.get("state") == "known":
            selected = (brief, value)
    if selected is None:
        return _knowledge("unknown")
    brief, value = selected
    result = copy.deepcopy(dict(value))
    result["evidence"] = _brief_evidence(brief, value.get("evidence"))
    return result


def _merge_representative_states(briefs: list[dict[str, Any]]) -> dict[str, Any]:
    values: list[Any] = []
    evidence: list[str] = []
    confidence = 0.0
    for brief in briefs:
        state = brief.get("representative_states")
        if not isinstance(state, Mapping) or state.get("state") != "known":
            continue
        raw_values = state.get("value")
        candidates = raw_values if isinstance(raw_values, list) else [raw_values]
        for value in candidates:
            if value not in (None, "") and value not in values:
                values.append(copy.deepcopy(value))
        evidence.extend(_brief_evidence(brief, state.get("evidence")))
        confidence = max(confidence, float(state.get("confidence") or 0))
    if not values:
        return _knowledge("unknown")
    return _knowledge(
        "known",
        values,
        evidence=list(dict.fromkeys(evidence)),
        confidence=confidence or 1.0,
    )


def merge_prototype_briefs(*values: Mapping[str, Any]) -> dict[str, Any]:
    """Merge accepted turn Briefs into one content-addressed project Brief."""

    briefs = [copy.deepcopy(dict(value)) for value in values if value]
    if not briefs:
        raise ValueError("At least one Prototype Brief is required")
    for brief in briefs:
        _validate("builder.prototype_brief.v1.schema.json", brief)
    unique = list({str(brief["digest"]): brief for brief in briefs}.values())
    if len(unique) == 1:
        return unique[0]
    briefs = unique
    current = briefs[-1]

    operations = _merge_requirement_list(briefs, "operations", "operation")
    unsigned: dict[str, Any] = {
        "schema": PROTOTYPE_BRIEF_SCHEMA,
        "intent_ref": current["intent_ref"],
        "intent_digest": current["intent_digest"],
        "problem": _merge_known_field(briefs, ("problem",)),
        "outcome": _merge_known_field(briefs, ("outcome",)),
        "actors": _merge_known_field(briefs, ("actors",)),
        "principal_jobs": _merge_requirement_list(
            briefs, "principal_jobs", "job"
        ),
        "residual_requirements": _merge_requirement_list(
            briefs, "residual_requirements", "residual"
        ),
        "exclusions": _merge_requirement_list(briefs, "exclusions", "exclusion"),
        "entities": _merge_known_field(briefs, ("entities",)),
        "information_requirements": _merge_requirement_list(
            briefs, "information_requirements", "information"
        ),
        "collection_requirements": _merge_requirement_list(
            briefs, "collection_requirements", "collection"
        ),
        "operations": operations,
        "representative_states": _merge_representative_states(briefs),
        "boundaries": {
            key: _merge_known_field(briefs, ("boundaries", key))
            for key in ("data_effects", "external_effects", "destructive_effects")
        },
        "constraints": {
            key: _merge_known_field(briefs, ("constraints", key))
            for key in ("locale", "responsive", "accessibility", "content_volume")
        },
        "assumptions": _merge_requirement_list(briefs, "assumptions", "assumption"),
        "open_questions": _merge_requirement_list(
            briefs, "open_questions", "question"
        ),
        "capability_gaps": _merge_requirement_list(
            briefs, "capability_gaps", "gap"
        ),
        "interpretation": {
            "mode": (
                "model_assisted"
                if any(
                    brief.get("interpretation", {}).get("mode") == "model_assisted"
                    for brief in briefs
                )
                else "deterministic_explicit"
            ),
            "confidence": min(
                float(brief.get("interpretation", {}).get("confidence") or 0)
                for brief in briefs
            ),
            "unresolved_fields": sorted(
                {
                    str(field)
                    for brief in briefs
                    for field in brief.get("interpretation", {}).get(
                        "unresolved_fields", []
                    )
                    if str(field).strip()
                }
            ),
        },
    }
    if operations:
        unsigned["interpretation"]["unresolved_fields"] = [
            field
            for field in unsigned["interpretation"]["unresolved_fields"]
            if field != "operations"
        ]
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
    "merge_prototype_briefs",
]
