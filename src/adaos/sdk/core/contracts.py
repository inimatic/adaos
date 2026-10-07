from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any, TypeVar


F = TypeVar("F", bound=Callable[..., Any])


def public_contract(
    *,
    capabilities: Sequence[str] = (),
    permissions: Sequence[str] = (),
    effects: Sequence[str] = (),
    errors: Sequence[str] = (),
    boundedness: Mapping[str, Any] | None = None,
    pagination: Mapping[str, Any] | None = None,
    stability: str = "experimental",
    since: str | None = None,
    runtime_support: Mapping[str, Any] | None = None,
    action_closure: Mapping[str, Any] | None = None,
) -> Callable[[F], F]:
    """Attach the build-time SDK contract consumed by metadata generation."""

    metadata = {
        "capabilities": list(capabilities),
        "permissions": list(permissions),
        "effects": list(effects),
        "errors": list(errors),
        "boundedness": dict(boundedness or {}),
        "pagination": dict(pagination or {}),
        "stability": str(stability or "experimental"),
        "since": since,
        "runtime_support": dict(runtime_support or {}),
        "action_closure": dict(action_closure or {}),
    }

    def decorate(fn: F) -> F:
        setattr(fn, "__adaos_public_contract__", metadata)
        return fn

    return decorate


__all__ = ["public_contract"]
