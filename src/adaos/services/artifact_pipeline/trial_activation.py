from __future__ import annotations

import copy
import hashlib
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import WorkspaceLock
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import (
    atomic_write_json,
    mutation_lock,
    replace_with_retry,
)


TRIAL_ACTIVATION_SCHEMA = "adaos.trial.activation.v1"
TRIAL_WORKSPACE_LAYOUT_SCHEMA = "adaos.trial.workspace_layout.v1"
_STATUSES = {"active", "reconciling", "detached", "failed", "expired", "completed"}
_DATA_MODES = {"empty", "mock", "snapshot", "read_only", "real"}


class TrialActivationError(ValueError):
    """Raised when an isolated Trial Workspace cannot be represented safely."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any, field: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise TrialActivationError(f"{field} is required")
    return token


def _safe_candidate_id(candidate_id: str) -> str:
    token = _text(candidate_id, "candidate_id")
    if not all(char.isalnum() or char in {"-", "_"} for char in token):
        raise TrialActivationError("candidate_id is not a safe Trial path segment")
    return token


def _workspace_child(workspace_root: Path, *parts: str) -> Path:
    root = Path(workspace_root).resolve()
    target = root.joinpath(*parts)
    if target.resolve() != target:
        raise TrialActivationError("Trial path traverses a Workspace link or junction")
    return target


def trial_workspace_root(workspace_root: Path, candidate_id: str) -> Path:
    """Return the canonical sibling root for an immutable Trial Workspace."""

    workspace = Path(workspace_root).resolve()
    return _workspace_child(
        workspace.parent,
        "trials",
        _safe_candidate_id(candidate_id),
    )


def legacy_workspace_trial_root(workspace_root: Path, candidate_id: str) -> Path:
    """Return the superseded Trial root that was nested below Workspace."""

    return _workspace_child(
        workspace_root,
        "trials",
        _safe_candidate_id(candidate_id),
    )


def legacy_runtime_trial_root(workspace_root: Path, candidate_id: str) -> Path:
    return _workspace_child(
        workspace_root,
        ".runtime",
        "trials",
        _safe_candidate_id(candidate_id),
    )


def legacy_runtime_trial_workspace(workspace_root: Path, candidate_id: str) -> Path:
    return legacy_runtime_trial_root(workspace_root, candidate_id) / "workspace"


def runtime_trial_root(workspace_root: Path, candidate_id: str) -> Path:
    """Compatibility alias for the canonical Trial Workspace root."""

    return trial_workspace_root(workspace_root, candidate_id)


def runtime_trial_workspace(workspace_root: Path, candidate_id: str) -> Path:
    """Compatibility alias retained for callers of the pre-layout API."""

    return trial_workspace_root(workspace_root, candidate_id)


def ensure_trial_workspace_shape(trial_root: Path) -> Path:
    """Create the executable directory skeleton for one Trial Workspace."""

    root = Path(trial_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    for relative in (
        ".adaos",
        ".runtime",
        "projects",
        "scenarios",
        "skills",
        "skills/.runtime",
    ):
        target = root / relative
        if target.resolve() != target:
            raise TrialActivationError(
                f"Trial Workspace directory traverses a link or junction: {relative}"
            )
        target.mkdir(parents=True, exist_ok=True)
    return root


def _tree_digest(root: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix().encode("utf-8")
        if path.is_symlink():
            digest.update(b"L\0" + relative + b"\0" + os.readlink(path).encode("utf-8"))
            continue
        if path.is_dir():
            digest.update(b"D\0" + relative + b"\0")
            continue
        if not path.is_file():
            raise TrialActivationError(f"unsupported Trial entry during migration: {path}")
        digest.update(b"F\0" + relative + b"\0")
        with path.open("rb") as handle:
            while chunk := handle.read(1024 * 1024):
                digest.update(chunk)
        digest.update(b"\0")
    return "sha256:" + digest.hexdigest()


@dataclass(frozen=True, slots=True)
class TrialWorkspaceLayout:
    workspace_root: Path
    state_root: Path

    def __init__(self, *, workspace_root: Path, state_root: Path) -> None:
        object.__setattr__(self, "workspace_root", Path(workspace_root).resolve())
        object.__setattr__(self, "state_root", Path(state_root).resolve())

    @property
    def migration_root(self) -> Path:
        return self.state_root / "trial-layout-migrations"

    @property
    def legacy_archive_root(self) -> Path:
        return self.state_root / "legacy-trial-layout"

    def canonical(self, candidate_id: str) -> Path:
        return trial_workspace_root(self.workspace_root, candidate_id)

    def legacy(self, candidate_id: str) -> Path:
        return legacy_runtime_trial_workspace(self.workspace_root, candidate_id)

    def legacy_workspace_child(self, candidate_id: str) -> Path:
        return legacy_workspace_trial_root(self.workspace_root, candidate_id)

    def receipt_path(self, candidate_id: str) -> Path:
        return self.migration_root / f"{_safe_candidate_id(candidate_id)}.json"

    def load_receipt(self, candidate_id: str) -> dict[str, Any] | None:
        path = self.receipt_path(candidate_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise TrialActivationError(f"cannot read Trial layout migration: {exc}") from exc
        if (
            not isinstance(payload, Mapping)
            or payload.get("schema") != TRIAL_WORKSPACE_LAYOUT_SCHEMA
        ):
            raise TrialActivationError("invalid Trial layout migration receipt")
        return copy.deepcopy(dict(payload))

    def _archive_legacy_wrapper(self, candidate_id: str) -> str | None:
        token = _safe_candidate_id(candidate_id)
        wrapper = legacy_runtime_trial_root(self.workspace_root, token)
        archive = self.legacy_archive_root / token
        if not wrapper.exists():
            return str(archive) if archive.is_dir() else None
        if archive.exists():
            raise TrialActivationError(
                "legacy Trial layout archive already exists; reconciliation is required"
            )
        archive.parent.mkdir(parents=True, exist_ok=True)
        replace_with_retry(wrapper, archive)
        legacy_trials = self.workspace_root / ".runtime" / "trials"
        if legacy_trials.is_dir() and not any(legacy_trials.iterdir()):
            legacy_trials.rmdir()
        legacy_runtime = self.workspace_root / ".runtime"
        if legacy_runtime.is_dir() and not any(legacy_runtime.iterdir()):
            legacy_runtime.rmdir()
        return str(archive)

    def _archive_workspace_child(self, candidate_id: str) -> str | None:
        token = _safe_candidate_id(candidate_id)
        source = self.legacy_workspace_child(token)
        archive = self.legacy_archive_root / "workspace-child" / token
        if not source.exists():
            return str(archive) if archive.is_dir() else None
        if archive.exists():
            raise TrialActivationError(
                "nested Workspace Trial archive already exists; reconciliation is required"
            )
        archive.parent.mkdir(parents=True, exist_ok=True)
        replace_with_retry(source, archive)
        parent = self.workspace_root / "trials"
        if parent.is_dir() and not any(parent.iterdir()):
            parent.rmdir()
        return str(archive)

    def _preserve_receipt(
        self,
        candidate_id: str,
        receipt: Mapping[str, Any] | None,
    ) -> str | None:
        if receipt is None:
            return None
        payload = copy.deepcopy(dict(receipt))
        digest = hashlib.sha256(
            json.dumps(
                payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        target = (
            self.migration_root
            / "history"
            / _safe_candidate_id(candidate_id)
            / f"{digest}.json"
        )
        if not target.is_file():
            atomic_write_json(target, payload)
        return str(target)

    def _legacy_sources(self, candidate_id: str) -> list[tuple[str, Path]]:
        token = _safe_candidate_id(candidate_id)
        sources = (
            ("workspace_child", self.legacy_workspace_child(token)),
            ("runtime_nested", self.legacy(token)),
        )
        result: list[tuple[str, Path]] = []
        for layout, path in sources:
            if path.exists() and not path.is_dir():
                raise TrialActivationError(
                    f"legacy Trial Workspace path is not a directory: {path}"
                )
            if path.is_dir():
                result.append((layout, path))
        return result

    def _finalize_legacy_sources(
        self,
        candidate_id: str,
        canonical: Path,
    ) -> list[dict[str, str]]:
        token = _safe_candidate_id(candidate_id)
        canonical_digest = _tree_digest(canonical)
        archives: list[dict[str, str]] = []
        workspace_child = self.legacy_workspace_child(token)
        if workspace_child.is_dir():
            if _tree_digest(workspace_child) != canonical_digest:
                raise TrialActivationError(
                    "canonical and nested Workspace Trial roots diverge"
                )
            archived = self._archive_workspace_child(token)
            if archived:
                archives.append({"layout": "workspace_child", "path": archived})
        else:
            parent = self.workspace_root / "trials"
            if parent.is_dir() and not any(parent.iterdir()):
                parent.rmdir()

        runtime_wrapper = legacy_runtime_trial_root(self.workspace_root, token)
        runtime_workspace = self.legacy(token)
        if runtime_workspace.is_dir() and _tree_digest(runtime_workspace) != canonical_digest:
            raise TrialActivationError(
                "canonical and runtime-nested Trial roots diverge"
            )
        if runtime_wrapper.exists():
            archived = self._archive_legacy_wrapper(token)
            if archived:
                archives.append({"layout": "runtime_wrapper", "path": archived})
        return archives

    @staticmethod
    def _legacy_archive_value(archives: list[dict[str, str]]) -> str | None:
        runtime = next(
            (item["path"] for item in archives if item["layout"] == "runtime_wrapper"),
            None,
        )
        return runtime or (archives[0]["path"] if archives else None)

    def _resume_migration(
        self,
        candidate_id: str,
        canonical: Path,
        receipt: Mapping[str, Any],
    ) -> dict[str, Any]:
        status = str(receipt.get("status") or "").strip()
        if status not in {"prepared", "verified", "verified_duplicate"}:
            return copy.deepcopy(dict(receipt))
        if not canonical.is_dir():
            raise TrialActivationError(
                "incomplete Trial layout migration has no canonical Workspace"
            )
        source_digest = str(receipt.get("source_digest") or "").strip()
        target_digest = _tree_digest(canonical)
        if not source_digest or target_digest != source_digest:
            raise TrialActivationError(
                "canonical Trial Workspace differs from the interrupted migration source"
            )
        resumed = copy.deepcopy(dict(receipt))
        resumed["status"] = "verified"
        resumed["target_digest"] = target_digest
        atomic_write_json(self.receipt_path(candidate_id), resumed)
        archives = self._finalize_legacy_sources(candidate_id, canonical)
        resumed["legacy_archives"] = archives
        resumed["legacy_archive"] = self._legacy_archive_value(archives)
        resumed["status"] = "completed"
        resumed["completed_at"] = _now()
        atomic_write_json(self.receipt_path(candidate_id), resumed)
        return resumed

    def ensure(self, candidate_id: str) -> tuple[Path, dict[str, Any] | None]:
        """Resolve one Trial root and migrate its legacy layout when present."""

        token = _safe_candidate_id(candidate_id)
        canonical = self.canonical(token)
        receipt_path = self.receipt_path(token)
        with mutation_lock(receipt_path.with_suffix(".lock")):
            canonical_exists = canonical.is_dir()
            if canonical.exists() and not canonical_exists:
                raise TrialActivationError("canonical Trial Workspace path is not a directory")
            previous = self.load_receipt(token)
            if previous is not None and str(previous.get("status") or "") in {
                "prepared",
                "verified",
                "verified_duplicate",
            }:
                resumed = self._resume_migration(token, canonical, previous)
                return canonical, resumed

            sources = self._legacy_sources(token)
            if not sources:
                if canonical_exists:
                    recorded_path = str(
                        (previous or {}).get("canonical_path") or ""
                    ).strip()
                    if recorded_path and Path(recorded_path).resolve() != canonical:
                        raise TrialActivationError(
                            "Trial migration receipt points to another canonical root"
                        )
                    return canonical, previous
                if previous is not None:
                    raise TrialActivationError(
                        "completed Trial migration has no canonical or legacy Workspace"
                    )
                return canonical, None

            source_records = [
                {
                    "layout": layout,
                    "path": str(path),
                    "digest": _tree_digest(path),
                }
                for layout, path in sources
            ]
            source_digests = {item["digest"] for item in source_records}
            if len(source_digests) != 1:
                raise TrialActivationError(
                    "legacy Trial Workspaces diverge; manual reconciliation is required"
                )
            source_digest = str(source_records[0]["digest"])
            started_at = _now()
            if canonical_exists:
                target_digest = _tree_digest(canonical)
                if target_digest != source_digest:
                    raise TrialActivationError(
                        "canonical and legacy Trial Workspaces diverge; manual reconciliation is required"
                    )
                receipt = {
                    "schema": TRIAL_WORKSPACE_LAYOUT_SCHEMA,
                    "candidate_id": token,
                    "status": "verified_duplicate",
                    "source_layout": source_records[0]["layout"],
                    "legacy_path": source_records[0]["path"],
                    "legacy_paths": source_records,
                    "canonical_path": str(canonical),
                    "source_digest": source_digest,
                    "target_digest": target_digest,
                    "started_at": started_at,
                    "completed_at": started_at,
                }
                previous_receipt = self._preserve_receipt(token, previous)
                if previous_receipt:
                    receipt["previous_receipt"] = previous_receipt
                atomic_write_json(receipt_path, receipt)
                archives = self._finalize_legacy_sources(token, canonical)
                receipt["status"] = "completed"
                receipt["legacy_archives"] = archives
                receipt["legacy_archive"] = self._legacy_archive_value(archives)
                receipt["completed_at"] = _now()
                atomic_write_json(receipt_path, receipt)
                return canonical, copy.deepcopy(receipt)

            source_layout, source = sources[0]
            receipt = {
                "schema": TRIAL_WORKSPACE_LAYOUT_SCHEMA,
                "candidate_id": token,
                "status": "prepared",
                "source_layout": source_layout,
                "legacy_path": str(source),
                "legacy_paths": source_records,
                "canonical_path": str(canonical),
                "source_digest": source_digest,
                "target_digest": None,
                "started_at": started_at,
                "completed_at": None,
            }
            previous_receipt = self._preserve_receipt(token, previous)
            if previous_receipt:
                receipt["previous_receipt"] = previous_receipt
            atomic_write_json(receipt_path, receipt)
            canonical.parent.mkdir(parents=True, exist_ok=True)
            replace_with_retry(source, canonical)
            target_digest = _tree_digest(canonical)
            if target_digest != source_digest:
                raise TrialActivationError(
                    "migrated Trial Workspace digest differs from the legacy source"
                )
            receipt["status"] = "verified"
            receipt["target_digest"] = target_digest
            atomic_write_json(receipt_path, receipt)
            archives = self._finalize_legacy_sources(token, canonical)
            receipt["status"] = "completed"
            receipt["legacy_archives"] = archives
            receipt["legacy_archive"] = self._legacy_archive_value(archives)
            receipt["completed_at"] = _now()
            atomic_write_json(receipt_path, receipt)
            return canonical, copy.deepcopy(receipt)

    def migrate_all(self) -> list[dict[str, Any]]:
        candidate_ids: set[str] = set()
        for parent in (
            self.workspace_root / "trials",
            self.workspace_root / ".runtime" / "trials",
        ):
            if not parent.is_dir():
                continue
            for entry in parent.iterdir():
                if not entry.is_dir():
                    raise TrialActivationError(
                        f"unexpected entry in legacy Trial root: {entry}"
                    )
                candidate_ids.add(_safe_candidate_id(entry.name))
        receipts: list[dict[str, Any]] = []
        for token in sorted(candidate_ids):
            runtime_wrapper = legacy_runtime_trial_root(self.workspace_root, token)
            if not self._legacy_sources(token) and runtime_wrapper.is_dir():
                receipt_path = self.receipt_path(token)
                with mutation_lock(receipt_path.with_suffix(".lock")):
                    previous = self.load_receipt(token)
                    previous_receipt = self._preserve_receipt(token, previous)
                    started_at = _now()
                    archived = self._archive_legacy_wrapper(token)
                    receipt = {
                        "schema": TRIAL_WORKSPACE_LAYOUT_SCHEMA,
                        "candidate_id": token,
                        "status": "archived_legacy_metadata",
                        "legacy_path": str(runtime_wrapper),
                        "canonical_path": str(self.canonical(token)),
                        "source_digest": None,
                        "target_digest": None,
                        "legacy_archive": archived,
                        "legacy_archives": (
                            [{"layout": "runtime_wrapper", "path": archived}]
                            if archived
                            else []
                        ),
                        "started_at": started_at,
                        "completed_at": _now(),
                    }
                    if previous_receipt:
                        receipt["previous_receipt"] = previous_receipt
                    atomic_write_json(receipt_path, receipt)
                receipts.append(receipt)
                continue
            _, receipt = self.ensure(token)
            if receipt is not None:
                receipts.append(receipt)
        return receipts


def load_workspace_lock(path: Path) -> WorkspaceLock | None:
    source = Path(path)
    if not source.is_file():
        return None
    try:
        payload = json.loads(source.read_text(encoding="utf-8-sig"))
        if not isinstance(payload, Mapping):
            raise TrialActivationError("WorkspaceLock must contain an object")
        return WorkspaceLock.from_mapping(payload)
    except TrialActivationError:
        raise
    except Exception as exc:
        raise TrialActivationError(f"cannot trust active WorkspaceLock: {exc}") from exc


def shared_skill_conflicts(
    plan: ReleasePlan,
    active_lock: WorkspaceLock | None,
) -> list[dict[str, Any]]:
    """Return unsafe candidate skill substitutions in the single-version runtime.

    A changed skill is admitted only when every active reverse consumer is also
    part of this candidate release. That bounded closure is the only resolver
    proof available in the MVP; otherwise the Trial must fail closed.
    """

    if active_lock is None:
        return []
    candidate_by_key = {item.key: item for item in plan.packages}
    active_by_key = {item.key: item for item in active_lock.components}
    conflicts: list[dict[str, Any]] = []
    for key, candidate_package in candidate_by_key.items():
        if candidate_package.kind != "skill":
            continue
        active_package = active_by_key.get(key)
        if active_package is None or active_package.digest == candidate_package.digest:
            continue
        external_consumers = sorted(
            {
                binding.consumer
                for binding in active_lock.bindings
                if binding.dependency == key and binding.consumer not in candidate_by_key
            }
        )
        if external_consumers:
            conflicts.append(
                {
                    "skill": key,
                    "active_digest": active_package.digest,
                    "candidate_digest": candidate_package.digest,
                    "active_consumers": external_consumers,
                    "reason": "shared_skill_version_conflict",
                }
            )
    return conflicts


def build_trial_activation(
    *,
    candidate: Mapping[str, Any],
    plan: ReleasePlan,
    trial_id: str,
    activation_operation_id: str,
    workspace_root: Path,
    workspace_lock: WorkspaceLock,
    target: Mapping[str, Any],
    audience: str,
    data_mode: str,
    data_ref: str | None,
    isolation_evidence: Mapping[str, Any] | None,
    health_evidence: Mapping[str, Any] | None,
    previous_bindings: list[Mapping[str, Any]],
    idempotency_key: str,
    started_at: str | None = None,
    expires_at: str | None = None,
) -> dict[str, Any]:
    mode = str(data_mode or "empty").strip().lower()
    if mode not in _DATA_MODES:
        raise TrialActivationError(f"unsupported Trial data mode: {mode}")
    safety = dict(isolation_evidence or {})
    if mode == "real" and not (
        bool(safety.get("approved")) and bool(safety.get("reversible"))
    ):
        raise TrialActivationError(
            "real Trial data requires explicit approval and reversible effects"
        )
    status = "active"
    if status not in _STATUSES:  # pragma: no cover - keeps schema and code coupled
        raise TrialActivationError("invalid Trial activation status")
    started = str(started_at or _now())
    expiry = str(
        expires_at
        or (datetime.fromisoformat(started) + timedelta(days=7)).replace(microsecond=0).isoformat()
    )
    candidate_id = _text(candidate.get("candidate_id"), "candidate_id")
    release_digest = _text(candidate.get("release_digest"), "candidate.release_digest")
    package_digest = _text(candidate.get("package_digest"), "candidate.package_digest")
    project_id = _text(plan.release.project_id, "release.project_id")
    target_map = dict(target or {})
    target_webspace = _text(target_map.get("webspace_id"), "target.webspace_id")
    runtime_workspace = trial_workspace_root(workspace_root, candidate_id)
    return {
        "schema": TRIAL_ACTIVATION_SCHEMA,
        "activation_id": f"trial-activation:{candidate_id}",
        "trial_id": _text(trial_id, "trial_id"),
        "project_ref": {"kind": plan.packages[0].kind, "id": project_id},
        "candidate_ref": {
            "candidate_id": candidate_id,
            "release_digest": release_digest,
            "package_digest": package_digest,
        },
        "release_ref": {
            "project_id": project_id,
            "version": plan.release.version,
            "digest": release_digest,
        },
        "package_refs": [item.to_dict() for item in plan.packages],
        "target": {
            "zone": str(target_map.get("zone") or "").strip() or None,
            "subnet_id": str(target_map.get("subnet_id") or "").strip() or None,
            "webspace_id": target_webspace,
            "space_kind": str(target_map.get("space_kind") or "development").strip(),
            "scenario_id": str(target_map.get("scenario_id") or project_id).strip() or project_id,
        },
        "audience": str(audience or "owner").strip() or "owner",
        "data_mode": mode,
        "data_ref": str(data_ref or "").strip() or None,
        "safety_evidence": copy.deepcopy(safety),
        "runtime_binding": {
            "kind": "isolated_trial_workspace",
            "path": str(runtime_workspace),
            "channel": "beta",
            "authority": "immutable_candidate",
            "layout_schema": TRIAL_WORKSPACE_LAYOUT_SCHEMA,
            "activation_operation_id": activation_operation_id,
            "workspace_lock_digest": workspace_lock.to_dict()["lock_digest"],
            "components": [item.to_dict() for item in workspace_lock.components],
            "bindings": [item.to_dict() for item in workspace_lock.bindings],
        },
        "previous_bindings": [copy.deepcopy(dict(item)) for item in previous_bindings],
        "status": status,
        "health_evidence": copy.deepcopy(dict(health_evidence or {})),
        "rollback": {"status": "available", "mode": "derived_runtime_detach"},
        "idempotency_key": _text(idempotency_key, "idempotency_key"),
        "started_at": started,
        "expires_at": expiry,
        "completed_at": None,
        "detached_at": None,
        "reconciled_at": None,
        "updated_at": started,
    }


class TrialActivationStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    def path(self, candidate_id: str) -> Path:
        token = _safe_candidate_id(candidate_id)
        return self.root / f"{token}.json"

    def load(self, candidate_id: str) -> dict[str, Any] | None:
        path = self.path(candidate_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8-sig"))
        except Exception as exc:
            raise TrialActivationError(f"cannot read TrialActivation: {exc}") from exc
        if not isinstance(payload, Mapping) or payload.get("schema") != TRIAL_ACTIVATION_SCHEMA:
            raise TrialActivationError("invalid TrialActivation record")
        return copy.deepcopy(dict(payload))

    def save(self, record: Mapping[str, Any]) -> dict[str, Any]:
        candidate_ref = record.get("candidate_ref")
        if not isinstance(candidate_ref, Mapping):
            raise TrialActivationError("TrialActivation candidate_ref is required")
        candidate_id = _text(candidate_ref.get("candidate_id"), "candidate_id")
        payload = copy.deepcopy(dict(record))
        if payload.get("schema") != TRIAL_ACTIVATION_SCHEMA:
            raise TrialActivationError("invalid TrialActivation schema")
        path = self.path(candidate_id)
        with mutation_lock(path.with_suffix(".lock")):
            existing = self.load(candidate_id)
            if existing is not None:
                if (
                    existing.get("idempotency_key") != payload.get("idempotency_key")
                    or existing.get("candidate_ref") != payload.get("candidate_ref")
                ):
                    raise TrialActivationError(
                        "TrialActivation candidate is already bound to another immutable operation"
                    )
            atomic_write_json(path, payload)
        return copy.deepcopy(payload)

    def update(self, candidate_id: str, **changes: Any) -> dict[str, Any]:
        path = self.path(candidate_id)
        with mutation_lock(path.with_suffix(".lock")):
            record = self.load(candidate_id)
            if record is None:
                raise TrialActivationError("TrialActivation record is missing")
            record.update(copy.deepcopy(changes))
            record["updated_at"] = _now()
            atomic_write_json(path, record)
        return copy.deepcopy(record)

    def list_all(self) -> list[dict[str, Any]]:
        if not self.root.is_dir():
            return []
        records: list[dict[str, Any]] = []
        for path in sorted(self.root.glob("*.json"), key=lambda item: item.name):
            record = self.load(path.stem)
            if record is not None:
                records.append(record)
        return records

    def find_for_target(
        self,
        *,
        scenario_id: str,
        revision: str | None = None,
        webspace_id: str | None = None,
    ) -> dict[str, Any] | None:
        scenario = _text(scenario_id, "scenario_id")
        expected_revision = str(revision or "").strip()
        expected_webspace = str(webspace_id or "").strip()
        matches: list[dict[str, Any]] = []
        if not self.root.is_dir():
            return None
        for path in self.root.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8-sig"))
            except Exception:
                continue
            if not isinstance(payload, Mapping) or payload.get("schema") != TRIAL_ACTIVATION_SCHEMA:
                continue
            if str(payload.get("status") or "") not in {"active", "completed"}:
                continue
            target = payload.get("target") if isinstance(payload.get("target"), Mapping) else {}
            release = (
                payload.get("release_ref")
                if isinstance(payload.get("release_ref"), Mapping)
                else {}
            )
            if str(target.get("scenario_id") or "") != scenario:
                continue
            if expected_revision and str(release.get("version") or "") != expected_revision:
                continue
            if expected_webspace and str(target.get("webspace_id") or "") != expected_webspace:
                continue
            matches.append(copy.deepcopy(dict(payload)))
        if not matches:
            return None
        matches.sort(key=lambda item: str(item.get("updated_at") or ""))
        return matches[-1]


__all__ = [
    "TRIAL_ACTIVATION_SCHEMA",
    "TRIAL_WORKSPACE_LAYOUT_SCHEMA",
    "TrialActivationError",
    "TrialActivationStore",
    "TrialWorkspaceLayout",
    "build_trial_activation",
    "ensure_trial_workspace_shape",
    "legacy_runtime_trial_root",
    "legacy_runtime_trial_workspace",
    "legacy_workspace_trial_root",
    "load_workspace_lock",
    "runtime_trial_root",
    "runtime_trial_workspace",
    "shared_skill_conflicts",
    "trial_workspace_root",
]
