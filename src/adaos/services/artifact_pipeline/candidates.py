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
TrialDataMode = Literal["mock", "snapshot", "read_only", "real"]
CANDIDATE_SCHEMA = "adaos.artifact.candidate.v1"
TRIAL_SCHEMA = "adaos.artifact.trial.v1"


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

    def __post_init__(self) -> None:
        if self.data_mode not in {"mock", "snapshot", "read_only", "real"}:
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
        return payload

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "TrialEvidence":
        return cls(
            trial_id=str(value.get("trial_id") or ""),
            candidate_digest=str(value.get("candidate_digest") or ""),
            audience=str(value.get("audience") or ""),
            data_mode=value.get("data_mode"),
            lock_digest=str(value.get("lock_digest") or ""),
            started_at=str(value.get("started_at") or ""),
            status=value.get("status") or "running",
            ended_at=value.get("ended_at"),
            observations=tuple(
                item for item in value.get("observations") or () if isinstance(item, Mapping)
            ),
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
        source = value.get("source_ref")
        base_source = value.get("base_source_ref")
        if not isinstance(source, Mapping) or not isinstance(base_source, Mapping):
            raise CandidateError("candidate source refs must be objects")
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
            change_ids=tuple(str(item) for item in value.get("change_ids") or ()),
            created_at=str(value.get("created_at") or ""),
            updated_at=str(value.get("updated_at") or ""),
            status=value.get("status") or "draft",
            source_tree=value.get("source_tree"),
            validation_evidence=tuple(
                item for item in value.get("validation_evidence") or () if isinstance(item, Mapping)
            ),
            trials=tuple(
                TrialEvidence.from_mapping(item)
                for item in value.get("trials") or ()
                if isinstance(item, Mapping)
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
    base_release: ProjectRelease,
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
        base_release=f"{base_release.project_id}@{base_release.version}",
        base_release_digest=base_release.release_digest or base_release.computed_digest(),
        base_source_ref=base_release.source_ref,
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
    real_data_read_only: bool = False,
    real_data_reversible: bool = False,
) -> CandidateRecord:
    if candidate.status != "validated":
        raise CandidateError("trial requires a validated candidate")
    if data_mode == "real" and not (real_data_read_only or real_data_reversible):
        raise CandidateError("real-data trial requires proven read-only or reversible behavior")
    trial = TrialEvidence(
        trial_id=trial_id,
        candidate_digest=candidate.digest,
        audience=audience,
        data_mode=data_mode,
        lock_digest=lock_digest,
        started_at=now,
    )
    return replace(candidate, status="trial", trials=(*candidate.trials, trial), updated_at=now)


def complete_trial(
    candidate: CandidateRecord,
    *,
    trial_id: str,
    accepted: bool,
    now: str,
    observations: tuple[Mapping[str, Any], ...] = (),
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
        found = True
        updated.append(
            replace(
                trial,
                status="accepted" if accepted else "rejected",
                ended_at=now,
                observations=observations,
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


def assess_freshness(candidate: CandidateRecord, current_stable: ProjectRelease) -> tuple[bool, str | None]:
    current_digest = current_stable.release_digest or current_stable.computed_digest()
    if candidate.base_release_digest != current_digest:
        return False, "base_release_moved"
    if candidate.base_source_ref.revision != current_stable.source_ref.revision:
        return False, "base_source_revision_moved"
    return True, None


def mark_stale(candidate: CandidateRecord, *, reason: str, now: str) -> CandidateRecord:
    return replace(candidate, status="stale", stale_reason=reason, updated_at=now)


def assert_promotable(candidate: CandidateRecord, release: ProjectRelease, current_stable: ProjectRelease) -> None:
    if candidate.status != "accepted":
        raise CandidateError("stable promotion requires an accepted candidate")
    if candidate.release_digest != (release.release_digest or release.computed_digest()):
        raise CandidateError("candidate and ProjectRelease digests differ")
    if not candidate.validation_evidence:
        raise CandidateError("candidate has no deterministic validation evidence")
    if not any(trial.status == "accepted" for trial in candidate.trials):
        raise CandidateError("candidate has no accepted trial evidence")
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
