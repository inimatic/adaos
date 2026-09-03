from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


QUALIFICATION_CANDIDATE_SCHEMA = "adaos.builder.repair_qualification_candidate.v1"
SOURCE_INDEX_SCHEMA = "adaos.builder.component_source_index.v1"

_SOURCE_EXTENSIONS = {
    ".css",
    ".html",
    ".js",
    ".json",
    ".md",
    ".py",
    ".scss",
    ".ts",
    ".tsx",
    ".yaml",
    ".yml",
}
_IGNORED_PARTS = {"__pycache__", ".git", ".runtime", "dist", "node_modules"}
_IGNORED_FILES = {"prompt_state.json"}
_MAX_SOURCE_BYTES = 512 * 1024
_MAX_INDEXED_FILES = 160
_TOKEN_RE = re.compile(r"[A-Za-z0-9_]+|[\u0400-\u04ff]+")
_VALIDATION_FINDING_RE = re.compile(
    r"(?P<path>(?:skills|scenarios)/[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+)"
    r":\s+(?P<code>[A-Za-z0-9_.-]+):"
)
_DATA_ROUTE_LOCATION_RE = re.compile(r"data_routes\[(?P<index>\d+)]\.budget")
_DEFAULT_BOUNDED_ROUTE_PAYLOAD_BYTES = 65_536
_STOP_WORDS = {
    "and",
    "for",
    "from",
    "into",
    "must",
    "that",
    "the",
    "this",
    "with",
    "без",
    "был",
    "была",
    "были",
    "для",
    "его",
    "или",
    "как",
    "мне",
    "надо",
    "она",
    "они",
    "после",
    "при",
    "раз",
    "сам",
    "что",
    "это",
}
_CONCEPT_PREFIXES = {
    "ui": {
        "button",
        "changelog",
        "header",
        "icon",
        "interface",
        "layout",
        "modal",
        "screen",
        "table",
        "title",
        "ui",
        "widget",
        "виджет",
        "заголов",
        "икон",
        "интерф",
        "кноп",
        "модал",
        "окн",
        "таблиц",
        "экран",
        "шапк",
    },
    "data": {
        "data",
        "load",
        "metric",
        "quota",
        "refresh",
        "resource",
        "subscription",
        "summary",
        "token",
        "usage",
        "данн",
        "загруз",
        "квот",
        "метрик",
        "обнов",
        "подпис",
        "расход",
        "ресурс",
        "саммари",
        "токен",
    },
    "crud": {
        "create",
        "crud",
        "delete",
        "edit",
        "remove",
        "update",
        "добав",
        "измен",
        "редакт",
        "созда",
        "удал",
    },
    "subnet": {
        "api",
        "mcp",
        "network",
        "node",
        "public",
        "root",
        "sdk",
        "subnet",
        "источник",
        "нод",
        "подсет",
        "публичн",
        "сервер",
        "ядр",
    },
    "validation": {
        "accept",
        "check",
        "test",
        "validate",
        "verify",
        "провер",
        "прием",
        "тест",
    },
}


@dataclass(frozen=True)
class _SourceEntry:
    relative_path: str
    workspace_path: str
    role: str
    tokens: frozenset[str]
    semantic_refs: tuple[str, ...]
    sha256: str
    size: int


def _text(value: Any) -> str:
    return str(value or "").strip()


def _tokens(value: Any) -> set[str]:
    return {
        token
        for raw in _TOKEN_RE.findall(_text(value).casefold())
        for token in [raw.strip("_")]
        if len(token) >= 2 and token not in _STOP_WORDS
    }


def _concepts(tokens: set[str]) -> set[str]:
    return {
        concept
        for concept, prefixes in _CONCEPT_PREFIXES.items()
        if any(any(token.startswith(prefix) for prefix in prefixes) for token in tokens)
    }


def _flatten_structured(value: Any, *, pointer: str = "") -> tuple[set[str], list[tuple[str, set[str]]]]:
    tokens: set[str] = set()
    refs: list[tuple[str, set[str]]] = []
    if isinstance(value, Mapping):
        for raw_key, item in value.items():
            key = _text(raw_key)
            encoded = key.replace("~", "~0").replace("/", "~1")
            child_pointer = f"{pointer}/{encoded}"
            item_tokens = _tokens(key)
            if isinstance(item, (str, int, float, bool)):
                item_tokens.update(_tokens(item))
            tokens.update(item_tokens)
            refs.append((child_pointer, item_tokens))
            nested_tokens, nested_refs = _flatten_structured(item, pointer=child_pointer)
            tokens.update(nested_tokens)
            refs.extend(nested_refs)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            child_pointer = f"{pointer}/{index}"
            nested_tokens, nested_refs = _flatten_structured(item, pointer=child_pointer)
            tokens.update(nested_tokens)
            refs.extend(nested_refs)
    elif isinstance(value, (str, int, float, bool)):
        tokens.update(_tokens(value))
    return tokens, refs


def _python_symbols(text: str) -> tuple[set[str], list[tuple[str, set[str]]]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return _tokens(text), []
    tokens: set[str] = set()
    refs: list[tuple[str, set[str]]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            symbol_tokens = _tokens(node.name)
            tokens.update(symbol_tokens)
            refs.append((f"symbol:{node.name}", symbol_tokens))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            tokens.update(_tokens(node.value))
    return tokens, refs


def _source_role(relative_path: str) -> str:
    token = relative_path.casefold().replace("\\", "/")
    name = Path(token).name
    if token.startswith("tests/") or name.startswith("test_"):
        return "test"
    if name in {"webui.json", "scenario.json"} or Path(name).suffix in {".css", ".html", ".scss", ".ts", ".tsx"}:
        return "ui"
    if token.startswith("handlers/") or name in {"main.py", "handler.py"}:
        return "data"
    if name in {"skill.yaml", "skill.yml", "scenario.yaml", "scenario.yml", "project.yaml"}:
        return "manifest"
    return "source"


def _workspace_path(object_type: str, object_id: str, relative_path: str) -> str:
    collection = "skills" if object_type == "skill" else "scenarios"
    return f"{collection}/{object_id}/{relative_path}".replace("\\", "/")


def _read_source_entry(path: Path, *, root: Path, object_type: str, object_id: str) -> _SourceEntry | None:
    try:
        size = path.stat().st_size
        if size > _MAX_SOURCE_BYTES:
            return None
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeDecodeError):
        return None
    relative_path = path.relative_to(root).as_posix()
    suffix = path.suffix.casefold()
    semantic_tokens = _tokens(relative_path)
    refs: list[tuple[str, set[str]]] = []
    try:
        if suffix == ".json":
            nested_tokens, refs = _flatten_structured(json.loads(text))
            semantic_tokens.update(nested_tokens)
        elif suffix in {".yaml", ".yml"}:
            nested_tokens, refs = _flatten_structured(yaml.safe_load(text))
            semantic_tokens.update(nested_tokens)
        elif suffix == ".py":
            nested_tokens, refs = _python_symbols(text)
            semantic_tokens.update(nested_tokens)
        else:
            semantic_tokens.update(_tokens(text))
    except (json.JSONDecodeError, yaml.YAMLError):
        semantic_tokens.update(_tokens(text))
    return _SourceEntry(
        relative_path=relative_path,
        workspace_path=_workspace_path(object_type, object_id, relative_path),
        role=_source_role(relative_path),
        tokens=frozenset(semantic_tokens),
        semantic_refs=tuple(ref for ref, ref_tokens in refs if ref_tokens),
        sha256="sha256:" + hashlib.sha256(raw).hexdigest(),
        size=size,
    )


def build_component_source_index(
    *,
    source_root: Path,
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    root = source_root.expanduser().resolve()
    if not root.is_dir():
        return {
            "schema": SOURCE_INDEX_SCHEMA,
            "status": "unavailable",
            "object_type": object_type,
            "object_id": object_id,
            "source_root": str(root),
            "entries": [],
            "reason": "development source root is unavailable",
        }
    entries: list[_SourceEntry] = []
    for path in sorted(root.rglob("*")):
        if len(entries) >= _MAX_INDEXED_FILES:
            break
        if not path.is_file() or path.suffix.casefold() not in _SOURCE_EXTENSIONS:
            continue
        if path.name.casefold() in _IGNORED_FILES:
            continue
        if any(part in _IGNORED_PARTS for part in path.relative_to(root).parts):
            continue
        entry = _read_source_entry(path, root=root, object_type=object_type, object_id=object_id)
        if entry:
            entries.append(entry)
    return {
        "schema": SOURCE_INDEX_SCHEMA,
        "status": "ready" if entries else "empty",
        "object_type": object_type,
        "object_id": object_id,
        "source_root": str(root),
        "entries": [
            {
                "relative_path": entry.relative_path,
                "workspace_path": entry.workspace_path,
                "role": entry.role,
                "tokens": sorted(entry.tokens),
                "semantic_refs": list(entry.semantic_refs[:40]),
                "sha256": entry.sha256,
                "size": entry.size,
            }
            for entry in entries
        ],
        "file_count": len(entries),
        "truncated": len(entries) >= _MAX_INDEXED_FILES,
    }


def _evidence_file_paths(ticket: Mapping[str, Any]) -> set[str]:
    paths: set[str] = set()
    for raw in ticket.get("evidence_refs") or []:
        if not isinstance(raw, Mapping) or _text(raw.get("type")).lower() not in {"file", "test"}:
            continue
        value = _text(raw.get("path") or raw.get("id") or raw.get("ref")).replace("\\", "/")
        if value:
            paths.add(value.strip("/"))
    return paths


def _validation_findings(ticket: Mapping[str, Any]) -> list[dict[str, Any]]:
    messages: list[str] = []
    metadata = ticket.get("metadata") if isinstance(ticket.get("metadata"), Mapping) else {}
    for value in (metadata.get("error"), ticket.get("summary")):
        text = _text(value)
        if text and text not in messages:
            messages.append(text)
    for raw in ticket.get("evidence_refs") or []:
        if not isinstance(raw, Mapping):
            continue
        for value in (raw.get("error"), raw.get("message")):
            text = _text(value)
            if text and text not in messages:
                messages.append(text)
    findings: list[dict[str, Any]] = []
    for message in messages:
        for match in _VALIDATION_FINDING_RE.finditer(message.replace("\\", "/")):
            finding = {
                "path": match.group("path").strip("/"),
                "code": match.group("code"),
                "message": message,
            }
            if finding not in findings:
                findings.append(finding)
    return findings


def _bounded_route_budget_edit(
    *,
    source_root: Path,
    entry: Mapping[str, Any],
    finding: Mapping[str, Any],
) -> dict[str, Any] | None:
    if _text(finding.get("code")) != "data_routes.budget_missing":
        return None
    location = _DATA_ROUTE_LOCATION_RE.search(_text(finding.get("message")))
    if location is None:
        return None
    route_index = int(location.group("index"))
    relative_path = _text(entry.get("relative_path"))
    source_path = (source_root / relative_path).resolve()
    try:
        source_path.relative_to(source_root.resolve())
        original = source_path.read_text(encoding="utf-8")
        document = yaml.safe_load(original)
    except (OSError, UnicodeDecodeError, ValueError, yaml.YAMLError):
        return None
    routes = document.get("data_routes") if isinstance(document, Mapping) else None
    if (
        not isinstance(routes, list)
        or route_index >= len(routes)
        or not isinstance(routes[route_index], Mapping)
        or isinstance(routes[route_index].get("budget"), Mapping)
    ):
        return None

    lines = original.splitlines(keepends=True)
    data_line = next(
        (
            index
            for index, line in enumerate(lines)
            if re.match(r"^(?P<indent>\s*)data_routes:\s*(?:#.*)?(?:\r?\n)?$", line)
        ),
        None,
    )
    if data_line is None:
        return None
    data_indent = len(lines[data_line]) - len(lines[data_line].lstrip())
    item_starts = [
        index
        for index in range(data_line + 1, len(lines))
        if re.match(rf"^\s{{{data_indent}}}-\s", lines[index])
    ]
    if route_index >= len(item_starts):
        return None
    start = item_starts[route_index]
    next_item = item_starts[route_index + 1] if route_index + 1 < len(item_starts) else len(lines)
    for index in range(start + 1, next_item):
        line = lines[index]
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        if stripped.strip() and indent <= data_indent and not stripped.startswith("-"):
            next_item = index
            break
    block = "".join(lines[start:next_item])
    if not block or original.count(block) != 1:
        return None
    newline = "\r\n" if "\r\n" in original else "\n"
    child_indent = " " * (data_indent + 2)
    insertion = (
        f"{child_indent}budget:{newline}"
        f"{child_indent}  max_payload_bytes: {_DEFAULT_BOUNDED_ROUTE_PAYLOAD_BYTES}{newline}"
    )
    block_lines = lines[start:next_item]
    guard_offset = next(
        (
            index
            for index, line in enumerate(block_lines)
            if re.match(rf"^\s{{{data_indent + 2}}}guard_visibility:\s*", line)
        ),
        len(block_lines),
    )
    replacement = "".join(
        [*block_lines[:guard_offset], insertion, *block_lines[guard_offset:]]
    )
    return {
        "id": f"add-bounded-budget-route-{route_index}",
        "op": "replace_text",
        "path": _text(entry.get("workspace_path")),
        "old": block,
        "new": replacement,
        "expected_count": 1,
    }


def _validation_gate_qualification(
    ticket: Mapping[str, Any],
    *,
    source_root: Path,
    source_index: Mapping[str, Any],
    entries: list[Mapping[str, Any]],
    object_type: str,
    object_id: str,
) -> dict[str, Any] | None:
    findings = _validation_findings(ticket)
    if not findings:
        return None
    by_path = {_text(entry.get("workspace_path")): entry for entry in entries}
    matched = [(finding, by_path.get(_text(finding.get("path")))) for finding in findings]
    matched = [(finding, entry) for finding, entry in matched if entry is not None]
    if not matched:
        return None
    selected = list(dict.fromkeys(_text(entry.get("workspace_path")) for _, entry in matched))
    selected_entries = [by_path[path] for path in selected]
    operations = [
        operation
        for finding, entry in matched
        for operation in [
            _bounded_route_budget_edit(
                source_root=source_root,
                entry=entry,
                finding=finding,
            )
        ]
        if operation is not None
    ]
    summary = _text(ticket.get("summary"))
    codes = list(dict.fromkeys(_text(finding.get("code")) for finding, _ in matched))
    repair: dict[str, Any] = {
        "profile": "project_batch",
        "change_summary": summary[:1000],
        "target_files": selected,
        "target_refs": [f"file:{path}" for path in selected],
        "acceptance_checks": [
            *[f"Clear validation finding: {code}" for code in codes],
            f"Validate {object_type}:{object_id} after the bounded change.",
        ],
        "max_changed_files": len(selected),
        "requires_root_mcp": False,
        "target_object_type": object_type,
        "target_object_id": object_id,
        "source_preconditions": [
            {
                "path": _text(entry.get("workspace_path")),
                "sha256": _text(entry.get("sha256")),
                "size": int(entry.get("size") or 0),
            }
            for entry in selected_entries
        ],
    }
    if len(operations) == len(matched):
        repair["structured_edits"] = {
            "schema": "adaos.builder.structured_edit_set.v1",
            "operations": operations,
        }
    return {
        "schema": QUALIFICATION_CANDIDATE_SCHEMA,
        "status": "ready",
        "ready": True,
        "confidence": "high",
        "model_call_expected": False,
        "estimated_model_tokens": 0 if operations else None,
        "recommended_next": "apply_local_qualification",
        "reason": "exact validation findings were mapped to authoritative DEV source",
        "concepts": ["validation"],
        "validation_findings": [
            {"path": _text(finding.get("path")), "code": _text(finding.get("code"))}
            for finding, _ in matched
        ],
        "builder_repair": repair,
        "source_index": _compact_source_index(source_index, selected_entries),
    }


def _rank_entries(
    entries: list[Mapping[str, Any]],
    *,
    query_tokens: set[str],
    concepts: set[str],
    evidence_paths: set[str],
) -> list[tuple[int, Mapping[str, Any]]]:
    ranked: list[tuple[int, Mapping[str, Any]]] = []
    for entry in entries:
        path = _text(entry.get("workspace_path"))
        relative = _text(entry.get("relative_path"))
        role = _text(entry.get("role"))
        entry_tokens = set(entry.get("tokens") or [])
        overlap = query_tokens & entry_tokens
        score = min(18, len(overlap) * 3)
        if any(path.endswith(candidate) or candidate.endswith(relative) for candidate in evidence_paths):
            score += 40
        if "ui" in concepts and role == "ui":
            score += 16
        if "data" in concepts and role == "data":
            score += 14
        if "crud" in concepts and role in {"ui", "data"}:
            score += 10
        if "subnet" in concepts and role == "data":
            score += 8
        if "validation" in concepts and role == "test":
            score += 8
        if role == "manifest":
            score += 2
        ranked.append((score, entry))
    return sorted(ranked, key=lambda item: (-item[0], _text(item[1].get("workspace_path"))))


def _profile_for(concepts: set[str]) -> str:
    if "subnet" in concepts and "data" in concepts:
        return "subnet_data_integration"
    if "crud" in concepts:
        return "resource_crud"
    if "ui" in concepts:
        return "surgical_ui"
    if "data" in concepts:
        return "surgical_data"
    return "project_batch"


def _compact_source_index(
    source_index: Mapping[str, Any],
    entries: list[Mapping[str, Any]],
    *,
    limit: int = 12,
) -> dict[str, Any]:
    return {
        "schema": source_index.get("schema"),
        "status": source_index.get("status"),
        "object_type": source_index.get("object_type"),
        "object_id": source_index.get("object_id"),
        "file_count": source_index.get("file_count"),
        "truncated": source_index.get("truncated"),
        "entries": [
            {
                key: entry.get(key)
                for key in ("relative_path", "workspace_path", "role", "sha256", "size")
            }
            for entry in entries[: max(1, min(limit, 24))]
        ],
    }


def prepare_repair_qualification(
    ticket: Mapping[str, Any],
    *,
    development_source: Mapping[str, Any],
    object_type: str,
    object_id: str,
) -> dict[str, Any]:
    source_root_text = _text(
        development_source.get("dev_source_path") or development_source.get("source_path")
    )
    if _text(development_source.get("status")) != "source_available" or not source_root_text:
        return {
            "schema": QUALIFICATION_CANDIDATE_SCHEMA,
            "status": "needs_source",
            "ready": False,
            "confidence": "high",
            "model_call_expected": False,
            "recommended_next": "materialize_or_resolve_development_source",
            "reason": "an authoritative development source path is required before qualification",
        }
    source_index = build_component_source_index(
        source_root=Path(source_root_text),
        object_type=object_type,
        object_id=object_id,
    )
    entries = [dict(item) for item in source_index.get("entries") or [] if isinstance(item, Mapping)]
    if source_index.get("status") != "ready" or not entries:
        return {
            "schema": QUALIFICATION_CANDIDATE_SCHEMA,
            "status": "unavailable",
            "ready": False,
            "confidence": "high",
            "model_call_expected": False,
            "recommended_next": "repair_development_source_index",
            "reason": source_index.get("reason") or "development source index is empty",
            "source_index": source_index,
        }

    validation_qualification = _validation_gate_qualification(
        ticket,
        source_root=Path(source_root_text).expanduser().resolve(),
        source_index=source_index,
        entries=entries,
        object_type=object_type,
        object_id=object_id,
    )
    if validation_qualification is not None:
        return validation_qualification

    summary = _text(ticket.get("summary"))
    summary_tokens = _tokens(summary)
    summary_concepts = _concepts(summary_tokens)
    query_tokens = set(summary_tokens)
    target_scope = ticket.get("target_scope") if isinstance(ticket.get("target_scope"), Mapping) else {}
    query_tokens.update(_tokens(ticket.get("component_ref")))
    query_tokens.update(_tokens(target_scope.get("surface")))
    concepts = _concepts(query_tokens)
    evidence_paths = _evidence_file_paths(ticket)
    ranked = _rank_entries(
        entries,
        query_tokens=query_tokens,
        concepts=concepts,
        evidence_paths=evidence_paths,
    )
    selected: list[Mapping[str, Any]] = []
    for score, entry in ranked:
        role = _text(entry.get("role"))
        role_relevant = (
            (role == "ui" and "ui" in concepts)
            or (role == "data" and bool(concepts & {"data", "subnet"}))
            or (role == "source" and score >= 18)
            or any(
                _text(entry.get("workspace_path")).endswith(candidate)
                or candidate.endswith(_text(entry.get("relative_path")))
                for candidate in evidence_paths
            )
        )
        if role_relevant and score >= 8:
            selected.append(entry)
        if len(selected) >= 3:
            break
    if selected and not any(_text(item.get("role")) == "test" for item in selected):
        test_entry = next((entry for _score, entry in ranked if _text(entry.get("role")) == "test"), None)
        if test_entry is not None and len(selected) < 4:
            selected.append(test_entry)

    primary_score = ranked[0][0] if ranked else 0
    role_match = any(
        (_text(item.get("role")) == "ui" and "ui" in concepts)
        or (_text(item.get("role")) == "data" and bool(concepts & {"data", "subnet"}))
        for item in selected
    )
    evidence_match = bool(evidence_paths) and primary_score >= 40
    high_confidence = (
        bool(summary)
        and bool(selected)
        and (evidence_match or (role_match and bool(summary_concepts)))
    )
    if not high_confidence:
        return {
            "schema": QUALIFICATION_CANDIDATE_SCHEMA,
            "status": "needs_clarification",
            "ready": False,
            "confidence": "medium" if selected else "low",
            "model_call_expected": False,
            "recommended_next": "bounded_language_qualification_or_user_clarification",
            "reason": "the local source index could not identify an exact repair surface with high confidence",
            "concepts": sorted(concepts),
            "candidate_files": [_text(item.get("workspace_path")) for item in selected],
            "source_index": _compact_source_index(
                source_index,
                [entry for _score, entry in ranked],
            ),
        }

    target_files = [_text(item.get("workspace_path")) for item in selected]
    component_ref = _text(
        ticket.get("component_ref") or target_scope.get("component_ref")
    )
    target_refs = [
        *(
            [component_ref]
            if component_ref.partition(":")[0]
            in {"component", "modal", "panel", "view", "widget"}
            else []
        ),
        *[f"file:{path}" for path in target_files],
    ]
    acceptance_checks = [f"User-visible acceptance: {summary[:420]}"]
    test_paths = [
        _text(item.get("workspace_path"))
        for item in selected
        if _text(item.get("role")) == "test"
    ]
    acceptance_checks.extend(f"Run focused test file: {path}" for path in test_paths[:2])
    acceptance_checks.append(f"Validate {object_type}:{object_id} after the bounded change.")
    repair = {
        "profile": _profile_for(concepts),
        "change_summary": summary[:1000],
        "target_files": target_files,
        "target_refs": target_refs,
        "acceptance_checks": acceptance_checks,
        "max_changed_files": len(target_files),
        "requires_root_mcp": "subnet" in concepts,
        "target_object_type": object_type,
        "target_object_id": object_id,
        "source_preconditions": [
            {
                "path": _text(item.get("workspace_path")),
                "sha256": _text(item.get("sha256")),
                "size": int(item.get("size") or 0),
            }
            for item in selected
        ],
    }
    return {
        "schema": QUALIFICATION_CANDIDATE_SCHEMA,
        "status": "ready",
        "ready": True,
        "confidence": "high",
        "model_call_expected": False,
        "estimated_model_tokens": 0,
        "recommended_next": "apply_local_qualification",
        "reason": "exact target source and acceptance were derived from the authoritative dev source",
        "concepts": sorted(concepts),
        "builder_repair": repair,
        "source_index": _compact_source_index(source_index, selected),
    }


__all__ = [
    "QUALIFICATION_CANDIDATE_SCHEMA",
    "SOURCE_INDEX_SCHEMA",
    "build_component_source_index",
    "prepare_repair_qualification",
]
