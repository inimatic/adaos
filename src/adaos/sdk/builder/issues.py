"""Stable SDK facade for structural edits to Builder Issues."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from . import workflow


def split(
    object_type: str,
    object_id: str,
    issue_id: str,
    issues: Sequence[Mapping[str, Any]],
    *,
    change_id: str | None = None,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    return workflow.transition(
        object_type,
        object_id,
        "change_issue_split",
        actor="builder.issue_editor",
        metadata={
            "change_set_id": change_id,
            "issue_id": issue_id,
            "issues": [dict(item) for item in issues],
        },
        expected_generation=expected_generation,
    )


def merge(
    object_type: str,
    object_id: str,
    issue_ids: Sequence[str],
    issue: Mapping[str, Any],
    *,
    change_id: str | None = None,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    return workflow.transition(
        object_type,
        object_id,
        "change_issues_merged",
        actor="builder.issue_editor",
        metadata={
            "change_set_id": change_id,
            "issue_ids": list(issue_ids),
            "issue": dict(issue),
        },
        expected_generation=expected_generation,
    )


__all__ = ["merge", "split"]
