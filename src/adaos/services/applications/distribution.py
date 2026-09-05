from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, Protocol

from adaos.domain.application import ApplicationRelease, utc_now
from adaos.services.artifact_pipeline.attestation_sets import ReleaseAttestationSet
from adaos.services.artifact_pipeline.candidates import CandidateRecord, CandidateStore, assert_promotable
from adaos.services.artifact_pipeline.channels import ChannelPointer, ReleaseRepository
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock

from .service import ApplicationService, ApplicationServiceError
from .store import _read


class ApplicationDistributionError(ApplicationServiceError):
    pass


class DistributionOutcomeUnknown(ApplicationDistributionError):
    pass


class DistributionRemote(Protocol):
    def put_release(self, plan: ReleasePlan, archives: Mapping[str, bytes]) -> None: ...

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...

    def get_channel(self, project_id: str, channel: str = "stable") -> ChannelPointer: ...

    def fetch_package(self, package: Any) -> bytes: ...

    def set_channel(
        self,
        plan: ReleasePlan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ) -> ChannelPointer: ...

    def clear_channel(
        self,
        project_id: str,
        channel: str,
        *,
        expected_release_digest: str,
    ) -> ChannelPointer: ...


class ProvenanceAdmission(Protocol):
    release_sets: Any

    def verify_release_plan(self, plan: ReleasePlan) -> Mapping[str, Any]: ...


def _missing(exc: Exception) -> bool:
    return (
        isinstance(exc, FileNotFoundError)
        or getattr(exc, "status_code", None) == 404
        or str(getattr(exc, "error_code", "") or "")
        in {"release_not_found", "channel_not_found", "package_not_found"}
    )


@dataclass(slots=True)
class ApplicationDistributionService:
    applications: ApplicationService
    candidates: CandidateStore
    releases: ReleaseRepository
    packages: ContentAddressedPackageStore
    remote: DistributionRemote
    admission: ProvenanceAdmission
    clock: Callable[[], str] = utc_now

    @property
    def root(self) -> Path:
        path = self.applications.store.root / "distribution"
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _operation_path(self, candidate_id: str) -> Path:
        safe = "".join(char for char in candidate_id if char.isalnum() or char in {"-", "_"})
        if not safe or safe != candidate_id:
            raise ApplicationDistributionError("candidate_id contains unsafe characters")
        return self.root / f"{safe}.json"

    def _load_operation(self, candidate_id: str) -> dict[str, Any] | None:
        path = self._operation_path(candidate_id)
        return _read(path) if path.is_file() else None

    def _save_operation(self, operation: Mapping[str, Any]) -> None:
        atomic_write_json(self._operation_path(str(operation["candidate_id"])), operation)

    def _candidate_plan(
        self,
        application_id: str,
        candidate_id: str,
        publisher_ref: str,
        *,
        allow_completed_stable: bool = False,
    ) -> tuple[CandidateRecord, ReleasePlan]:
        application = self.applications.store.get_application(application_id)
        if application.publisher_ref != publisher_ref:
            raise ApplicationDistributionError("only the Application publisher may publish releases")
        candidate = self.candidates.load(candidate_id)
        if candidate.project_id != application.legacy_project_id:
            raise ApplicationDistributionError("candidate belongs to another Application")
        plan = self.releases.get_release(candidate.project_id, candidate.release_digest)
        channels = self.applications.store.get_channels(application_id).get("channels") or {}
        stable = (
            self.applications.store.get_release(application_id, str(channels["stable"])).project_release
            if channels.get("stable")
            else None
        )
        if stable is not None and stable.release_digest == candidate.release_digest:
            operation = self._load_operation(candidate_id)
            stable_state = (
                (operation.get("publications") or {}).get("stable")
                if isinstance(operation, Mapping)
                else None
            )
            if allow_completed_stable and isinstance(stable_state, Mapping) and stable_state.get("status") == "completed":
                return candidate, plan
        assert_promotable(candidate, plan.release, stable)
        return candidate, plan

    def _provenance(self, plan: ReleasePlan) -> tuple[dict[str, Any], ReleaseAttestationSet]:
        receipt = dict(self.admission.verify_release_plan(plan))
        if receipt.get("status") != "verified":
            raise ApplicationDistributionError("release provenance admission did not verify")
        if self.admission.release_sets is None:
            raise ApplicationDistributionError("an exact release attestation binding is required")
        digest = str(plan.release.release_digest or plan.release.computed_digest())
        try:
            raw = self.admission.release_sets.get_release_attestation_set(
                plan.release.project_id,
                digest,
            )
            binding = (
                raw if isinstance(raw, ReleaseAttestationSet) else ReleaseAttestationSet.from_mapping(raw)
            ).validate_plan(plan)
        except Exception as exc:
            raise ApplicationDistributionError(f"release attestation binding is invalid: {exc}") from exc
        return receipt, binding

    def _new_operation(
        self,
        application_id: str,
        candidate: CandidateRecord,
        binding: ReleaseAttestationSet,
    ) -> dict[str, Any]:
        return {
            "schema": "adaos.application.distribution_operation.v1",
            "application_id": application_id,
            "candidate_id": candidate.candidate_id,
            "release_digest": candidate.release_digest,
            "attestation_set_digest": binding.attestation_set_digest,
            "upload": {"status": "ready"},
            "publications": {},
            "created_at": self.clock(),
            "updated_at": self.clock(),
        }

    def _operation(
        self,
        application_id: str,
        candidate: CandidateRecord,
        binding: ReleaseAttestationSet,
    ) -> dict[str, Any]:
        operation = self._load_operation(candidate.candidate_id)
        if operation is None:
            operation = self._new_operation(application_id, candidate, binding)
            self._save_operation(operation)
        if (
            operation.get("application_id") != application_id
            or operation.get("release_digest") != candidate.release_digest
            or operation.get("attestation_set_digest") != binding.attestation_set_digest
        ):
            raise ApplicationDistributionError("distribution operation identity conflict")
        return operation

    def _remote_release(self, plan: ReleasePlan) -> ReleasePlan | None:
        digest = str(plan.release.release_digest)
        try:
            observed = self.remote.get_release(plan.release.project_id, digest)
        except Exception as exc:
            if _missing(exc):
                return None
            raise
        if observed != plan:
            raise ApplicationDistributionError("remote immutable release differs from accepted Trial")
        return observed

    def _ensure_uploaded(self, operation: dict[str, Any], plan: ReleasePlan) -> None:
        upload = operation["upload"]
        if upload.get("status") == "completed":
            if self._remote_release(plan) is None:
                raise ApplicationDistributionError("completed upload is missing remotely")
            return
        if upload.get("status") in {"dispatching", "unknown"}:
            raise DistributionOutcomeUnknown(
                "release upload outcome is unknown; reconcile before retry"
            )
        observed = self._remote_release(plan)
        if observed is not None:
            upload.update({"status": "completed", "completed_via": "observation", "updated_at": self.clock()})
            self._save_operation(operation)
            return
        archives = {package.digest: self.packages.read(package.digest) for package in plan.packages}
        upload.update({"status": "dispatching", "updated_at": self.clock()})
        self._save_operation(operation)
        try:
            self.remote.put_release(plan, archives)
        except Exception as exc:
            upload.update(
                {
                    "status": "unknown",
                    "last_error": f"{type(exc).__name__}: {exc}"[:1024],
                    "updated_at": self.clock(),
                }
            )
            self._save_operation(operation)
            raise DistributionOutcomeUnknown(
                "release upload outcome is unknown; reconcile before retry"
            ) from exc
        if self._remote_release(plan) is None:
            raise ApplicationDistributionError("remote did not expose acknowledged release")
        upload.update({"status": "completed", "completed_via": "write_acknowledgement", "updated_at": self.clock()})
        self._save_operation(operation)

    def _register_release(
        self,
        application_id: str,
        publisher_ref: str,
        candidate: CandidateRecord,
        plan: ReleasePlan,
        binding: ReleaseAttestationSet,
        addresses_report_ids: tuple[str, ...],
    ) -> ApplicationRelease:
        try:
            existing = self.applications.store.get_release(application_id, candidate.release_digest)
        except FileNotFoundError:
            existing = None
        if existing is not None:
            if (
                existing.publisher_ref != publisher_ref
                or existing.accepted_candidate_id != candidate.candidate_id
                or existing.project_release != plan.release
                or existing.addresses_report_ids != tuple(sorted(set(addresses_report_ids)))
            ):
                raise ApplicationDistributionError("immutable ApplicationRelease differs from accepted Trial")
            return existing
        accepted = [item.to_dict() for item in candidate.trials if item.status == "accepted"]
        provenance_refs = tuple(
            sorted(
                {
                    str(binding.attestation_set_digest),
                    *(str(item.attestation_digest) for item in binding.attestations),
                }
            )
        )
        release = ApplicationRelease(
            application_id=application_id,
            publisher_ref=publisher_ref,
            project_release=plan.release,
            accepted_candidate_id=candidate.candidate_id,
            acceptance_evidence=tuple([*candidate.validation_evidence, *accepted]),
            provenance_refs=provenance_refs,
            addresses_report_ids=addresses_report_ids,
            lifecycle="trial",
            published_at=self.clock(),
        )
        return self.applications.register_release(release)

    def _channel_or_none(self, project_id: str, channel: str) -> ChannelPointer | None:
        try:
            return self.remote.get_channel(project_id, channel)
        except Exception as exc:
            if _missing(exc):
                return None
            raise

    def _move_channel(
        self,
        operation: dict[str, Any],
        *,
        application_id: str,
        publisher_ref: str,
        plan: ReleasePlan,
        channel: str,
        expected_release_digest: str | None,
    ) -> dict[str, Any]:
        publications = operation["publications"]
        state = publications.get(channel)
        if isinstance(state, Mapping) and state.get("status") == "completed":
            pointer = self._channel_or_none(plan.release.project_id, channel)
            if pointer is None or pointer.release_digest != plan.release.release_digest:
                raise ApplicationDistributionError("completed channel publication differs remotely")
        else:
            if isinstance(state, Mapping) and state.get("status") in {"dispatching", "unknown"}:
                raise DistributionOutcomeUnknown(
                    f"{channel} channel outcome is unknown; reconcile before retry"
                )
            observed = self._channel_or_none(plan.release.project_id, channel)
            if observed is not None and observed.release_digest == plan.release.release_digest:
                state = {"status": "completed", "completed_via": "observation", "pointer": observed.to_dict()}
            else:
                state = {
                    "status": "dispatching",
                    "expected_release_digest": expected_release_digest,
                    "updated_at": self.clock(),
                }
                publications[channel] = state
                self._save_operation(operation)
                try:
                    pointer = self.remote.set_channel(
                        plan,
                        channel,
                        expected_release_digest=expected_release_digest,
                    )
                except Exception as exc:
                    state.update(
                        {
                            "status": "unknown",
                            "last_error": f"{type(exc).__name__}: {exc}"[:1024],
                            "updated_at": self.clock(),
                        }
                    )
                    self._save_operation(operation)
                    raise DistributionOutcomeUnknown(
                        f"{channel} channel outcome is unknown; reconcile before retry"
                    ) from exc
                if pointer.release_digest != plan.release.release_digest:
                    raise ApplicationDistributionError("remote channel acknowledged another release")
                state = {
                    "status": "completed",
                    "completed_via": "write_acknowledgement",
                    "pointer": pointer.to_dict(),
                    "updated_at": self.clock(),
                }
            publications[channel] = state
            self._save_operation(operation)
        local_channels = self.applications.store.get_channels(application_id).get("channels") or {}
        observed_local = local_channels.get(channel)
        if observed_local != plan.release.release_digest:
            if observed_local != expected_release_digest:
                raise ApplicationDistributionError("local and Root channel generations diverged")
            self.applications.move_channel(
                application_id,
                channel,
                str(plan.release.release_digest),
                publisher_ref=publisher_ref,
                expected_release_digest=expected_release_digest,
            )
        return dict(state)

    def _clear_prerelease(
        self,
        operation: dict[str, Any],
        *,
        project_id: str,
        release_digest: str,
    ) -> dict[str, Any]:
        retirement = operation.get("prerelease_retirement")
        if isinstance(retirement, Mapping) and retirement.get("status") == "completed":
            if self._channel_or_none(project_id, "prerelease") is not None:
                raise ApplicationDistributionError("retired prerelease channel reappeared")
            return dict(retirement)
        if isinstance(retirement, Mapping) and retirement.get("status") in {"dispatching", "unknown"}:
            raise DistributionOutcomeUnknown(
                "prerelease retirement outcome is unknown; reconcile before retry"
            )
        observed = self._channel_or_none(project_id, "prerelease")
        if observed is None:
            retirement = {"status": "completed", "completed_via": "observation", "updated_at": self.clock()}
        else:
            if observed.release_digest != release_digest:
                raise ApplicationDistributionError("prerelease moved before stable retirement")
            retirement = {"status": "dispatching", "release_digest": release_digest, "updated_at": self.clock()}
            operation["prerelease_retirement"] = retirement
            self._save_operation(operation)
            try:
                cleared = self.remote.clear_channel(
                    project_id,
                    "prerelease",
                    expected_release_digest=release_digest,
                )
            except Exception as exc:
                retirement.update(
                    {
                        "status": "unknown",
                        "last_error": f"{type(exc).__name__}: {exc}"[:1024],
                        "updated_at": self.clock(),
                    }
                )
                self._save_operation(operation)
                raise DistributionOutcomeUnknown(
                    "prerelease retirement outcome is unknown; reconcile before retry"
                ) from exc
            retirement = {
                "status": "completed",
                "completed_via": "write_acknowledgement",
                "pointer": cleared.to_dict(),
                "updated_at": self.clock(),
            }
        operation["prerelease_retirement"] = retirement
        self._save_operation(operation)
        return dict(retirement)

    def publish_trial(
        self,
        application_id: str,
        candidate_id: str,
        *,
        publisher_ref: str,
        mode: str,
        expected_prerelease_digest: str | None = None,
        addresses_report_ids: tuple[str, ...] = (),
    ) -> dict[str, Any]:
        if mode not in {"link_only", "prerelease"}:
            raise ApplicationDistributionError("Trial publication mode must be link_only or prerelease")
        with mutation_lock(self.applications.store.lock_path, timeout_s=30.0):
            application = self.applications.store.get_application(application_id)
            channels = self.applications.store.get_channels(application_id).get("channels") or {}
            if mode == "link_only" and channels.get("stable") and application.visibility == "public":
                raise ApplicationDistributionError("public post-stable Trial must use prerelease")
            if mode == "prerelease":
                if application.visibility != "public":
                    raise ApplicationDistributionError("private or link Application Trial requires a capability link")
                if not channels.get("stable"):
                    raise ApplicationDistributionError("public prerelease requires the first stable release")
            candidate, plan = self._candidate_plan(application_id, candidate_id, publisher_ref)
            _, binding = self._provenance(plan)
            operation = self._operation(application_id, candidate, binding)
            self._ensure_uploaded(operation, plan)
            release = self._register_release(
                application_id, publisher_ref, candidate, plan, binding,
                tuple(sorted(set(addresses_report_ids))),
            )
            publication: dict[str, Any] = {"mode": mode, "release": release.to_dict()}
            if mode == "prerelease":
                publication["channel"] = self._move_channel(
                    operation,
                    application_id=application_id,
                    publisher_ref=publisher_ref,
                    plan=plan,
                    channel="prerelease",
                    expected_release_digest=expected_prerelease_digest,
                )
            operation["trial_publication"] = {
                "status": "completed",
                "mode": mode,
                "completed_at": self.clock(),
            }
            operation["updated_at"] = self.clock()
            self._save_operation(operation)
            return {**publication, "operation": operation}

    def promote_stable(
        self,
        application_id: str,
        candidate_id: str,
        *,
        publisher_ref: str,
        expected_stable_digest: str | None,
    ) -> dict[str, Any]:
        with mutation_lock(self.applications.store.lock_path, timeout_s=30.0):
            candidate, plan = self._candidate_plan(
                application_id,
                candidate_id,
                publisher_ref,
                allow_completed_stable=True,
            )
            _, binding = self._provenance(plan)
            operation = self._operation(application_id, candidate, binding)
            trial = operation.get("trial_publication")
            channels = self.applications.store.get_channels(application_id).get("channels") or {}
            completed = operation.get("stable_promotion")
            if isinstance(completed, Mapping) and completed.get("status") == "completed":
                stable_state = (operation.get("publications") or {}).get("stable")
                if not isinstance(stable_state, Mapping):
                    raise ApplicationDistributionError("stable promotion receipt has no channel state")
                return {
                    "release": self.applications.store.get_release(
                        application_id, candidate.release_digest
                    ).to_dict(),
                    "channel": dict(stable_state),
                    "operation": operation,
                }
            if not isinstance(trial, Mapping) or trial.get("status") != "completed":
                raise ApplicationDistributionError("stable promotion requires a completed Trial publication")
            stable_state = (operation.get("publications") or {}).get("stable")
            stable_already_moved = (
                channels.get("stable") == candidate.release_digest
                and isinstance(stable_state, Mapping)
                and stable_state.get("status") == "completed"
            )
            if not stable_already_moved:
                if channels.get("stable") is None and trial.get("mode") != "link_only":
                    raise ApplicationDistributionError("first stable must bootstrap from an exact link-only Trial")
                if channels.get("stable") is not None:
                    if trial.get("mode") != "prerelease" or channels.get("prerelease") != candidate.release_digest:
                        raise ApplicationDistributionError("later stable must promote the exact current prerelease")
            retire_prerelease = trial.get("mode") == "prerelease"
            self._ensure_uploaded(operation, plan)
            try:
                existing_release = self.applications.store.get_release(application_id, candidate.release_digest)
                addresses_report_ids = existing_release.addresses_report_ids
            except FileNotFoundError:
                addresses_report_ids = ()
            release = self._register_release(
                application_id, publisher_ref, candidate, plan, binding,
                addresses_report_ids,
            )
            channel = self._move_channel(
                operation,
                application_id=application_id,
                publisher_ref=publisher_ref,
                plan=plan,
                channel="stable",
                expected_release_digest=expected_stable_digest,
            )
            if retire_prerelease:
                self._clear_prerelease(
                    operation,
                    project_id=plan.release.project_id,
                    release_digest=candidate.release_digest,
                )
            operation["stable_promotion"] = {
                "status": "completed",
                "release_digest": candidate.release_digest,
                "completed_at": self.clock(),
            }
            operation["updated_at"] = self.clock()
            self._save_operation(operation)
            return {"release": release.to_dict(), "channel": channel, "operation": operation}

    def reconcile(self, candidate_id: str) -> dict[str, Any]:
        with mutation_lock(self.applications.store.lock_path, timeout_s=30.0):
            operation = self._load_operation(candidate_id)
            if operation is None:
                raise FileNotFoundError(f"distribution operation not found: {candidate_id}")
            plan = self.releases.get_release(
                self.candidates.load(candidate_id).project_id,
                str(operation["release_digest"]),
            )
            upload = operation.get("upload")
            if isinstance(upload, dict) and upload.get("status") in {"dispatching", "unknown"}:
                observed = self._remote_release(plan)
                if observed is None:
                    upload.update({"status": "ready", "reconciled_as": "not_applied", "updated_at": self.clock()})
                else:
                    for package in plan.packages:
                        archive = self.remote.fetch_package(package)
                        self.packages.put(archive, expected_digest=package.digest)
                    upload.update({"status": "completed", "completed_via": "reconciliation", "updated_at": self.clock()})
            publications = operation.get("publications")
            if isinstance(publications, dict):
                for channel, state in publications.items():
                    if not isinstance(state, dict) or state.get("status") not in {"dispatching", "unknown"}:
                        continue
                    pointer = self._channel_or_none(plan.release.project_id, channel)
                    if pointer is None:
                        state.update({"status": "ready", "reconciled_as": "not_applied", "updated_at": self.clock()})
                    elif pointer.release_digest == plan.release.release_digest:
                        state.update(
                            {
                                "status": "completed",
                                "completed_via": "reconciliation",
                                "pointer": pointer.to_dict(),
                                "updated_at": self.clock(),
                            }
                        )
                    else:
                        raise ApplicationDistributionError("remote channel differs from distribution intent")
            retirement = operation.get("prerelease_retirement")
            if isinstance(retirement, dict) and retirement.get("status") in {"dispatching", "unknown"}:
                pointer = self._channel_or_none(plan.release.project_id, "prerelease")
                if pointer is None:
                    retirement.update(
                        {"status": "completed", "completed_via": "reconciliation", "updated_at": self.clock()}
                    )
                elif pointer.release_digest == plan.release.release_digest:
                    retirement.update({"status": "ready", "reconciled_as": "not_applied", "updated_at": self.clock()})
                else:
                    raise ApplicationDistributionError("prerelease differs from retirement intent")
            operation["reconciled_at"] = self.clock()
            operation["updated_at"] = self.clock()
            self._save_operation(operation)
            return operation


__all__ = [
    "ApplicationDistributionError",
    "ApplicationDistributionService",
    "DistributionOutcomeUnknown",
    "DistributionRemote",
    "ProvenanceAdmission",
]
