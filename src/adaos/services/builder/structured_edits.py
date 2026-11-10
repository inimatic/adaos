"""Shared admission for deterministic edits before queueing or source I/O."""
from __future__ import annotations
from collections.abc import Mapping, Sequence
from pathlib import PurePosixPath
import re
from typing import Any

STRUCTURED_EDIT_SCHEMA = "adaos.builder.structured_edit_set.v1"


def json_pointer_tokens(pointer: str) -> list[str]:
    if not isinstance(pointer, str) or not pointer.startswith("/"):
        raise ValueError("structured JSON edit requires a non-root RFC 6901 pointer")
    tokens = []
    for raw in pointer[1:].split("/"):
        if re.search(r"~(?![01])", raw):
            raise ValueError(f"invalid RFC 6901 pointer escape: {pointer}")
        tokens.append(raw.replace("~1", "/").replace("~0", "~"))
    return tokens


def validate_structured_edit_set(value: Any, *, allowed_paths: Sequence[str] | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping) or value.get("schema") != STRUCTURED_EDIT_SCHEMA:
        raise ValueError(f"structured edit schema must be {STRUCTURED_EDIT_SCHEMA}")
    operations = value.get("operations")
    if not isinstance(operations, list) or not 1 <= len(operations) <= 24:
        raise ValueError("structured edit set requires 1..24 operations")
    allowed = {str(p).replace("\\", "/").strip("/") for p in allowed_paths} if allowed_paths is not None else None
    for index, operation in enumerate(operations):
        prefix = f"structured edit {index}"
        if not isinstance(operation, Mapping):
            raise ValueError(f"{prefix} must be an object")
        relative = str(operation.get("path") or "").replace("\\", "/")
        if (not relative or relative.startswith("/") or ":" in relative
                or ".." in PurePosixPath(relative).parts):
            raise ValueError(f"{prefix} requires a relative source path")
        if allowed is not None and relative not in allowed:
            raise ValueError(f"{prefix} path is outside exact_changed_paths: {relative}")
        op = str(operation.get("op") or "").strip().lower()
        if op == "replace_text":
            if not isinstance(operation.get("old"), str) or not operation["old"] or not isinstance(operation.get("new"), str):
                raise ValueError(f"{prefix}: replace_text requires non-empty old and string new")
            count = operation.get("expected_count", 1)
            if type(count) is not int or count < 1:
                raise ValueError(f"{prefix}: expected_count must be a positive integer")
        elif op == "delete_file":
            expected_digest = str(operation.get("expected_digest") or "").strip()
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", expected_digest):
                raise ValueError(
                    f"{prefix}: delete_file requires a sha256 expected_digest"
                )
        elif op in {"json_add", "json_remove", "json_replace", "json_move"}:
            if PurePosixPath(relative).suffix.lower() != ".json":
                raise ValueError(f"{prefix}: {op} requires a JSON file")
            try:
                target = json_pointer_tokens(operation.get("pointer"))
                if op == "json_move":
                    source = json_pointer_tokens(operation.get("from_pointer"))
                    if target[:len(source)] == source:
                        raise ValueError("json_move cannot move a value into its own child")
            except ValueError as exc:
                raise ValueError(f"{prefix} ({relative}): {exc}") from exc
            required = {"value"} if op == "json_add" else {"expected", "value"} if op == "json_replace" else {"expected"}
            if not required.issubset(operation):
                raise ValueError(f"{prefix}: {op} requires {', '.join(sorted(required))}")
        else:
            raise ValueError(f"{prefix}: unsupported structured edit operation: {op}")
    return dict(value)
