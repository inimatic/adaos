"""Operator-safe inspection and explicit recovery of transport ingress."""

from __future__ import annotations

import time
import uuid
from typing import Any, Mapping

from adaos.services import conversation_store


class ConversationTransportRecoveryError(ValueError):
    """Raised when transport recovery would violate no-replay semantics."""


def inspect_ingress(
    *,
    status: str | None = None,
    older_than_seconds: float | None = None,
    limit: int = 100,
    now: float | None = None,
) -> dict[str, Any]:
    cutoff = None
    if older_than_seconds is not None:
        cutoff = float(now if now is not None else time.time()) - max(
            0.0, float(older_than_seconds)
        )
    items = conversation_store.list_transport_ingress(
        status=status,
        older_than=cutoff,
        limit=limit,
    )
    return {
        "schema": "adaos.conversation.transport_ingress_inspection.v1",
        "items": items,
        "count": len(items),
        "payloads_exposed": False,
        "automatic_replay_allowed": False,
    }


def request_recovery(
    original_idempotency_key: str,
    *,
    actor_id: str,
    reason: str,
    operation_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a new inspectable recovery operation; never replay the input.

    The returned operation is intentionally not dispatched.  An operator must
    inspect current state and submit a fresh user/business command with a new
    idempotency key.  This keeps an uncertain original mutation inert.
    """

    original_key = str(original_idempotency_key or "").strip()
    actor = str(actor_id or "").strip()
    rationale = " ".join(str(reason or "").split()).strip()
    if not original_key or not actor or not rationale:
        raise ConversationTransportRecoveryError(
            "original_idempotency_key, actor_id, and reason are required"
        )
    original = conversation_store.get_transport_ingress(original_key)
    if original is None:
        raise ConversationTransportRecoveryError("transport ingress claim is unavailable")
    if str(original.get("status") or "") != "claimed":
        raise ConversationTransportRecoveryError(
            "only a claimed-but-not-dispatched ingress can request recovery"
        )
    recovery_id = str(operation_id or f"transport-recovery:{uuid.uuid4().hex}").strip()
    payload = {
        "intent": "transport.recovery.inspect_and_resubmit",
        "original_idempotency_key": original_key,
        "original_payload_digest": original.get("payload_digest"),
        "actor_id": actor,
        "reason": rationale[:1000],
    }
    claim = conversation_store.claim_transport_ingress(
        idempotency_key=recovery_id,
        transport="operator",
        event_id=recovery_id,
        payload=payload,
        meta={
            "recovery_for": original_key,
            "requires_fresh_command": True,
            "automatic_replay_allowed": False,
            **dict(metadata or {}),
        },
    )
    if not claim.get("claimed"):
        raise ConversationTransportRecoveryError("recovery operation identity already exists")
    return {
        "schema": "adaos.conversation.transport_recovery_request.v1",
        "operation_id": recovery_id,
        "status": "inspection_required",
        "original": original,
        "claim": claim,
        "next_step": "inspect current state and issue a fresh command",
        "replayed": False,
    }


__all__ = [
    "ConversationTransportRecoveryError",
    "inspect_ingress",
    "request_recovery",
]
