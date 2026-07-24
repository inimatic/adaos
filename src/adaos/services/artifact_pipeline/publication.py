from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    StableSubscription,
)
from adaos.services.artifact_pipeline.activation import (
    ActivationResult,
    WorkspaceActivationManager,
)
from adaos.services.artifact_pipeline.candidates import (
    CandidateRecord,
    CandidateStore,
    assert_promotable,
    begin_trial,
    candidate_from_release,
    complete_trial,
    record_validation,
)
from adaos.services.artifact_pipeline.channels import (
    ChannelPointer,
    ReleaseRepository,
    SubscriptionStore,
)
from adaos.services.artifact_pipeline.packages import (
    BuiltArtifactPackage,
    ContentAddressedPackageStore,
    build_artifact_package,
)
from adaos.services.artifact_pipeline.releases import (
    PackageCatalog,
    ReleasePlan,
    build_project_release,
)
from adaos.services.artifact_pipeline.storage import atomic_write_json
from adaos.services.workspace_registry import (
    set_workspace_registry_channel,
    upsert_workspace_registry_entry,
)


PUSHED_SOURCE_SCHEMA = "adaos.artifact.pushed_source.v1"


class PublicationError(RuntimeError):
    pass


class PublicationRemote(Protocol):
    def put_release(self, plan: ReleasePlan, archives: Mapping[str, bytes]) -> None: ...

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...

    def set_channel(self, plan: ReleasePlan, channel: str = "stable") -> ChannelPointer: ...

    def get_channel(self, project_id: str, channel: str = "stable") -> ChannelPointer: ...

    def fetch_package(self, package: ArtifactPackageRef) -> bytes: ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(frozen=True, slots=True)
class PushedSourceRecord:
    kind: str
    artifact_id: str
    source_ref: ArtifactSourceRef
    package: ArtifactPackageRef
    pushed_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PUSHED_SOURCE_SCHEMA,
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "source_ref": self.source_ref.to_dict(),
            "package": self.package.to_dict(),
            "pushed_at": self.pushed_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "PushedSourceRecord":
        source = value.get("source_ref")
        package = value.get("package")
        if not isinstance(source, Mapping) or not isinstance(package, Mapping):
            raise PublicationError("pushed source record is missing package/source identity")
        return cls(
            kind=str(value.get("kind") or ""),
            artifact_id=str(value.get("artifact_id") or ""),
            source_ref=ArtifactSourceRef.from_mapping(source),
            package=ArtifactPackageRef.from_mapping(package),
            pushed_at=str(value.get("pushed_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class PreparedCandidate:
    candidate: CandidateRecord
    plan: ReleasePlan
    trial_workspace: Path


@dataclass(frozen=True, slots=True)
class PromotionResult:
    candidate: CandidateRecord
    plan: ReleasePlan
    pointer: ChannelPointer
    activation: ActivationResult
    subscription: StableSubscription


class ArtifactPublicationService:
    def __init__(
        self,
        *,
        state_root: Path,
        workspace_root: Path,
        remote: PublicationRemote,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.remote = remote
        self.package_store = ContentAddressedPackageStore(self.state_root / "packages")
        self.release_cache = ReleaseRepository(self.state_root / "release-cache")
        self.candidate_store = CandidateStore(self.state_root / "candidates")
        self.subscriptions = SubscriptionStore(self.workspace_root / ".adaos" / "subscriptions.json")

    def pushed_source_path(self, kind: str, artifact_id: str) -> Path:
        plural = "skills" if kind == "skill" else "scenarios"
        return self.state_root / "pushed-sources" / plural / f"{artifact_id}.json"

    def record_push(
        self,
        *,
        kind: str,
        artifact_id: str,
        artifact_dir: Path,
        source_ref: ArtifactSourceRef,
    ) -> PushedSourceRecord:
        built = build_artifact_package(
            artifact_dir,
            kind=kind,  # type: ignore[arg-type]
            source_ref=source_ref,
        )
        self.package_store.put(built.archive_bytes, expected_digest=built.ref.digest)
        record = PushedSourceRecord(
            kind=kind,
            artifact_id=artifact_id,
            source_ref=source_ref,
            package=built.ref,
            pushed_at=_now(),
        )
        atomic_write_json(self.pushed_source_path(kind, artifact_id), record.to_dict())
        return record

    def load_pushed_source(self, kind: str, artifact_id: str) -> PushedSourceRecord:
        path = self.pushed_source_path(kind, artifact_id)
        if not path.is_file():
            raise PublicationError(
                f"{kind} {artifact_id} has no exact Forge checkpoint; push it before candidate creation"
            )
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping):
            raise PublicationError("pushed source record must contain an object")
        return PushedSourceRecord.from_mapping(payload)

    def _verify_current_source(
        self,
        record: PushedSourceRecord,
        artifact_dir: Path,
    ) -> BuiltArtifactPackage:
        current = build_artifact_package(
            artifact_dir,
            kind=record.kind,  # type: ignore[arg-type]
            source_ref=record.source_ref,
        )
        if current.ref != record.package:
            raise PublicationError(
                "DEV content changed after the exact Forge checkpoint; push a new checkpoint"
            )
        return current

    def current_stable(self, project_id: str) -> ReleasePlan | None:
        try:
            pointer = self.remote.get_channel(project_id, "stable")
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            code = str(getattr(exc, "error_code", "") or "")
            if status == 404 or code in {"channel_not_found", "release_not_found"} or isinstance(exc, FileNotFoundError):
                return None
            raise
        return self.remote.get_release(project_id, pointer.release_digest)

    def prepare_candidate(
        self,
        *,
        kind: str,
        artifact_id: str,
        artifact_dir: Path,
        change_ids: tuple[str, ...],
        validation_evidence: Mapping[str, Any],
        current_stable: ReleasePlan | None = None,
        source_tree: str | None = None,
        audience: str = "owner",
        data_mode: str = "snapshot",
    ) -> PreparedCandidate:
        record = self.load_pushed_source(kind, artifact_id)
        built = self._verify_current_source(record, artifact_dir)
        stable = current_stable if current_stable is not None else self.current_stable(artifact_id)
        plan = build_project_release(
            project_id=artifact_id,
            version=built.ref.version,
            source_ref=record.source_ref,
            components=(built.ref,),
            catalog=PackageCatalog(),
            validation_evidence=(validation_evidence,),
        )
        self.release_cache.put_release(plan)
        self.remote.put_release(plan, {built.ref.digest: built.archive_bytes})
        candidate_id = f"{artifact_id}-{built.ref.version.replace('.', '-')}-{built.ref.digest[-12:]}"
        candidate = candidate_from_release(
            candidate_id=candidate_id,
            release=plan.release,
            base_release=stable.release if stable is not None else None,
            package_digest=built.ref.digest,
            change_ids=change_ids,
            source_tree=source_tree,
        )
        candidate = record_validation(candidate, validation_evidence, now=_now())

        trial_workspace = self.state_root / "trials" / candidate_id / "workspace"
        trial_activation = WorkspaceActivationManager(
            workspace_root=trial_workspace,
            package_store=self.package_store,
            state_root=self.state_root / "trials" / candidate_id / "state",
        ).activate(
            plan,
            idempotency_key=f"candidate-trial:{candidate.digest}",
            audience=audience,
        )
        candidate = begin_trial(
            candidate,
            trial_id=f"trial-{candidate_id}",
            audience=audience,
            data_mode=data_mode,  # type: ignore[arg-type]
            lock_digest=trial_activation.workspace_lock.to_dict()["lock_digest"],
            now=_now(),
        )
        self.candidate_store.save(candidate)
        return PreparedCandidate(candidate, plan, trial_workspace)

    def decide_candidate(
        self,
        candidate_id: str,
        *,
        accepted: bool,
        observations: tuple[Mapping[str, Any], ...] = (),
    ) -> CandidateRecord:
        candidate = self.candidate_store.load(candidate_id)
        running = next((item for item in candidate.trials if item.status == "running"), None)
        if running is None:
            raise PublicationError("candidate has no running trial")
        candidate = complete_trial(
            candidate,
            trial_id=running.trial_id,
            accepted=accepted,
            now=_now(),
            observations=observations,
        )
        self.candidate_store.save(candidate)
        return candidate

    def promote(
        self,
        candidate_id: str,
        *,
        health_check=None,
        reload_runtime=None,
    ) -> PromotionResult:
        candidate = self.candidate_store.load(candidate_id)
        plan = self.release_cache.get_release(candidate.project_id, candidate.release_digest)
        stable = self.current_stable(candidate.project_id)
        assert_promotable(candidate, plan.release, stable.release if stable is not None else None)

        pointer = self.remote.set_channel(plan, "stable")
        activation = WorkspaceActivationManager(
            workspace_root=self.workspace_root,
            package_store=self.package_store,
            state_root=self.state_root / "activation",
        ).activate(
            plan,
            idempotency_key=f"stable:{candidate.release_digest}",
            fetch_package=self.remote.fetch_package,
            reload_runtime=reload_runtime,
            health_check=health_check,
        )
        self.release_cache.put_release(plan)
        plural = "skills" if plan.release.components[0].kind == "skill" else "scenarios"
        artifact_dir = self.workspace_root / plural / plan.release.components[0].artifact_id
        upsert_workspace_registry_entry(
            self.workspace_root,
            plural,  # type: ignore[arg-type]
            artifact_dir,
        )
        set_workspace_registry_channel(
            self.workspace_root,
            plural,  # type: ignore[arg-type]
            plan.release.components[0].artifact_id,
            channel="stable",
            release=plan.release,
        )
        subscription = StableSubscription(
            project_id=candidate.project_id,
            installed_release=pointer.release,
            installed_digest=pointer.release_digest,
        )
        self.subscriptions.save(subscription)
        return PromotionResult(candidate, plan, pointer, activation, subscription)


__all__ = [
    "PUSHED_SOURCE_SCHEMA",
    "ArtifactPublicationService",
    "PreparedCandidate",
    "PromotionResult",
    "PublicationError",
    "PublicationRemote",
    "PushedSourceRecord",
]
