from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

import yaml

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
    DependencyRequirement,
    PackageCatalog,
    ReleasePlan,
    build_project_release,
    parse_artifact_requirements,
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
    change_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": PUSHED_SOURCE_SCHEMA,
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "source_ref": self.source_ref.to_dict(),
            "package": self.package.to_dict(),
            "pushed_at": self.pushed_at,
            "change_ids": list(self.change_ids),
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
            change_ids=tuple(
                sorted(
                    {
                        str(item).strip()
                        for item in value.get("change_ids") or ()
                        if str(item).strip()
                    }
                )
            ),
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
        change_ids: tuple[str, ...] = (),
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
            change_ids=tuple(sorted({str(item).strip() for item in change_ids if str(item).strip()})),
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

    def verify_pushed_source(
        self,
        record: PushedSourceRecord,
        artifact_dir: Path,
    ) -> BuiltArtifactPackage:
        """Verify that a recorded checkpoint still matches its DEV source."""

        return self._verify_current_source(record, artifact_dir)

    @staticmethod
    def _manifest(artifact_dir: Path, kind: str) -> Mapping[str, Any]:
        path = artifact_dir / ("skill.yaml" if kind == "skill" else "scenario.yaml")
        try:
            payload = yaml.safe_load(path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise PublicationError(f"cannot read canonical manifest {path}: {exc}") from exc
        if not isinstance(payload, Mapping):
            raise PublicationError(f"canonical manifest {path} must contain an object")
        return payload

    def _dependency_inputs(
        self,
        *,
        kind: str,
        artifact_dir: Path,
        own_package: ArtifactPackageRef,
        checkpoint_change_ids: tuple[str, ...],
    ) -> tuple[
        PackageCatalog,
        dict[str, tuple[DependencyRequirement, ...]],
        dict[str, bytes],
    ]:
        requirements = parse_artifact_requirements(
            self._manifest(artifact_dir, kind),
            kind=kind,  # type: ignore[arg-type]
        )
        catalog = PackageCatalog()
        requirements_by_package: dict[str, list[DependencyRequirement]] = {
            own_package.digest: list(requirements)
        }
        archives: dict[str, bytes] = {}
        loaded_releases: set[str] = set()

        pending_requirements = list(requirements)
        processed_requirements: set[tuple[str, str, str]] = set()
        dev_root = artifact_dir.parent.parent
        checkpoint_group = {item for item in checkpoint_change_ids if item}

        while pending_requirements:
            requirement = pending_requirements.pop(0)
            requirement_token = (
                requirement.kind,
                requirement.artifact_id,
                requirement.version_spec,
            )
            if requirement_token in processed_requirements:
                continue
            processed_requirements.add(requirement_token)

            local_record: PushedSourceRecord | None = None
            local_built: BuiltArtifactPackage | None = None
            dependency_dir = (
                dev_root
                / ("skills" if requirement.kind == "skill" else "scenarios")
                / requirement.artifact_id
            )
            if checkpoint_group and dependency_dir.is_dir():
                try:
                    candidate_record = self.load_pushed_source(
                        requirement.kind,
                        requirement.artifact_id,
                    )
                except PublicationError:
                    candidate_record = None
                if (
                    candidate_record is not None
                    and checkpoint_group.intersection(candidate_record.change_ids)
                ):
                    local_record = candidate_record
                    local_built = self._verify_current_source(local_record, dependency_dir)

            if local_built is not None:
                catalog.add(local_built.ref)
                archives[local_built.ref.digest] = local_built.archive_bytes
                local_requirements = parse_artifact_requirements(
                    self._manifest(dependency_dir, requirement.kind),
                    kind=requirement.kind,  # type: ignore[arg-type]
                )
                requirements_by_package.setdefault(local_built.ref.digest, []).extend(
                    local_requirements
                )
                pending_requirements.extend(local_requirements)
                continue

            try:
                pointer = self.remote.get_channel(requirement.artifact_id, "stable")
                dependency_plan = self.remote.get_release(
                    requirement.artifact_id,
                    pointer.release_digest,
                )
            except Exception as exc:
                status = getattr(exc, "status_code", None)
                code = str(getattr(exc, "error_code", "") or "")
                missing = status == 404 or code in {"channel_not_found", "release_not_found"} or isinstance(
                    exc, FileNotFoundError
                )
                if missing and requirement.optional:
                    continue
                if missing:
                    raise PublicationError(
                        f"required stable dependency is unavailable: {requirement.key}"
                    ) from exc
                raise

            release_digest = (
                dependency_plan.release.release_digest
                or dependency_plan.release.computed_digest()
            )
            if release_digest in loaded_releases:
                continue
            loaded_releases.add(release_digest)
            package_by_key = {item.key: item for item in dependency_plan.packages}
            component = package_by_key.get(requirement.key)
            if component is None:
                raise PublicationError(
                    f"stable release for {requirement.key} does not own the requested component"
                )
            for package in dependency_plan.packages:
                catalog.add(package)
                archives.setdefault(package.digest, self.remote.fetch_package(package))
            for binding in dependency_plan.bindings:
                consumer = package_by_key.get(binding.consumer)
                dependency = package_by_key.get(binding.dependency)
                if consumer is None or dependency is None:
                    raise PublicationError(
                        f"dependency release has an incomplete binding: {binding.consumer} -> {binding.dependency}"
                    )
                requirements_by_package.setdefault(consumer.digest, []).append(
                    DependencyRequirement(
                        kind=dependency.kind,
                        artifact_id=dependency.artifact_id,
                        version_spec=f"=={dependency.version}",
                    )
                )

        return (
            catalog,
            {
                digest: tuple(values)
                for digest, values in requirements_by_package.items()
            },
            archives,
        )

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
        catalog, requirements_by_package, dependency_archives = self._dependency_inputs(
            kind=kind,
            artifact_dir=artifact_dir,
            own_package=built.ref,
            checkpoint_change_ids=record.change_ids,
        )
        plan = build_project_release(
            project_id=artifact_id,
            version=built.ref.version,
            source_ref=record.source_ref,
            components=(built.ref,),
            catalog=catalog,
            requirements_by_package=requirements_by_package,
            validation_evidence=(validation_evidence,),
        )
        self.release_cache.put_release(plan)
        self.remote.put_release(
            plan,
            {built.ref.digest: built.archive_bytes, **dependency_archives},
        )
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
            fetch_package=self.remote.fetch_package,
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
        component = next(
            (
                item
                for item in plan.release.components
                if item.artifact_id == candidate.project_id
            ),
            None,
        )
        if component is None:
            raise PublicationError("release does not contain the candidate project component")
        for package in plan.packages:
            package_plural = "skills" if package.kind == "skill" else "scenarios"
            package_dir = self.workspace_root / package_plural / package.artifact_id
            upsert_workspace_registry_entry(
                self.workspace_root,
                package_plural,  # type: ignore[arg-type]
                package_dir,
                extra={
                    "activation": {
                        "mode": "package_lock",
                        "project_id": plan.release.project_id,
                        "release": f"{plan.release.project_id}@{plan.release.version}",
                        "release_digest": (
                            plan.release.release_digest
                            or plan.release.computed_digest()
                        ),
                        "package_digest": package.digest,
                    }
                },
            )
        plural = "skills" if component.kind == "skill" else "scenarios"
        set_workspace_registry_channel(
            self.workspace_root,
            plural,  # type: ignore[arg-type]
            component.artifact_id,
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
