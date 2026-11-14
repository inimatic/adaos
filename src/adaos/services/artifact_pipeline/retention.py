from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping

from adaos.domain.artifact_release import WorkspaceLock
from adaos.services.artifact_pipeline.activation import WorkspaceActivationManager
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root
from adaos.services.artifact_pipeline.storage import (
    MutationLockTimeout,
    mutation_lock,
)
from adaos.services.storage_hygiene import (
    apply_storage_cleanup_plan,
    create_storage_cleanup_plan,
)


TERMINAL_RECORD_STATES = {
    "cancelled",
    "completed",
    "failed",
    "passed",
    "promoted",
    "recovered",
    "rejected",
    "rolled_back",
    "stale",
    "superseded",
}


class ArtifactRetentionError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ArtifactRetentionPolicy:
    orphan_grace_seconds: float = 24 * 60 * 60
    package_retention_seconds: float = 30 * 24 * 60 * 60
    record_retention_seconds: float = 90 * 24 * 60 * 60
    lock_history_retention_seconds: float = 90 * 24 * 60 * 60
    keep_lock_histories: int = 20
    trial_retention_seconds: float = 7 * 24 * 60 * 60
    keep_trials_per_project: int = 3

    def __post_init__(self) -> None:
        for name in (
            "orphan_grace_seconds",
            "package_retention_seconds",
            "record_retention_seconds",
            "lock_history_retention_seconds",
            "trial_retention_seconds",
        ):
            value = float(getattr(self, name))
            if value < 0 or value > 10 * 365 * 24 * 60 * 60:
                raise ArtifactRetentionError(
                    f"{name} must be between 0 and ten years"
                )
        if self.keep_lock_histories < 2 or self.keep_lock_histories > 10_000:
            raise ArtifactRetentionError(
                "keep_lock_histories must be between 2 and 10000"
            )
        if self.keep_trials_per_project < 0 or self.keep_trials_per_project > 1_000:
            raise ArtifactRetentionError(
                "keep_trials_per_project must be between 0 and 1000"
            )


def _status(value: Mapping[str, Any]) -> str:
    return str(value.get("status") or value.get("state") or "").strip().lower()


def _requires_recovery(value: Any) -> bool:
    if isinstance(value, Mapping):
        if value.get("rollback_error"):
            return True
        state = _status(value)
        if state in {"dispatching", "pending", "running", "uncertain"}:
            return True
        return any(_requires_recovery(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_requires_recovery(item) for item in value)
    return False


def _safe_terminal(value: Mapping[str, Any]) -> bool:
    return _status(value) in TERMINAL_RECORD_STATES and not _requires_recovery(value)


def _json(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _digests(value: Any) -> set[str]:
    found: set[str] = set()
    if isinstance(value, Mapping):
        for item in value.values():
            found.update(_digests(item))
    elif isinstance(value, (list, tuple)):
        for item in value:
            found.update(_digests(item))
    elif isinstance(value, str):
        token = value.strip().lower()
        if (
            token.startswith("sha256:")
            and len(token) == 71
            and all(char in "0123456789abcdef" for char in token[7:])
        ):
            found.add(token)
    return found


def _age_seconds(path: Path, now: float) -> float:
    return max(0.0, now - path.stat().st_mtime)


class ArtifactPipelineRetentionManager:
    """Plan and apply conservative cleanup without touching reachable state."""

    def __init__(
        self,
        *,
        state_root: Path,
        workspace_root: Path,
        policy: ArtifactRetentionPolicy | None = None,
        protected_digests_provider: Callable[[], Iterable[str]] | None = None,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.policy = policy or ArtifactRetentionPolicy()
        self.protected_digests_provider = protected_digests_provider
        self.package_store = ContentAddressedPackageStore(self.state_root / "packages")
        self.activation = WorkspaceActivationManager(
            workspace_root=self.workspace_root,
            package_store=self.package_store,
            state_root=self.state_root / "activation",
        )
        self.retention_lock_path = self.state_root / ".retention.lock"

    @property
    def _allowed_file_roots(self) -> tuple[Path, ...]:
        return (
            self.package_store.root,
            self.activation.operations_root,
            self.activation.lock_history_root,
            self.activation.releases_root,
            self.state_root / "release-cache",
        )

    @property
    def _allowed_tree_roots(self) -> tuple[Path, ...]:
        return (
            self.activation.staging_root,
            self.activation.backups_root,
            self.workspace_root.parent / "trials",
            self.state_root / "trials",
            self.state_root / "trial-rollbacks",
            self.state_root / "legacy-trial-layout",
            self.state_root / "legacy-trial-layout" / "workspace-child",
        )

    @staticmethod
    def _lock_digests(lock: WorkspaceLock | None) -> set[str]:
        if lock is None:
            return set()
        return {
            *(component.digest for component in lock.components),
            *(slot.release_digest for slot in lock.slots),
        }

    def _package_inventory(self) -> dict[str, Path]:
        result: dict[str, Path] = {}
        root = self.package_store.root / "sha256"
        if not root.is_dir():
            return result
        for path in root.glob("*/*.zip"):
            token = path.stem.strip().lower()
            if len(token) == 64 and all(
                char in "0123456789abcdef" for char in token
            ):
                result[f"sha256:{token}"] = path.resolve()
        return result

    def _history_records(
        self,
        *,
        now: float,
    ) -> tuple[list[tuple[Path, dict[str, Any]]], list[dict[str, Any]]]:
        records: list[tuple[Path, dict[str, Any]]] = []
        actions: list[dict[str, Any]] = []
        history_paths = sorted(
            self.activation.lock_history_root.glob("*.json"),
            reverse=True,
        )
        active_index = 0
        for path in history_paths:
            payload = _json(path)
            age = _age_seconds(path, now)
            status_path = path.with_suffix(".status")
            status_exists = status_path.is_file()
            status_payload = _json(status_path) if status_exists else None
            if status_exists and status_payload is None:
                if payload is not None:
                    records.append((path, payload))
                continue
            status = (
                str(status_payload.get("status") or "").strip().lower()
                if status_payload is not None
                else "legacy_active"
            )
            if status_payload is not None and (
                status_payload.get("schema") != "adaos.artifact.lock_history_status.v1"
                or status not in {"pending", "active", "rolled_back"}
            ):
                if payload is not None:
                    records.append((path, payload))
                continue
            if status == "pending":
                if payload is not None:
                    records.append((path, payload))
                continue
            if status == "rolled_back":
                if age >= self.policy.lock_history_retention_seconds:
                    actions.append(self._file_action(path, "expired_rolled_back_history", now))
                    actions.append(
                        self._file_action(
                            status_path,
                            "expired_rolled_back_history_status",
                            now,
                        )
                    )
                continue
            keep = (
                active_index < self.policy.keep_lock_histories
                or age < self.policy.lock_history_retention_seconds
            )
            active_index += 1
            if keep and payload is not None:
                records.append((path, payload))
            elif not keep:
                actions.append(self._file_action(path, "expired_lock_history", now))
                if status_path.is_file():
                    actions.append(
                        self._file_action(status_path, "expired_lock_history_status", now)
                    )
        return records, actions

    def _recent_reference_records(self, *, now: float) -> Iterable[dict[str, Any]]:
        excluded_roots = {
            self.package_store.root,
            self.state_root / "proofs",
            self.state_root / "release-cache",
            self.activation.staging_root,
            self.activation.backups_root,
        }
        for path in self.state_root.rglob("*.json"):
            resolved = path.resolve()
            if any(root == resolved or root in resolved.parents for root in excluded_roots):
                continue
            payload = _json(path)
            if payload is None:
                continue
            if (
                not _safe_terminal(payload)
                or _age_seconds(path, now) < self.policy.record_retention_seconds
            ):
                yield payload

    def _operation_actions(self, *, now: float) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        pending_ids: set[str] = set()
        for marker in self.activation.pending_observations_root.glob("*.json"):
            payload = _json(marker)
            if payload is not None:
                token = str(payload.get("operation_id") or "").strip().lower()
                if token:
                    pending_ids.add(token)
        if not self.activation.operations_root.is_dir():
            return actions
        for path in self.activation.operations_root.glob("*.json"):
            payload = _json(path)
            if payload is None:
                continue
            operation_id = str(payload.get("operation_id") or path.stem).strip().lower()
            if operation_id in pending_ids:
                continue
            if not _safe_terminal(payload):
                continue
            if _age_seconds(path, now) < self.policy.record_retention_seconds:
                continue
            actions.append(self._file_action(path, "expired_terminal_operation", now))
        return actions

    def _orphan_tree_actions(self, *, now: float) -> list[dict[str, Any]]:
        actions: list[dict[str, Any]] = []
        for root, label in (
            (self.activation.staging_root, "orphan_staging"),
            (self.activation.backups_root, "orphan_backup"),
        ):
            if not root.is_dir():
                continue
            for path in root.iterdir():
                if not path.is_dir():
                    continue
                operation_path = self.activation.operations_root / f"{path.name}.json"
                if operation_path.is_file():
                    operation = _json(operation_path)
                    if operation is None or not _safe_terminal(operation):
                        continue
                if _age_seconds(path, now) < self.policy.orphan_grace_seconds:
                    continue
                actions.append(self._tree_action(path, label, now))
        return actions

    def _terminal_trial_actions(self, *, now: float) -> list[dict[str, Any]]:
        """Remove old derived Trial trees only after a durable terminal outcome."""

        from adaos.services.applications.trial_retention import SupersededTrialRetention

        superseded = SupersededTrialRetention(
            state_dir=self.state_root.parent, workspace_root=self.workspace_root)
        protected_refs, held_digests = superseded._protected()
        supersession_plan = superseded.plan()
        self._trial_retention_deferred = supersession_plan["deferred"]
        actions: list[dict[str, Any]] = supersession_plan["actions"]
        replaced_candidates = {action["candidate_id"] for action in actions}
        activations_root = self.state_root / "trial-activations"
        if activations_root.is_dir():
            activation_records: list[tuple[Path, dict[str, Any], str, str]] = []
            for activation_path in activations_root.glob("*.json"):
                activation = _json(activation_path)
                if (
                    activation is None
                    or activation.get("schema") != "adaos.trial.activation.v1"
                ):
                    continue
                candidate_ref = activation.get("candidate_ref")
                candidate_id = str(
                    candidate_ref.get("candidate_id")
                    if isinstance(candidate_ref, Mapping)
                    else ""
                ).strip()
                if not candidate_id or activation_path.stem != candidate_id:
                    continue
                release_ref = activation.get("release_ref")
                project_ref = activation.get("project_ref")
                project_id = str(
                    release_ref.get("project_id")
                    if isinstance(release_ref, Mapping)
                    else project_ref.get("id")
                    if isinstance(project_ref, Mapping)
                    else ""
                ).strip() or candidate_id
                activation_records.append((activation_path, activation, candidate_id, project_id))

            protected_candidates: set[str] = set()
            grouped: dict[str, list[tuple[float, str]]] = {}
            for activation_path, _activation, candidate_id, project_id in activation_records:
                try:
                    modified = float(activation_path.stat().st_mtime)
                except OSError:
                    modified = 0.0
                grouped.setdefault(project_id, []).append((modified, candidate_id))
            for records in grouped.values():
                records.sort(reverse=True)
                protected_candidates.update(
                    candidate_id
                    for _modified, candidate_id in records[: self.policy.keep_trials_per_project]
                )

            for activation_path, activation, candidate_id, _project_id in activation_records:
                if (f"trial:{candidate_id}" in protected_refs or candidate_id in replaced_candidates
                        or (activation.get("candidate_ref") or {}).get("release_digest") in held_digests):
                    continue
                if candidate_id in protected_candidates:
                    continue
                if _age_seconds(activation_path, now) < self.policy.trial_retention_seconds:
                    continue
                status = _status(activation)
                terminal_proven = False
                reason = ""
                if status == "completed":
                    promotion_path = self.state_root / "promotions" / f"{candidate_id}.json"
                    promotion = _json(promotion_path)
                    terminal_proven = bool(
                        promotion
                        and promotion.get("schema")
                        == "adaos.artifact.promotion_operation.v1"
                        and promotion.get("candidate_id") == candidate_id
                        and _status(promotion) == "completed"
                        and _age_seconds(promotion_path, now)
                        >= self.policy.trial_retention_seconds
                    )
                    reason = "expired_promoted_trial"
                elif status == "detached":
                    candidate_path = self.state_root / "candidates" / f"{candidate_id}.json"
                    candidate = _json(candidate_path)
                    terminal_proven = bool(
                        candidate
                        and candidate.get("candidate_id") == candidate_id
                        and _status(candidate) == "rejected"
                        and _age_seconds(candidate_path, now)
                        >= self.policy.trial_retention_seconds
                    )
                    reason = "expired_rejected_trial"
                if not terminal_proven:
                    continue
                roots = [self.state_root / "trials" / candidate_id]
                if status == "completed":
                    roots.append(trial_workspace_root(self.workspace_root, candidate_id))
                else:
                    roots.append(self.state_root / "trial-rollbacks" / candidate_id)
                for root in roots:
                    if (
                        root.is_dir()
                        and _age_seconds(root, now)
                        >= self.policy.trial_retention_seconds
                    ):
                        actions.append(self._tree_action(root, reason, now))

        migrations_root = self.state_root / "trial-layout-migrations"
        if migrations_root.is_dir():
            for migration_path in migrations_root.glob("*.json"):
                migration = _json(migration_path)
                if (
                    migration is None
                    or migration.get("schema") != "adaos.trial.workspace_layout.v1"
                    or _status(migration) != "completed"
                    or _age_seconds(migration_path, now)
                    < self.policy.record_retention_seconds
                ):
                    continue
                candidate_id = str(migration.get("candidate_id") or "").strip()
                if not candidate_id or migration_path.stem != candidate_id:
                    continue
                archives = {
                    self.state_root / "legacy-trial-layout" / candidate_id,
                    self.state_root
                    / "legacy-trial-layout"
                    / "workspace-child"
                    / candidate_id,
                }
                for raw in migration.get("legacy_archives") or []:
                    if isinstance(raw, Mapping) and str(raw.get("path") or "").strip():
                        archives.add(Path(str(raw["path"])).expanduser().resolve())
                for archive in archives:
                    if (
                        archive.is_dir()
                        and _age_seconds(archive, now)
                        >= self.policy.record_retention_seconds
                    ):
                        actions.append(
                            self._tree_action(
                                archive,
                                "expired_legacy_trial_layout",
                                now,
                            )
                        )
        return actions

    @staticmethod
    def _file_action(path: Path, reason: str, now: float) -> dict[str, Any]:
        stat = path.stat()
        return {
            "action": "delete_file",
            "path": str(path.resolve()),
            "reason": reason,
            "bytes": stat.st_size,
            "age_seconds": max(0.0, now - stat.st_mtime),
            "mtime_ns": stat.st_mtime_ns,
        }

    @staticmethod
    def _tree_action(path: Path, reason: str, now: float) -> dict[str, Any]:
        stat = path.stat()
        return {
            "action": "delete_tree",
            "path": str(path.resolve()),
            "reason": reason,
            "bytes": 0,
            "bytes_measured": False,
            "age_seconds": max(0.0, now - stat.st_mtime),
            "mtime_ns": stat.st_mtime_ns,
        }

    def _plan_locked(self, *, now: float) -> dict[str, Any]:
        packages = self._package_inventory()
        protected = self._lock_digests(self.activation.load_lock())
        if self.protected_digests_provider is not None:
            try:
                protected.update(str(item) for item in self.protected_digests_provider())
            except Exception as exc:
                raise ArtifactRetentionError(
                    f"Application reference projection failed; CAS GC is blocked: {exc}"
                ) from exc
        history, actions = self._history_records(now=now)
        for _path, payload in history:
            protected.update(_digests(payload))
        for payload in self._recent_reference_records(now=now):
            protected.update(_digests(payload))
        protected_release_digests = set(protected)
        protected_packages = set(packages).intersection(protected)

        for digest, path in sorted(packages.items()):
            if digest in protected_packages:
                continue
            if _age_seconds(path, now) < self.policy.package_retention_seconds:
                continue
            actions.append(self._file_action(path, "unreferenced_package", now))

        actions.extend(self._operation_actions(now=now))
        actions.extend(self._orphan_tree_actions(now=now))
        actions.extend(self._terminal_trial_actions(now=now))

        release_roots = (
            self.activation.releases_root,
            self.state_root / "release-cache",
        )
        for root in release_roots:
            if not root.is_dir():
                continue
            for path in root.rglob("*.json"):
                payload = _json(path)
                if payload is None:
                    continue
                release_digest = str(payload.get("release_digest") or "").strip().lower()
                if release_digest in protected_release_digests:
                    continue
                if _age_seconds(path, now) < self.policy.record_retention_seconds:
                    continue
                actions.append(self._file_action(path, "expired_release_record", now))

        unique: dict[str, dict[str, Any]] = {}
        for action in actions:
            unique[str(action["path"])] = action
        ordered = sorted(unique.values(), key=lambda item: (item["action"], item["path"]))
        return {
            "schema": "adaos.artifact.retention_plan.v1",
            "dry_run_default": True,
            "generated_at": now,
            "policy": {
                "orphan_grace_seconds": self.policy.orphan_grace_seconds,
                "package_retention_seconds": self.policy.package_retention_seconds,
                "record_retention_seconds": self.policy.record_retention_seconds,
                "lock_history_retention_seconds": self.policy.lock_history_retention_seconds,
                "keep_lock_histories": self.policy.keep_lock_histories,
                "trial_retention_seconds": self.policy.trial_retention_seconds,
                "keep_trials_per_project": self.policy.keep_trials_per_project,
            },
            "protected_package_digests": sorted(protected_packages),
            "actions": ordered,
            "candidate_count": len(ordered),
            "candidate_bytes": sum(int(item.get("bytes") or 0) for item in ordered),
            "deferred_trial_cleanup": getattr(self, "_trial_retention_deferred", []),
        }

    def _assert_allowed(self, path: Path, *, tree: bool) -> None:
        roots = self._allowed_tree_roots if tree else self._allowed_file_roots
        if not any(root in path.parents for root in roots):
            raise ArtifactRetentionError(f"retention target is outside allowed roots: {path}")
        if tree and not any(path.parent == root for root in roots):
            raise ArtifactRetentionError(
                f"recursive retention target is not an immediate orphan root child: {path}"
            )

    def _maintenance_roots(self) -> tuple[Path, Path]:
        base = self.workspace_root.parent
        if base.name == ".adaos":
            return base.parent, base
        return base, base / ".adaos"

    def _apply_plan_with_quarantine(self, plan: Mapping[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        from adaos.services.applications.trial_retention import REASON, SupersededTrialRetention

        workspace_root, base_dir = self._maintenance_roots()
        replacements = [action for action in plan.get("actions") or [] if action.get("reason") == REASON]
        replaced = SupersededTrialRetention(state_dir=self.state_root.parent, workspace_root=self.workspace_root).apply(
            candidate_ids={action["candidate_id"] for action in replacements}) if replacements else {}
        replacement_results = [
            {**action, **next((row for row in replaced.get("results", []) if row["path"] == action["path"]),
                             {"status": "deferred"})}
            for action in replacements]
        candidates: list[dict[str, Any]] = []
        for action in plan.get("actions") or []:
            if action.get("reason") == REASON:
                continue
            kind = str(action.get("action") or "")
            path = Path(str(action.get("path") or "")).resolve()
            tree = kind == "delete_tree"
            if kind not in {"delete_file", "delete_tree"}:
                raise ArtifactRetentionError(f"unsupported retention action: {kind}")
            self._assert_allowed(path, tree=tree)
            candidates.append(
                {
                    **dict(action),
                    "spec_id": "artifact_pipeline_retention",
                    "owner": "artifact-pipeline",
                    "expected_mtime_ns": int(action.get("mtime_ns") or -1),
                }
            )
        storage_plan = create_storage_cleanup_plan(
            candidates,
            workspace_root=workspace_root,
            base_dir=base_dir,
            source="artifact-pipeline",
            profile="artifact-retention",
        )
        job = apply_storage_cleanup_plan(
            storage_plan["plan_digest"],
            workspace_root=workspace_root,
            base_dir=base_dir,
            max_seconds=2.0,
            max_entries=1000,
        )
        by_source = {str(item.get("source_path")): item for item in job.get("items") or []}
        results = []
        for action in plan.get("actions") or []:
            if action.get("reason") == REASON:
                continue
            source = str(Path(str(action.get("path") or "")).resolve())
            item = by_source.get(source) or {}
            results.append(
                {
                    **dict(action),
                    "status": str(item.get("status") or "missing"),
                    "cleanup_job_id": job["job_id"],
                    "deleted_entries": int(item.get("deleted_entries") or 0),
                    "deleted_bytes": int(item.get("deleted_bytes") or 0),
                    **({"error": item["error"]} if item.get("error") else {}),
                }
            )
        job["superseded_trial_jobs"] = replaced.get("jobs", [])
        job["superseded_trial_deferred"] = replaced.get("deferred", [])
        return [*replacement_results, *results], job

    def run(
        self,
        *,
        dry_run: bool = True,
        now: float | None = None,
    ) -> dict[str, Any]:
        observed_at = float(time.time() if now is None else now)
        try:
            with mutation_lock(self.retention_lock_path):
                with mutation_lock(self.activation.writer_lock_path):
                    plan = self._plan_locked(now=observed_at)
                    if dry_run:
                        return {**plan, "ok": True, "dry_run": True, "results": []}
                    results, cleanup_job = self._apply_plan_with_quarantine(plan)
                    removed_statuses = {"quarantined", "deleting", "complete"}
                    return {
                        **plan,
                        "ok": not any(item["status"] in {"error", "deferred"} for item in results),
                        "dry_run": False,
                        "results": results,
                        "deleted_count": sum(1 for item in results if item["status"] in removed_statuses),
                        "deleted_bytes": sum(
                            int(item.get("deleted_bytes") or 0)
                            for item in results
                            if item["status"] in removed_statuses
                        ),
                        "cleanup_job": cleanup_job,
                    }
        except MutationLockTimeout as exc:
            raise ArtifactRetentionError("artifact retention lease is busy") from exc


def run_artifact_retention(
    ctx: Any,
    *,
    dry_run: bool = True,
    policy: ArtifactRetentionPolicy | None = None,
) -> dict[str, Any]:
    from adaos.services.applications import ApplicationRetentionService, ApplicationService, ApplicationStore

    application_retention = ApplicationRetentionService(
        ApplicationService(ApplicationStore(Path(ctx.paths.state_dir())))
    )
    return ArtifactPipelineRetentionManager(
        state_root=Path(ctx.paths.state_dir()) / "artifact_pipeline",
        workspace_root=Path(ctx.paths.workspace_dir()),
        policy=policy,
        protected_digests_provider=application_retention.protected_digests,
    ).run(dry_run=dry_run)


__all__ = [
    "ArtifactPipelineRetentionManager",
    "ArtifactRetentionError",
    "ArtifactRetentionPolicy",
    "run_artifact_retention",
]
