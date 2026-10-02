"""Trusted tool-invocation identity bound by Core ingress.

Request identity is transport metadata, not an application argument.  Skills may
read it through :mod:`adaos.sdk.access`, but only HTTP or authenticated subnet
RPC ingress is allowed to bind it.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator


_INVOCATION: ContextVar[dict[str, str | None] | None] = ContextVar(
    "adaos_verified_invocation",
    default=None,
)


def current_invocation() -> dict[str, Any] | None:
    """Return a defensive copy of the current verified ingress identity."""

    value = _INVOCATION.get()
    return dict(value) if value is not None else None


@contextmanager
def verified_invocation(
    *,
    request_id: str | None,
    idempotency_key: str | None,
) -> Iterator[None]:
    """Bind request identity for one trusted tool-dispatch boundary."""

    marker = _INVOCATION.set(
        {
            "request_id": str(request_id or "").strip() or None,
            "idempotency_key": str(idempotency_key or "").strip() or None,
        }
    )
    try:
        yield
    finally:
        _INVOCATION.reset(marker)


__all__ = ["current_invocation", "verified_invocation"]
