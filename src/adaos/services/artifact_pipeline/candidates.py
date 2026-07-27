from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping

from adaos.domain.artifact_release import (
    ArtifactSourceRef,
    ProjectRelease,
    canonical_payload_digest,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json


CandidateStatus = Literal["draft", "validated", "trial", "accepted", "rejected", "stale"]
TrialStatus = Literal["running", "accepted", "rejected", "failed"]
TrialDataMode = Literal["empty", "mock", "snapshot", "read_only", "real"]
CANDIDATE_SCHEMA = "adaos.artifact.candidate.v1"
TRIAL_SCHEMA = "adaos.artifact.trial.v1"
GENESIS_RELEASE_DIGEST = "sha256:" + "0" * 64


class CandidateError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class TrialEvidence:
    trial_id: str
    candidate_digest: str
    audience: str
    data_mode: TrialDataMode
    lock_digest: str
    started_at: str
    status: TrialStatus = "running"
    ended_at: str | None = None
    observations: tuple[Mapping[str, Any], ...] = ()
    data_ref: str | None = None
    isolation_evidence: Mapping[str, Any] | None = None
    reload_receipt: Mapping[str, Any] | None = None
    health_receipt: Mapping[str, Any] | None = None
    rollback_receipt: Mapping[str, Any] | None = None
    duration_seconds: int | None = None

    def __post_init__(self) -> None:
        if self.data_mode not in {"empty", "mock", "snapshot", "read_only", "real"}:
            raise CandidateError("unsupported trial data mode")
        if self.status not in {"running", "accepted", "rejected", "failed"}:
            raise CandidateError("unsupported trial status")
        for field, value in (
            ("trial_id", self.trial_id),
            ("candidate_digest", self.candidate_digest),
            ("audience", self.audience),
            ("lock_digest", self.lock_digest),
            ("started_at", self.started_at),
        ):
            if not str(value or "").strip():
                raise CandidateError(f"{field} must not be empty")
        if self.data_mode in {"mock", "snapshot", "read_only", "real"} and not str(
            self.data_ref or ""
        ).strip():
            raise CandidateError(f"trial data mode {self.data_mode} requires data_ref")
        if self.duration_seconds is not None and self.duration_seconds < 0:
            raise CandidateError("trial duration_seconds must not be negative")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": TRIAL_SCHEMA,
            "trial_id": self.trial_id,
            "candidate_digest": self.candidate_digest,
            "audience": self.audience,
            "data_mode": self.data_mode,
            "lock_digest": self.lock_digest,
            "started_at": self.started_at,
            "status": self.status,
            "observations": [dict(item) for item in self.observations],
        }
        if self.ended_at:
            payload["ended_at"] = self.ended_at
        if self.data_ref:
            payload["data_ref"] = self.data_ref
        if self.isolation_evidence is not None:
            payload["isolation_evidence"] = dict(self.isolation_evidence)
        if self.reload_receipt is not None:
            payload["reload_receipt"] = dict(self.reload_receipt)
        if self.health_receipt is not None:
            payload["health_receipt"] = dict(self.health_receipt)
        if self.rollback_receipt is not None:
            payload["rollback_receipt"] = dict(self.rollback_receipt)
        if self.duration_seconds is not None:
            payload["duration_seconds"] = self.duration_seconds
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrialEvidence":
        allowed = {
            "schema",
            "trial_id",
            "candidate_digest",
            "audience",
            "data_mode",
            "data_ref",
            "isolation_evidence",
            "lock_digest",
            "started_at",
            "status",
            "ended_at",
            "observations",
            "reload_receipt",
            "health_receipt",
            "rollback_receipt",
            "duration_seconds",
        }
        required = {
            "schema",
            "trial_id",
            "candidate_digest",
            "audience",
            "data_mode",
            "lock_digest",
            "started_at",
            "status",
            "observations",
        }
        if value.get("schema") != TRIAL_SCHEMA:
            raise CandidateError("unsupported trial evidence schema")
        unknown = sorted(set(value) - allowed)
        missing = sorted(required - set(value))
        if unknown:
            raise CandidateError(
                f"trial evidence contains unsupported fields: {', '.join(unknown)}"
            )
        if missing:
            raise CandidateError(
                f"trial evidence is missing required fields: {', '.join(missing)}"
            )
        observations = value.get("observations")
        if not isinstance(observations, list) or any(
            not isinstance(item, Mapping) for item in observations
        ):
            raise CandidateError("trial observations must contain only objects")
        for field in (
            "isolation_evidence",
            "reload_receipt",
            "health_receipt",
            "rollback_receipt",
        ):
            if value.get(field) is not None and not isinstance(value.get(field), Mapping):
                raise CandidateError(f"trial {field} must be an object")
        return cls(
            trial_id=str(value.get("trial_id") or ""),
            candidate_digest=str(value.get("candidate_digest") or ""),
            audience=str(value.get("audience") or ""),
            data_mode=value.get("data_mode"),
            lock_digest=str(value.get("lock_digest") or ""),
            started_at=str(value.get("started_at") or ""),
            status=value.get("status") or "running",
            ended_at=value.get("ended_at"),
            observations=tuple(observations),
            data_ref=value.get("data_ref"),
            isolation_evidence=value.get("isolation_evidence"),
            reload_receipt=value.get("reload_receipt"),
            health_receipt=value.get("health_receipt"),
            rollback_receipt=value.get("rollback_receipt"),
            duration_seconds=value.get("duration_seconds"),
        )


@dataclass(frozen=True, slots=True)
class CandidateRecord:
    candidate_id: str
    project_id: str
    version: str
    source_ref: ArtifactSourceRef
    base_release: str
    base_release_digest: str
    base_source_ref: ArtifactSourceRef
    package_digest: str
    release_digest: str
    change_ids: tuple[str, ...]
    created_at: str
    updated_at: str
    status: CandidateStatus = "draft"
    source_tree: str | None = None
    validation_evidence: tuple[Mapping[str, Any], ...] = ()
    trials: tuple[TrialEvidence, ...] = ()
    stale_reason: str | None = None

    def __post_init__(self) -> None:
        if self.status not in {"draft", "validated", "trial", "accepted", "rejected", "stale"}:
            raise CandidateError("unsupported candidate status")
        if not self.change_ids:
            raise CandidateError("candidate must contain at least one bounded change id")
        if not isinstance(self.source_ref, ArtifactSourceRef) or not isinstance(
            self.base_source_ref, ArtifactSourceRef
        ):
            raise CandidateError("candidate source references must be ArtifactSourceRef")
        for field, value in (
            ("candidate_id", self.candidate_id),
            ("project_id", self.project_id),
            ("version", self.version),
            ("base_release", self.base_release),
            ("base_release_digest", self.base_release_digest),
            ("package_digest", self.package_digest),
            ("release_digest", self.release_digest),
            ("created_at", self.created_at),
            ("updated_at", self.updated_at),
        ):
            if not str(value or "").strip():
                raise CandidateError(f"{field} must not be empty")
        if any(item.candidate_digest != self.digest for item in self.trials):
            raise CandidateError("trial evidence belongs to a different candidate digest")

    @property
    def digest(self) -> str:
        return canonical_payload_digest(self.identity_dict())

    def identity_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": CANDIDATE_SCHEMA,
            "candidate_id": self.candidate_id,
            "project_id": self.project_id,
            "version": self.version,
            "source_ref": self.source_ref.to_dict(),
            "base_release": self.base_release,
            "base_release_digest": self.base_release_digest,
            "base_source_ref": self.base_source_ref.to_dict(),
            "package_digest": self.package_digest,
            "release_digest": self.release_digest,
            "change_ids": list(self.change_ids),
            "created_at": self.created_at,
        }
        if self.source_tree:
            payload["source_tree"] = self.source_tree
        return payload

    def unsigned_dict(self) -> dict[str, Any]:
        payload = {
            **self.identity_dict(),
            "updated_at": self.updated_at,
            "status": self.status,
            "validation_evidence": [dict(item) for item in self.validation_evidence],
        }
        if self.stale_reason:
            payload["stale_reason"] = self.stale_reason
        return payload

    def to_dict(self) -> dict[str, Any]:
        payload = self.unsigned_dict()
        payload["candidate_digest"] = self.digest
        payload["trials"] = [item.to_dict() for item in self.trials]
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateRecord":
        allowed = {
            "schema",
            "candidate_id",
            "candidate_digest",
            "project_id",
            "version",
            "source_ref",
            "base_release",
            "base_release_digest",
            "base_source_ref",
            "package_digest",
            "release_digest",
            "change_ids",
            "created_at",
            "updated_at",
            "status",
            "source_tree",
            "validation_evidence",
            "trials",
            "stale_reason",
        }
        required = {
            "schema",
            "candidate_id",
            "candidate_digest",
            "project_id",
            "version",
            "source_ref",
            "base_release",
            "base_release_digest",
            "base_source_ref",
            "package_digest",
            "release_digest",
            "change_ids",
            "created_at",
            "updated_at",
            "status",
            "validation_evidence",
            "trials",
        }
        if value.get("schema") != CANDIDATE_SCHEMA:
            raise CandidateError("unsupported candidate schema")
        unknown = sorted(set(value) - allowed)
        missing = sorted(required - set(value))
        if unknown:
            raise CandidateError(
                f"candidate contains unsupported fields: {', '.join(unknown)}"
            )
        if missing:
            raise CandidateError(
                f"candidate is missing required fields: {', '.join(missing)}"
            )
        source = value.get("source_ref")
        base_source = value.get("base_source_ref")
        if not isinstance(source, Mapping) or not isinstance(base_source, Mapping):
            raise CandidateError("candidate source refs must be objects")
        raw_change_ids = value.get("change_ids")
        raw_validation = value.get("validation_evidence")
        raw_trials = value.get("trials")
        if not isinstance(raw_change_ids, list) or any(
            not isinstance(item, str) for item in raw_change_ids
        ):
            raise CandidateError("candidate change_ids must contain only strings")
        if not isinstance(raw_validation, list) or any(
            not isinstance(item, Mapping) for item in raw_validation
        ):
            raise CandidateError("candidate validation_evidence must contain only objects")
        if not isinstance(raw_trials, list) or any(
            not isinstance(item, Mapping) for item in raw_trials
        ):
            raise CandidateError("candidate trials must contain only objects")
        candidate = cls(
            candidate_id=str(value.get("candidate_id") or ""),
            project_id=str(value.get("project_id") or ""),
            version=str(value.get("version") or ""),
            source_ref=ArtifactSourceRef.from_mapping(source),
            base_release=str(value.get("base_release") or ""),
            base_release_digest=str(value.get("base_release_digest") or ""),
            base_source_ref=ArtifactSourceRef.from_mapping(base_source),
            package_digest=str(value.get("package_digest") or ""),
            release_digest=str(value.get("release_digest") or ""),
            change_ids=tuple(raw_change_ids),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            status=value.get("status") or "draft",
            source_tree=value.get("source_tree"),
            validation_evidence=tuple(raw_validation),
            trials=tuple(
                TrialEvidence.from_mapping(item)
                for item in raw_trials
            ),
            stale_reason=value.get("stale_reason"),
        )
        expected = value.get("candidate_digest")
        if expected and expected != candidate.digest:
            raise CandidateError("candidate digest does not match content")
        return candidate


def candidate_from_release(
    *,
    candidate_id: str,
    release: ProjectRelease,
    base_release: ProjectRelease | None,
    package_digest: str,
    change_ids: tuple[str, ...],
    source_tree: str | None = None,
    now: str | None = None,
) -> CandidateRecord:
    timestamp = now or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return CandidateRecord(
        candidate_id=candidate_id,
        project_id=release.project_id,
        version=release.version,
        source_ref=release.source_ref,
        base_release=(
            f"{base_release.project_id}@{base_release.version}"
            if base_release is not None
            else "unpublished"
        ),
        base_release_digest=(
            base_release.release_digest or base_release.computed_digest()
            if base_release is not None
            else GENESIS_RELEASE_DIGEST
        ),
        base_source_ref=base_release.source_ref if base_release is not None else release.source_ref,
        package_digest=package_digest,
        release_digest=release.release_digest or release.computed_digest(),
        change_ids=change_ids,
        created_at=timestamp,
        updated_at=timestamp,
        source_tree=source_tree,
    )


def record_validation(
    candidate: CandidateRecord,
    evidence: Mapping[str, Any],
    *,
    now: str,
) -> CandidateRecord:
    if candidate.status not in {"draft", "validated"}:
        raise CandidateError(f"cannot validate candidate in {candidate.status} state")
    if not evidence:
        raise CandidateError("validation evidence must not be empty")
    return replace(
        candidate,
        status="validated",
        validation_evidence=(*candidate.validation_evidence, dict(evidence)),
        updated_at=now,
    )


def begin_trial(
    candidate: CandidateRecord,
    *,
    trial_id: str,
    audience: str,
    data_mode: TrialDataMode,
    lock_digest: str,
    now: str,
    data_ref: str | None = None,
    isolation_evidence: Mapping[str, Any] | None = None,
    reload_receipt: Mapping[str, Any] | None = None,
    health_receipt: Mapping[str, Any] | None = None,
    real_data_read_only: bool = False,
    real_data_reversible: bool = False,
) -> CandidateRecord:
    if candidate.status != "validated":
        raise CandidateError("trial requires a validated candidate")
    if data_mode in {"mock", "snapshot", "read_only", "real"} and not str(
        data_ref or ""
    ).strip():
        raise CandidateError(f"{data_mode} trial requires an immutable data_ref")
    if data_mode in {"mock", "snapshot"}:
        if not isinstance(isolation_evidence, Mapping) or str(
            isolation_evidence.get("status") or ""
        ).strip().lower() not in {"passed", "verified"}:
            raise CandidateError(
                f"{data_mode} trial requires verified data isolation evidence"
            )
    if data_mode == "read_only" and not real_data_read_only:
        raise CandidateError("read_only trial requires a proven read-only adapter")
    if data_mode == "real" and not (real_data_read_only or real_data_reversible):
        raise CandidateError("real-data trial requires proven read-only or reversible behavior")
    trial = TrialEvidence(
        trial_id=trial_id,
        candidate_digest=candidate.digest,
        audience=audience,
        data_mode=data_mode,
        lock_digest=lock_digest,
        started_at=now,
        data_ref=data_ref,
        isolation_evidence=isolation_evidence,
        reload_receipt=reload_receipt,
        health_receipt=health_receipt,
    )
    return replace(candidate, status="trial", trials=(*candidate.trials, trial), updated_at=now)


def complete_trial(
    candidate: CandidateRecord,
    *,
    trial_id: str,
    accepted: bool,
    now: str,
    observations: tuple[Mapping[str, Any], ...] = (),
    rollback_receipt: Mapping[str, Any] | None = None,
) -> CandidateRecord:
    if candidate.status != "trial":
        raise CandidateError("candidate has no active trial")
    updated: list[TrialEvidence] = []
    found = False
    for trial in candidate.trials:
        if trial.trial_id != trial_id:
            updated.append(trial)
            continue
        if trial.status != "running":
            raise CandidateError("trial is already complete")
        if accepted:
            health_status = str(
                (trial.health_receipt or {}).get("status") or ""
            ).strip().lower()
            if health_status not in {"completed", "healthy", "passed"}:
                raise CandidateError(
                    "accepted trial requires a successful health receipt"
                )
        rollback_status = str(
            (rollback_receipt or {}).get("status") or ""
        ).strip().lower()
        allowed_rollback = {"not_required"} if accepted else {"rolled_back"}
        if rollback_status not in allowed_rollback:
            raise CandidateError(
                "trial decision requires a durable rollback receipt"
            )
        try:
            started = datetime.fromisoformat(trial.started_at.replace("Z", "+00:00"))
            ended = datetime.fromisoformat(now.replace("Z", "+00:00"))
            duration_seconds = max(0, int((ended - started).total_seconds()))
        except ValueError as exc:
            raise CandidateError("trial timestamps must be ISO-8601 values") from exc
        found = True
        updated.append(
            replace(
                trial,
                status="accepted" if accepted else "rejected",
                ended_at=now,
                observations=observations,
                rollback_receipt=dict(rollback_receipt),
                duration_seconds=duration_seconds,
            )
        )
    if not found:
        raise CandidateError(f"trial not found: {trial_id}")
    return replace(
        candidate,
        status="accepted" if accepted else "rejected",
        trials=tuple(updated),
        updated_at=now,
    )


def assess_freshness(
    candidate: CandidateRecord,
    current_stable: ProjectRelease | None,
) -> tuple[bool, str | None]:
    if current_stable is None:
        if candidate.base_release_digest == GENESIS_RELEASE_DIGEST:
            return True, None
        return False, "stable_release_missing"
    current_digest = current_stable.release_digest or current_stable.computed_digest()
    if candidate.base_release_digest != current_digest:
        return False, "base_release_moved"
    if candidate.base_source_ref.revision != current_stable.source_ref.revision:
        return False, "base_source_revision_moved"
    return True, None


def mark_stale(candidate: CandidateRecord, *, reason: str, now: str) -> CandidateRecord:
    return replace(candidate, status="stale", stale_reason=reason, updated_at=now)


def assert_promotable(
    candidate: CandidateRecord,
    release: ProjectRelease,
    current_stable: ProjectRelease | None,
) -> None:
    if candidate.status != "accepted":
        raise CandidateError("stable promotion requires an accepted candidate")
    if candidate.release_digest != (release.release_digest or release.computed_digest()):
        raise CandidateError("candidate and ProjectRelease digests differ")
    if not candidate.validation_evidence:
        raise CandidateError("candidate has no deterministic validation evidence")
    accepted_trials = [trial for trial in candidate.trials if trial.status == "accepted"]
    if not accepted_trials:
        raise CandidateError("candidate has no accepted trial evidence")
    if not any(
        str((trial.health_receipt or {}).get("status") or "").strip().lower()
        in {"completed", "healthy", "passed"}
        for trial in accepted_trials
    ):
        raise CandidateError("candidate has no accepted healthy trial evidence")
    if not any(
        str((trial.rollback_receipt or {}).get("status") or "").strip().lower()
        == "not_required"
        for trial in accepted_trials
    ):
        raise CandidateError("candidate has no complete trial rollback disposition")
    fresh, reason = assess_freshness(candidate, current_stable)
    if not fresh:
        raise CandidateError(f"candidate is stale: {reason}")


class CandidateStore:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve()

    def path(self, candidate_id: str) -> Path:
        safe = "".join(char for char in str(candidate_id) if char.isalnum() or char in {"-", "_"})
        if safe != candidate_id or not safe:
            raise CandidateError("candidate id contains unsafe characters")
        return self.root / f"{safe}.json"

    def save(self, candidate: CandidateRecord) -> Path:
        path = self.path(candidate.candidate_id)
        atomic_write_json(path, candidate.to_dict())
        return path

    def load(self, candidate_id: str) -> CandidateRecord:
        path = self.path(candidate_id)
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise CandidateError("candidate file must contain an object")
        return CandidateRecord.from_mapping(payload)


__all__ = [
    "CANDIDATE_SCHEMA",
    "GENESIS_RELEASE_DIGEST",
    "TRIAL_SCHEMA",
    "CandidateError",
    "CandidateRecord",
    "CandidateStatus",
    "CandidateStore",
    "TrialDataMode",
    "TrialEvidence",
    "TrialStatus",
    "assert_promotable",
    "assess_freshness",
    "begin_trial",
    "candidate_from_release",
    "complete_trial",
    "mark_stale",
    "record_validation",
]
