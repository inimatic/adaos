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


__all__ = ["evaluate_current", "register_constraint"]
