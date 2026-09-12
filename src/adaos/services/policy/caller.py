"""Trusted invocation identity, separate from application arguments and skill identity."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from adaos.domain.personalization_access import ScopeRef, SubjectRef


_CALLER: ContextVar[SubjectRef | None] = ContextVar("adaos_verified_caller", default=None)
_SCOPE: ContextVar[ScopeRef | None] = ContextVar("adaos_verified_caller_scope", default=None)


class CallerAccessDenied(PermissionError):
    """An authenticated caller lacks access, or trusted caller context is absent."""


def current_caller() -> SubjectRef | None:
    return _CALLER.get()


def current_caller_scope() -> ScopeRef | None:
    return _SCOPE.get()


@contextmanager
def verified_caller(actor: SubjectRef | None, scope: ScopeRef | None = None) -> Iterator[None]:
    """Bind identity at a trusted ingress; never deserialize it from tool arguments."""
    marker = _CALLER.set(actor)
    scope_marker = _SCOPE.set(scope)
    try:
        yield
    finally:
        _SCOPE.reset(scope_marker)
        _CALLER.reset(marker)
