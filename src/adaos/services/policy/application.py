"""Verified Application invocation context, bound only by trusted runtime ingress."""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any, Mapping


_APPLICATION: ContextVar[dict[str, Any] | None] = ContextVar(
    "adaos_verified_application",
    default=None,
)


def current_application() -> dict[str, Any] | None:
    value = _APPLICATION.get()
    return dict(value) if value is not None else None


def bind_application(value: Mapping[str, Any]) -> None:
    _APPLICATION.set(dict(value))


def clear_application() -> None:
    _APPLICATION.set(None)


__all__ = ["bind_application", "clear_application", "current_application"]
