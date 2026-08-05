"""Stable SDK facade for Builder Review acceptance constraints."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


def _service():
    from adaos.services.builder.review import BuilderReviewService

    return BuilderReviewService.from_context()


def register_constraint(
    review: Mapping[str, Any],
    *,
    kind: str,
    expected: Any,
    source_revision: str,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    return dict(
        _service().register_constraint(
            review,
            kind=kind,
            expected=expected,
            source_revision=source_revision,
            expected_generation=expected_generation,
        )
    )


def evaluate_current(
    object_type: str,
    object_id: str,
    *,
    revision: str | None = None,
    expected_generation: int | None = None,
) -> dict[str, Any]:
    return dict(
        _service().evaluate_current(
            object_type,
            object_id,
            revision=revision,
            expected_generation=expected_generation,
        )
    )


def submit(review: Mapping[str, Any], *, expected_generation: int | None = None) -> dict[str, Any]:
    return dict(_service().submit(review, expected_generation=expected_generation))


def context_for_next_request(object_type: str, object_id: str) -> dict[str, Any]:
    return dict(_service().context_for_next_request(object_type, object_id))


def withdraw(
    object_type: str,
    object_id: str,
    review_id: str,
    *,
    reason: str | None = None,
) -> dict[str, Any]:
    return dict(_service().withdraw(object_type, object_id, review_id, reason=reason))


def dismiss(
    object_type: str,
    object_id: str,
    review_id: str,
    *,
    reason: str,
) -> dict[str, Any]:
    return dict(_service().dismiss(object_type, object_id, review_id, reason=reason))


def accept_as_constraint(
    object_type: str,
    object_id: str,
    review_id: str,
    *,
    kind: str,
    expected: Any,
    source_revision: str,
) -> dict[str, Any]:
    return dict(
        _service().accept_as_constraint(
            object_type,
            object_id,
            review_id,
            kind=kind,
            expected=expected,
            source_revision=source_revision,
        )
    )


def convert_to_issue(
    object_type: str,
    object_id: str,
    review_id: str,
    *,
    issue: Mapping[str, Any],
) -> dict[str, Any]:
    return dict(
        _service().convert_to_issue(
            object_type,
            object_id,
            review_id,
            issue=issue,
        )
    )


def supersede(
    object_type: str,
    object_id: str,
    review_id: str,
    *,
    reason: str,
    superseded_by_ref: str | None = None,
    waiver: bool = False,
) -> dict[str, Any]:
    return dict(
        _service().supersede(
            object_type,
            object_id,
            review_id,
            reason=reason,
            superseded_by_ref=superseded_by_ref,
            waiver=waiver,
        )
    )


def resolve(
    object_type: str,
    object_id: str,
    review_id: str,
    *,
    resolution_ref: str,
) -> dict[str, Any]:
    return dict(
        _service().resolve(
            object_type,
            object_id,
            review_id,
            resolution_ref=resolution_ref,
        )
    )


__all__ = [
    "accept_as_constraint",
    "context_for_next_request",
    "convert_to_issue",
    "dismiss",
    "evaluate_current",
    "register_constraint",
    "resolve",
    "submit",
    "supersede",
    "withdraw",
]
