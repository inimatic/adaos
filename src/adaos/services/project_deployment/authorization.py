from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


class ProjectDeploymentAuthorizationError(PermissionError):
    pass


_APPROVAL_PERMISSIONS = {
    "remote_install": "project.component.install.remote",
    "component_drain": "project.component.drain",
    "component_remove": "project.component.remove",
    "runtime_data_delete": "project.data.runtime.delete",
    "derived_data_delete": "project.data.derived.delete",
}


@dataclass(frozen=True, slots=True)
class DeploymentPrincipal:
    actor_ref: str
    permissions: frozenset[str]
    approvals: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        actor = str(self.actor_ref or "").strip()
        if not actor:
            raise ProjectDeploymentAuthorizationError("actor_ref is required")
        object.__setattr__(self, "actor_ref", actor)
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
    ) -> "DeploymentPrincipal":
        return cls(
            actor_ref=actor_ref,
            permissions=frozenset(permissions),
            approvals=frozenset(approvals),
        )

    def require(self, permission: str) -> None:
        if permission not in self.permissions:
            raise ProjectDeploymentAuthorizationError(
                f"missing_permission:{permission}"
            )

    def require_plan_approvals(self, required: Iterable[str]) -> None:
        for approval in sorted(set(required)):
            if approval not in self.approvals:
                raise ProjectDeploymentAuthorizationError(
                    f"missing_approval:{approval}"
                )
            permission = _APPROVAL_PERMISSIONS.get(approval)
            if permission is None:
                raise ProjectDeploymentAuthorizationError(
                    f"unsupported_approval:{approval}"
                )
            self.require(permission)


__all__ = ["DeploymentPrincipal", "ProjectDeploymentAuthorizationError"]
