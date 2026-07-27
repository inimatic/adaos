from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Mapping, Protocol

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactReleaseContractError,
    ProjectRelease,
    StableSubscription,
    WorkspaceLock,
    canonical_payload_digest,
    sha256_digest,
)
from adaos.services.artifact_pipeline.activation import WorkspaceActivationManager
from adaos.services.artifact_pipeline.candidates import CandidateRecord, CandidateStore
from adaos.services.artifact_pipeline.channels import ChannelPointer, SubscriptionStore
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


REMOTE_REGISTRY_RECOVERY_PLAN_SCHEMA = "adaos.artifact.remote_registry_recovery_plan.v1"
REMOTE_REGISTRY_RECOVERY_OPERATION_SCHEMA = (
    "adaos.artifact.remote_registry_recovery_operation.v1"
)
REMOTE_REGISTRY_REVALIDATION_SCHEMA = "adaos.artifact.remote_registry_revalidation.v1"
ArtifactKind = Literal["skill", "scenario"]


class RemoteRegistryRecoveryRemote(Protocol):
    def get_channel(self, project_id: str, channel: str = "stable") -> ChannelPointer: ...

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...

    def fetch_package(self, package: ArtifactPackageRef) -> bytes: ...

    def put_package(self, package: ArtifactPackageRef, archive_bytes: bytes) -> None: ...

    def put_release_record(self, plan: ReleasePlan) -> None: ...

    def set_channel(
        self,
        plan: ReleasePlan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ) -> ChannelPointer: ...

    def tree_revision(self, source_ref) -> str: ...


class RemoteRegistryRecoveryError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class _RecoveryEvidence:
    release_plan: ReleasePlan
    subscription: StableSubscription
    workspace_lock: WorkspaceLock
    candidate: CandidateRecord
    candidate_record_digest: str
    legacy_candidate: bool
    trial_operation_digest: str
    revalidation_receipt_digest: str | None
    package_archives: Mapping[str, bytes]
    local_evidence_digest: str


@dataclass(frozen=True, slots=True)
class RemoteRegistryRecoveryPlan:
    project_id: str
    kind: ArtifactKind
    channel: str
    release_plan: ReleasePlan
    subscription: StableSubscription
    workspace_lock_digest: str
    candidate: CandidateRecord
    candidate_record_digest: str
    legacy_candidate: bool
    trial_operation_digest: str
    revalidation_receipt_digest: str | None
    local_evidence_digest: str
    source_attestations: tuple[Mapping[str, Any], ...]
    remote_packages_present: tuple[str, ...]
    remote_release_present: bool
    remote_channel: ChannelPointer | None
    actions: tuple[str, ...]
    action: str
    allowed: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": REMOTE_REGISTRY_RECOVERY_PLAN_SCHEMA,
            "project_id": self.project_id,
            "kind": self.kind,
            "channel": self.channel,
            "release_plan": {
                "schema": "adaos.artifact.release_plan.v1",
                **self.release_plan.explain(),
            },
            "subscription": self.subscription.to_dict(),
            "workspace_lock_digest": self.workspace_lock_digest,
            "candidate": self.candidate.to_dict(),
            "candidate_record_digest": self.candidate_record_digest,
            "legacy_candidate": self.legacy_candidate,
            "trial_operation_digest": self.trial_operation_digest,
            "revalidation_receipt_digest": self.revalidation_receipt_digest,
            "local_evidence_digest": self.local_evidence_digest,
            "source_attestations": [dict(item) for item in self.source_attestations],
            "remote_packages_present": list(self.remote_packages_present),
            "remote_release_present": self.remote_release_present,
            "remote_channel": self.remote_channel.to_dict() if self.remote_channel else None,
            "actions": list(self.actions),
            "action": self.action,
            "allowed": self.allowed,
            "warnings": list(self.warnings),
        }
        payload["plan_digest"] = canonical_payload_digest(payload)
        return payload

    @property
    def plan_digest(self) -> str:
        return str(self.to_dict()["plan_digest"])


class RemoteRegistryRecoveryManager:
    """Restore missing remote immutable state from strongly bound local receipts."""

    def __init__(
        self,
        *,
        state_root: Path,
        workspace_root: Path,
        remote: RemoteRegistryRecoveryRemote,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.remote = remote
        self.package_store = ContentAddressedPackageStore(self.state_root / "packages")
        self.candidates = CandidateStore(self.state_root / "candidates")
        self.subscriptions = SubscriptionStore(
            self.workspace_root / ".adaos" / "subscriptions.json"
        )
        self.workspace_writer_lock = (
            self.workspace_root / ".adaos" / ".workspace-writer.lock"
        )

    def plan(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        channel: str = "stable",
    ) -> RemoteRegistryRecoveryPlan:
        evidence = self._load_local_evidence(project_id, kind=kind, channel=channel)
        return self._build_plan(evidence, kind=kind, channel=channel)

    def revalidate(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        channel: str = "stable",
    ) -> dict[str, Any]:
        """Run one isolated current-contract trial for a legacy accepted release."""

        with mutation_lock(self.workspace_writer_lock):
            evidence = self._load_local_evidence(
                project_id,
                kind=kind,
                channel=channel,
                require_revalidation=False,
            )
            if not evidence.legacy_candidate:
                return {
                    "schema": REMOTE_REGISTRY_REVALIDATION_SCHEMA,
                    "status": "not_required",
                    "project_id": project_id,
                    "release_digest": evidence.release_plan.release.release_digest,
                }
            release_digest = str(evidence.release_plan.release.release_digest)
            token = release_digest.split(":", 1)[1]
            revalidation_root = self.state_root / "remote-registry-revalidations" / token
            manager = WorkspaceActivationManager(
                workspace_root=revalidation_root / "workspace",
                package_store=self.package_store,
                state_root=revalidation_root / "state",
            )
            activation = manager.activate(
                evidence.release_plan,
                idempotency_key=f"remote-registry-revalidation:{release_digest}",
                audience="owner",
                data_mode="empty",
                reload_policy={
                    "mode": "skip",
                    "approved_by": "artifact_pipeline.remote_registry_revalidation",
                    "reason": "isolated recovery revalidation Workspace has no live runtime",
                },
                health_check=lambda lock: {
                    "status": "passed",
                    "check": "verified_package_materialization",
                    "lock_digest": lock.to_dict()["lock_digest"],
                },
            )
            operation_path = manager.operation_path(activation.operation_id)
            operation_bytes = operation_path.read_bytes()
            operation = json.loads(operation_bytes.decode("utf-8"))
            if (
                not isinstance(operation, Mapping)
                or operation.get("status") != "completed"
                or operation.get("release_digest") != release_digest
            ):
                raise RemoteRegistryRecoveryError(
                    "isolated recovery revalidation did not complete"
                )
            receipt = {
                "schema": REMOTE_REGISTRY_REVALIDATION_SCHEMA,
                "status": "completed",
                "project_id": project_id,
                "kind": kind,
                "channel": channel,
                "release_digest": release_digest,
                "candidate_digest": evidence.candidate.digest,
                "candidate_record_digest": evidence.candidate_record_digest,
                "legacy_candidate": True,
                "activation_operation_id": activation.operation_id,
                "activation_operation_digest": sha256_digest(operation_bytes),
                "lock_digest": activation.workspace_lock.to_dict()["lock_digest"],
                "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
            }
            atomic_write_json(self.revalidation_receipt_path(release_digest), receipt)
            return receipt

    def apply(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        reviewed_plan_digest: str,
        channel: str = "stable",
    ) -> dict[str, Any]:
        reviewed = self._validate_digest(reviewed_plan_digest)
        operation_path = self.operation_path(reviewed)
        with mutation_lock(self.operation_lock_path(reviewed)):
            operation = self._load_operation(operation_path, reviewed)
            if operation is not None and operation.get("status") == "completed":
                return dict(operation)

            current = self.plan(project_id, kind=kind, channel=channel)
            if operation is None:
                if current.plan_digest != reviewed:
                    raise RemoteRegistryRecoveryError(
                        "remote registry recovery plan changed after review"
                    )
                if not current.allowed:
                    raise RemoteRegistryRecoveryError(
                        "remote registry recovery is blocked: "
                        + ", ".join(current.warnings)
                    )
                operation = {
                    "schema": REMOTE_REGISTRY_RECOVERY_OPERATION_SCHEMA,
                    "status": "prepared",
                    "phase": "prepared",
                    "plan_digest": reviewed,
                    "plan": current.to_dict(),
                    "receipts": {},
                }
                atomic_write_json(operation_path, operation)
            else:
                self._validate_progress(operation, current)

            with mutation_lock(self.workspace_writer_lock):
                local = self._load_local_evidence(project_id, kind=kind, channel=channel)
                plan_payload = operation.get("plan")
                if not isinstance(plan_payload, Mapping):
                    raise RemoteRegistryRecoveryError("recovery operation has no plan")
                if local.local_evidence_digest != plan_payload.get("local_evidence_digest"):
                    raise RemoteRegistryRecoveryError(
                        "local recovery evidence changed after review"
                    )
                try:
                    self._restore_remote(operation_path, operation, current, local)
                except Exception as exc:
                    operation["status"] = "paused"
                    operation["error"] = {
                        "type": type(exc).__name__,
                        "message": str(exc),
                    }
                    atomic_write_json(operation_path, operation)
                    if isinstance(exc, RemoteRegistryRecoveryError):
                        raise
                    raise RemoteRegistryRecoveryError(str(exc)) from exc

            operation["status"] = "completed"
            operation["phase"] = "completed"
            operation.pop("error", None)
            operation["result"] = {
                "status": "remote_registry_restored",
                "release_digest": current.release_plan.release.release_digest,
                "channel": channel,
            }
            atomic_write_json(operation_path, operation)
            return dict(operation)

    def operation_path(self, plan_digest: str) -> Path:
        reviewed = self._validate_digest(plan_digest)
        return (
            self.state_root
            / "remote-registry-recoveries"
            / f"{reviewed.split(':', 1)[1]}.json"
        )

    def operation_lock_path(self, plan_digest: str) -> Path:
        return self.operation_path(plan_digest).with_suffix(".lock")

    def revalidation_receipt_path(self, release_digest: str) -> Path:
        token = self._validate_digest(release_digest).split(":", 1)[1]
        return self.state_root / "remote-registry-revalidation-receipts" / f"{token}.json"

    def _build_plan(
        self,
        evidence: _RecoveryEvidence,
        *,
        kind: ArtifactKind,
        channel: str,
    ) -> RemoteRegistryRecoveryPlan:
        release = evidence.release_plan.release
        source_attestations: list[Mapping[str, Any]] = []
        for package in evidence.release_plan.packages:
            tree = str(self.remote.tree_revision(package.source_ref) or "").strip().lower()
            if len(tree) not in {40, 64} or any(
                char not in "0123456789abcdef" for char in tree
            ):
                raise RemoteRegistryRecoveryError(
                    f"Forge returned an invalid tree for {package.key}"
                )
            if (
                package.key == f"{kind}:{release.project_id}"
                and evidence.candidate.source_tree != tree
            ):
                raise RemoteRegistryRecoveryError(
                    "candidate source tree no longer matches immutable Forge source"
                )
            source_attestations.append(
                {
                    "package": package.key,
                    "package_digest": package.digest,
                    "source_ref": package.source_ref.to_dict(),
                    "source_tree": tree,
                    "status": "resolved_immutable",
                }
            )

        present_packages: list[str] = []
        for package in evidence.release_plan.packages:
            try:
                self.remote.fetch_package(package)
            except Exception as exc:
                if not self._is_not_found(exc):
                    raise
            else:
                present_packages.append(package.digest)

        release_present = False
        try:
            remote_release = self.remote.get_release(
                release.project_id,
                str(release.release_digest),
            )
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
        else:
            if remote_release.explain() != evidence.release_plan.explain():
                raise RemoteRegistryRecoveryError(
                    "remote release digest resolves to different release content"
                )
            release_present = True

        remote_channel: ChannelPointer | None = None
        try:
            remote_channel = self.remote.get_channel(release.project_id, channel)
        except Exception as exc:
            if not self._is_not_found(exc):
                raise

        warnings: list[str] = []
        allowed = True
        release_digest = str(release.release_digest)
        if remote_channel is not None and remote_channel.release_digest != release_digest:
            allowed = False
            warnings.append("remote_channel_conflicts_with_local_recovery_release")

        actions = [
            f"put_package:{package.digest}"
            for package in evidence.release_plan.packages
            if package.digest not in present_packages
        ]
        if not release_present:
            actions.append(f"put_release:{release_digest}")
        if remote_channel is None:
            actions.append(f"create_channel:{channel}")
        action = "noop" if not actions else "restore_remote_registry"
        if not allowed:
            action = "blocked"
        return RemoteRegistryRecoveryPlan(
            project_id=release.project_id,
            kind=kind,
            channel=channel,
            release_plan=evidence.release_plan,
            subscription=evidence.subscription,
            workspace_lock_digest=str(evidence.workspace_lock.to_dict()["lock_digest"]),
            candidate=evidence.candidate,
            candidate_record_digest=evidence.candidate_record_digest,
            legacy_candidate=evidence.legacy_candidate,
            trial_operation_digest=evidence.trial_operation_digest,
            revalidation_receipt_digest=evidence.revalidation_receipt_digest,
            local_evidence_digest=evidence.local_evidence_digest,
            source_attestations=tuple(source_attestations),
            remote_packages_present=tuple(sorted(present_packages)),
            remote_release_present=release_present,
            remote_channel=remote_channel,
            actions=tuple(actions),
            action=action,
            allowed=allowed,
            warnings=tuple(warnings),
        )

    def _load_local_evidence(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        channel: str,
        require_revalidation: bool = True,
    ) -> _RecoveryEvidence:
        try:
            subscription = self.subscriptions.load()[project_id]
        except KeyError as exc:
            raise RemoteRegistryRecoveryError(
                f"project has no installed subscription: {project_id}"
            ) from exc
        if subscription.channel != channel:
            raise RemoteRegistryRecoveryError(
                "installed subscription channel does not match recovery channel"
            )
        lock = self._load_workspace_lock()
        slot = next(
            (
                item
                for item in lock.slots
                if item.project_id == project_id
                and item.release == subscription.installed_release
                and item.release_digest == subscription.installed_digest
            ),
            None,
        )
        if slot is None:
            raise RemoteRegistryRecoveryError(
                "installed subscription does not match active WorkspaceLock"
            )

        release_plan = self._load_release_plan(project_id, subscription.installed_digest)
        release = release_plan.release
        if (
            f"{release.project_id}@{release.version}" != subscription.installed_release
            or release.release_digest != subscription.installed_digest
        ):
            raise RemoteRegistryRecoveryError(
                "cached release plan does not match installed subscription"
            )
        main = next(
            (
                item
                for item in release_plan.packages
                if item.key == f"{kind}:{project_id}"
            ),
            None,
        )
        if main is None:
            raise RemoteRegistryRecoveryError(
                f"cached release plan has no {kind}:{project_id} package"
            )
        active = {item.key: item for item in lock.components}
        if any(active.get(item.key) != item for item in release_plan.packages):
            raise RemoteRegistryRecoveryError(
                "cached release plan does not match active WorkspaceLock packages"
            )
        self._validate_workspace_release_receipt(release)

        package_archives: dict[str, bytes] = {}
        for package in release_plan.packages:
            data, verified = self.package_store.read_verified(package.digest)
            if verified.ref != package:
                raise RemoteRegistryRecoveryError(
                    f"local package does not match release plan: {package.key}"
                )
            package_archives[package.digest] = data

        candidate, candidate_payload, candidate_record_digest, legacy_candidate = (
            self._load_candidate(project_id, subscription.installed_digest)
        )
        trial_digest = self._validate_accepted_trial(
            candidate,
            candidate_payload,
            release_plan,
        )
        revalidation_receipt_digest = self._load_revalidation_receipt(
            project_id,
            kind=kind,
            channel=channel,
            release_plan=release_plan,
            candidate=candidate,
            candidate_record_digest=candidate_record_digest,
            required=legacy_candidate and require_revalidation,
        )
        evidence_payload = {
            "project_id": project_id,
            "kind": kind,
            "channel": channel,
            "subscription": subscription.to_dict(),
            "workspace_lock_digest": lock.to_dict()["lock_digest"],
            "release_plan": {
                "schema": "adaos.artifact.release_plan.v1",
                **release_plan.explain(),
            },
            "candidate": candidate.to_dict(),
            "candidate_record_digest": candidate_record_digest,
            "legacy_candidate": legacy_candidate,
            "trial_operation_digest": trial_digest,
            "revalidation_receipt_digest": revalidation_receipt_digest,
            "packages": [
                {
                    "ref": package.to_dict(),
                    "archive_digest": sha256_digest(package_archives[package.digest]),
                    "archive_size": len(package_archives[package.digest]),
                }
                for package in release_plan.packages
            ],
        }
        return _RecoveryEvidence(
            release_plan=release_plan,
            subscription=subscription,
            workspace_lock=lock,
            candidate=candidate,
            candidate_record_digest=candidate_record_digest,
            legacy_candidate=legacy_candidate,
            trial_operation_digest=trial_digest,
            revalidation_receipt_digest=revalidation_receipt_digest,
            package_archives=package_archives,
            local_evidence_digest=canonical_payload_digest(evidence_payload),
        )

    def _load_workspace_lock(self) -> WorkspaceLock:
        path = self.workspace_root / ".adaos" / "workspace.lock.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise RemoteRegistryRecoveryError("WorkspaceLock must contain an object")
            return WorkspaceLock.from_mapping(payload)
        except RemoteRegistryRecoveryError:
            raise
        except (OSError, json.JSONDecodeError, ArtifactReleaseContractError) as exc:
            raise RemoteRegistryRecoveryError(f"cannot trust WorkspaceLock: {exc}") from exc

    def _load_release_plan(self, project_id: str, release_digest: str) -> ReleasePlan:
        token = self._validate_digest(release_digest).split(":", 1)[1]
        path = (
            self.state_root
            / "release-cache"
            / "projects"
            / project_id
            / "releases"
            / f"{token}.json"
        )
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise RemoteRegistryRecoveryError("release plan must contain an object")
            plan = ReleasePlan.from_mapping(payload)
        except RemoteRegistryRecoveryError:
            raise
        except Exception as exc:
            raise RemoteRegistryRecoveryError(
                f"cannot trust cached release plan: {exc}"
            ) from exc
        actual = plan.release.release_digest or plan.release.computed_digest()
        if plan.release.project_id != project_id or actual != release_digest:
            raise RemoteRegistryRecoveryError(
                "cached release plan identity does not match requested recovery"
            )
        return plan

    def _validate_workspace_release_receipt(self, release: ProjectRelease) -> None:
        token = str(release.release_digest).split(":", 1)[1]
        path = self.workspace_root / ".adaos" / "releases" / f"{token}.json"
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, Mapping):
                raise RemoteRegistryRecoveryError("release receipt must contain an object")
            receipt = ProjectRelease.from_mapping(payload)
        except RemoteRegistryRecoveryError:
            raise
        except Exception as exc:
            raise RemoteRegistryRecoveryError(
                f"cannot trust Workspace release receipt: {exc}"
            ) from exc
        if receipt != release:
            raise RemoteRegistryRecoveryError(
                "Workspace release receipt differs from cached release plan"
            )

    def _load_candidate(
        self,
        project_id: str,
        release_digest: str,
    ) -> tuple[CandidateRecord, Mapping[str, Any], str, bool]:
        matches: list[tuple[CandidateRecord, Mapping[str, Any], str, bool]] = []
        if self.candidates.root.is_dir():
            for path in sorted(self.candidates.root.glob("*.json")):
                try:
                    raw = path.read_bytes()
                    payload = json.loads(raw.decode("utf-8"))
                    if not isinstance(payload, Mapping):
                        raise RemoteRegistryRecoveryError(
                            f"candidate record is malformed: {path}"
                        )
                    if (
                        payload.get("project_id") != project_id
                        or payload.get("release_digest") != release_digest
                    ):
                        continue
                    legacy_candidate = False
                    try:
                        candidate = CandidateRecord.from_mapping(payload)
                    except Exception:
                        adapted = dict(payload)
                        adapted["trials"] = []
                        candidate = CandidateRecord.from_mapping(adapted)
                        legacy_candidate = True
                except RemoteRegistryRecoveryError:
                    raise
                except Exception as exc:
                    raise RemoteRegistryRecoveryError(
                        f"cannot trust candidate record {path}: {exc}"
                    ) from exc
                if (
                    candidate.project_id == project_id
                    and candidate.release_digest == release_digest
                ):
                    matches.append(
                        (candidate, payload, sha256_digest(raw), legacy_candidate)
                    )
        if len(matches) != 1:
            raise RemoteRegistryRecoveryError(
                "recovery requires exactly one candidate for the installed release"
            )
        candidate, payload, record_digest, legacy_candidate = matches[0]
        if candidate.status != "accepted":
            raise RemoteRegistryRecoveryError(
                "recovery candidate does not have an accepted trial decision"
            )
        return candidate, payload, record_digest, legacy_candidate

    def _validate_accepted_trial(
        self,
        candidate: CandidateRecord,
        candidate_payload: Mapping[str, Any],
        release_plan: ReleasePlan,
    ) -> str:
        raw_trials = candidate_payload.get("trials")
        if not isinstance(raw_trials, list) or any(
            not isinstance(item, Mapping) for item in raw_trials
        ):
            raise RemoteRegistryRecoveryError("candidate trial evidence is malformed")
        accepted_digests = {
            str(item.get("lock_digest") or "")
            for item in raw_trials
            if item.get("status") == "accepted"
            and item.get("candidate_digest") == candidate.digest
        }
        if not accepted_digests:
            raise RemoteRegistryRecoveryError("candidate has no accepted trial")
        operation_root = (
            self.state_root
            / "trials"
            / candidate.candidate_id
            / "state"
            / "artifact_pipeline"
            / "operations"
        )
        matches: list[tuple[str, Mapping[str, Any]]] = []
        if operation_root.is_dir():
            for path in sorted(operation_root.glob("*.json")):
                raw = path.read_bytes()
                try:
                    payload = json.loads(raw.decode("utf-8"))
                except (UnicodeError, json.JSONDecodeError) as exc:
                    raise RemoteRegistryRecoveryError(
                        f"cannot trust trial activation operation {path}: {exc}"
                    ) from exc
                if not isinstance(payload, Mapping):
                    raise RemoteRegistryRecoveryError(
                        f"trial activation operation is malformed: {path}"
                    )
                if (
                    payload.get("status") == "completed"
                    and payload.get("release_digest")
                    == release_plan.release.release_digest
                    and payload.get("lock_digest") in accepted_digests
                ):
                    matches.append((sha256_digest(raw), payload))
        if len(matches) != 1:
            raise RemoteRegistryRecoveryError(
                "recovery requires exactly one completed accepted-trial activation"
            )
        digest, operation = matches[0]
        desired = operation.get("desired_lock")
        if not isinstance(desired, Mapping):
            raise RemoteRegistryRecoveryError(
                "accepted-trial activation has no desired WorkspaceLock"
            )
        try:
            trial_lock = WorkspaceLock.from_mapping(desired)
        except ArtifactReleaseContractError as exc:
            raise RemoteRegistryRecoveryError(
                f"accepted-trial WorkspaceLock is invalid: {exc}"
            ) from exc
        active = {item.key: item for item in trial_lock.components}
        if any(active.get(item.key) != item for item in release_plan.packages):
            raise RemoteRegistryRecoveryError(
                "accepted-trial WorkspaceLock differs from cached release plan"
            )
        return digest

    def _load_revalidation_receipt(
        self,
        project_id: str,
        *,
        kind: ArtifactKind,
        channel: str,
        release_plan: ReleasePlan,
        candidate: CandidateRecord,
        candidate_record_digest: str,
        required: bool,
    ) -> str | None:
        release_digest = str(release_plan.release.release_digest)
        path = self.revalidation_receipt_path(release_digest)
        if not path.is_file():
            if required:
                raise RemoteRegistryRecoveryError(
                    "legacy accepted trial requires explicit isolated revalidation"
                )
            return None
        raw = path.read_bytes()
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeError, json.JSONDecodeError) as exc:
            raise RemoteRegistryRecoveryError(
                f"cannot trust recovery revalidation receipt: {exc}"
            ) from exc
        expected_fields = {
            "schema",
            "status",
            "project_id",
            "kind",
            "channel",
            "release_digest",
            "candidate_digest",
            "candidate_record_digest",
            "legacy_candidate",
            "activation_operation_id",
            "activation_operation_digest",
            "lock_digest",
            "completed_at",
        }
        if not isinstance(payload, Mapping) or set(payload) != expected_fields:
            raise RemoteRegistryRecoveryError(
                "recovery revalidation receipt has an unsupported contract"
            )
        if (
            payload.get("schema") != REMOTE_REGISTRY_REVALIDATION_SCHEMA
            or payload.get("status") != "completed"
            or payload.get("project_id") != project_id
            or payload.get("kind") != kind
            or payload.get("channel") != channel
            or payload.get("release_digest") != release_digest
            or payload.get("candidate_digest") != candidate.digest
            or payload.get("candidate_record_digest") != candidate_record_digest
            or payload.get("legacy_candidate") is not True
        ):
            raise RemoteRegistryRecoveryError(
                "recovery revalidation receipt does not match local evidence"
            )
        token = release_digest.split(":", 1)[1]
        operation_id = str(payload.get("activation_operation_id") or "")
        operation_path = (
            self.state_root
            / "remote-registry-revalidations"
            / token
            / "state"
            / "artifact_pipeline"
            / "operations"
            / f"{operation_id}.json"
        )
        try:
            operation_bytes = operation_path.read_bytes()
            operation = json.loads(operation_bytes.decode("utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RemoteRegistryRecoveryError(
                f"cannot trust revalidation activation operation: {exc}"
            ) from exc
        if (
            not isinstance(operation, Mapping)
            or operation.get("status") != "completed"
            or operation.get("release_digest") != release_digest
            or operation.get("lock_digest") != payload.get("lock_digest")
            or sha256_digest(operation_bytes)
            != payload.get("activation_operation_digest")
        ):
            raise RemoteRegistryRecoveryError(
                "revalidation activation operation does not match its receipt"
            )
        return sha256_digest(raw)

    def _restore_remote(
        self,
        operation_path: Path,
        operation: dict[str, Any],
        current: RemoteRegistryRecoveryPlan,
        local: _RecoveryEvidence,
    ) -> None:
        receipts = operation.setdefault("receipts", {})
        for package in local.release_plan.packages:
            receipt_key = f"package:{package.digest}"
            try:
                self.remote.fetch_package(package)
                outcome = "already_present"
            except Exception as exc:
                if not self._is_not_found(exc):
                    raise
                operation["status"] = "running"
                operation["phase"] = f"put_package:{package.digest}"
                atomic_write_json(operation_path, operation)
                self.remote.put_package(package, local.package_archives[package.digest])
                self.remote.fetch_package(package)
                outcome = "uploaded"
            receipts[receipt_key] = {"status": outcome, "package": package.to_dict()}
            atomic_write_json(operation_path, operation)

        release = local.release_plan.release
        release_digest = str(release.release_digest)
        try:
            observed = self.remote.get_release(release.project_id, release_digest)
            outcome = "already_present"
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
            operation["status"] = "running"
            operation["phase"] = f"put_release:{release_digest}"
            atomic_write_json(operation_path, operation)
            self.remote.put_release_record(local.release_plan)
            observed = self.remote.get_release(release.project_id, release_digest)
            outcome = "uploaded"
        if observed.explain() != local.release_plan.explain():
            raise RemoteRegistryRecoveryError(
                "remote release verification returned different content"
            )
        receipts["release"] = {
            "status": outcome,
            "release_digest": release_digest,
        }
        atomic_write_json(operation_path, operation)

        try:
            pointer = self.remote.get_channel(release.project_id, current.channel)
            outcome = "already_present"
        except Exception as exc:
            if not self._is_not_found(exc):
                raise
            operation["status"] = "running"
            operation["phase"] = f"create_channel:{current.channel}"
            atomic_write_json(operation_path, operation)
            pointer = self.remote.set_channel(
                local.release_plan,
                current.channel,
                expected_release_digest=None,
            )
            outcome = "created"
        if pointer.release_digest != release_digest:
            raise RemoteRegistryRecoveryError(
                "remote channel conflicts with reviewed recovery release"
            )
        receipts["channel"] = {
            "status": outcome,
            "pointer": pointer.to_dict(),
        }
        atomic_write_json(operation_path, operation)

    def _validate_progress(
        self,
        operation: Mapping[str, Any],
        current: RemoteRegistryRecoveryPlan,
    ) -> None:
        plan = operation.get("plan")
        if not isinstance(plan, Mapping):
            raise RemoteRegistryRecoveryError("recovery operation has no plan")
        if plan.get("project_id") != current.project_id or plan.get("kind") != current.kind:
            raise RemoteRegistryRecoveryError("recovery operation identity changed")
        if plan.get("channel") != current.channel:
            raise RemoteRegistryRecoveryError("recovery channel changed")
        if plan.get("local_evidence_digest") != current.local_evidence_digest:
            raise RemoteRegistryRecoveryError("local recovery evidence changed")
        if plan.get("source_attestations") != [
            dict(item) for item in current.source_attestations
        ]:
            raise RemoteRegistryRecoveryError("immutable source attestations changed")
        if current.remote_channel is not None and (
            current.remote_channel.release_digest
            != current.release_plan.release.release_digest
        ):
            raise RemoteRegistryRecoveryError(
                "remote channel moved to a conflicting release during recovery"
            )

    def _load_operation(
        self,
        path: Path,
        reviewed_plan_digest: str,
    ) -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RemoteRegistryRecoveryError(
                f"cannot read remote recovery operation: {exc}"
            ) from exc
        if not isinstance(payload, dict):
            raise RemoteRegistryRecoveryError("remote recovery operation must be an object")
        if payload.get("schema") != REMOTE_REGISTRY_RECOVERY_OPERATION_SCHEMA:
            raise RemoteRegistryRecoveryError("unsupported remote recovery operation")
        if payload.get("plan_digest") != reviewed_plan_digest:
            raise RemoteRegistryRecoveryError("remote recovery operation digest mismatch")
        if payload.get("status") not in {"prepared", "running", "paused", "completed"}:
            raise RemoteRegistryRecoveryError("unsupported remote recovery operation status")
        return payload

    @staticmethod
    def _is_not_found(exc: Exception) -> bool:
        status = getattr(exc, "status_code", None)
        code = str(getattr(exc, "error_code", "") or "").strip().lower()
        message = str(exc).strip().lower()
        return (
            status == 404
            or code in {"package_not_found", "release_not_found", "channel_not_found"}
            or message in {
                "errors.package_not_found",
                "errors.release_not_found",
                "errors.channel_not_found",
            }
            or isinstance(exc, (FileNotFoundError, KeyError))
        )

    @staticmethod
    def _validate_digest(value: str) -> str:
        token = str(value or "").strip().lower()
        if (
            not token.startswith("sha256:")
            or len(token) != 71
            or any(char not in "0123456789abcdef" for char in token.split(":", 1)[1])
        ):
            raise RemoteRegistryRecoveryError(
                "digest must be sha256:<64 lowercase hex characters>"
            )
        return token


__all__ = [
    "REMOTE_REGISTRY_RECOVERY_OPERATION_SCHEMA",
    "REMOTE_REGISTRY_RECOVERY_PLAN_SCHEMA",
    "REMOTE_REGISTRY_REVALIDATION_SCHEMA",
    "RemoteRegistryRecoveryError",
    "RemoteRegistryRecoveryManager",
    "RemoteRegistryRecoveryPlan",
    "RemoteRegistryRecoveryRemote",
]
