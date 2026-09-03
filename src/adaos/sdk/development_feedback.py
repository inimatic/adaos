"""Typed facade for model and developer feedback observations."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from adaos.services.development_feedback import DevelopmentFeedbackService


def _service() -> DevelopmentFeedbackService:
    return DevelopmentFeedbackService()


def capture_feedback(
    summary: str,
    *,
    source: str,
    category: str,
    blocking: bool = False,
    confidence: float = 1.0,
    impact: Sequence[str] = (),
    target_refs: Sequence[str] = (),
    details: str = "",
    recommendation: str = "",
    evidence_refs: Sequence[Mapping[str, Any]] = (),
    relation_refs: Sequence[Mapping[str, Any]] = (),
    classification: Mapping[str, Any] | None = None,
    dedup_key: str | None = None,
    actor: str = "sdk",
    idempotent_replay: bool = False,
) -> dict[str, Any]:
    return _service().capture(
        source=source,
        category=category,
        summary=summary,
        blocking=blocking,
        confidence=confidence,
        impact=impact,
        target_refs=target_refs,
        details=details,
        recommendation=recommendation,
        evidence_refs=evidence_refs,
        relation_refs=relation_refs,
        classification=classification,
        dedup_key=dedup_key,
        actor=actor,
        idempotent_replay=idempotent_replay,
    )


def list_feedback(**filters: Any) -> list[dict[str, Any]]:
    return _service().list(**filters)


def get_feedback(feedback_id: str) -> dict[str, Any] | None:
    return _service().get(feedback_id)


def transition_feedback(
    feedback_id: str,
    status: str,
    *,
    actor: str = "sdk",
    reason: str = "",
    classification: Mapping[str, Any] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    return _service().transition(
        feedback_id,
        status=status,
        actor=actor,
        reason=reason,
        classification=classification,
        expected_revision=expected_revision,
    )


def comment_feedback(
    feedback_id: str,
    body: str,
    *,
    actor: str = "sdk",
    expected_revision: int | None = None,
) -> dict[str, Any]:
    return _service().comment(
        feedback_id,
        body=body,
        actor=actor,
        expected_revision=expected_revision,
    )


def promote_feedback(
    feedback_id: str,
    route: str,
    *,
    actor: str = "sdk",
    payload: Mapping[str, Any] | None = None,
    expected_revision: int | None = None,
) -> dict[str, Any]:
    return _service().promote(
        feedback_id,
        route=route,
        actor=actor,
        payload=payload,
        expected_revision=expected_revision,
    )


__all__ = [
    "capture_feedback",
    "comment_feedback",
    "get_feedback",
    "list_feedback",
    "promote_feedback",
    "transition_feedback",
]
