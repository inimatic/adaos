from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from adaos.domain.artifact_release import ArtifactPackageRef
from adaos.domain.project_deployment import (
    ComponentActivation,
    DeploymentPlanChange,
    NodeInventoryRecord,
    ProjectDeployment,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .execution import (
    ProjectDeploymentExecutionError,
    UncertainDeploymentPhaseError,
)


class ComponentLifecycleHooks(Protocol):
    def activate(
        self, *, kind: str, component_id: str, version: str
    ) -> Mapping[str, Any]: ...

    def health(
        self, *, kind: str, component_id: str, version: str
    ) -> Mapping[str, Any]: ...

    def cordon(self, *, kind: str, component_id: str) -> Mapping[str, Any]: ...

    def drain(self, *, kind: str, component_id: str) -> Mapping[str, Any]: ...

    def deactivate(self, *, kind: str, component_id: str) -> Mapping[str, Any]: ...


class NodeDeploymentTransport(Protocol):
    def execute_component_phase(
        self,
        *,
        node_id: str,
        phase: str,
        change: DeploymentPlanChange,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        package: ArtifactPackageRef | None,
        current_activation: ComponentActivation | None,
        idempotency_key: str,
        attempt: int,
    ) -> Mapping[str, Any]: ...


class NoopComponentLifecycleHooks:
    """Materialization-only hooks for tests and runtimes without live reload."""

    def activate(
        self, *, kind: str, component_id: str, version: str
    ) -> Mapping[str, Any]:
        return {
            "activated": True,
            "kind": kind,
            "component_id": component_id,
            "version": version,
        }

    def health(
        self, *, kind: str, component_id: str, version: str
    ) -> Mapping[str, Any]:
        return {
            "ready": True,
            "kind": kind,
            "component_id": component_id,
            "version": version,
        }

    def cordon(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return {"cordoned": True, "kind": kind, "component_id": component_id}

    def drain(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return {"drained": True, "kind": kind, "component_id": component_id}

    def deactivate(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return {"deactivated": True, "kind": kind, "component_id": component_id}


@dataclass(slots=True)
class CallbackComponentLifecycleHooks:
    activate_callback: Callable[[str, str, str], Mapping[str, Any]]
    health_callback: Callable[[str, str, str], Mapping[str, Any]]
    cordon_callback: Callable[[str, str], Mapping[str, Any]]
    drain_callback: Callable[[str, str], Mapping[str, Any]]
    deactivate_callback: Callable[[str, str], Mapping[str, Any]]

    def activate(
        self, *, kind: str, component_id: str, version: str
    ) -> Mapping[str, Any]:
        return self.activate_callback(kind, component_id, version)

    def health(
        self, *, kind: str, component_id: str, version: str
    ) -> Mapping[str, Any]:
        return self.health_callback(kind, component_id, version)

    def cordon(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return self.cordon_callback(kind, component_id)

    def drain(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return self.drain_callback(kind, component_id)

    def deactivate(self, *, kind: str, component_id: str) -> Mapping[str, Any]:
        return self.deactivate_callback(kind, component_id)


def _operation_token(idempotency_key: str) -> str:
    value = str(idempotency_key or "").rsplit(":", 1)[0]
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ProjectDeploymentExecutionError(
            "component deployment state is unreadable"
        ) from exc
    if not isinstance(payload, Mapping):
        raise ProjectDeploymentExecutionError("component deployment state is invalid")
    return dict(payload)


@dataclass(slots=True)
class LocalComponentDeploymentAdapter:
    local_node_id: str
    workspace_root: Path
    state_root: Path
    package_store: ContentAddressedPackageStore
    fetch_package: Callable[[ArtifactPackageRef], bytes]
    hooks: ComponentLifecycleHooks

    def __post_init__(self) -> None:
        self.workspace_root = Path(self.workspace_root).expanduser().resolve()
        self.state_root = Path(self.state_root).expanduser().resolve()

    @property
    def operations_root(self) -> Path:
        return self.state_root / "project_deployments" / "component_operations"

    @property
    def lock_path(self) -> Path:
        return self.operations_root / ".mutation.lock"

    def execute_phase(
        self,
        *,
        phase: str,
        node: NodeInventoryRecord,
        change: DeploymentPlanChange,
        desired: ProjectDeployment,
        release_plan: ReleasePlan,
        package: ArtifactPackageRef | None,
        current_activation: ComponentActivation | None,
        idempotency_key: str,
        attempt: int,
    ) -> Mapping[str, Any]:
        del desired, release_plan, current_activation, attempt
        if node.node_id != self.local_node_id:
            raise ProjectDeploymentExecutionError(
                "local adapter received a remote node"
            )
        token = _operation_token(idempotency_key)
        root = (self.operations_root / token).resolve()
        if self.operations_root.resolve() not in root.parents:
            raise ProjectDeploymentExecutionError(
                "component operation path escaped state root"
            )
        root.mkdir(parents=True, exist_ok=True)
        state_path = root / "state.json"
        with mutation_lock(self.lock_path, timeout_s=60.0):
            state = _read(state_path)
            self._validate_state(state, change=change, package=package)
            handler = getattr(self, f"_phase_{phase.replace('-', '_')}", None)
            if not callable(handler):
                raise ProjectDeploymentExecutionError(
                    f"unsupported component phase: {phase}"
                )
            receipt = dict(
                handler(root=root, state=state, change=change, package=package)
            )
            phases = dict(state.get("phases") or {})
            phases[phase] = receipt
            state.update(
                {
                    "schema": "adaos.project.local_component_operation.v1",
                    "node_id": self.local_node_id,
                    "component_ref": change.component_ref,
                    "action": change.action,
                    "package_digest": None if package is None else package.digest,
                    "phases": phases,
                }
            )
            atomic_write_json(state_path, state)
            return receipt

    def _validate_state(
        self,
        state: Mapping[str, Any],
        *,
        change: DeploymentPlanChange,
        package: ArtifactPackageRef | None,
    ) -> None:
        if not state:
            return
        expected = {
            "node_id": self.local_node_id,
            "component_ref": change.component_ref,
            "action": change.action,
            "package_digest": None if package is None else package.digest,
        }
        if any(state.get(key) != value for key, value in expected.items()):
            raise ProjectDeploymentExecutionError(
                "component operation identity changed"
            )

    def _target(
        self, change: DeploymentPlanChange, package: ArtifactPackageRef | None
    ) -> Path:
        kind, component_id = change.component_ref.split(":", 1)
        relative = (
            package.materialization_path
            if package is not None and package.materialization_path
            else f"{kind}s/{component_id}"
        )
        target = (self.workspace_root / relative).resolve()
        if self.workspace_root != target and self.workspace_root not in target.parents:
            raise ProjectDeploymentExecutionError("component target escaped workspace")
        allowed_parent = (self.workspace_root / f"{kind}s").resolve()
        if target.parent != allowed_parent:
            raise ProjectDeploymentExecutionError("component target is not canonical")
        return target

    @staticmethod
    def _require_package(package: ArtifactPackageRef | None) -> ArtifactPackageRef:
        if package is None:
            raise ProjectDeploymentExecutionError("component package is required")
        return package

    def _phase_fetch(self, **kwargs: Any) -> Mapping[str, Any]:
        package = self._require_package(kwargs["package"])
        if not self.package_store.has(package.digest):
            verified = self.package_store.put(
                self.fetch_package(package), expected_digest=package.digest
            )
        else:
            verified = self.package_store.verify(package.digest)
        if verified.ref != package:
            raise ProjectDeploymentExecutionError("fetched package identity mismatch")
        return {"package_digest": package.digest, "cached": True}

    def _phase_observe(self, **kwargs: Any) -> Mapping[str, Any]:
        change: DeploymentPlanChange = kwargs["change"]
        package = self._require_package(kwargs["package"])
        verified = self.package_store.verify(package.digest)
        if verified.ref != package:
            raise ProjectDeploymentExecutionError("observed package identity mismatch")
        target = self._target(change, package)
        if not target.is_dir():
            raise ProjectDeploymentExecutionError(
                "observed component materialization is missing"
            )
        kind, component_id = change.component_ref.split(":", 1)
        health = dict(
            self.hooks.health(
                kind=kind,
                component_id=component_id,
                version=package.version,
            )
        )
        if health.get("ready") is not True:
            raise ProjectDeploymentExecutionError(
                "observed component health did not report ready"
            )
        return {
            "observed": True,
            "package_digest": package.digest,
            "health": health,
        }

    def _phase_verify(self, **kwargs: Any) -> Mapping[str, Any]:
        package = self._require_package(kwargs["package"])
        verified = self.package_store.verify(package.digest)
        if verified.ref != package:
            raise ProjectDeploymentExecutionError("verified package identity mismatch")
        return {
            "package_digest": package.digest,
            "manifest_digest": package.manifest_digest,
            "files": len(verified.file_names),
            "bytes": verified.uncompressed_bytes,
        }

    def _phase_stage(self, **kwargs: Any) -> Mapping[str, Any]:
        root: Path = kwargs["root"]
        package = self._require_package(kwargs["package"])
        staged = root / "staged"
        if staged.exists():
            shutil.rmtree(staged)
        verified = self.package_store.extract_to_directory(package.digest, staged)
        return {
            "package_digest": verified.ref.digest,
            "staged_ref": f"component-operation:{root.name}:staged",
        }

    def _phase_activate(self, **kwargs: Any) -> Mapping[str, Any]:
        root: Path = kwargs["root"]
        state: Mapping[str, Any] = kwargs["state"]
        change: DeploymentPlanChange = kwargs["change"]
        package = self._require_package(kwargs["package"])
        prior = dict(state.get("phases") or {}).get("activate")
        target = self._target(change, package)
        if isinstance(prior, Mapping) and target.is_dir():
            return dict(prior)
        staged = root / "staged"
        if not staged.is_dir():
            raise ProjectDeploymentExecutionError(
                "component staging directory is missing"
            )
        backup = root / "backup"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if backup.exists():
                raise ProjectDeploymentExecutionError("component backup already exists")
            target.replace(backup)
        try:
            staged.replace(target)
        except Exception:
            if backup.exists() and not target.exists():
                backup.replace(target)
            raise
        kind, component_id = change.component_ref.split(":", 1)
        hook = dict(
            self.hooks.activate(
                kind=kind, component_id=component_id, version=package.version
            )
        )
        return {
            "target_ref": package.materialization_path,
            "package_digest": package.digest,
            "hook": hook,
        }

    def _phase_health(self, **kwargs: Any) -> Mapping[str, Any]:
        change: DeploymentPlanChange = kwargs["change"]
        package = self._require_package(kwargs["package"])
        kind, component_id = change.component_ref.split(":", 1)
        receipt = dict(
            self.hooks.health(
                kind=kind, component_id=component_id, version=package.version
            )
        )
        if receipt.get("ready") is not True:
            raise ProjectDeploymentExecutionError(
                "component health did not report ready"
            )
        return receipt

    def _phase_commit(self, **kwargs: Any) -> Mapping[str, Any]:
        root: Path = kwargs["root"]
        package = self._require_package(kwargs["package"])
        backup = root / "backup"
        if backup.exists():
            shutil.rmtree(backup)
        return {"committed": True, "package_digest": package.digest}

    def _phase_rollback(self, **kwargs: Any) -> Mapping[str, Any]:
        root: Path = kwargs["root"]
        change: DeploymentPlanChange = kwargs["change"]
        package: ArtifactPackageRef | None = kwargs["package"]
        target = self._target(change, package)
        backup = root / "backup"
        if target.exists():
            failed = root / "failed"
            if failed.exists():
                shutil.rmtree(failed)
            target.replace(failed)
        if backup.exists():
            backup.replace(target)
            restored = True
        else:
            restored = False
        return {"rolled_back": True, "previous_restored": restored}

    def _phase_cordon(self, **kwargs: Any) -> Mapping[str, Any]:
        kind, component_id = kwargs["change"].component_ref.split(":", 1)
        return dict(self.hooks.cordon(kind=kind, component_id=component_id))

    def _phase_drain(self, **kwargs: Any) -> Mapping[str, Any]:
        kind, component_id = kwargs["change"].component_ref.split(":", 1)
        return dict(self.hooks.drain(kind=kind, component_id=component_id))

    def _phase_deactivate(self, **kwargs: Any) -> Mapping[str, Any]:
        kind, component_id = kwargs["change"].component_ref.split(":", 1)
        return dict(self.hooks.deactivate(kind=kind, component_id=component_id))

    def _phase_remove(self, **kwargs: Any) -> Mapping[str, Any]:
        root: Path = kwargs["root"]
        change: DeploymentPlanChange = kwargs["change"]
        package: ArtifactPackageRef | None = kwargs["package"]
        target = self._target(change, package)
        removed = False
        if target.exists():
            tombstone = root / "removed"
            if tombstone.exists():
                shutil.rmtree(tombstone)
            target.replace(tombstone)
            shutil.rmtree(tombstone)
            removed = True
        return {"removed": removed, "external_data": "retained"}


@dataclass(slots=True)
class RoutingComponentDeploymentAdapter:
    local_node_id: str
    local: LocalComponentDeploymentAdapter
    remote: NodeDeploymentTransport

    def execute_phase(self, **kwargs: Any) -> Mapping[str, Any]:
        node: NodeInventoryRecord = kwargs["node"]
        if node.node_id == self.local_node_id:
            return self.local.execute_phase(**kwargs)
        try:
            return self.remote.execute_component_phase(node_id=node.node_id, **kwargs)
        except UncertainDeploymentPhaseError:
            raise
        except TimeoutError as exc:
            raise UncertainDeploymentPhaseError(
                "remote component phase timed out after dispatch",
                details={"node_id": node.node_id, "phase": kwargs.get("phase")},
            ) from exc


__all__ = [
    "CallbackComponentLifecycleHooks",
    "ComponentLifecycleHooks",
    "LocalComponentDeploymentAdapter",
    "NodeDeploymentTransport",
    "NoopComponentLifecycleHooks",
    "RoutingComponentDeploymentAdapter",
]
