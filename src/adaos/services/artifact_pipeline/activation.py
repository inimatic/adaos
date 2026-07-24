from __future__ import annotations

import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactReleaseContractError,
    ProjectRelease,
    WorkspaceLock,
    WorkspaceSlot,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, replace_with_retry


ACTIVATION_OPERATION_SCHEMA = "adaos.artifact.activation_operation.v1"
ACTIVATION_PHASES = (
    "resolve",
    "fetch",
    "verify",
    "dependency-plan",
    "permission-plan",
    "migration-plan",
    "stage",
    "checkpoint",
    "switch-lock",
    "reload",
    "health-verify",
    "commit",
)


class ActivationError(RuntimeError):
    pass


class ActivationReplayBlocked(ActivationError):
    pass


@dataclass(frozen=True, slots=True)
class ActivationResult:
    operation_id: str
    status: str
    workspace_lock: WorkspaceLock
    release_digest: str
    idempotent_replay: bool = False


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class WorkspaceActivationManager:
    def __init__(
        self,
        *,
        workspace_root: Path,
        package_store: ContentAddressedPackageStore,
        state_root: Path | None = None,
    ) -> None:
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.package_store = package_store
        self.metadata_root = self.workspace_root / ".adaos"
        self.state_root = Path(state_root or self.metadata_root).expanduser().resolve()
        self.lock_path = self.metadata_root / "workspace.lock.json"
        self.operations_root = self.state_root / "artifact_pipeline" / "operations"
        self.staging_root = self.state_root / "artifact_pipeline" / "staging"
        self.backups_root = self.state_root / "artifact_pipeline" / "backups"
        self.lock_history_root = self.metadata_root / "lock-history"
        self.releases_root = self.metadata_root / "releases"

    def load_lock(self) -> WorkspaceLock | None:
        if not self.lock_path.is_file():
            return None
        try:
            payload = json.loads(self.lock_path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ActivationError(f"cannot read WorkspaceLock: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise ActivationError("WorkspaceLock must contain an object")
        try:
            return WorkspaceLock.from_mapping(payload)
        except ArtifactReleaseContractError as exc:
            raise ActivationError(f"invalid WorkspaceLock: {exc}") from exc

    def operation_path(self, operation_id: str) -> Path:
        token = str(operation_id or "").strip().lower()
        if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
            raise ActivationError("operation id must be 32 lowercase hex characters")
        return self.operations_root / f"{token}.json"

    @staticmethod
    def operation_id(idempotency_key: str) -> str:
        value = str(idempotency_key or "").strip()
        if not value:
            raise ActivationError("idempotency_key must not be empty")
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:32]

    def _read_operation(self, operation_id: str) -> dict[str, Any] | None:
        path = self.operation_path(operation_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise ActivationError(f"cannot read activation operation {operation_id}: {exc}") from exc
        if not isinstance(payload, dict):
            raise ActivationError(f"activation operation {operation_id} must contain an object")
        return payload

    def _write_operation(self, operation: dict[str, Any]) -> None:
        operation["updated_at"] = _now_iso()
        atomic_write_json(self.operation_path(str(operation["operation_id"])), operation)

    def _phase(
        self,
        operation: dict[str, Any],
        phase: str,
        *,
        phase_hook: Callable[[str], None] | None,
        evidence: Mapping[str, Any] | None = None,
    ) -> None:
        operation["phase"] = phase
        event: dict[str, Any] = {"phase": phase, "at": _now_iso()}
        if evidence:
            event["evidence"] = dict(evidence)
        operation.setdefault("events", []).append(event)
        self._write_operation(operation)
        if phase_hook is not None:
            phase_hook(phase)

    def _assert_plan(self, plan: ReleasePlan) -> None:
        release_digest = plan.release.release_digest or plan.release.computed_digest()
        if release_digest != plan.release.computed_digest():
            raise ActivationError("ProjectRelease digest does not match release content")
        packages = {item.key: item for item in plan.packages}
        if len(packages) != len(plan.packages):
            raise ActivationError("activation plan has multiple package versions for one identity")
        for component in plan.release.components:
            if packages.get(component.key) != component:
                raise ActivationError(f"release component {component.key} is missing from activation plan")
        for dependency in plan.release.resolved_dependencies:
            package = packages.get(dependency.key)
            if package is None or package.digest != dependency.package_digest:
                raise ActivationError(
                    f"resolved dependency {dependency.key} is missing from activation plan"
                )

    def _release_path(self, release_digest: str) -> Path:
        token = str(release_digest or "").strip().lower()
        if not token.startswith("sha256:") or len(token) != 71:
            raise ActivationError("release digest must be a SHA-256 digest")
        digest = token.split(":", 1)[1]
        if any(char not in "0123456789abcdef" for char in digest):
            raise ActivationError("release digest must be a lowercase SHA-256 digest")
        return self.releases_root / f"{digest}.json"

    def _load_release(self, release_digest: str) -> ProjectRelease | None:
        path = self._release_path(release_digest)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise ValueError("release record must contain an object")
            release = ProjectRelease.from_mapping(payload)
        except Exception as exc:
            raise ActivationError(f"cannot read active ProjectRelease {release_digest}: {exc}") from exc
        if (release.release_digest or release.computed_digest()) != release_digest:
            raise ActivationError("active ProjectRelease record has a different digest")
        return release

    def _active_release(
        self,
        current: WorkspaceLock | None,
        *,
        slot_id: str,
    ) -> ProjectRelease | None:
        if current is None:
            return None
        slot = next((item for item in current.slots if item.slot_id == slot_id), None)
        return self._load_release(slot.release_digest) if slot is not None else None

    @staticmethod
    def _permission_plan(
        current_release: ProjectRelease | None,
        desired_release: ProjectRelease,
    ) -> dict[str, Any]:
        current = set(current_release.permissions if current_release is not None else ())
        desired = set(desired_release.permissions)
        return {
            "current": sorted(current),
            "desired": sorted(desired),
            "introduced": sorted(desired - current),
            "removed": sorted(current - desired),
            "current_release_known": current_release is not None,
        }

    @staticmethod
    def _migration_plan(release: ProjectRelease) -> dict[str, Any]:
        migrations = [dict(item) for item in release.migrations]
        entries: list[dict[str, Any]] = []
        for index, migration in enumerate(migrations):
            rollback = migration.get("rollback")
            rollback_contract = dict(rollback) if isinstance(rollback, Mapping) else {}
            entries.append(
                {
                    "id": str(migration.get("id") or migration.get("name") or f"migration-{index + 1}"),
                    "from_schema": migration.get("from_schema"),
                    "to_schema": migration.get("to_schema"),
                    "rollback_supported": rollback_contract.get("supported") is True,
                    "rollback_procedure_ref": str(rollback_contract.get("procedure_ref") or "").strip() or None,
                }
            )
        rollback_ready = all(
            item["rollback_supported"] and item["rollback_procedure_ref"]
            for item in entries
        )
        return {
            "count": len(entries),
            "entries": entries,
            "rollback_ready": rollback_ready,
            "policy": "reversible_only",
        }

    def _desired_lock(
        self,
        *,
        current: WorkspaceLock | None,
        plan: ReleasePlan,
        slot_id: str,
        audience: str | None,
    ) -> WorkspaceLock:
        release_digest = plan.release.release_digest or plan.release.computed_digest()
        slots = {item.slot_id: item for item in (current.slots if current else ())}
        slots[slot_id] = WorkspaceSlot(
            slot_id=slot_id,
            project_id=plan.release.project_id,
            release=f"{plan.release.project_id}@{plan.release.version}",
            release_digest=release_digest,
            audience=audience,
        )
        components = {item.key: item for item in (current.components if current else ())}
        for package in plan.packages:
            components[package.key] = package
        plan_consumers = {item.key for item in plan.release.components}
        plan_consumers.update(item.consumer for item in plan.bindings)
        bindings = {
            (item.consumer, item.dependency): item
            for item in (current.bindings if current else ())
            if item.consumer not in plan_consumers
        }
        for binding in plan.bindings:
            bindings[(binding.consumer, binding.dependency)] = binding
        revision = (current.lock_revision if current else 0) + 1
        try:
            return WorkspaceLock(
                lock_revision=revision,
                previous_lock_revision=current.lock_revision if current else None,
                updated_at=_now_iso(),
                slots=tuple(slots.values()),
                components=tuple(components.values()),
                bindings=tuple(bindings.values()),
            )
        except ArtifactReleaseContractError as exc:
            raise ActivationError(f"activation would create an incompatible WorkspaceLock: {exc}") from exc

    def _write_lock(self, lock: WorkspaceLock) -> None:
        atomic_write_json(self.lock_path, lock.to_dict())

    def _target_for(self, package: ArtifactPackageRef) -> Path:
        plural = "skills" if package.kind == "skill" else "scenarios"
        target = (self.workspace_root / plural / package.artifact_id).resolve()
        if self.workspace_root not in target.parents:
            raise ActivationError(f"package target escapes Workspace: {target}")
        return target

    def _rollback(self, operation: dict[str, Any]) -> None:
        for raw in reversed(operation.get("moves") or []):
            if not isinstance(raw, Mapping):
                continue
            target = Path(str(raw.get("target") or "")).resolve()
            backup = Path(str(raw.get("backup") or "")).resolve()
            had_target = raw.get("had_target") is True
            if self.workspace_root not in target.parents:
                raise ActivationError(f"refusing rollback outside Workspace: {target}")
            if backup.exists():
                if target.exists():
                    shutil.rmtree(target)
                backup.parent.mkdir(parents=True, exist_ok=True)
                replace_with_retry(backup, target)
            elif not had_target and target.exists():
                shutil.rmtree(target)
        previous = operation.get("previous_lock")
        if isinstance(previous, Mapping):
            atomic_write_json(self.lock_path, previous)
        else:
            self.lock_path.unlink(missing_ok=True)
        stage = self.staging_root / str(operation["operation_id"])
        backup_root = self.backups_root / str(operation["operation_id"])
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(backup_root, ignore_errors=True)
        operation["rolled_back"] = True

    def activate(
        self,
        plan: ReleasePlan,
        *,
        idempotency_key: str,
        slot_id: str = "primary",
        audience: str | None = None,
        fetch_package: Callable[[ArtifactPackageRef], bytes] | None = None,
        reload_runtime: Callable[[WorkspaceLock], None] | None = None,
        health_check: Callable[[WorkspaceLock], bool] | None = None,
        permission_decision: (
            bool
            | Mapping[str, Any]
            | Callable[[Mapping[str, Any]], bool | Mapping[str, Any]]
            | None
        ) = None,
        migration_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        migration_rollback: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        phase_hook: Callable[[str], None] | None = None,
    ) -> ActivationResult:
        operation_id = self.operation_id(idempotency_key)
        release_digest = plan.release.release_digest or plan.release.computed_digest()
        existing = self._read_operation(operation_id)
        if existing is not None:
            if existing.get("release_digest") != release_digest:
                raise ActivationReplayBlocked(
                    "idempotency key is already bound to a different ProjectRelease"
                )
            if existing.get("status") == "completed":
                lock = self.load_lock()
                if lock is None or existing.get("lock_digest") != lock.to_dict()["lock_digest"]:
                    raise ActivationReplayBlocked(
                        "completed activation no longer matches the active WorkspaceLock"
                    )
                return ActivationResult(
                    operation_id=operation_id,
                    status="completed",
                    workspace_lock=lock,
                    release_digest=release_digest,
                    idempotent_replay=True,
                )
            raise ActivationReplayBlocked(
                f"activation {operation_id} is {existing.get('status')}; "
                "recover it or use an explicitly new idempotency key"
            )

        current = self.load_lock()
        operation: dict[str, Any] = {
            "schema": ACTIVATION_OPERATION_SCHEMA,
            "operation_id": operation_id,
            "idempotency_key": str(idempotency_key),
            "release_digest": release_digest,
            "status": "running",
            "phase": "created",
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
            "events": [],
            "previous_lock": current.to_dict() if current else None,
            "moves": [],
        }
        self._write_operation(operation)
        stage_root = self.staging_root / operation_id
        backup_root = self.backups_root / operation_id
        desired: WorkspaceLock | None = None
        try:
            self._phase(operation, "resolve", phase_hook=phase_hook)
            self._assert_plan(plan)

            self._phase(operation, "fetch", phase_hook=phase_hook)
            for package in plan.packages:
                if self.package_store.has(package.digest):
                    continue
                if fetch_package is None:
                    raise ActivationError(f"package is not present in local store: {package.digest}")
                self.package_store.put(
                    fetch_package(package),
                    expected_digest=package.digest,
                )

            self._phase(operation, "verify", phase_hook=phase_hook)
            for package in plan.packages:
                verified = self.package_store.verify(package.digest)
                if verified.ref != package:
                    raise ActivationError(
                        f"stored package reference differs from release: {package.key}"
                    )

            desired = self._desired_lock(
                current=current,
                plan=plan,
                slot_id=slot_id,
                audience=audience,
            )
            operation["desired_lock"] = desired.to_dict()
            self._phase(
                operation,
                "dependency-plan",
                phase_hook=phase_hook,
                evidence={"lock_digest": desired.to_dict()["lock_digest"]},
            )

            current_release = self._active_release(current, slot_id=slot_id)
            permission_plan = self._permission_plan(current_release, plan.release)
            operation["permission_plan"] = permission_plan
            self._phase(
                operation,
                "permission-plan",
                phase_hook=phase_hook,
                evidence=permission_plan,
            )
            introduced_permissions = list(permission_plan["introduced"])
            if introduced_permissions:
                if permission_decision is None:
                    operation["permission_decision"] = {
                        "approved": False,
                        "reason": "explicit_permission_approval_required",
                    }
                    self._write_operation(operation)
                    raise ActivationError(
                        "activation introduces permissions but has no explicit permission decision"
                    )
                raw_decision = (
                    permission_decision(dict(permission_plan))
                    if callable(permission_decision)
                    else permission_decision
                )
                if isinstance(raw_decision, Mapping):
                    decision = dict(raw_decision)
                    approved = decision.get("approved") is True or decision.get("allowed") is True
                else:
                    approved = raw_decision is True
                    decision = {"approved": approved}
                decision["approved"] = approved
                operation["permission_decision"] = decision
                self._write_operation(operation)
                if not approved:
                    raise ActivationError("permission plan was not approved")
            else:
                operation["permission_decision"] = {
                    "approved": True,
                    "reason": "no_introduced_permissions",
                }
                self._write_operation(operation)

            migration_plan = self._migration_plan(plan.release)
            operation["migration_plan"] = migration_plan
            self._phase(
                operation,
                "migration-plan",
                phase_hook=phase_hook,
                evidence=migration_plan,
            )
            if migration_plan["count"]:
                if not migration_plan["rollback_ready"]:
                    raise ActivationError(
                        "irreversible or unspecified migrations require a deferred attended workflow"
                    )
                if migration_executor is None or migration_rollback is None:
                    raise ActivationError(
                        "reversible migrations require one-shot executor and rollback handlers"
                    )

            self._phase(operation, "stage", phase_hook=phase_hook)
            stage_root.mkdir(parents=True, exist_ok=False)
            staged: dict[str, Path] = {}
            for package in plan.packages:
                path = stage_root / package.kind / package.artifact_id
                self.package_store.extract_to_directory(package.digest, path)
                staged[package.key] = path

            moves: list[dict[str, Any]] = []
            for package in plan.packages:
                target = self._target_for(package)
                backup = backup_root / package.kind / package.artifact_id
                moves.append(
                    {
                        "package": package.key,
                        "target": str(target),
                        "staged": str(staged[package.key]),
                        "backup": str(backup),
                        "had_target": target.exists(),
                    }
                )
            operation["moves"] = moves
            self._phase(
                operation,
                "checkpoint",
                phase_hook=phase_hook,
                evidence={
                    "release_digest": release_digest,
                    "migration_count": migration_plan["count"],
                },
            )
            atomic_write_json(self._release_path(release_digest), plan.release.to_dict())
            if migration_plan["count"]:
                operation["migration_execution"] = {
                    "status": "dispatching",
                    "started_at": _now_iso(),
                }
                self._write_operation(operation)
                migration_request = {
                    "operation_id": operation_id,
                    "release_digest": release_digest,
                    "migrations": [dict(item) for item in plan.release.migrations],
                    "previous_lock": current.to_dict() if current else None,
                    "desired_lock": desired.to_dict(),
                }
                try:
                    migration_receipt = migration_executor(migration_request)  # type: ignore[misc]
                except Exception:
                    operation["migration_execution"] = {
                        "status": "uncertain",
                        "started_at": operation["migration_execution"]["started_at"],
                        "failed_at": _now_iso(),
                        "reason": "executor_raised_before_a_durable_receipt",
                    }
                    self._write_operation(operation)
                    raise
                if not isinstance(migration_receipt, Mapping):
                    operation["migration_execution"] = {
                        "status": "uncertain",
                        "started_at": operation["migration_execution"]["started_at"],
                        "failed_at": _now_iso(),
                        "reason": "executor_returned_no_receipt",
                    }
                    self._write_operation(operation)
                    raise ActivationError("migration executor returned no durable receipt")
                receipt = dict(migration_receipt)
                if str(receipt.get("status") or "").strip().lower() != "completed":
                    operation["migration_execution"] = {
                        "status": "uncertain",
                        "receipt": receipt,
                        "failed_at": _now_iso(),
                        "reason": "executor_receipt_not_completed",
                    }
                    self._write_operation(operation)
                    raise ActivationError("migration executor did not confirm completion")
                if not receipt.get("checkpoint"):
                    operation["migration_execution"] = {
                        "status": "uncertain",
                        "receipt": receipt,
                        "failed_at": _now_iso(),
                        "reason": "executor_receipt_has_no_checkpoint",
                    }
                    self._write_operation(operation)
                    raise ActivationError("migration executor receipt has no checkpoint identity")
                operation["migration_execution"] = {
                    "status": "completed",
                    "receipt": receipt,
                    "completed_at": _now_iso(),
                }
                self._write_operation(operation)
            else:
                operation["migration_execution"] = {
                    "status": "not_required",
                    "completed_at": _now_iso(),
                }
                self._write_operation(operation)

            self._phase(operation, "switch-lock", phase_hook=phase_hook)
            for move in moves:
                target = Path(move["target"])
                staged_path = Path(move["staged"])
                backup = Path(move["backup"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    replace_with_retry(target, backup)
                replace_with_retry(staged_path, target)
            self._write_lock(desired)

            self._phase(operation, "reload", phase_hook=phase_hook)
            if reload_runtime is not None:
                reload_runtime(desired)

            self._phase(operation, "health-verify", phase_hook=phase_hook)
            if health_check is not None and health_check(desired) is not True:
                raise ActivationError("post-activation health check failed")

            self._phase(
                operation,
                "commit",
                phase_hook=phase_hook,
                evidence={"lock_digest": desired.to_dict()["lock_digest"]},
            )
            history = self.lock_history_root / (
                f"{desired.lock_revision:08d}-{desired.to_dict()['lock_digest'].split(':', 1)[1]}.json"
            )
            atomic_write_json(history, desired.to_dict())
            shutil.rmtree(stage_root, ignore_errors=True)
            shutil.rmtree(backup_root, ignore_errors=True)
            operation["status"] = "completed"
            operation["lock_digest"] = desired.to_dict()["lock_digest"]
            operation["completed_at"] = _now_iso()
            self._write_operation(operation)
            return ActivationResult(
                operation_id=operation_id,
                status="completed",
                workspace_lock=desired,
                release_digest=release_digest,
            )
        except Exception as exc:
            migration_state = operation.get("migration_execution")
            if isinstance(migration_state, Mapping) and migration_state.get("status") == "completed":
                rollback_request = {
                    "operation_id": operation_id,
                    "release_digest": release_digest,
                    "migrations": [dict(item) for item in plan.release.migrations],
                    "receipt": dict(migration_state.get("receipt") or {}),
                    "previous_lock": current.to_dict() if current else None,
                    "desired_lock": desired.to_dict() if desired else None,
                }
                operation["migration_rollback"] = {
                    "status": "dispatching",
                    "started_at": _now_iso(),
                }
                self._write_operation(operation)
                try:
                    rollback_receipt = migration_rollback(rollback_request)  # type: ignore[misc]
                    if not isinstance(rollback_receipt, Mapping) or str(
                        rollback_receipt.get("status") or ""
                    ).strip().lower() not in {"completed", "rolled_back"}:
                        raise ActivationError("migration rollback returned no durable completion receipt")
                    operation["migration_rollback"] = {
                        "status": "completed",
                        "receipt": dict(rollback_receipt),
                        "completed_at": _now_iso(),
                    }
                except Exception as rollback_exc:
                    operation["migration_rollback"] = {
                        "status": "uncertain",
                        "error": f"{type(rollback_exc).__name__}: {rollback_exc}",
                        "failed_at": _now_iso(),
                    }
                    operation["rollback_error"] = operation["migration_rollback"]["error"]
            try:
                self._rollback(operation)
            except Exception as rollback_exc:
                operation["rollback_error"] = f"{type(rollback_exc).__name__}: {rollback_exc}"
            if reload_runtime is not None and current is not None:
                try:
                    reload_runtime(current)
                    operation["runtime_rollback"] = {
                        "status": "completed",
                        "lock_digest": current.to_dict()["lock_digest"],
                    }
                except Exception as rollback_exc:
                    operation["runtime_rollback"] = {
                        "status": "failed",
                        "error": f"{type(rollback_exc).__name__}: {rollback_exc}",
                    }
                    operation["rollback_error"] = operation["runtime_rollback"]["error"]
            operation["status"] = "failed"
            operation["error"] = f"{type(exc).__name__}: {exc}"
            operation["failed_at"] = _now_iso()
            self._write_operation(operation)
            if isinstance(exc, ActivationError):
                raise
            raise ActivationError(str(exc)) from exc

    def recover_interrupted(
        self,
        operation_id: str,
        *,
        migration_reconciler: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        migration_rollback: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
    ) -> dict[str, Any]:
        operation = self._read_operation(operation_id)
        if operation is None:
            raise FileNotFoundError(f"activation operation not found: {operation_id}")
        if operation.get("status") == "completed":
            raise ActivationReplayBlocked("completed activation does not need recovery")
        migration_state = operation.get("migration_execution")
        migration_status = (
            str(migration_state.get("status") or "").strip().lower()
            if isinstance(migration_state, Mapping)
            else ""
        )
        migration_rollback_state = operation.get("migration_rollback")
        migration_rollback_status = (
            str(migration_rollback_state.get("status") or "").strip().lower()
            if isinstance(migration_rollback_state, Mapping)
            else ""
        )
        if migration_status in {"dispatching", "uncertain"}:
            if migration_reconciler is None:
                raise ActivationReplayBlocked(
                    "migration outcome is unknown; explicit one-shot reconciliation is required"
                )
            operation["migration_reconciliation"] = {
                "status": "dispatching",
                "started_at": _now_iso(),
            }
            self._write_operation(operation)
            receipt = migration_reconciler(dict(operation))
            if not isinstance(receipt, Mapping) or str(receipt.get("status") or "").strip().lower() not in {
                "not_applied",
                "rolled_back",
            }:
                operation["migration_reconciliation"] = {
                    "status": "uncertain",
                    "receipt": dict(receipt) if isinstance(receipt, Mapping) else None,
                    "failed_at": _now_iso(),
                }
                self._write_operation(operation)
                raise ActivationReplayBlocked(
                    "migration reconciliation did not prove a safe rolled-back state"
                )
            operation["migration_reconciliation"] = {
                "status": "completed",
                "receipt": dict(receipt),
                "completed_at": _now_iso(),
            }
            self._write_operation(operation)
        elif migration_status == "completed" and migration_rollback_status != "completed":
            if migration_rollback is None:
                raise ActivationReplayBlocked(
                    "completed migration requires explicit rollback before operation recovery"
                )
            operation["migration_rollback"] = {
                "status": "dispatching",
                "started_at": _now_iso(),
            }
            self._write_operation(operation)
            receipt = migration_rollback(dict(operation))
            if not isinstance(receipt, Mapping) or str(receipt.get("status") or "").strip().lower() not in {
                "completed",
                "rolled_back",
            }:
                operation["migration_rollback"] = {
                    "status": "uncertain",
                    "receipt": dict(receipt) if isinstance(receipt, Mapping) else None,
                    "failed_at": _now_iso(),
                }
                self._write_operation(operation)
                raise ActivationReplayBlocked("migration rollback was not durably confirmed")
            operation["migration_rollback"] = {
                "status": "completed",
                "receipt": dict(receipt),
                "completed_at": _now_iso(),
            }
            self._write_operation(operation)
        self._rollback(operation)
        operation["status"] = "recovered"
        operation["recovered_at"] = _now_iso()
        self._write_operation(operation)
        return operation


__all__ = [
    "ACTIVATION_OPERATION_SCHEMA",
    "ACTIVATION_PHASES",
    "ActivationError",
    "ActivationReplayBlocked",
    "ActivationResult",
    "WorkspaceActivationManager",
]
