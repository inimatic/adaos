from __future__ import annotations

import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Callable, Mapping

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactReleaseContractError,
    ProjectRelease,
    WorkspaceLock,
    WorkspaceSlot,
    canonical_payload_digest,
)
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore,
    PackageVerificationError,
)
from adaos.services.artifact_pipeline.attestations import (
    ArtifactAttestationAdmission,
    ArtifactAttestationVerificationError,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import (
    MutationLockTimeout,
    atomic_write_json,
    mutation_lock,
    replace_with_retry,
)
from adaos.services.workflow_admission import (
    WorkflowAdmissionError,
    workflow_admission_record,
)
from adaos.services.governed_workflow import validate_workflow_record


ACTIVATION_OPERATION_SCHEMA = "adaos.artifact.activation_operation.v1"
DELAYED_VERIFICATION_SCHEMA = "adaos.artifact.delayed_verification.v1"
LOCK_HISTORY_STATUS_SCHEMA = "adaos.artifact.lock_history_status.v1"
WORKFLOW_PUBLICATION_ADMISSION_SCHEMA = "adaos.workflow.publication_admission.v1"
ACTIVATION_PHASES = (
    "resolve",
    "fetch",
    "verify",
    "workflow-bind",
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
_CAPTURE_CURRENT_LOCK = object()


class ActivationError(RuntimeError):
    pass


class ActivationReplayBlocked(ActivationError):
    pass


class ActivationConflictError(ActivationError):
    pass


@dataclass(frozen=True, slots=True)
class ActivationResult:
    operation_id: str
    status: str
    workspace_lock: WorkspaceLock
    release_digest: str
    idempotent_replay: bool = False
    delayed_verification_id: str | None = None


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _parse_iso(value: Any) -> datetime:
    token = str(value or "").strip()
    if not token:
        raise ActivationError("timestamp is required")
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ActivationError(f"invalid timestamp: {token}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _observation_delay_seconds(value: float | None) -> float:
    raw: Any = value
    if raw is None:
        raw = os.getenv("ADAOS_ARTIFACT_OBSERVATION_DELAY_SEC", "30")
    try:
        seconds = float(raw)
    except (TypeError, ValueError) as exc:
        raise ActivationError("delayed verification interval must be numeric") from exc
    if seconds < 0 or seconds > 86_400:
        raise ActivationError("delayed verification interval must be between 0 and 86400 seconds")
    return seconds


class WorkspaceActivationManager:
    def __init__(
        self,
        *,
        workspace_root: Path,
        package_store: ContentAddressedPackageStore,
        state_root: Path | None = None,
        delayed_verification_seconds: float | None = None,
        attestation_admission: ArtifactAttestationAdmission | None = None,
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
        self.writer_lock_path = self.metadata_root / ".workspace-writer.lock"
        self.observation_lock_path = self.state_root / "artifact_pipeline" / ".delayed-verification.lock"
        self.pending_observations_root = (
            self.state_root / "artifact_pipeline" / "pending-observations"
        )
        self.delayed_verification_seconds = _observation_delay_seconds(
            delayed_verification_seconds
        )
        self.attestation_admission = attestation_admission

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

    def _lock_history_paths(self, history_id: str) -> tuple[Path, Path]:
        token = str(history_id or "").strip().lower()
        valid = (
            len(token) == 73
            and token[8] == "-"
            and token[:8].isdigit()
            and all(char in "0123456789abcdef" for char in token[9:])
        )
        if not valid:
            raise ActivationError("lock history id is invalid")
        return (
            self.lock_history_root / f"{token}.json",
            self.lock_history_root / f"{token}.status",
        )

    def _set_lock_history_status(
        self,
        operation: dict[str, Any],
        status: str,
        *,
        reason: str | None = None,
    ) -> None:
        raw = operation.get("lock_history")
        if not isinstance(raw, Mapping):
            return
        normalized = str(status or "").strip().lower()
        if normalized not in {"pending", "active", "rolled_back"}:
            raise ActivationError("lock history status is invalid")
        history_id = str(raw.get("history_id") or "")
        history_path, status_path = self._lock_history_paths(history_id)
        if normalized == "active" and not history_path.is_file():
            raise ActivationError("active lock history has no WorkspaceLock record")
        payload: dict[str, Any] = {
            "schema": LOCK_HISTORY_STATUS_SCHEMA,
            "history_id": history_id,
            "operation_id": str(operation.get("operation_id") or ""),
            "lock_revision": int(raw.get("lock_revision") or 0),
            "lock_digest": str(raw.get("lock_digest") or ""),
            "status": normalized,
            "updated_at": _now_iso(),
        }
        if reason:
            payload["reason"] = str(reason)
        atomic_write_json(status_path, payload)
        updated = dict(raw)
        updated["status"] = normalized
        updated["status_record"] = status_path.name
        updated["updated_at"] = payload["updated_at"]
        if reason:
            updated["reason"] = str(reason)
        operation["lock_history"] = updated

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

    def _schedule_delayed_verification(
        self,
        operation: dict[str, Any],
        workspace_lock: WorkspaceLock,
    ) -> str:
        lock_payload = workspace_lock.to_dict()
        lock_digest = str(lock_payload["lock_digest"])
        observation_id = hashlib.sha256(
            f"{operation['operation_id']}:{lock_digest}".encode("utf-8")
        ).hexdigest()[:32]
        scheduled = datetime.now(timezone.utc).replace(microsecond=0)
        operation["delayed_verification"] = {
            "schema": DELAYED_VERIFICATION_SCHEMA,
            "observation_id": observation_id,
            "status": "pending",
            "scheduled_at": scheduled.isoformat(),
            "due_at": (
                scheduled + timedelta(seconds=self.delayed_verification_seconds)
            ).isoformat(),
            "expected_lock_digest": lock_digest,
            "expected_lock_revision": workspace_lock.lock_revision,
            "release_digest": operation.get("release_digest"),
            "checks": [
                "workspace_lock_identity",
                "package_store_integrity",
                "materialized_component_content",
            ],
            "attempts": 0,
        }
        atomic_write_json(
            self._pending_observation_path(observation_id),
            {
                "schema": DELAYED_VERIFICATION_SCHEMA,
                "observation_id": observation_id,
                "operation_id": operation["operation_id"],
                "due_at": operation["delayed_verification"]["due_at"],
            },
        )
        return observation_id

    def _pending_observation_path(self, observation_id: str) -> Path:
        token = str(observation_id or "").strip().lower()
        if len(token) != 32 or any(char not in "0123456789abcdef" for char in token):
            raise ActivationError("observation id must be 32 lowercase hex characters")
        return self.pending_observations_root / f"{token}.json"

    def _complete_pending_observation(self, observation_id: Any) -> None:
        token = str(observation_id or "").strip().lower()
        if not token:
            return
        self._pending_observation_path(token).unlink(missing_ok=True)

    @staticmethod
    def _file_digest(path: Path) -> str:
        digest = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
        return f"sha256:{digest.hexdigest()}"

    def _verify_materialized_component(
        self,
        package: ArtifactPackageRef,
    ) -> dict[str, Any]:
        verified = self.package_store.verify(package.digest)
        if verified.ref != package:
            raise ActivationError(
                f"stored package reference differs from WorkspaceLock: {package.key}"
            )
        target = self._target_for(package)
        if not target.is_dir():
            raise ActivationError(f"materialized component is missing: {package.key}")
        raw_files = verified.package_manifest.get("files")
        if not isinstance(raw_files, list):
            raise ActivationError(f"package manifest has no file list: {package.key}")
        checked_files = 0
        checked_bytes = 0
        for item in raw_files:
            if not isinstance(item, Mapping):
                raise ActivationError(f"package manifest file is invalid: {package.key}")
            relative = PurePosixPath(str(item.get("path") or ""))
            materialized = target.joinpath(*relative.parts).resolve()
            if materialized != target and target not in materialized.parents:
                raise ActivationError(
                    f"materialized package path escapes its target: {package.key}"
                )
            if not materialized.is_file():
                raise ActivationError(
                    f"materialized package file is missing: {package.key}:{relative.as_posix()}"
                )
            expected_size = int(item.get("size"))
            actual_size = materialized.stat().st_size
            if actual_size != expected_size:
                raise ActivationError(
                    f"materialized package file size changed: {package.key}:{relative.as_posix()}"
                )
            expected_digest = str(item.get("digest") or "").strip().lower()
            if self._file_digest(materialized) != expected_digest:
                raise ActivationError(
                    f"materialized package file digest changed: {package.key}:{relative.as_posix()}"
                )
            checked_files += 1
            checked_bytes += actual_size
        return {
            "package": package.key,
            "package_digest": package.digest,
            "materialization_path": package.materialization_path,
            "files": checked_files,
            "bytes": checked_bytes,
        }

    def run_delayed_verification(
        self,
        operation_id: str,
        *,
        now: datetime | None = None,
        force: bool = False,
    ) -> dict[str, Any]:
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        try:
            with mutation_lock(self.observation_lock_path):
                with mutation_lock(self.writer_lock_path):
                    operation = self._read_operation(operation_id)
                    if operation is None:
                        raise FileNotFoundError(
                            f"activation operation not found: {operation_id}"
                        )
                    raw = operation.get("delayed_verification")
                    if not isinstance(raw, Mapping):
                        raise ActivationError(
                            "activation has no delayed verification record"
                        )
                    observation = dict(raw)
                    if observation.get("schema") != DELAYED_VERIFICATION_SCHEMA:
                        raise ActivationError("unsupported delayed verification schema")
                    status = str(observation.get("status") or "").strip().lower()
                    if status in {"passed", "failed", "superseded", "cancelled"}:
                        self._complete_pending_observation(
                            observation.get("observation_id")
                        )
                        return observation
                    if status not in {"pending", "running"}:
                        raise ActivationError(
                            f"delayed verification has invalid status: {status or '<empty>'}"
                        )
                    if not force and _parse_iso(observation.get("due_at")) > observed_at:
                        return observation
                    if str(operation.get("status") or "").strip().lower() != "completed":
                        observation.update(
                            {
                                "status": "cancelled",
                                "observed_at": observed_at.replace(microsecond=0).isoformat(),
                                "reason": "activation_not_completed",
                            }
                        )
                        operation["delayed_verification"] = observation
                        self._write_operation(operation)
                        self._complete_pending_observation(
                            observation.get("observation_id")
                        )
                        return observation
                    observation["status"] = "running"
                    observation["attempts"] = int(observation.get("attempts") or 0) + 1
                    observation["started_at"] = observed_at.replace(microsecond=0).isoformat()
                    if status == "running":
                        observation["recovered_read_only_attempt"] = True
                    operation["delayed_verification"] = observation
                    self._write_operation(operation)

                    current = self.load_lock()
                    observed_digest = self._lock_digest(current)
                    expected_digest = str(
                        observation.get("expected_lock_digest") or ""
                    )
                    if current is None or observed_digest != expected_digest:
                        observation.update(
                            {
                                "status": "superseded",
                                "observed_at": observed_at.replace(microsecond=0).isoformat(),
                                "observed_lock_digest": observed_digest,
                                "reason": "workspace_lock_moved",
                            }
                        )
                    else:
                        expected_revision = int(
                            observation.get("expected_lock_revision") or 0
                        )
                        if current.lock_revision != expected_revision:
                            raise ActivationError(
                                "WorkspaceLock revision differs from delayed verification"
                            )
                        components = [
                            self._verify_materialized_component(package)
                            for package in current.components
                        ]
                        observation.update(
                            {
                                "status": "passed",
                                "observed_at": observed_at.replace(microsecond=0).isoformat(),
                                "observed_lock_digest": observed_digest,
                                "receipt": {
                                    "status": "passed",
                                    "lock_digest": observed_digest,
                                    "lock_revision": current.lock_revision,
                                    "components": components,
                                },
                            }
                        )
                    operation["delayed_verification"] = observation
                    self._write_operation(operation)
                    self._complete_pending_observation(
                        observation.get("observation_id")
                    )
                    return observation
        except MutationLockTimeout as exc:
            raise ActivationError("delayed verification lease is busy") from exc
        except Exception as exc:
            try:
                with mutation_lock(self.observation_lock_path):
                    operation = self._read_operation(operation_id)
                    raw = operation.get("delayed_verification") if operation else None
                    if operation is not None and isinstance(raw, Mapping):
                        observation = dict(raw)
                        observation.update(
                            {
                                "status": "failed",
                                "observed_at": observed_at.replace(microsecond=0).isoformat(),
                                "error": f"{type(exc).__name__}: {exc}",
                            }
                        )
                        operation["delayed_verification"] = observation
                        self._write_operation(operation)
                        self._complete_pending_observation(
                            observation.get("observation_id")
                        )
                        return observation
            except Exception:
                pass
            if isinstance(exc, ActivationError):
                raise
            raise ActivationError(str(exc)) from exc

    def run_due_delayed_verifications(
        self,
        *,
        now: datetime | None = None,
        limit: int = 32,
    ) -> list[dict[str, Any]]:
        if limit < 1 or limit > 256:
            raise ActivationError("delayed verification limit must be between 1 and 256")
        observed_at = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
        if not self.pending_observations_root.is_dir():
            return []
        results: list[dict[str, Any]] = []
        for path in sorted(self.pending_observations_root.glob("*.json")):
            if len(results) >= limit:
                break
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                continue
            if not isinstance(payload, Mapping):
                continue
            operation_id = str(payload.get("operation_id") or "").strip().lower()
            observation_id = str(payload.get("observation_id") or path.stem).strip().lower()
            if not operation_id:
                continue
            try:
                due_at = _parse_iso(payload.get("due_at"))
            except ActivationError:
                continue
            if due_at > observed_at:
                continue
            try:
                results.append(
                    self.run_delayed_verification(
                        operation_id,
                        now=observed_at,
                        force=True,
                    )
                )
            except FileNotFoundError:
                self._complete_pending_observation(observation_id)
        return results

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
        materialization_targets = [
            item.materialization_path
            or (f"skills/{item.artifact_id}" if item.kind == "skill" else f"scenarios/{item.artifact_id}")
            for item in plan.packages
        ]
        if len(materialization_targets) != len(set(materialization_targets)):
            raise ActivationError("activation plan has multiple packages for one materialization target")
        if plan.release.contract_locks_present:
            expected_schema_locks = tuple(
                sorted(
                    (lock for package in plan.packages for lock in package.schema_locks),
                    key=lambda item: item.lock_id,
                )
            )
            if plan.release.schema_locks != expected_schema_locks:
                raise ActivationError(
                    "ProjectRelease schema locks do not match activation packages"
                )
        for component in plan.release.components:
            if packages.get(component.key) != component:
                raise ActivationError(f"release component {component.key} is missing from activation plan")
        for dependency in plan.release.resolved_dependencies:
            package = packages.get(dependency.key)
            if package is None or package.digest != dependency.package_digest:
                raise ActivationError(
                    f"resolved dependency {dependency.key} is missing from activation plan"
                )
        bindings_by_consumer: dict[str, list[Any]] = {}
        for binding in plan.bindings:
            dependency = packages.get(binding.dependency)
            if binding.consumer not in packages:
                raise ActivationError(
                    f"dependency binding has unknown consumer {binding.consumer}"
                )
            if dependency is None or dependency.digest != binding.package_digest:
                raise ActivationError(
                    f"dependency binding selects an inconsistent package {binding.dependency}"
                )
            bindings_by_consumer.setdefault(binding.consumer, []).append(binding)
        reachable: set[str] = set()
        pending = [item.key for item in plan.release.components]
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            reachable.add(key)
            pending.extend(
                binding.dependency for binding in bindings_by_consumer.get(key, ())
            )
        unreachable = sorted(set(packages) - reachable)
        if unreachable:
            raise ActivationError(
                f"activation plan contains unreachable packages: {', '.join(unreachable)}"
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

    @staticmethod
    def _workflow_candidate_plan(
        current: WorkspaceLock | None,
        desired: WorkspaceLock,
        release: ProjectRelease,
        *,
        candidate_keys: set[str],
    ) -> dict[str, Any]:
        try:
            return workflow_admission_record(
                current=current,
                desired=desired,
                release=release,
                candidate_keys=candidate_keys,
            )
        except WorkflowAdmissionError as exc:
            raise ActivationError(str(exc)) from exc

    @staticmethod
    def _release_admission_record(
        *,
        current: WorkspaceLock | None,
        desired: WorkspaceLock,
        plan: ReleasePlan,
        slot_id: str,
        verified_packages: tuple[ArtifactPackageRef, ...],
    ) -> dict[str, Any]:
        expected = {item.key: item for item in plan.packages}
        verified = {item.key: item for item in verified_packages}
        if verified != expected:
            raise ActivationError(
                "verified package references differ from the release candidate"
            )
        workflow_admission = WorkspaceActivationManager._workflow_candidate_plan(
            current,
            desired,
            plan.release,
            candidate_keys=set(expected),
        )
        unsigned = {
            "schema": WORKFLOW_PUBLICATION_ADMISSION_SCHEMA,
            "release_digest": plan.release.release_digest
            or plan.release.computed_digest(),
            "slot_id": slot_id,
            "observed_lock_digest": WorkspaceActivationManager._lock_digest(current),
            "desired_lock_digest": desired.to_dict()["lock_digest"],
            "desired_lock_updated_at": desired.updated_at,
            "packages": [
                {
                    "component": package.key,
                    "package_digest": package.digest,
                    "manifest_digest": package.manifest_digest,
                    "definition_digest": (
                        package.workflow_lock.digest
                        if package.workflow_lock is not None
                        else None
                    ),
                    "validation_digest": (
                        package.workflow_validation_lock.digest
                        if package.workflow_validation_lock is not None
                        else None
                    ),
                    "binding_digest": package.workflow_binding_digest,
                    "role_policy_digest": package.workflow_role_policy_digest,
                }
                for package in sorted(verified_packages, key=lambda item: item.key)
            ],
            "workflow_admission": workflow_admission,
        }
        record = {
            **unsigned,
            "admission_digest": canonical_payload_digest(unsigned),
            "status": "admitted",
        }
        validate_workflow_record(WORKFLOW_PUBLICATION_ADMISSION_SCHEMA, record)
        return record

    def admit_release_candidate(
        self,
        plan: ReleasePlan,
        *,
        slot_id: str = "primary",
        audience: str | None = None,
        data_mode: str | None = None,
        data_ref: str | None = None,
        fetch_package: Callable[[ArtifactPackageRef], bytes] | None = None,
        desired_lock_updated_at: str | None = None,
    ) -> dict[str, Any]:
        """Verify code and workflow policy as one pre-publication gate."""

        self._assert_plan(plan)
        verified: list[ArtifactPackageRef] = []
        for package in plan.packages:
            try:
                if not self.package_store.has(package.digest):
                    if fetch_package is None:
                        raise ActivationError(
                            f"package is not present in local store: {package.digest}"
                        )
                    self.package_store.put(
                        fetch_package(package),
                        expected_digest=package.digest,
                    )
                observed = self.package_store.verify(package.digest)
            except (FileNotFoundError, PackageVerificationError) as exc:
                raise ActivationError(
                    f"release admission package verification failed: {exc}"
                ) from exc
            if observed.ref != package:
                raise ActivationError(
                    f"verified package reference differs from release: {package.key}"
                )
            verified.append(observed.ref)
        current = self.load_lock()
        desired = self._desired_lock(
            current=current,
            plan=plan,
            slot_id=slot_id,
            audience=audience,
            data_mode=data_mode,
            data_ref=data_ref,
            updated_at=desired_lock_updated_at,
        )
        return self._release_admission_record(
            current=current,
            desired=desired,
            plan=plan,
            slot_id=slot_id,
            verified_packages=tuple(verified),
        )

    def validate_release_admission(
        self,
        plan: ReleasePlan,
        record: Mapping[str, Any],
        *,
        slot_id: str = "primary",
    ) -> dict[str, Any]:
        persisted = dict(record)
        validate_workflow_record(WORKFLOW_PUBLICATION_ADMISSION_SCHEMA, persisted)
        current = self.load_lock()
        current_digest = self._lock_digest(current)
        if current_digest == persisted.get("desired_lock_digest"):
            if persisted.get("release_digest") != (
                plan.release.release_digest or plan.release.computed_digest()
            ) or persisted.get("slot_id") != slot_id:
                raise ActivationError("workflow publication admission targets another release")
            for package in plan.packages:
                verified = self.package_store.verify(package.digest)
                if verified.ref != package:
                    raise ActivationError(
                        f"verified package reference differs from release: {package.key}"
                    )
            return persisted
        observed = self.admit_release_candidate(
            plan,
            slot_id=slot_id,
            desired_lock_updated_at=str(persisted["desired_lock_updated_at"]),
        )
        if observed != persisted:
            raise ActivationError(
                "workflow publication admission no longer matches Workspace or package state"
            )
        return persisted

    @staticmethod
    def _approved_skip(policy: Mapping[str, Any] | None, *, phase: str) -> dict[str, Any]:
        if not isinstance(policy, Mapping) or str(policy.get("mode") or "") != "skip":
            raise ActivationError(
                f"{phase} requires an executor/check or an explicit approved skip policy"
            )
        approved_by = str(policy.get("approved_by") or "").strip()
        reason = str(policy.get("reason") or "").strip()
        if not approved_by or not reason:
            raise ActivationError(
                f"{phase} skip policy requires approved_by and reason"
            )
        return {
            "status": "skipped",
            "mode": "policy_skip",
            "approved_by": approved_by,
            "reason": reason,
            "recorded_at": _now_iso(),
        }

    @staticmethod
    def _reload_receipt(
        callback: Callable[[WorkspaceLock], Any] | None,
        policy: Mapping[str, Any] | None,
        lock: WorkspaceLock,
    ) -> dict[str, Any]:
        if callback is None:
            return WorkspaceActivationManager._approved_skip(policy, phase="runtime reload")
        raw = callback(lock)
        if isinstance(raw, Mapping):
            receipt = dict(raw)
            status = str(receipt.get("status") or "").strip().lower()
            if status not in {"completed", "reloaded"}:
                raise ActivationError("runtime reload returned no completion receipt")
        else:
            receipt = {"status": "completed"}
        receipt.setdefault("mode", "callback")
        receipt.setdefault("recorded_at", _now_iso())
        return receipt

    @staticmethod
    def _health_receipt(
        callback: Callable[[WorkspaceLock], Any] | None,
        policy: Mapping[str, Any] | None,
        lock: WorkspaceLock,
    ) -> dict[str, Any]:
        if callback is None:
            return WorkspaceActivationManager._approved_skip(policy, phase="health verification")
        raw = callback(lock)
        if isinstance(raw, Mapping):
            receipt = dict(raw)
            status = str(receipt.get("status") or "").strip().lower()
            passed = status in {"completed", "healthy", "passed"}
        else:
            passed = raw is True
            receipt = {"status": "passed" if passed else "failed"}
        receipt.setdefault("mode", "callback")
        receipt.setdefault("recorded_at", _now_iso())
        if not passed:
            raise ActivationError("post-activation health check failed")
        return receipt

    def _desired_lock(
        self,
        *,
        current: WorkspaceLock | None,
        plan: ReleasePlan,
        slot_id: str,
        audience: str | None,
        data_mode: str | None,
        data_ref: str | None,
        updated_at: str | None = None,
    ) -> WorkspaceLock:
        release_digest = plan.release.release_digest or plan.release.computed_digest()
        slots = {item.slot_id: item for item in (current.slots if current else ())}
        slots[slot_id] = WorkspaceSlot(
            slot_id=slot_id,
            project_id=plan.release.project_id,
            release=f"{plan.release.project_id}@{plan.release.version}",
            release_digest=release_digest,
            audience=audience,
            data_mode=data_mode,
            data_ref=data_ref,
        )
        current_components = {item.key: item for item in (current.components if current else ())}
        components = dict(current_components)
        for package in plan.packages:
            components[package.key] = package
        plan_consumers = {item.key for item in plan.packages}
        bindings = {
            (item.consumer, item.dependency): item
            for item in (current.bindings if current else ())
            if item.consumer not in plan_consumers
        }
        for binding in plan.bindings:
            bindings[(binding.consumer, binding.dependency)] = binding

        roots: set[str] = set()
        for slot in slots.values():
            if slot.slot_id == slot_id:
                slot_release = plan.release
            else:
                slot_release = self._load_release(slot.release_digest)
                if slot_release is None:
                    raise ActivationError(
                        f"cannot rebuild WorkspaceLock: release record is missing for slot {slot.slot_id}"
                    )
            for component in slot_release.components:
                active = components.get(component.key)
                if active != component:
                    raise ActivationError(
                        f"active slots require incompatible packages for {component.key}"
                    )
                roots.add(component.key)

        bindings_by_consumer: dict[str, list[Any]] = {}
        for binding in bindings.values():
            bindings_by_consumer.setdefault(binding.consumer, []).append(binding)
        reachable: set[str] = set()
        pending = list(sorted(roots))
        while pending:
            key = pending.pop()
            if key in reachable:
                continue
            if key not in components:
                raise ActivationError(f"WorkspaceLock root package is missing: {key}")
            reachable.add(key)
            for binding in bindings_by_consumer.get(key, ()):
                dependency = components.get(binding.dependency)
                if dependency is None or dependency.digest != binding.package_digest:
                    raise ActivationError(
                        f"binding for {key} selects an unavailable package {binding.dependency}"
                    )
                pending.append(binding.dependency)

        components = {key: package for key, package in components.items() if key in reachable}
        bindings = {
            key: binding
            for key, binding in bindings.items()
            if binding.consumer in reachable and binding.dependency in reachable
        }
        revision = (current.lock_revision if current else 0) + 1
        try:
            return WorkspaceLock(
                lock_revision=revision,
                previous_lock_revision=current.lock_revision if current else None,
                updated_at=updated_at or _now_iso(),
                slots=tuple(slots.values()),
                components=tuple(components.values()),
                bindings=tuple(bindings.values()),
            )
        except ArtifactReleaseContractError as exc:
            raise ActivationError(f"activation would create an incompatible WorkspaceLock: {exc}") from exc

    def _write_lock(self, lock: WorkspaceLock) -> None:
        atomic_write_json(self.lock_path, lock.to_dict())

    @staticmethod
    def _lock_digest(lock: WorkspaceLock | None) -> str | None:
        return lock.to_dict()["lock_digest"] if lock is not None else None

    @staticmethod
    def _component_plan(
        current: WorkspaceLock | None,
        desired: WorkspaceLock,
    ) -> dict[str, list[str]]:
        before = {item.key: item for item in (current.components if current else ())}
        after = {item.key: item for item in desired.components}
        return {
            "added": sorted(key for key in after if key not in before),
            "changed": sorted(
                key for key in after if key in before and after[key].digest != before[key].digest
            ),
            "retained": sorted(
                key for key in after if key in before and after[key].digest == before[key].digest
            ),
            "removed": sorted(key for key in before if key not in after),
        }

    def _target_for(self, package: ArtifactPackageRef) -> Path:
        relative = package.materialization_path or (
            f"skills/{package.artifact_id}"
            if package.kind == "skill"
            else f"scenarios/{package.artifact_id}"
        )
        target = (self.workspace_root / Path(relative)).resolve()
        if self.workspace_root not in target.parents:
            raise ActivationError(f"package target escapes Workspace: {target}")
        return target

    def plan_activation(
        self,
        plan: ReleasePlan,
        *,
        slot_id: str = "primary",
        audience: str | None = None,
        data_mode: str | None = None,
        data_ref: str | None = None,
    ) -> dict[str, Any]:
        """Return a deterministic, read-only plan bound to the observed WorkspaceLock."""

        self._assert_plan(plan)
        current = self.load_lock()
        desired = self._desired_lock(
            current=current,
            plan=plan,
            slot_id=slot_id,
            audience=audience,
            data_mode=data_mode,
            data_ref=data_ref,
            updated_at=(
                current.updated_at
                if current is not None
                else "1970-01-01T00:00:00+00:00"
            ),
        )
        current_release = self._active_release(current, slot_id=slot_id)
        permission_plan = self._permission_plan(current_release, plan.release)
        migration_plan = self._migration_plan(plan.release)
        workflow_plan = self._workflow_candidate_plan(
            current,
            desired,
            plan.release,
            candidate_keys={item.key for item in plan.packages},
        )
        migration_locks = {item.lock_id: item.digest for item in plan.release.migration_locks}
        for entry in migration_plan["entries"]:
            entry["digest"] = migration_locks.get(str(entry["id"]))

        current_schemas = {
            item.lock_id: item.digest
            for item in (current_release.schema_locks if current_release is not None else ())
        }
        target_schemas = {item.lock_id: item.digest for item in plan.release.schema_locks}
        schema_plan = {
            "current_release_known": current_release is not None,
            "added": sorted(key for key in target_schemas if key not in current_schemas),
            "changed": sorted(
                key
                for key in target_schemas
                if key in current_schemas and target_schemas[key] != current_schemas[key]
            ),
            "removed": sorted(key for key in current_schemas if key not in target_schemas),
            "target": [item.to_dict() for item in plan.release.schema_locks],
        }
        component_changes = self._component_plan(current, desired)
        warnings: list[str] = []
        legacy_targets = sorted(item.key for item in plan.packages if item.materialization_path is None)
        if legacy_targets:
            warnings.append(
                "legacy packages use canonical materialization targets: " + ", ".join(legacy_targets)
            )
        if current is not None and current_release is None:
            warnings.append(
                "active ProjectRelease is unavailable; introduced permissions and schema removals cannot be fully compared"
            )
        rollback_available = current is not None and (
            migration_plan["count"] == 0 or migration_plan["rollback_ready"]
        )
        payload: dict[str, Any] = {
            "schema": "adaos.artifact.activation_plan.v1",
            "project_id": plan.release.project_id,
            "target_release": f"{plan.release.project_id}@{plan.release.version}",
            "target_release_digest": plan.release.release_digest
            or plan.release.computed_digest(),
            "slot_id": slot_id,
            "observed_lock_digest": self._lock_digest(current),
            "component_changes": component_changes,
            "target_components": [
                {
                    "key": item.key,
                    "version": item.version,
                    "package_digest": item.digest,
                    "materialization_path": item.materialization_path
                    or (
                        f"skills/{item.artifact_id}"
                        if item.kind == "skill"
                        else f"scenarios/{item.artifact_id}"
                    ),
                }
                for item in sorted(plan.packages, key=lambda value: value.key)
            ],
            "resolved_dependencies": [
                item.to_dict()
                for item in sorted(plan.release.resolved_dependencies, key=lambda value: value.key)
            ],
            "permissions": permission_plan,
            "schemas": schema_plan,
            "migrations": migration_plan,
            "workflows": workflow_plan,
            "rollback": {
                "available": rollback_available,
                "reason": (
                    "previous_workspace_lock_and_reversible_migrations"
                    if rollback_available
                    else "no_previous_workspace_lock"
                    if current is None
                    else "migration_rollback_contract_incomplete"
                ),
            },
            "runtime": {
                "reload_required": True,
                "health_verification_required": True,
            },
            "warnings": warnings,
        }
        if self.attestation_admission is not None:
            payload["attestations"] = {
                "required": True,
                "policy": self.attestation_admission.policy_summary(),
            }
        payload["plan_digest"] = canonical_payload_digest(payload)
        return payload

    def _rollback(self, operation: dict[str, Any]) -> None:
        mutation = operation.get("workspace_mutation")
        mutated = isinstance(mutation, Mapping) and mutation.get("status") in {
            "dispatching",
            "completed",
        }
        if mutated:
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
        self._set_lock_history_status(
            operation,
            "rolled_back",
            reason="activation did not reach a durable terminal commit",
        )
        operation["rolled_back"] = True

    def activate(
        self,
        plan: ReleasePlan,
        *,
        idempotency_key: str,
        slot_id: str = "primary",
        audience: str | None = None,
        data_mode: str | None = None,
        data_ref: str | None = None,
        fetch_package: Callable[[ArtifactPackageRef], bytes] | None = None,
        reload_runtime: Callable[[WorkspaceLock], Any] | None = None,
        health_check: Callable[[WorkspaceLock], Any] | None = None,
        reload_policy: Mapping[str, Any] | None = None,
        health_policy: Mapping[str, Any] | None = None,
        permission_decision: (
            bool
            | Mapping[str, Any]
            | Callable[[Mapping[str, Any]], bool | Mapping[str, Any]]
            | None
        ) = None,
        migration_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        migration_rollback: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        phase_hook: Callable[[str], None] | None = None,
        expected_lock_digest: str | None | object = _CAPTURE_CURRENT_LOCK,
    ) -> ActivationResult:
        self._assert_plan(plan)
        if self.attestation_admission is not None:
            try:
                self.attestation_admission.verify_release_plan(plan)
            except ArtifactAttestationVerificationError as exc:
                raise ActivationError(f"artifact attestation admission failed: {exc}") from exc
        if fetch_package is not None:
            for package in plan.packages:
                if self.package_store.has(package.digest):
                    continue
                self.package_store.put(
                    fetch_package(package),
                    expected_digest=package.digest,
                )
        try:
            with mutation_lock(self.writer_lock_path):
                return self._activate_under_writer_lease(
                    plan,
                    idempotency_key=idempotency_key,
                    slot_id=slot_id,
                    audience=audience,
                    data_mode=data_mode,
                    data_ref=data_ref,
                    fetch_package=fetch_package,
                    reload_runtime=reload_runtime,
                    health_check=health_check,
                    reload_policy=reload_policy,
                    health_policy=health_policy,
                    permission_decision=permission_decision,
                    migration_executor=migration_executor,
                    migration_rollback=migration_rollback,
                    phase_hook=phase_hook,
                    expected_lock_digest=expected_lock_digest,
                )
        except MutationLockTimeout as exc:
            raise ActivationError("Workspace writer lease is busy") from exc

    def _activate_under_writer_lease(
        self,
        plan: ReleasePlan,
        *,
        idempotency_key: str,
        slot_id: str = "primary",
        audience: str | None = None,
        data_mode: str | None = None,
        data_ref: str | None = None,
        fetch_package: Callable[[ArtifactPackageRef], bytes] | None = None,
        reload_runtime: Callable[[WorkspaceLock], Any] | None = None,
        health_check: Callable[[WorkspaceLock], Any] | None = None,
        reload_policy: Mapping[str, Any] | None = None,
        health_policy: Mapping[str, Any] | None = None,
        permission_decision: (
            bool
            | Mapping[str, Any]
            | Callable[[Mapping[str, Any]], bool | Mapping[str, Any]]
            | None
        ) = None,
        migration_executor: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        migration_rollback: Callable[[Mapping[str, Any]], Mapping[str, Any]] | None = None,
        phase_hook: Callable[[str], None] | None = None,
        expected_lock_digest: str | None | object = _CAPTURE_CURRENT_LOCK,
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
                if not isinstance(existing.get("reload_receipt"), Mapping) or not isinstance(
                    existing.get("health_receipt"), Mapping
                ):
                    raise ActivationReplayBlocked(
                        "completed activation predates mandatory reload/health receipts"
                    )
                lock = self.load_lock()
                if lock is None or existing.get("lock_digest") != lock.to_dict()["lock_digest"]:
                    raise ActivationReplayBlocked(
                        "completed activation no longer matches the active WorkspaceLock"
                    )
                history = existing.get("lock_history")
                if isinstance(history, Mapping) and history.get("status") != "active":
                    self._set_lock_history_status(existing, "active")
                    self._write_operation(existing)
                existing_observation = (
                    existing.get("delayed_verification")
                    if isinstance(existing.get("delayed_verification"), Mapping)
                    else {}
                )
                return ActivationResult(
                    operation_id=operation_id,
                    status="completed",
                    workspace_lock=lock,
                    release_digest=release_digest,
                    idempotent_replay=True,
                    delayed_verification_id=(
                        str(existing_observation.get("observation_id") or "") or None
                    ),
                )
            raise ActivationReplayBlocked(
                f"activation {operation_id} is {existing.get('status')}; "
                "recover it or use an explicitly new idempotency key"
            )

        current = self.load_lock()
        observed_lock_digest = self._lock_digest(current)
        if (
            expected_lock_digest is not _CAPTURE_CURRENT_LOCK
            and expected_lock_digest != observed_lock_digest
        ):
            raise ActivationConflictError(
                "WorkspaceLock compare-and-switch conflict: "
                f"expected {expected_lock_digest or '<absent>'}, "
                f"observed {observed_lock_digest or '<absent>'}"
            )
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
            "expected_lock_digest": observed_lock_digest,
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
            if self.attestation_admission is not None:
                try:
                    operation["attestation_verification"] = (
                        self.attestation_admission.verify_release_plan(plan)
                    )
                except ArtifactAttestationVerificationError as exc:
                    raise ActivationError(
                        f"artifact attestation admission failed: {exc}"
                    ) from exc
                self._write_operation(operation)
            stage_root.mkdir(parents=True, exist_ok=False)
            staged: dict[str, Path] = {}
            verified_packages: list[dict[str, Any]] = []
            verified_package_refs: list[ArtifactPackageRef] = []
            for package in plan.packages:
                path = stage_root / package.kind / package.artifact_id
                verified = self.package_store.extract_to_directory(package.digest, path)
                if verified.ref != package:
                    raise ActivationError(
                        f"stored package reference differs from release: {package.key}"
                    )
                staged[package.key] = path
                verified_package_refs.append(verified.ref)
                verified_packages.append(
                    {
                        "package": package.key,
                        "digest": package.digest,
                        "file_count": len(verified.file_names),
                        "uncompressed_bytes": verified.uncompressed_bytes,
                    }
                )
            operation["package_verification"] = {
                "status": "completed",
                "mode": "verify_and_extract_once",
                "packages": verified_packages,
            }
            self._write_operation(operation)

            desired = self._desired_lock(
                current=current,
                plan=plan,
                slot_id=slot_id,
                audience=audience,
                data_mode=data_mode,
                data_ref=data_ref,
            )
            operation["desired_lock"] = desired.to_dict()
            release_admission = self._release_admission_record(
                current=current,
                desired=desired,
                plan=plan,
                slot_id=slot_id,
                verified_packages=tuple(verified_package_refs),
            )
            operation["publication_admission"] = release_admission
            workflow_plan = release_admission["workflow_admission"]
            operation["workflow_candidate"] = workflow_plan
            self._phase(
                operation,
                "workflow-bind",
                phase_hook=phase_hook,
                evidence={
                    "status": workflow_plan["status"],
                    "candidate_generation_digest": workflow_plan[
                        "candidate_generation_digest"
                    ],
                    "workflow_count": len(workflow_plan["workflows"]),
                },
            )
            component_plan = self._component_plan(current, desired)
            operation["component_plan"] = component_plan
            self._phase(
                operation,
                "dependency-plan",
                phase_hook=phase_hook,
                evidence={
                    "lock_digest": desired.to_dict()["lock_digest"],
                    **component_plan,
                },
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
            if reload_runtime is None:
                self._approved_skip(reload_policy, phase="runtime reload")
            if health_check is None:
                self._approved_skip(health_policy, phase="health verification")

            self._phase(
                operation,
                "stage",
                phase_hook=phase_hook,
                evidence={
                    "mode": "verified_private_staging",
                    "package_count": len(staged),
                },
            )

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
            desired_keys = {item.key for item in desired.components}
            for package in current.components if current else ():
                if package.key in desired_keys:
                    continue
                target = self._target_for(package)
                backup = backup_root / package.kind / package.artifact_id
                moves.append(
                    {
                        "package": package.key,
                        "target": str(target),
                        "staged": None,
                        "backup": str(backup),
                        "had_target": target.exists(),
                        "action": "remove",
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
            observed_before_switch = self._lock_digest(self.load_lock())
            if observed_before_switch != observed_lock_digest:
                raise ActivationConflictError(
                    "WorkspaceLock changed after activation planning: "
                    f"expected {observed_lock_digest or '<absent>'}, "
                    f"observed {observed_before_switch or '<absent>'}"
                )
            operation["workspace_mutation"] = {
                "status": "dispatching",
                "expected_lock_digest": observed_lock_digest,
                "started_at": _now_iso(),
            }
            self._write_operation(operation)
            for move in moves:
                target = Path(move["target"])
                staged_value = move.get("staged")
                backup = Path(move["backup"])
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    replace_with_retry(target, backup)
                if staged_value:
                    replace_with_retry(Path(str(staged_value)), target)
            self._write_lock(desired)
            operation["workspace_mutation"] = {
                "status": "completed",
                "expected_lock_digest": observed_lock_digest,
                "lock_digest": desired.to_dict()["lock_digest"],
                "completed_at": _now_iso(),
            }
            self._write_operation(operation)

            self._phase(operation, "reload", phase_hook=phase_hook)
            try:
                operation["reload_receipt"] = self._reload_receipt(
                    reload_runtime,
                    reload_policy,
                    desired,
                )
            except Exception:
                operation["reload_receipt"] = {
                    "status": "failed",
                    "mode": "callback" if reload_runtime is not None else "policy",
                    "recorded_at": _now_iso(),
                }
                self._write_operation(operation)
                raise
            self._write_operation(operation)

            self._phase(operation, "health-verify", phase_hook=phase_hook)
            try:
                operation["health_receipt"] = self._health_receipt(
                    health_check,
                    health_policy,
                    desired,
                )
            except Exception:
                operation["health_receipt"] = {
                    "status": "failed",
                    "mode": "callback" if health_check is not None else "policy",
                    "recorded_at": _now_iso(),
                }
                self._write_operation(operation)
                raise
            self._write_operation(operation)

            self._phase(
                operation,
                "commit",
                phase_hook=phase_hook,
                evidence={"lock_digest": desired.to_dict()["lock_digest"]},
            )
            lock_digest = str(desired.to_dict()["lock_digest"])
            history_id = f"{desired.lock_revision:08d}-{lock_digest.split(':', 1)[1]}"
            history, _history_status = self._lock_history_paths(history_id)
            operation["lock_history"] = {
                "history_id": history_id,
                "lock_revision": desired.lock_revision,
                "lock_digest": lock_digest,
                "status": "pending",
            }
            self._write_operation(operation)
            self._set_lock_history_status(operation, "pending")
            atomic_write_json(history, desired.to_dict())
            operation["status"] = "completed"
            operation["lock_digest"] = lock_digest
            operation["completed_at"] = _now_iso()
            delayed_verification_id = self._schedule_delayed_verification(
                operation,
                desired,
            )
            self._write_operation(operation)
            self._set_lock_history_status(operation, "active")
            self._write_operation(operation)
            shutil.rmtree(stage_root, ignore_errors=True)
            shutil.rmtree(backup_root, ignore_errors=True)
            return ActivationResult(
                operation_id=operation_id,
                status="completed",
                workspace_lock=desired,
                release_digest=release_digest,
                delayed_verification_id=delayed_verification_id,
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
            workspace_mutation = operation.get("workspace_mutation")
            workspace_was_mutated = isinstance(
                workspace_mutation, Mapping
            ) and workspace_mutation.get("status") in {"dispatching", "completed"}
            if reload_runtime is not None and current is not None and workspace_was_mutated:
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
        try:
            with mutation_lock(self.writer_lock_path):
                return self._recover_interrupted_under_writer_lease(
                    operation_id,
                    migration_reconciler=migration_reconciler,
                    migration_rollback=migration_rollback,
                )
        except MutationLockTimeout as exc:
            raise ActivationError("Workspace writer lease is busy") from exc

    def _recover_interrupted_under_writer_lease(
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
        workspace_mutation = operation.get("workspace_mutation")
        if isinstance(workspace_mutation, Mapping) and workspace_mutation.get("status") in {
            "dispatching",
            "completed",
        }:
            previous = operation.get("previous_lock")
            desired = operation.get("desired_lock")
            previous_digest = (
                str(previous.get("lock_digest") or "")
                if isinstance(previous, Mapping)
                else None
            )
            desired_digest = (
                str(desired.get("lock_digest") or "")
                if isinstance(desired, Mapping)
                else None
            )
            observed = self._lock_digest(self.load_lock())
            if observed not in {previous_digest, desired_digest}:
                raise ActivationReplayBlocked(
                    "interrupted activation no longer owns the observed WorkspaceLock; "
                    "manual reconciliation is required"
                )
        self._rollback(operation)
        operation["status"] = "recovered"
        operation["recovered_at"] = _now_iso()
        self._write_operation(operation)
        return operation


__all__ = [
    "ACTIVATION_OPERATION_SCHEMA",
    "ACTIVATION_PHASES",
    "DELAYED_VERIFICATION_SCHEMA",
    "LOCK_HISTORY_STATUS_SCHEMA",
    "ActivationError",
    "ActivationConflictError",
    "ActivationReplayBlocked",
    "ActivationResult",
    "WorkspaceActivationManager",
]
