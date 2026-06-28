from __future__ import annotations

from typing import Any, Mapping

from adaos.services import conversation_store


def remember(
    *,
    scope: str,
    owner: str,
    subject_id: str | None = None,
    key: str | None = None,
    text: str | None = None,
    value: Mapping[str, Any] | None = None,
    confidence: float | None = None,
    consent_state: str = "unknown",
    policy: Mapping[str, Any] | None = None,
    source_ref: Mapping[str, Any] | None = None,
    retention_class: str = "normal",
    retention_until: float | None = None,
    redaction_state: str = "active",
    redacted_at: float | None = None,
    redaction_reason: str | None = None,
) -> str | None:
    return conversation_store.remember(
        scope=scope,
        owner=owner,
        subject_id=subject_id,
        key=key,
        text=text,
        value=value,
        confidence=confidence,
        consent_state=consent_state,
        policy=policy,
        source_ref=source_ref,
        retention_class=retention_class,
        retention_until=retention_until,
        redaction_state=redaction_state,
        redacted_at=redacted_at,
        redaction_reason=redaction_reason,
    )


def list(
    *,
    scope: str | None = None,
    owner: str | None = None,
    subject_id: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    return conversation_store.list_memory(scope=scope, owner=owner, subject_id=subject_id, limit=limit)
