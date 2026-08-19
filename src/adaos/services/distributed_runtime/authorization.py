from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class DistributedAuthorizationError(PermissionError):
    pass


@dataclass(frozen=True, slots=True)
class DistributedPrincipal:
    actor_ref: str
    permissions: frozenset[str]
    approvals: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        actor_ref = str(self.actor_ref or "").strip()
        if not actor_ref:
            raise DistributedAuthorizationError("actor_ref is required")
        object.__setattr__(self, "actor_ref", actor_ref)
        object.__setattr__(
            self,
            "permissions",
            frozenset(
                str(item).strip() for item in self.permissions if str(item).strip()
            ),
        )
        object.__setattr__(
            self,
            "approvals",
            frozenset(
                str(item).strip() for item in self.approvals if str(item).strip()
            ),
        )

    @classmethod
    def create(
        cls,
        *,
        actor_ref: str,
        permissions: Iterable[str],
        approvals: Iterable[str] = (),
    ) -> "DistributedPrincipal":
        return cls(
            actor_ref=actor_ref,
            permissions=frozenset(permissions),
            approvals=frozenset(approvals),
        )

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise DistributedAuthorizationError(f"missing_permission:{permission}")

    def require_approval(self, approval: str, *, permission: str) -> None:
        if approval not in self.approvals:
            raise DistributedAuthorizationError(f"missing_approval:{approval}")
        self.require(permission)


__all__ = ["DistributedAuthorizationError", "DistributedPrincipal"]
