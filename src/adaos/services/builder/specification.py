"""Read-only Builder Specification projection with text-integrity evidence."""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any


SPECIFICATION_PROJECTION_SCHEMA = "adaos.builder.specification_projection.v1"


def text_integrity(value: Any) -> dict[str, Any]:
    """Describe existing text without guessing the bytes that were lost."""

    raw = str(value or "")
    markers: list[str] = []
    if "\ufffd" in raw:
        markers.append("unicode_replacement_character")
    if "????" in raw:
        markers.append("question_mark_run")
    corrupted = bool(markers)
    return {
        "raw": raw,
        "display": raw,
        "integrity": "transport_corrupted" if corrupted else "preserved",
        "reason_code": "historical_text_lossy" if corrupted else None,
        "markers": markers,
        "repair_policy": "explicit_source_required" if corrupted else "not_required",
    }


def specification_projection(change: Mapping[str, Any] | None) -> dict[str, Any]:
    source = copy.deepcopy(dict(change or {}))
    issues: list[dict[str, Any]] = []
    corrupted_paths: list[str] = []
    request = text_integrity(source.get("request"))
    if request["integrity"] == "transport_corrupted":
        corrupted_paths.append("request")
    addenda = []
    for index, item in enumerate(source.get("request_addenda") or []):
        projected = text_integrity(item)
        if projected["integrity"] == "transport_corrupted":
            corrupted_paths.append(f"request_addenda[{index}]")
        addenda.append(projected)
    for issue_index, raw_issue in enumerate(source.get("issues") or []):
        if not isinstance(raw_issue, Mapping):
            continue
        issue = dict(raw_issue)
        title = text_integrity(issue.get("title"))
        if title["integrity"] == "transport_corrupted":
            corrupted_paths.append(f"issues[{issue_index}].title")
        criteria = []
        for criterion_index, value in enumerate(issue.get("acceptance_criteria") or []):
            projected = text_integrity(value)
            if projected["integrity"] == "transport_corrupted":
                corrupted_paths.append(
                    f"issues[{issue_index}].acceptance_criteria[{criterion_index}]"
                )
            criteria.append(projected)
        issues.append(
            {
                "issue_id": str(issue.get("issue_id") or ""),
                "title": title,
                "acceptance_criteria": criteria,
            }
        )
    return {
        "schema": SPECIFICATION_PROJECTION_SCHEMA,
        "change_id": str(source.get("change_id") or source.get("change_set_id") or "") or None,
        "request": request,
        "request_addenda": addenda,
        "issues": issues,
        "integrity": "transport_corrupted" if corrupted_paths else "preserved",
        "corrupted_paths": corrupted_paths,
        "repair_policy": (
            "retain_raw_and_request_explicit_replacement"
            if corrupted_paths
            else "not_required"
        ),
    }


__all__ = ["SPECIFICATION_PROJECTION_SCHEMA", "specification_projection", "text_integrity"]
