from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactReleaseContractError,
    ProjectRelease,
    StableSubscription,
    WorkspaceLock,
    canonical_payload_digest,
)
from adaos.services.artifact_pipeline.channels import ChannelPointer, SubscriptionStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.workspace_registry import (
    WorkspaceRegistryError,
    find_workspace_registry_entry,
    set_workspace_registry_channel,
)


REGISTRY_RECONCILIATION_PLAN_SCHEMA = "adaos.artifact.registry_reconciliation_plan.v1"
REGISTRY_RECONCILIATION_OPERATION_SCHEMA = "adaos.artifact.registry_reconciliation_operation.v1"
ArtifactKind = Literal["skill", "scenario"]


class RegistryProjectionRemote(Protocol):
    def get_channel(self, project_id: str, channel: str = "stable") -> ChannelPointer: ...

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...


class RegistryReconciliationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class RegistryReconciliationPlan:
    project_id: str
    kind: ArtifactKind
    artifact_id: str
    channel: str
    pointer: ChannelPointer
    release: ProjectRelease
    component: ArtifactPackageRef
    subscription: StableSubscription | None
    observed_registry_entry_digest: str | None
    observed_workspace_lock_digest: str | None
    target_registry_channel: Mapping[str, Any]
    target_registry_source: Mapping[str, Any]
    action: str
    allowed: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": REGISTRY_RECONCILIATION_PLAN_SCHEMA,
            "project_id": self.project_id,
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "channel": self.channel,
            "pointer": self.pointer.to_dict(),
            "release_digest": self.release.release_digest,
            "component": self.component.to_dict(),
            "subscription": self.subscription.to_dict() if self.subscription else None,
            "observed_registry_entry_digest": self.observed_registry_entry_digest,
            "observed_workspace_lock_digest": self.observed_workspace_lock_digest,
            "target_registry_channel": dict(self.target_registry_channel),
            "target_registry_source": dict(self.target_registry_source),
            "action": self.action,
            "allowed": self.allowed,
            "warnings": list(self.warnings),
        }
        payload["plan_digest"] = canonical_payload_digest(payload)
        return payload

    @property
    def plan_digest(self) -> str:
        return str(self.to_dict()["plan_digest"])


class WorkspaceRegistryReconciler:
    """Project a freshly observed remote channel into local registry discovery."""

    def __init__(
        self,
        *,
        state_root: Path,
        workspace_root: Path,
        remote: RegistryProjectionRemote,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.remote = remote
        self.subscriptions = SubscriptionStore(
            self.workspace_root / ".adaos" / "subscriptions.json"
        )
        self.writer_lock_path = self.workspace_root / ".adaos" / ".workspace-writer.lock"

    def plan(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        channel: str = "stable",
    ) -> RegistryReconciliationPlan:
        pointer, release_plan = self._observe_remote(project_id, channel=channel)
        return self._build_plan(pointer, release_plan, kind=kind)

    def apply(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        reviewed_plan_digest: str,
        channel: str = "stable",
    ) -> dict[str, Any]:
        reviewed = str(reviewed_plan_digest or "").strip()
        if not reviewed:
            raise RegistryReconciliationError("reviewed_plan_digest is required")
        operation_path = self.operation_path(reviewed)
        operation = self._load_operation(operation_path, expected_plan_digest=reviewed)
        if operation is not None and operation.get("status") == "completed":
            return dict(operation)

        pointer, release_plan = self._observe_remote(project_id, channel=channel)
        with mutation_lock(self.writer_lock_path):
            current = self._build_plan(pointer, release_plan, kind=kind)
            if operation is None:
                if current.plan_digest != reviewed:
                    raise RegistryReconciliationError(
                        "registry reconciliation plan changed after review"
                    )
                if not current.allowed:
                    raise RegistryReconciliationError(
                        "registry reconciliation is blocked: "
                        + ", ".join(current.warnings)
                    )
                if current.action == "noop":
                    return {
                        "schema": REGISTRY_RECONCILIATION_OPERATION_SCHEMA,
                        "status": "noop",
                        "plan_digest": reviewed,
                        "plan": current.to_dict(),
                    }
                operation = {
                    "schema": REGISTRY_RECONCILIATION_OPERATION_SCHEMA,
                    "status": "prepared",
                    "plan_digest": reviewed,
                    "plan": current.to_dict(),
                }
                atomic_write_json(operation_path, operation)
            else:
                self._validate_prepared_retry(operation, current)

            plan_payload = operation.get("plan")
            if not isinstance(plan_payload, Mapping):
                raise RegistryReconciliationError("reconciliation operation has no plan")
            if self._entry_matches_target(current, plan_payload):
                operation["status"] = "completed"
                operation["result"] = {
                    "status": "recovered_after_projection",
                    "registry_entry_digest": current.observed_registry_entry_digest,
                }
                atomic_write_json(operation_path, operation)
                return dict(operation)

            try:
                entry = set_workspace_registry_channel(
                    self.workspace_root,
                    "skills" if kind == "skill" else "scenarios",
                    current.artifact_id,
                    channel=channel,
                    release=current.release,
                    expected_entry_digest=str(
                        plan_payload.get("observed_registry_entry_digest") or ""
                    ),
                )
            except WorkspaceRegistryError as exc:
                raise RegistryReconciliationError(str(exc)) from exc

            after = self._build_plan(pointer, release_plan, kind=kind)
            if after.action != "noop":
                raise RegistryReconciliationError(
                    "registry projection did not reach the reviewed target"
                )
            operation["status"] = "completed"
            operation["result"] = {
                "status": "projected",
                "registry_entry_digest": canonical_payload_digest(entry),
                "verified_plan_digest": after.plan_digest,
            }
            atomic_write_json(operation_path, operation)
            return dict(operation)

    def operation_path(self, plan_digest: str) -> Path:
        token = str(plan_digest or "").strip().lower()
        if not token.startswith("sha256:") or len(token) != 71:
            raise RegistryReconciliationError(
                "plan digest must be sha256:<64 lowercase hex characters>"
            )
        hex_digest = token.split(":", 1)[1]
        if any(char not in "0123456789abcdef" for char in hex_digest):
            raise RegistryReconciliationError(
                "plan digest must be sha256:<64 lowercase hex characters>"
            )
        return self.state_root / "registry-reconciliations" / f"{hex_digest}.json"

    def _observe_remote(
        self,
        project_id: str,
        *,
        channel: str,
    ) -> tuple[ChannelPointer, ReleasePlan]:
        pointer = self.remote.get_channel(project_id, channel)
        if pointer.project_id != project_id or pointer.channel != channel:
            raise RegistryReconciliationError(
                "remote channel pointer does not match requested identity"
            )
        release_plan = self.remote.get_release(project_id, pointer.release_digest)
        release = release_plan.release
        release_digest = release.release_digest or release.computed_digest()
        if release.project_id != project_id or release_digest != pointer.release_digest:
            raise RegistryReconciliationError(
                "remote release does not match the observed channel pointer"
            )
        if pointer.release != f"{project_id}@{release.version}":
            raise RegistryReconciliationError(
                "remote channel release reference does not match immutable release"
            )
        if pointer.source_revision != release.source_ref.revision:
            raise RegistryReconciliationError(
                "remote channel source revision does not match immutable release"
            )
        return pointer, release_plan

    def _build_plan(
        self,
        pointer: ChannelPointer,
        release_plan: ReleasePlan,
        *,
        kind: ArtifactKind,
    ) -> RegistryReconciliationPlan:
        release = release_plan.release
        component = next(
            (
                item
                for item in release.components
                if item.artifact_id == release.project_id and item.kind == kind
            ),
            None,
        )
        if component is None:
            raise RegistryReconciliationError(
                f"remote release has no {kind} component matching project identity"
            )

        registry_kind = "skills" if kind == "skill" else "scenarios"
        entry = find_workspace_registry_entry(
            self.workspace_root,
            kind=registry_kind,
            name_or_id=component.artifact_id,
            fallback_to_scan=False,
        )
        entry_digest = canonical_payload_digest(entry) if entry is not None else None
        warnings: list[str] = []
        allowed = True
        if entry is None:
            allowed = False
            warnings.append("registry_entry_not_present")

        subscriptions = self.subscriptions.load()
        subscription = subscriptions.get(release.project_id)
        if subscription is None:
            allowed = False
            warnings.append("stable_subscription_not_present")

        lock = self._load_workspace_lock()
        lock_payload = lock.to_dict() if lock is not None else None
        lock_digest = str(lock_payload["lock_digest"]) if lock_payload else None
        if lock is None:
            allowed = False
            warnings.append("workspace_lock_not_present")

        if subscription is not None and lock is not None:
            installed_slot = next(
                (
                    item
                    for item in lock.slots
                    if item.project_id == subscription.project_id
                    and item.release == subscription.installed_release
                    and item.release_digest == subscription.installed_digest
                ),
                None,
            )
            if installed_slot is None:
                allowed = False
                warnings.append("subscription_does_not_match_active_workspace_slot")
            if not self._installed_release_is_trusted(subscription, lock):
                allowed = False
                warnings.append("installed_release_record_not_trusted")
            if subscription.installed_digest != pointer.release_digest:
                warnings.append("remote_channel_moved_update_available")

        target = {
            "release": pointer.release,
            "release_digest": pointer.release_digest,
            "source_revision": pointer.source_revision,
            "package_digest": component.digest,
            "version": component.version,
        }
        target_source = {
            "forge": release.source_ref.forge,
            "repository": release.source_ref.repository,
            "revision": release.source_ref.revision,
            "path_scope": list(release.source_ref.path_scope),
        }
        observed_channels = entry.get("channels") if isinstance(entry, Mapping) else None
        observed = (
            observed_channels.get(pointer.channel)
            if isinstance(observed_channels, Mapping)
            else None
        )
        observed_source = entry.get("source") if isinstance(entry, Mapping) else None
        source_matches = isinstance(observed_source, Mapping) and all(
            observed_source.get(key) == value for key, value in target_source.items()
        )
        action = (
            "noop"
            if observed == target and source_matches
            else "project_remote_channel"
        )
        return RegistryReconciliationPlan(
            project_id=release.project_id,
            kind=kind,
            artifact_id=component.artifact_id,
            channel=pointer.channel,
            pointer=pointer,
            release=release,
            component=component,
            subscription=subscription,
            observed_registry_entry_digest=entry_digest,
            observed_workspace_lock_digest=lock_digest,
            target_registry_channel=target,
            target_registry_source=target_source,
            action=action,
            allowed=allowed,
            warnings=tuple(warnings),
        )

    def _load_workspace_lock(self) -> WorkspaceLock | None:
        path = self.workspace_root / ".adaos" / "workspace.lock.json"
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise RegistryReconciliationError("WorkspaceLock must contain an object")
            return WorkspaceLock.from_mapping(payload)
        except RegistryReconciliationError:
            raise
        except (OSError, json.JSONDecodeError, ArtifactReleaseContractError) as exc:
            raise RegistryReconciliationError(f"cannot trust WorkspaceLock: {exc}") from exc

    def _installed_release_is_trusted(
        self,
        subscription: StableSubscription,
        lock: WorkspaceLock,
    ) -> bool:
        digest = str(subscription.installed_digest or "")
        if not digest.startswith("sha256:"):
            return False
        path = self.workspace_root / ".adaos" / "releases" / f"{digest.split(':', 1)[1]}.json"
        if not path.is_file():
            return False
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                return False
            release = ProjectRelease.from_mapping(payload)
        except (OSError, json.JSONDecodeError, ArtifactReleaseContractError):
            return False
        release_digest = release.release_digest or release.computed_digest()
        if (
            release.project_id != subscription.project_id
            or f"{release.project_id}@{release.version}" != subscription.installed_release
            or release_digest != subscription.installed_digest
        ):
            return False
        active = {item.key: item for item in lock.components}
        return all(active.get(item.key) == item for item in release.components)

    def _load_operation(
        self,
        path: Path,
        *,
        expected_plan_digest: str,
    ) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RegistryReconciliationError(
                f"cannot read reconciliation operation: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise RegistryReconciliationError("reconciliation operation must be an object")
        if payload.get("schema") != REGISTRY_RECONCILIATION_OPERATION_SCHEMA:
            raise RegistryReconciliationError("unsupported reconciliation operation")
        if payload.get("plan_digest") != expected_plan_digest:
            raise RegistryReconciliationError("reconciliation operation digest mismatch")
        if payload.get("status") not in {"prepared", "completed"}:
            raise RegistryReconciliationError("unsupported reconciliation operation status")
        return payload

    def _validate_prepared_retry(
        self,
        operation: Mapping[str, Any],
        current: RegistryReconciliationPlan,
    ) -> None:
        plan = operation.get("plan")
        if not isinstance(plan, Mapping):
            raise RegistryReconciliationError("reconciliation operation has no plan")
        if plan.get("pointer") != current.pointer.to_dict():
            raise RegistryReconciliationError(
                "remote channel moved after reconciliation was prepared"
            )
        if plan.get("release_digest") != current.release.release_digest:
            raise RegistryReconciliationError(
                "remote release changed after reconciliation was prepared"
            )
        if not current.allowed:
            raise RegistryReconciliationError(
                "registry reconciliation is now blocked: "
                + ", ".join(current.warnings)
            )
        expected_entry = plan.get("observed_registry_entry_digest")
        if (
            not self._entry_matches_target(current, plan)
            and current.observed_registry_entry_digest != expected_entry
        ):
            raise RegistryReconciliationError(
                "registry entry changed after reconciliation was prepared"
            )
        if current.observed_workspace_lock_digest != plan.get("observed_workspace_lock_digest"):
            raise RegistryReconciliationError(
                "WorkspaceLock changed after reconciliation was prepared"
            )

    @staticmethod
    def _entry_matches_target(
        current: RegistryReconciliationPlan,
        prepared_plan: Mapping[str, Any],
    ) -> bool:
        return (
            current.action == "noop"
            and dict(current.target_registry_channel)
            == prepared_plan.get("target_registry_channel")
            and dict(current.target_registry_source)
            == prepared_plan.get("target_registry_source")
        )


__all__ = [
    "REGISTRY_RECONCILIATION_OPERATION_SCHEMA",
    "REGISTRY_RECONCILIATION_PLAN_SCHEMA",
    "RegistryProjectionRemote",
    "RegistryReconciliationError",
    "RegistryReconciliationPlan",
    "WorkspaceRegistryReconciler",
]
