"""Core-bound authority for one declared skill lifecycle hook.

Lifecycle hooks are not user tool calls. Core invokes them while installing,
activating, draining, or rehydrating a skill runtime. This binding lets SDK
authorization distinguish that trusted control-plane call from an
unauthenticated invocation without accepting authority from tool arguments.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Iterable, Iterator


@dataclass(frozen=True, slots=True)
class LifecycleAuthority:
    skill_name: str
    hook_key: str
    tool_name: str
    permissions: tuple[str, ...]


_LIFECYCLE_AUTHORITY: ContextVar[LifecycleAuthority | None] = ContextVar(
    "adaos_verified_lifecycle_authority",
    default=None,
)


def current_lifecycle_authority() -> LifecycleAuthority | None:
    return _LIFECYCLE_AUTHORITY.get()


@contextmanager
def verified_lifecycle_authority(
    *,
    skill_name: str,
    hook_key: str,
    tool_name: str,
    permissions: Iterable[str],
) -> Iterator[None]:
    """Bind an exact manifest-declared lifecycle operation inside Core only."""

    authority = LifecycleAuthority(
        skill_name=str(skill_name or "").strip(),
        hook_key=str(hook_key or "").strip(),
        tool_name=str(tool_name or "").strip(),
        permissions=tuple(
            sorted(
                {
                    str(permission or "").strip().lower()
                    for permission in permissions
                    if str(permission or "").strip()
                }
            )
        ),
    )
    marker = _LIFECYCLE_AUTHORITY.set(authority)
    try:
        yield
    finally:
        _LIFECYCLE_AUTHORITY.reset(marker)


__all__ = [
    "LifecycleAuthority",
    "current_lifecycle_authority",
    "verified_lifecycle_authority",
]
