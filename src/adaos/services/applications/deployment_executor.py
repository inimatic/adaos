from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.application import utc_now
from adaos.domain.artifact_release import canonical_payload_digest
from adaos.domain.project_deployment import (
    ComponentPlacementPolicy,
    DataRetentionPolicy,
    DeploymentCompatibilityPolicy,
    ProjectDeployment,
    RolloutPolicy,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock
from adaos.services.project_deployment import DeploymentPrincipal, ProjectDeploymentRuntime


class ApplicationDeploymentExecutorError(RuntimeError):
    pass


class ApplicationDataSnapshotStore:
    """Bounded snapshots of the canonical per-Application data namespace."""

    def __init__(self, state_dir: Path, *, max_bytes: int = 512 * 1024 * 1024, max_files: int = 10_000) -> None:
        self.state_dir = Path(state_dir).expanduser().resolve()
        self.max_bytes = int(max_bytes)
        self.max_files = int(max_files)

    @property
    def data_root(self) -> Path:
        path = self.state_dir / "applications" / "data"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def snapshots_root(self) -> Path:
        path = self.state_dir / "applications" / "data_snapshots"
        path.mkdir(parents=True, exist_ok=True)
        return path

    @property
    def lock_path(self) -> Path:
        return self.snapshots_root / ".mutation.lock"

    @staticmethod
    def _token(application_id: str) -> str:
        token = str(application_id or "").strip().lower()
        if (
            not token
            or token[0] not in "abcdefghijklmnopqrstuvwxyz0123456789"
            or any(char not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for char in token)
        ):
            raise ApplicationDeploymentExecutorError("application_id is invalid for data snapshot")
        return token

    def _inventory(self, root: Path) -> tuple[list[dict[str, Any]], int]:
        records: list[dict[str, Any]] = []
        total = 0
        if root.is_dir():
            for path in sorted(root.rglob("*")):
                if path.is_symlink():
                    raise ApplicationDeploymentExecutorError("Application data snapshots reject symbolic links")
                if not path.is_file():
                    continue
                relative = path.relative_to(root).as_posix()
                size = path.stat().st_size
                total += size
                if len(records) >= self.max_files or total > self.max_bytes:
                    raise ApplicationDeploymentExecutorError("Application data exceeds snapshot bounds")
                hasher = hashlib.sha256()
                with path.open("rb") as stream:
                    while True:
                        chunk = stream.read(1024 * 1024)
                        if not chunk:
                            break
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                records.append({"path": relative, "size_bytes": size, "digest": f"sha256:{digest}"})
        return records, total

    def create(self, application_id: str, *, source_release_digest: str, consistency_boundary: str) -> dict[str, Any]:
        token = self._token(application_id)
        source = (self.data_root / token).resolve()
        if source.parent != self.data_root:
            raise ApplicationDeploymentExecutorError("Application data path escaped authority root")
        records, total = self._inventory(source)
        identity = canonical_payload_digest(
            {
                "application_id": token,
                "source_release_digest": source_release_digest,
                "consistency_boundary": consistency_boundary,
                "files": records,
            }
        )
        snapshot_ref = f"application-snapshot:{token}:{identity.split(':', 1)[1]}"
        target = self.snapshots_root / identity.split(":", 1)[1]
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if not target.is_dir():
                temporary = self.snapshots_root / f".{target.name}.tmp-{uuid.uuid4().hex}"
                temporary.mkdir(parents=True, exist_ok=False)
                try:
                    if source.is_dir():
                        shutil.copytree(source, temporary / "data", dirs_exist_ok=False)
                    else:
                        (temporary / "data").mkdir()
                    receipt = {
                        "schema": "adaos.application.data_snapshot.v1",
                        "snapshot_ref": snapshot_ref,
                        "application_id": token,
                        "source_release_digest": source_release_digest,
                        "consistency_boundary": consistency_boundary,
                        "snapshot_digest": identity,
                        "file_count": len(records), "size_bytes": total,
                        "files": records, "created_at": utc_now(), "status": "captured",
                    }
                    atomic_write_json(temporary / "receipt.json", receipt)
                    temporary.replace(target)
                except Exception:
                    shutil.rmtree(temporary, ignore_errors=True)
                    raise
            receipt = json.loads((target / "receipt.json").read_text(encoding="utf-8"))
        return dict(receipt)

    def restore(self, snapshot_ref: str) -> dict[str, Any]:
        parts = str(snapshot_ref or "").split(":")
        if len(parts) != 3 or parts[0] != "application-snapshot" or len(parts[2]) != 64:
            raise ApplicationDeploymentExecutorError("snapshot_ref is invalid")
        application_id = self._token(parts[1])
        snapshot = (self.snapshots_root / parts[2]).resolve()
        if snapshot.parent != self.snapshots_root or not (snapshot / "receipt.json").is_file():
            raise ApplicationDeploymentExecutorError("Application data snapshot is unavailable")
        receipt = json.loads((snapshot / "receipt.json").read_text(encoding="utf-8"))
        source = snapshot / "data"
        observed, _ = self._inventory(source)
        if observed != receipt.get("files"):
            raise ApplicationDeploymentExecutorError("Application data snapshot integrity check failed")
        target = (self.data_root / application_id).resolve()
        backup = self.data_root / f".{application_id}.restore-{uuid.uuid4().hex}"
        temporary = self.data_root / f".{application_id}.tmp-{uuid.uuid4().hex}"
        with mutation_lock(self.lock_path, timeout_s=30.0):
            shutil.copytree(source, temporary)
            try:
                if target.exists():
                    target.replace(backup)
                temporary.replace(target)
                shutil.rmtree(backup, ignore_errors=True)
            except Exception:
                shutil.rmtree(temporary, ignore_errors=True)
                if backup.exists() and not target.exists():
                    backup.replace(target)
                raise
        return {
            "schema": "adaos.application.data_restore.v1",
            "snapshot_ref": snapshot_ref,
            "application_id": application_id,
            "restored_release_digest": receipt["source_release_digest"],
            "snapshot_digest": receipt["snapshot_digest"],
            "status": "restored", "restored_at": utc_now(),
        }

    def delete_data(self, application_id: str) -> None:
        token = self._token(application_id)
        target = (self.data_root / token).resolve()
        if target.parent != self.data_root:
            raise ApplicationDeploymentExecutorError("Application data path escaped authority root")
        with mutation_lock(self.lock_path, timeout_s=30.0):
            if target.exists():
                shutil.rmtree(target)


class ApplicationDeploymentExecutor:
    def __init__(self, *, runtime: ProjectDeploymentRuntime, state_dir: Path) -> None:
        self.runtime = runtime
        self.snapshots = ApplicationDataSnapshotStore(state_dir)

    @staticmethod
    def _principal(actor_ref: str) -> DeploymentPrincipal:
        return DeploymentPrincipal.create(
            actor_ref=actor_ref,
            permissions={
                "project.deployment.manage", "project.deployment.inspect",
                "project.deployment.apply", "project.deployment.reconcile",
                "project.component.install.remote", "project.component.drain",
                "project.component.remove", "project.data.runtime.delete",
                "project.data.derived.delete",
            },
            approvals={"remote_install", "component_drain", "component_remove", "runtime_data_delete", "derived_data_delete"},
        )

    def _desired(self, plan: Mapping[str, Any], *, status: str = "planned") -> tuple[ProjectDeployment, int, ProjectDeployment | None]:
        application_id = str(plan["application_id"])
        deployment_id = f"application-deployment:{application_id}"
        try:
            previous = self.runtime.store.get_deployment(deployment_id)
            expected_revision = previous.revision
            revision = previous.revision + 1
            created_at = previous.created_at
        except FileNotFoundError:
            previous = None
            expected_revision = 0
            revision = 1
            created_at = utc_now()
        placements = tuple(
            ComponentPlacementPolicy(
                component_ref=str(item["component_ref"]), mode="singleton",
                required_capabilities=("project.activate",), min_instances=1, max_instances=1,
            )
            for item in plan.get("components") or ()
        )
        if not placements and previous is not None:
            placements = previous.placements
        if not placements:
            raise ApplicationDeploymentExecutorError("Application deployment has no components")
        data_policy = str(plan.get("data_policy") or "retain")
        desired = ProjectDeployment(
            deployment_id=deployment_id, project_ref=f"project:{plan['legacy_project_id']}",
            release_digest=str(plan.get("release_digest") or (previous.release_digest if previous else "")),
            subnet_id=str(plan["subnet_ref"]).split(":", 1)[-1], revision=revision,
            placements=placements,
            compatibility=DeploymentCompatibilityPolicy(),
            rollout=RolloutPolicy(batch_size=1, max_unavailable=1, stop_on_failure=True, rollback_on_failure=True),
            retention=DataRetentionPolicy(
                runtime_data="delete" if data_policy in {"delete", "snapshot_then_delete"} else "retain",
                derived_data="delete" if data_policy in {"delete", "snapshot_then_delete"} else "retain",
                external_data="retain",
            ),
            status=status, created_at=created_at, updated_at=utc_now(),
        )
        return desired, expected_revision, previous

    def _restore_desired(self, previous: ProjectDeployment, *, actor_ref: str) -> None:
        current = self.runtime.store.get_deployment(previous.deployment_id)
        self.runtime.define(
            replace(previous, revision=current.revision + 1, updated_at=utc_now()),
            expected_revision=current.revision,
            principal=self._principal(actor_ref), reason="application_operation_rollback",
        )

    def __call__(self, plan: Mapping[str, Any]) -> Mapping[str, Any]:
        kind = str(plan.get("kind") or "")
        actor_ref = str(plan.get("actor_ref") or "application-core")
        principal = self._principal(actor_ref)
        snapshot_receipt: dict[str, Any] | None = None
        if kind == "update":
            snapshot = plan.get("snapshot") if isinstance(plan.get("snapshot"), Mapping) else {}
            snapshot_receipt = self.snapshots.create(
                str(plan["application_id"]),
                source_release_digest=str(snapshot.get("source_release_digest") or ""),
                consistency_boundary=str(snapshot.get("consistency_boundary") or "artifact_activation_transaction"),
            )
        if kind == "remove":
            return self._remove(plan, principal=principal)
        if kind not in {"install", "update"}:
            raise ApplicationDeploymentExecutorError("unsupported Application deployment operation")
        desired, expected_revision, previous = self._desired(plan)
        self.runtime.define(
            desired, expected_revision=expected_revision, principal=principal,
            reason=f"application_{kind}",
        )
        deployment_plan = self.runtime.plan(desired.deployment_id, principal=principal)
        if deployment_plan.status == "blocked":
            restore = self.snapshots.restore(snapshot_receipt["snapshot_ref"]) if snapshot_receipt is not None else None
            if previous is not None:
                self._restore_desired(previous, actor_ref=actor_ref)
            return {"ok": False, "status": "failed", "reason": "deployment_plan_blocked", "warnings": list(deployment_plan.warnings), "snapshot_receipt": snapshot_receipt, "restore_receipt": restore}
        operation = self.runtime.apply(
            str(deployment_plan.plan_digest), principal=principal,
            idempotency_key=f"application:{plan['idempotency_key']}",
        )
        if operation.state == "succeeded":
            return {"ok": True, "status": "active", "deployment": desired.to_dict(), "deployment_plan": deployment_plan.to_dict(), "deployment_operation": operation.to_dict(), "snapshot_receipt": snapshot_receipt}
        if operation.uncertain or operation.state in {"uncertain", "partial"}:
            return {"ok": False, "status": "unknown", "reason": "deployment_outcome_uncertain", "deployment_operation": operation.to_dict(), "snapshot_receipt": snapshot_receipt}
        restore = self.snapshots.restore(snapshot_receipt["snapshot_ref"]) if snapshot_receipt is not None else None
        if previous is not None:
            self._restore_desired(previous, actor_ref=actor_ref)
        return {"ok": False, "status": "failed", "reason": "deployment_failed", "deployment_operation": operation.to_dict(), "snapshot_receipt": snapshot_receipt, "restore_receipt": restore}

    def _remove(self, plan: Mapping[str, Any], *, principal: DeploymentPrincipal) -> Mapping[str, Any]:
        desired, _, previous = self._desired(plan, status="removing")
        if previous is None:
            raise ApplicationDeploymentExecutorError("Application deployment is missing")
        activations = []
        cursor = None
        while True:
            page = self.runtime.inspect(
                previous.deployment_id,
                principal=principal,
                activation_cursor=cursor,
                limit=200,
            )
            activations.extend(page.activations)
            if page.activation_cursor is None:
                break
            if page.activation_cursor == cursor or len(activations) > 10_000:
                raise ApplicationDeploymentExecutorError(
                    "Application activation inventory cannot be enumerated safely"
                )
            cursor = page.activation_cursor
        results = []
        for activation in activations:
            if activation.status in {"removed", "inactive"}:
                continue
            result = self.runtime.remove(
                activation.activation_id, principal=principal,
                idempotency_key=f"application:{plan['idempotency_key']}:{activation.activation_id}",
            )
            results.append(result.to_dict())
            if result.uncertain or result.state in {"uncertain", "partial"}:
                return {"ok": False, "status": "unknown", "reason": "deployment_remove_uncertain", "deployment_operations": results}
            if result.state != "succeeded":
                return {"ok": False, "status": "failed", "reason": "deployment_remove_failed", "deployment_operations": results}
        snapshot_receipt = None
        data_policy = str(plan.get("data_policy") or "retain")
        if data_policy == "snapshot_then_delete":
            snapshot_receipt = self.snapshots.create(
                str(plan["application_id"]), source_release_digest=previous.release_digest,
                consistency_boundary="application_remove",
            )
        if data_policy in {"delete", "snapshot_then_delete"}:
            self.snapshots.delete_data(str(plan["application_id"]))
        current = self.runtime.store.get_deployment(previous.deployment_id)
        removed = replace(current, revision=current.revision + 1, status="removed", updated_at=utc_now())
        self.runtime.define(removed, expected_revision=current.revision, principal=principal, reason="application_remove_completed")
        return {"ok": True, "status": "removed", "deployment": removed.to_dict(), "deployment_operations": results, "snapshot_receipt": snapshot_receipt}
