from __future__ import annotations

import copy
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from adaos.domain.artifact_release import WorkspaceLock
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


TRIAL_ACTIVATION_SCHEMA = "adaos.trial.activation.v1"
_STATUSES = {"active", "reconciling", "detached", "failed", "expired", "completed"}
_DATA_MODES = {"empty", "mock", "snapshot", "read_only", "real"}


class TrialActivationError(ValueError):
    """Raised when a runtime-only Trial cannot be represented safely."""


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _text(value: Any, field: str) -> str:
    token = str(value or "").strip()
    if not token:
        raise TrialActivationError(f"{field} is required")
    return token


def runtime_trial_root(workspace_root: Path, candidate_id: str) -> Path:
    token = _text(candidate_id, "candidate_id")
    if token in {".", ".."} or "/" in token or "\\" in token:
        raise TrialActivationError("candidate_id is not a safe runtime path segment")
    return Path(workspace_root).resolve() / ".runtime" / "trials" / token


def runtime_trial_workspace(workspace_root: Path, candidate_id: str) -> Path:
    return runtime_trial_root(workspace_root, candidate_id) / "workspace"


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
    runtime_workspace = runtime_trial_workspace(workspace_root, candidate_id)
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
            "kind": "derived_workspace_runtime",
            "path": str(runtime_workspace),
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
        token = _text(candidate_id, "candidate_id")
        if token in {".", ".."} or "/" in token or "\\" in token:
            raise TrialActivationError("candidate_id is not a safe record path segment")
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
    "TrialActivationError",
    "TrialActivationStore",
    "build_trial_activation",
    "load_workspace_lock",
    "runtime_trial_root",
    "runtime_trial_workspace",
    "shared_skill_conflicts",
]
