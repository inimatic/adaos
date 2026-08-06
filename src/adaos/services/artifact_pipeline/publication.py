from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Protocol

import yaml
from packaging.version import InvalidVersion, Version

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    StableSubscription,
    WorkspaceLock,
    canonical_payload_digest,
)
from adaos.services.artifact_pipeline.activation import (
    ActivationError,
    ActivationResult,
    WorkspaceActivationManager,
)
from adaos.services.artifact_pipeline.attestation_publication import (
    ArtifactAttestationPublisher,
    AttestationPublicationResult,
)
from adaos.services.artifact_pipeline.attestation_sets import ReleaseAttestationSet
from adaos.services.artifact_pipeline.attestations import ArtifactAttestationAdmission
from adaos.services.artifact_pipeline.candidates import (
    CandidateRecord,
    CandidateStore,
    assert_promotable,
    assess_freshness,
    begin_trial,
    candidate_from_release,
    complete_trial,
    mark_stale,
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
from adaos.services.artifact_pipeline.reconciliation import (
    RegistryReconciliationPlan,
    WorkspaceRegistryReconciler,
)
from adaos.services.artifact_pipeline.recovery import (
    RemoteRegistryRecoveryManager,
    RemoteRegistryRecoveryPlan,
)
from adaos.services.artifact_pipeline.storage import (
    MutationLockTimeout,
    atomic_write_json,
    mutation_lock,
    replace_with_retry,
)
from adaos.services.artifact_pipeline.trial_activation import (
    TrialActivationError,
    TrialActivationStore,
    build_trial_activation,
    load_workspace_lock,
    runtime_trial_root,
    runtime_trial_workspace,
    shared_skill_conflicts,
)
from adaos.services.conversational_pipeline import compile_conversational_package
from adaos.services.workflow_artifacts import load_manifest_bound_workflow
from adaos.services.workflow_metrics import (
    workflow_metrics_evidence,
    workflow_metrics_report,
)
from adaos.services.workspace_registry import (
    set_workspace_registry_channel,
    upsert_workspace_registry_entry,
)
from adaos.services.skill.setup_plan import publication_setup_evidence


PUSHED_SOURCE_SCHEMA = "adaos.artifact.pushed_source.v1"
REBASE_PLAN_SCHEMA = "adaos.artifact.rebase_plan.v1"
PROMOTION_OPERATION_SCHEMA = "adaos.artifact.promotion_operation.v1"


class PublicationError(RuntimeError):
    pass


class PublicationStaleError(PublicationError):
    def __init__(self, plan: "CandidateRebasePlan") -> None:
        super().__init__(
            f"candidate is stale: {plan.stale_reason}; recreate DEV on the target base and reapply its bounded changes"
        )
        self.plan = plan


class PublicationRemote(Protocol):
    def put_release(self, plan: ReleasePlan, archives: Mapping[str, bytes]) -> None: ...

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan: ...

    def put_release_attestation_set(
        self,
        attestation_set: ReleaseAttestationSet,
    ) -> ReleaseAttestationSet: ...

    def get_release_attestation_set(
        self,
        project_id: str,
        release_digest: str,
    ) -> ReleaseAttestationSet: ...

    def set_channel(
        self,
        plan: ReleasePlan,
        channel: str = "stable",
        *,
        expected_release_digest: str | None,
    ) -> ChannelPointer: ...

    def get_channel(self, project_id: str, channel: str = "stable") -> ChannelPointer: ...

    def fetch_package(self, package: ArtifactPackageRef) -> bytes: ...

    def tree_revision(self, source_ref: ArtifactSourceRef) -> str: ...


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _source_tree(value: Any, *, required: bool = False) -> str | None:
    tree = str(value or "").strip().lower()
    if not tree and not required:
        return None
    if len(tree) not in {40, 64} or any(char not in "0123456789abcdef" for char in tree):
        raise PublicationError("source tree must be an immutable Git object id")
    return tree


@dataclass(frozen=True, slots=True)
class PushedSourceRecord:
    kind: str
    artifact_id: str
    source_ref: ArtifactSourceRef
    package: ArtifactPackageRef
    pushed_at: str
    change_ids: tuple[str, ...] = ()
    source_tree: str | None = None

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema": PUSHED_SOURCE_SCHEMA,
            "kind": self.kind,
            "artifact_id": self.artifact_id,
            "source_ref": self.source_ref.to_dict(),
            "package": self.package.to_dict(),
            "pushed_at": self.pushed_at,
            "change_ids": list(self.change_ids),
        }
        if self.source_tree:
            payload["source_tree"] = self.source_tree
        return payload

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
            source_tree=_source_tree(value.get("source_tree")),
        )


@dataclass(frozen=True, slots=True)
class CandidateRebasePlan:
    candidate_id: str
    project_id: str
    stale_reason: str
    change_ids: tuple[str, ...]
    previous_base_release: str
    previous_base_digest: str
    target_base_release: str
    target_base_digest: str
    target_source_ref: ArtifactSourceRef
    path_scope: tuple[str, ...]
    created_at: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema": REBASE_PLAN_SCHEMA,
            "candidate_id": self.candidate_id,
            "project_id": self.project_id,
            "stale_reason": self.stale_reason,
            "change_ids": list(self.change_ids),
            "previous_base_release": self.previous_base_release,
            "previous_base_digest": self.previous_base_digest,
            "target_base_release": self.target_base_release,
            "target_base_digest": self.target_base_digest,
            "target_source_ref": self.target_source_ref.to_dict(),
            "path_scope": list(self.path_scope),
            "action": "recreate_dev_and_reapply",
            "requires_new_validation": True,
            "requires_new_trial": True,
            "created_at": self.created_at,
        }

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> "CandidateRebasePlan":
        source = value.get("target_source_ref")
        if not isinstance(source, Mapping):
            raise PublicationError("rebase plan is missing its exact target source")
        return cls(
            candidate_id=str(value.get("candidate_id") or ""),
            project_id=str(value.get("project_id") or ""),
            stale_reason=str(value.get("stale_reason") or ""),
            change_ids=tuple(str(item) for item in value.get("change_ids") or ()),
            previous_base_release=str(value.get("previous_base_release") or ""),
            previous_base_digest=str(value.get("previous_base_digest") or ""),
            target_base_release=str(value.get("target_base_release") or ""),
            target_base_digest=str(value.get("target_base_digest") or ""),
            target_source_ref=ArtifactSourceRef.from_mapping(source),
            path_scope=tuple(str(item) for item in value.get("path_scope") or ()),
            created_at=str(value.get("created_at") or ""),
        )


@dataclass(frozen=True, slots=True)
class PreparedCandidate:
    candidate: CandidateRecord
    plan: ReleasePlan
    trial_workspace: Path
    trial_activation: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PromotionResult:
    candidate: CandidateRecord
    plan: ReleasePlan
    pointer: ChannelPointer
    activation: ActivationResult
    subscription: StableSubscription


@dataclass(frozen=True, slots=True)
class SubscriptionUpdateNotice:
    subscription: StableSubscription
    pointer: ChannelPointer
    available: bool
    activation_allowed: bool
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "subscription": self.subscription.to_dict(),
            "pointer": self.pointer.to_dict(),
            "available": self.available,
            "activation_allowed": self.activation_allowed,
            "reason": self.reason,
        }


@dataclass(frozen=True, slots=True)
class SubscriptionUpdateResult:
    subscription: StableSubscription
    pointer: ChannelPointer
    plan: ReleasePlan
    activation: ActivationResult


@dataclass(frozen=True, slots=True)
class SubscriptionUpdatePlan:
    notice: SubscriptionUpdateNotice
    release_plan: ReleasePlan
    activation_plan: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema": "adaos.artifact.subscription_update_plan.v1",
            "notice": self.notice.to_dict(),
            "activation": dict(self.activation_plan),
        }
        payload["plan_digest"] = canonical_payload_digest(payload)
        return payload

    @property
    def plan_digest(self) -> str:
        return str(self.to_dict()["plan_digest"])


class ArtifactPublicationService:
    def __init__(
        self,
        *,
        state_root: Path,
        workspace_root: Path,
        remote: PublicationRemote,
        attestation_publisher: ArtifactAttestationPublisher | None = None,
        attestation_admission: ArtifactAttestationAdmission | None = None,
    ) -> None:
        self.state_root = Path(state_root).expanduser().resolve()
        self.workspace_root = Path(workspace_root).expanduser().resolve()
        self.remote = remote
        self.attestation_publisher = attestation_publisher
        self.attestation_admission = attestation_admission
        self.package_store = ContentAddressedPackageStore(self.state_root / "packages")
        self.release_cache = ReleaseRepository(self.state_root / "release-cache")
        self.candidate_store = CandidateStore(self.state_root / "candidates")
        self.trial_activations = TrialActivationStore(
            self.state_root / "trial-activations"
        )
        self.subscriptions = SubscriptionStore(self.workspace_root / ".adaos" / "subscriptions.json")
        self.registry_reconciler = WorkspaceRegistryReconciler(
            state_root=self.state_root,
            workspace_root=self.workspace_root,
            remote=self.remote,
        )
        self.remote_registry_recovery = RemoteRegistryRecoveryManager(
            state_root=self.state_root,
            workspace_root=self.workspace_root,
            remote=self.remote,
        )

    def _record_builder_repair(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        from adaos.services.builder.repair import BuilderRepairService

        return BuilderRepairService(state_dir=self.state_root).report(
            project_id=str(payload.get("project_id") or "unknown"),
            signal_type=str(payload.get("signal_type") or "post_activation"),
            summary=str(payload.get("summary") or "Post-activation verification failed"),
            source_refs=tuple(
                dict(item)
                for item in payload.get("source_refs") or []
                if isinstance(item, Mapping)
            ),
            context=(
                dict(payload.get("context"))
                if isinstance(payload.get("context"), Mapping)
                else {}
            ),
        )

    def get_candidate_release(self, candidate_id: str) -> ReleasePlan:
        """Return the immutable release plan bound to one candidate.

        Runtime activation callers use this read-only projection to constrain
        reload and health checks to the candidate dependency closure.  A
        merged WorkspaceLock may contain unrelated, independently managed
        projects whose runtime versions are intentionally newer than their
        last package-lock projection.
        """

        candidate = self.candidate_store.load(str(candidate_id or "").strip())
        return self.release_cache.get_release(
            candidate.project_id,
            candidate.release_digest,
        )

    def _workspace_slot_id(
        self,
        project_id: str,
        *,
        activation_manager: WorkspaceActivationManager | None = None,
    ) -> str:
        """Keep each installed project in a stable Workspace slot.

        Older activations used the single ``primary`` slot. Preserve that slot
        for its existing project, but never replace it when another subscribed
        project is promoted or updated.
        """

        manager = activation_manager or WorkspaceActivationManager(
            workspace_root=self.workspace_root,
            package_store=self.package_store,
            state_root=self.state_root / "activation",
            attestation_admission=self.attestation_admission,
        )
        current = manager.load_lock()
        if current is None or not current.slots:
            return "primary"
        for slot in sorted(current.slots, key=lambda item: item.slot_id):
            if slot.project_id == project_id:
                return slot.slot_id
        occupied = {item.slot_id for item in current.slots}
        if project_id not in occupied:
            return project_id
        suffix = canonical_payload_digest({"project_id": project_id}).split(":", 1)[1][:16]
        return f"project-{suffix}"

    def reconcile_attestation_publication(
        self,
        operation_id: str,
    ) -> AttestationPublicationResult:
        if self.attestation_publisher is None:
            raise PublicationError("artifact attestation publication is not configured")
        return self.attestation_publisher.reconcile(operation_id)

    def reconcile_release_attestation_binding(
        self,
        candidate_id: str,
    ) -> ReleaseAttestationSet:
        try:
            with mutation_lock(self.promotion_lock_path(candidate_id)):
                operation = self.load_promotion(candidate_id)
                if operation is None:
                    raise PublicationError("candidate promotion has no binding operation")
                state = operation.get("attestation_binding")
                if not isinstance(state, dict):
                    raise PublicationError("candidate promotion has no attestation binding intent")
                if state.get("status") == "completed":
                    receipt = operation.get("receipts", {}).get("attestations_bound")
                    if not isinstance(receipt, Mapping):
                        raise PublicationError("completed attestation binding has no receipt")
                    return ReleaseAttestationSet.from_mapping(
                        receipt["attestation_set"]
                    )
                if state.get("status") not in {"dispatching", "uncertain"}:
                    raise PublicationError("attestation binding is not reconcilable")
                state["status"] = "uncertain"
                state["updated_at"] = _now()
                self._write_promotion(operation)
                raw_set = state.get("attestation_set")
                if not isinstance(raw_set, Mapping):
                    raise PublicationError("attestation binding intent has no exact set")
                expected = ReleaseAttestationSet.from_mapping(raw_set)
                observed = self.remote.get_release_attestation_set(
                    expected.project_id,
                    expected.release_digest,
                )
                if observed != expected:
                    raise PublicationError(
                        "remote release attestation binding differs from dispatch intent"
                    )
                state["status"] = "completed"
                state["completed_via"] = "reconciliation"
                state["updated_at"] = _now()
                state.pop("last_error", None)
                self._promotion_receipt(
                    operation,
                    "attestations_bound",
                    {"attestation_set": observed.to_dict()},
                )
                return observed
        except MutationLockTimeout as exc:
            raise PublicationError("candidate promotion is already running") from exc

    def recover_promotion_activation(
        self,
        candidate_id: str,
        operation_id: str,
    ) -> dict[str, Any]:
        """Reconcile one failed, rolled-back activation without replaying it."""

        try:
            with mutation_lock(self.promotion_lock_path(candidate_id)):
                promotion = self.load_promotion(candidate_id)
                if promotion is None:
                    raise PublicationError("candidate promotion has no durable operation")
                if promotion.get("status") != "paused":
                    raise PublicationError("candidate promotion is not paused")
                receipts = promotion.setdefault("receipts", {})
                if isinstance(receipts.get("workspace_activated"), Mapping):
                    raise PublicationError("candidate Workspace activation is already complete")
                release_digest = str(promotion.get("release_digest") or "").strip()
                if not release_digest:
                    raise PublicationError("candidate promotion has no immutable release digest")
                manager = WorkspaceActivationManager(
                    workspace_root=self.workspace_root,
                    package_store=self.package_store,
                    state_root=self.state_root / "activation",
                    attestation_admission=self.attestation_admission,
                )
                previous_recovery = receipts.get("activation_recovered")
                expected_key = f"stable:{release_digest}"
                if isinstance(previous_recovery, Mapping):
                    expected_key = str(
                        previous_recovery.get("new_idempotency_key") or ""
                    ).strip()
                expected_operation_id = manager.operation_id(expected_key)
                operation_token = str(operation_id or "").strip()
                if operation_token != expected_operation_id:
                    raise PublicationError(
                        "failed activation does not match the current promotion attempt"
                    )
                operation_path = manager.operation_path(operation_token)
                try:
                    activation_operation = json.loads(
                        operation_path.read_text(encoding="utf-8")
                    )
                except (OSError, ValueError) as exc:
                    raise PublicationError(
                        f"cannot read failed activation operation {operation_token}: {exc}"
                    ) from exc
                if (
                    activation_operation.get("release_digest") != release_digest
                    or activation_operation.get("status") != "failed"
                    or activation_operation.get("rolled_back") is not True
                ):
                    raise PublicationError(
                        "activation recovery requires the exact failed and rolled-back release operation"
                    )
                recovered = manager.recover_interrupted(operation_token)
                new_key = f"stable-recovery:{release_digest}:{operation_token}"
                self._promotion_receipt(
                    promotion,
                    "activation_recovered",
                    {
                        "operation_id": operation_token,
                        "operation_status": recovered.get("status"),
                        "new_idempotency_key": new_key,
                    },
                )
                promotion["status"] = "paused"
                promotion["phase"] = "activation_recovered"
                promotion["paused_at"] = _now()
                self._write_promotion(promotion)
                return {
                    "status": "recovered",
                    "candidate_id": candidate_id,
                    "release_digest": release_digest,
                    "operation_id": operation_token,
                    "next_operation_id": manager.operation_id(new_key),
                }
        except MutationLockTimeout as exc:
            raise PublicationError("candidate promotion is already running") from exc

    def plan_registry_reconciliation(
        self,
        project_id: str,
        *,
        kind: str,
        channel: str = "stable",
    ) -> RegistryReconciliationPlan:
        if kind not in {"skill", "scenario"}:
            raise PublicationError("artifact kind must be skill or scenario")
        return self.registry_reconciler.plan(
            project_id,
            kind=kind,
            channel=channel,
        )

    def apply_registry_reconciliation(
        self,
        project_id: str,
        *,
        kind: str,
        reviewed_plan_digest: str,
        channel: str = "stable",
    ) -> dict[str, Any]:
        if kind not in {"skill", "scenario"}:
            raise PublicationError("artifact kind must be skill or scenario")
        return self.registry_reconciler.apply(
            project_id,
            kind=kind,
            channel=channel,
            reviewed_plan_digest=reviewed_plan_digest,
        )

    def plan_remote_registry_recovery(
        self,
        project_id: str,
        *,
        kind: str,
        channel: str = "stable",
    ) -> RemoteRegistryRecoveryPlan:
        if kind not in {"skill", "scenario"}:
            raise PublicationError("artifact kind must be skill or scenario")
        return self.remote_registry_recovery.plan(
            project_id,
            kind=kind,
            channel=channel,
        )

    def revalidate_remote_registry_recovery(
        self,
        project_id: str,
        *,
        kind: str,
        channel: str = "stable",
    ) -> dict[str, Any]:
        if kind not in {"skill", "scenario"}:
            raise PublicationError("artifact kind must be skill or scenario")
        return self.remote_registry_recovery.revalidate(
            project_id,
            kind=kind,
            channel=channel,
        )

    def apply_remote_registry_recovery(
        self,
        project_id: str,
        *,
        kind: str,
        reviewed_plan_digest: str,
        channel: str = "stable",
    ) -> dict[str, Any]:
        if kind not in {"skill", "scenario"}:
            raise PublicationError("artifact kind must be skill or scenario")
        return self.remote_registry_recovery.apply(
            project_id,
            kind=kind,
            channel=channel,
            reviewed_plan_digest=reviewed_plan_digest,
        )

    def pushed_source_path(self, kind: str, artifact_id: str) -> Path:
        plural = "skills" if kind == "skill" else "scenarios"
        return self.state_root / "pushed-sources" / plural / f"{artifact_id}.json"

    def rebase_plan_path(self, candidate_id: str) -> Path:
        return self.state_root / "rebase-plans" / f"{self.candidate_store.path(candidate_id).stem}.json"

    def promotion_path(self, candidate_id: str) -> Path:
        return self.state_root / "promotions" / f"{self.candidate_store.path(candidate_id).stem}.json"

    def promotion_lock_path(self, candidate_id: str) -> Path:
        return self.state_root / "promotions" / f"{self.candidate_store.path(candidate_id).stem}.lock"

    def load_promotion(self, candidate_id: str) -> dict[str, Any] | None:
        path = self.promotion_path(candidate_id)
        if not path.is_file():
            return None
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            raise PublicationError(f"cannot read promotion operation: {exc}") from exc
        if not isinstance(payload, dict) or payload.get("schema") != PROMOTION_OPERATION_SCHEMA:
            raise PublicationError("unsupported promotion operation")
        if payload.get("candidate_id") != candidate_id:
            raise PublicationError("promotion operation belongs to another candidate")
        return payload

    def _write_promotion(self, operation: dict[str, Any]) -> None:
        operation["updated_at"] = _now()
        atomic_write_json(self.promotion_path(str(operation["candidate_id"])), operation)

    def _promotion_receipt(
        self,
        operation: dict[str, Any],
        phase: str,
        receipt: Mapping[str, Any],
    ) -> None:
        stamped = {**dict(receipt), "recorded_at": _now()}
        operation.setdefault("receipts", {})[phase] = stamped
        operation.setdefault("events", []).append({"phase": phase, "at": stamped["recorded_at"]})
        operation["phase"] = phase
        operation["status"] = "running"
        operation.pop("error", None)
        operation.pop("paused_at", None)
        self._write_promotion(operation)

    def load_rebase_plan(self, candidate_id: str) -> CandidateRebasePlan:
        payload = json.loads(self.rebase_plan_path(candidate_id).read_text(encoding="utf-8"))
        if not isinstance(payload, Mapping) or payload.get("schema") != REBASE_PLAN_SCHEMA:
            raise PublicationError("unsupported candidate rebase plan")
        return CandidateRebasePlan.from_mapping(payload)

    def check_subscription(self, project_id: str) -> SubscriptionUpdateNotice:
        try:
            subscription = self.subscriptions.load()[project_id]
        except KeyError as exc:
            raise PublicationError(f"project has no stable subscription: {project_id}") from exc
        pointer = self.remote.get_channel(project_id, subscription.channel)
        channel_moved = pointer.release_digest != subscription.installed_digest
        activation_manager = WorkspaceActivationManager(
            workspace_root=self.workspace_root,
            package_store=self.package_store,
            state_root=self.state_root / "activation",
            attestation_admission=self.attestation_admission,
        )
        lock = activation_manager.load_lock()
        installed_slot = next(
            (
                item
                for item in (lock.slots if lock is not None else ())
                if item.project_id == subscription.project_id
                and item.release == subscription.installed_release
                and item.release_digest == subscription.installed_digest
            ),
            None,
        )
        workspace_slot_missing = bool(subscription.installed_digest and installed_slot is None)
        available = channel_moved or workspace_slot_missing
        allowed = workspace_slot_missing and not channel_moved
        if channel_moved:
            allowed = subscription.policy == "notify"
        reason = "up_to_date"
        if channel_moved and subscription.policy == "pinned":
            reason = "pinned"
        elif channel_moved:
            reason = "channel_moved"
        elif workspace_slot_missing:
            reason = "workspace_slot_missing"
        return SubscriptionUpdateNotice(subscription, pointer, available, allowed, reason)

    def plan_subscription_update(
        self,
        project_id: str,
        *,
        notice: SubscriptionUpdateNotice | None = None,
    ) -> SubscriptionUpdatePlan:
        notice = notice or self.check_subscription(project_id)
        if notice.subscription.project_id != project_id:
            raise PublicationError("subscription update notice belongs to another project")
        if not notice.available:
            raise PublicationError("subscription is already up to date")
        release_plan = self.remote.get_release(project_id, notice.pointer.release_digest)
        if (
            release_plan.release.project_id != project_id
            or (release_plan.release.release_digest or release_plan.release.computed_digest())
            != notice.pointer.release_digest
        ):
            raise PublicationError("stable channel resolved to a different ProjectRelease identity")
        activation_manager = WorkspaceActivationManager(
            workspace_root=self.workspace_root,
            package_store=self.package_store,
            state_root=self.state_root / "activation",
            attestation_admission=self.attestation_admission,
        )
        activation_plan = activation_manager.plan_activation(
            release_plan,
            slot_id=self._workspace_slot_id(
                project_id,
                activation_manager=activation_manager,
            ),
        )
        return SubscriptionUpdatePlan(notice, release_plan, activation_plan)

    def activate_subscription_update(
        self,
        project_id: str,
        *,
        idempotency_key: str | None = None,
        expected_plan_digest: str | None = None,
        health_check=None,
        reload_runtime=None,
        health_policy=None,
        reload_policy=None,
        permission_decision=None,
        migration_executor=None,
        migration_rollback=None,
    ) -> SubscriptionUpdateResult:
        prepared = self.plan_subscription_update(project_id)
        notice = prepared.notice
        if not notice.activation_allowed:
            raise PublicationError("pinned subscription cannot be activated")
        if expected_plan_digest is not None and str(expected_plan_digest).strip().lower() != prepared.plan_digest:
            raise PublicationError("subscription update plan changed; review the new plan before activation")
        plan = prepared.release_plan
        activation_manager = WorkspaceActivationManager(
            workspace_root=self.workspace_root,
            package_store=self.package_store,
            state_root=self.state_root / "activation",
            attestation_admission=self.attestation_admission,
        )
        activation = activation_manager.activate(
            plan,
            idempotency_key=(
                str(idempotency_key or "").strip()
                or f"subscription:{project_id}:{notice.pointer.release_digest}"
            ),
            slot_id=str(prepared.activation_plan.get("slot_id") or "primary"),
            fetch_package=self.remote.fetch_package,
            reload_runtime=reload_runtime,
            health_check=health_check,
            reload_policy=reload_policy,
            health_policy=health_policy,
            permission_decision=permission_decision,
            migration_executor=migration_executor,
            migration_rollback=migration_rollback,
            repair_reporter=self._record_builder_repair,
            expected_lock_digest=prepared.activation_plan.get("observed_lock_digest"),
        )
        self.release_cache.put_release(plan)
        self._record_workspace_projection(plan)
        updated = StableSubscription(
            project_id=notice.subscription.project_id,
            channel=notice.subscription.channel,
            policy=notice.subscription.policy,
            installed_release=notice.pointer.release,
            installed_digest=notice.pointer.release_digest,
        )
        self.subscriptions.save(updated)
        return SubscriptionUpdateResult(updated, notice.pointer, plan, activation)

    def record_push(
        self,
        *,
        kind: str,
        artifact_id: str,
        artifact_dir: Path,
        source_ref: ArtifactSourceRef,
        change_ids: tuple[str, ...] = (),
        source_tree: str | None = None,
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
            source_tree=_source_tree(source_tree),
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
        if current.ref == record.package:
            return current

        try:
            checkpoint_bytes, checkpoint = self.package_store.read_verified(
                record.package.digest
            )
        except (FileNotFoundError, RuntimeError, ValueError) as exc:
            raise PublicationError(
                "exact Forge checkpoint package is unavailable or invalid"
            ) from exc
        if checkpoint.ref != record.package:
            raise PublicationError(
                "exact Forge checkpoint package does not match its pushed source receipt"
            )

        # A core update may change only the package build-policy digest (for
        # example, by adding a reserved transient directory to the exclusion
        # set). That changes deterministic archive bytes even when every
        # publishable source byte is unchanged. Compare the signed file
        # inventory and all other package semantics, then keep using the
        # immutable checkpoint archive. Any policy change that affects the
        # selected files still changes this projection and is rejected.
        def _source_projection(manifest: Mapping[str, Any]) -> dict[str, Any]:
            projected = dict(manifest)
            projected.pop("build_policy_digest", None)
            return projected

        if _source_projection(current.package_manifest) != _source_projection(
            checkpoint.package_manifest
        ):
            raise PublicationError(
                "DEV content changed after the exact Forge checkpoint; push a new checkpoint"
            )
        return BuiltArtifactPackage(
            ref=checkpoint.ref,
            archive_bytes=checkpoint_bytes,
            package_manifest=checkpoint.package_manifest,
        )

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

    def _installed_dependency_package(
        self,
        requirement: DependencyRequirement,
    ) -> tuple[BuiltArtifactPackage, Path] | None:
        """Synthesize an immutable package identity for a legacy Workspace install.

        This is the AP0 compatibility boundary for an installed dependency that
        predates independent stable release channels.  It never reads mutable
        DEV and labels the provenance explicitly as a Workspace migration.
        """

        dependency_dir = (
            self.workspace_root
            / ("skills" if requirement.kind == "skill" else "scenarios")
            / requirement.artifact_id
        )
        if not dependency_dir.is_dir():
            return None
        path_scope = (
            f"skills/{requirement.artifact_id}/"
            if requirement.kind == "skill"
            else f"scenarios/{requirement.artifact_id}/"
        )
        provisional_ref = ArtifactSourceRef(
            forge="workspace-migration",
            repository="installed-workspace",
            revision="workspace:bootstrap",
            path_scope=(path_scope,),
        )
        provisional = build_artifact_package(
            dependency_dir,
            kind=requirement.kind,
            source_ref=provisional_ref,
        )
        if provisional.ref.key != requirement.key:
            raise PublicationError(
                "installed Workspace dependency identity does not match its path: "
                f"expected {requirement.key}, found {provisional.ref.key}"
            )
        source_ref = ArtifactSourceRef(
            forge="workspace-migration",
            repository="installed-workspace",
            revision=f"sha256:{provisional.ref.digest.removeprefix('sha256:')}",
            path_scope=(path_scope,),
        )
        return (
            build_artifact_package(
                dependency_dir,
                kind=requirement.kind,
                source_ref=source_ref,
            ),
            dependency_dir,
        )

    def _dependency_inputs(
        self,
        *,
        kind: str,
        artifact_dir: Path,
        own_package: ArtifactPackageRef,
        checkpoint_change_ids: tuple[str, ...],
        base_release: ReleasePlan | None = None,
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
        base_packages = (
            {item.key: item for item in base_release.packages}
            if base_release is not None
            else {}
        )

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

            # A channel belongs to a project release set, not necessarily to
            # every component in that set.  Preserve the dependency selected
            # by the current stable project release unless the same change set
            # checkpoints a replacement above.  Only projects without such a
            # component fall back to an independently published dependency.
            if requirement.key in base_packages and base_release is not None:
                dependency_plan = base_release
            else:
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
                    if missing:
                        installed = self._installed_dependency_package(requirement)
                        if installed is None:
                            if requirement.optional:
                                continue
                            raise PublicationError(
                                f"required stable dependency is unavailable: {requirement.key}"
                            ) from exc
                        installed_built, installed_dir = installed
                        catalog.add(installed_built.ref)
                        archives[installed_built.ref.digest] = installed_built.archive_bytes
                        installed_requirements = parse_artifact_requirements(
                            self._manifest(installed_dir, requirement.kind),
                            kind=requirement.kind,  # type: ignore[arg-type]
                        )
                        requirements_by_package.setdefault(
                            installed_built.ref.digest,
                            [],
                        ).extend(installed_requirements)
                        pending_requirements.extend(installed_requirements)
                        continue
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

    def _channel_or_none(
        self,
        project_id: str,
        channel: str = "stable",
    ) -> ChannelPointer | None:
        try:
            return self.remote.get_channel(project_id, channel)
        except Exception as exc:
            status = getattr(exc, "status_code", None)
            code = str(getattr(exc, "error_code", "") or "")
            if (
                status == 404
                or code in {"channel_not_found", "release_not_found"}
                or isinstance(exc, (FileNotFoundError, KeyError))
            ):
                return None
            raise

    def _installed_workspace_version(self, kind: str, artifact_id: str) -> str | None:
        plural = "skills" if kind == "skill" else "scenarios"
        manifest_name = "skill.yaml" if kind == "skill" else "scenario.yaml"
        manifest_path = self.workspace_root / plural / artifact_id / manifest_name
        if not manifest_path.is_file():
            return None
        try:
            payload = yaml.safe_load(manifest_path.read_text(encoding="utf-8-sig")) or {}
        except (OSError, UnicodeError, yaml.YAMLError) as exc:
            raise PublicationError(
                f"installed workspace manifest is unreadable: {manifest_path}"
            ) from exc
        if not isinstance(payload, Mapping):
            raise PublicationError(
                f"installed workspace manifest must contain an object: {manifest_path}"
            )
        return str(payload.get("version") or "").strip() or None

    @staticmethod
    def _require_forward_version(candidate: str, base: str, *, source: str) -> None:
        try:
            candidate_version = Version(candidate)
            base_version = Version(base)
        except InvalidVersion as exc:
            raise PublicationError(
                f"candidate and {source} versions must be valid: {candidate!r}, {base!r}"
            ) from exc
        if candidate_version <= base_version:
            raise PublicationError(
                f"candidate version {candidate} must be newer than {source} version {base}; "
                "rebase DEV on the installed/stable release before creating a trial"
            )

    @staticmethod
    def _trial_workflow_metrics(
        artifact_dir: Path,
        *,
        kind: str,
        validation_evidence: Mapping[str, Any],
        generated_at: str,
    ) -> dict[str, Any] | None:
        manifest_name = "skill.yaml" if kind == "skill" else "scenario.yaml"
        workflow = load_manifest_bound_workflow(
            artifact_dir,
            manifest_name=manifest_name,
            allow_legacy_inline=False,
        )
        if workflow is None:
            return None
        manifest = yaml.safe_load(
            (Path(artifact_dir) / manifest_name).read_text(encoding="utf-8")
        ) or {}
        story_reports: tuple[Mapping[str, Any], ...] = tuple(
            dict(item)
            for item in validation_evidence.get("story_reports") or []
            if isinstance(item, Mapping)
        )
        if isinstance(manifest, Mapping) and isinstance(
            manifest.get("conversational"), Mapping
        ):
            conversational = compile_conversational_package(
                artifact_dir,
                manifest_name=manifest_name,
                run_stories=True,
                build_static_report=False,
                require_operation_catalog=False,
            )
            story_reports = tuple(
                dict(item)
                for item in conversational.validation.report.get("story_reports") or []
                if isinstance(item, Mapping)
            )
        context_packet = (
            dict(validation_evidence["context_packet"])
            if isinstance(validation_evidence.get("context_packet"), Mapping)
            else None
        )
        measurement = (
            dict(validation_evidence["workflow_measurement"])
            if isinstance(validation_evidence.get("workflow_measurement"), Mapping)
            else None
        )
        return workflow_metrics_evidence(
            workflow_metrics_report(
                workflow.compiled,
                story_reports=story_reports,
                context_packet=context_packet,
                measurement=measurement,
                report_id=(
                    f"workflow-metrics:trial:{kind}:{workflow.definition_digest[-24:]}"
                ),
                generated_at=generated_at,
            )
        )

    def prepare_candidate(
        self,
        *,
        kind: str,
        artifact_id: str,
        artifact_dir: Path,
        change_ids: tuple[str, ...],
        validation_evidence: Mapping[str, Any],
        current_stable: ReleasePlan | None = None,
        audience: str = "owner",
        data_mode: str = "empty",
        data_ref: str | None = None,
        data_isolation_evidence: Mapping[str, Any] | None = None,
        target_webspace_id: str = "desktop",
        target_space_kind: str = "development",
        target_zone: str | None = None,
        target_subnet_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> PreparedCandidate:
        effective_validation_evidence = dict(validation_evidence)
        if kind == "skill":
            effective_validation_evidence["setup_publication_gate"] = publication_setup_evidence(
                artifact_dir,
                validation_evidence=validation_evidence,
            )
        record = self.load_pushed_source(kind, artifact_id)
        built = self._verify_current_source(record, artifact_dir)
        if not record.source_tree:
            verified_tree = _source_tree(
                self.remote.tree_revision(record.source_ref),
                required=True,
            )
            record = replace(record, source_tree=verified_tree)
            atomic_write_json(
                self.pushed_source_path(kind, artifact_id),
                record.to_dict(),
            )
        stable = current_stable if current_stable is not None else self.current_stable(artifact_id)
        if stable is not None:
            self._require_forward_version(
                built.ref.version,
                stable.release.version,
                source="stable",
            )
        else:
            installed_version = self._installed_workspace_version(kind, artifact_id)
            if installed_version:
                self._require_forward_version(
                    built.ref.version,
                    installed_version,
                    source="installed Workspace",
                )
        catalog, requirements_by_package, dependency_archives = self._dependency_inputs(
            kind=kind,
            artifact_dir=artifact_dir,
            own_package=built.ref,
            checkpoint_change_ids=change_ids,
            base_release=stable,
        )
        plan = build_project_release(
            project_id=artifact_id,
            version=built.ref.version,
            source_ref=record.source_ref,
            components=(built.ref,),
            catalog=catalog,
            requirements_by_package=requirements_by_package,
            validation_evidence=(effective_validation_evidence,),
        )
        try:
            active_lock = load_workspace_lock(
                self.workspace_root / ".adaos" / "workspace.lock.json"
            )
            conflicts = shared_skill_conflicts(plan, active_lock)
        except TrialActivationError as exc:
            raise PublicationError(str(exc)) from exc
        if conflicts:
            summary = "; ".join(
                f"{item['skill']} used by {', '.join(item['active_consumers'])}"
                for item in conflicts
            )
            raise PublicationError(
                "Trial activation would replace a shared active skill version: "
                + summary
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
            source_tree=record.source_tree,
        )
        candidate = record_validation(candidate, effective_validation_evidence, now=_now())

        trial_workspace = runtime_trial_workspace(self.workspace_root, candidate_id)
        trial_manager = WorkspaceActivationManager(
            workspace_root=trial_workspace,
            package_store=self.package_store,
            state_root=self.state_root / "trials" / candidate_id / "state",
        )
        trial_idempotency_key = str(
            idempotency_key or f"candidate-trial:{candidate.digest}"
        )
        trial_activation = trial_manager.activate(
            plan,
            idempotency_key=trial_idempotency_key,
            audience=audience,
            data_mode=data_mode,
            data_ref=data_ref,
            fetch_package=self.remote.fetch_package,
            reload_policy={
                "mode": "skip",
                "approved_by": "artifact_pipeline.isolated_trial",
                "reason": "isolated trial Workspace is not attached to a live runtime",
            },
            health_check=lambda lock: {
                "status": "passed",
                "check": "verified_package_materialization",
                "lock_digest": lock.to_dict()["lock_digest"],
            },
        )
        trial_operation = json.loads(
            trial_manager.operation_path(trial_activation.operation_id).read_text(
                encoding="utf-8"
            )
        )
        isolation_evidence = data_isolation_evidence
        if data_mode == "empty" and isolation_evidence is None:
            isolation_evidence = {
                "status": "verified",
                "mode": "empty",
                "reason": "isolated trial Workspace has no seeded data",
            }
        trial_started_at = _now()
        candidate = begin_trial(
            candidate,
            trial_id=f"trial-{candidate_id}",
            audience=audience,
            data_mode=data_mode,  # type: ignore[arg-type]
            lock_digest=trial_activation.workspace_lock.to_dict()["lock_digest"],
            now=trial_started_at,
            data_ref=data_ref,
            isolation_evidence=isolation_evidence,
            reload_receipt=trial_operation.get("reload_receipt"),
            health_receipt=trial_operation.get("health_receipt"),
            workflow_metrics=self._trial_workflow_metrics(
                artifact_dir,
                kind=kind,
                validation_evidence=effective_validation_evidence,
                generated_at=trial_started_at,
            ),
        )
        target = {
            "zone": str(target_zone or "").strip() or None,
            "subnet_id": str(target_subnet_id or "").strip() or None,
            "webspace_id": str(target_webspace_id or "desktop").strip() or "desktop",
            "space_kind": str(target_space_kind or "development").strip()
            or "development",
            "scenario_id": artifact_id if kind == "scenario" else None,
        }
        try:
            activation_record = build_trial_activation(
                candidate=candidate.to_dict(),
                plan=plan,
                trial_id=f"trial-{candidate_id}",
                activation_operation_id=trial_activation.operation_id,
                workspace_root=self.workspace_root,
                workspace_lock=trial_activation.workspace_lock,
                target=target,
                audience=audience,
                data_mode=data_mode,
                data_ref=data_ref,
                isolation_evidence=isolation_evidence,
                health_evidence=trial_operation.get("health_receipt"),
                previous_bindings=(
                    [item.to_dict() for item in active_lock.bindings]
                    if active_lock is not None
                    else []
                ),
                idempotency_key=trial_idempotency_key,
                started_at=trial_started_at,
            )
            activation_record = self.trial_activations.save(activation_record)
        except TrialActivationError as exc:
            raise PublicationError(str(exc)) from exc
        self.candidate_store.save(candidate)
        return PreparedCandidate(
            candidate,
            plan,
            trial_workspace,
            activation_record,
        )

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
        rollback_receipt: dict[str, Any]
        trial_workspace: Path | None = None
        archive: Path | None = None
        if accepted:
            rollback_receipt = {
                "status": "not_required",
                "reason": "trial accepted for promotion",
                "recorded_at": _now(),
            }
        else:
            trial_root = runtime_trial_root(self.workspace_root, candidate_id)
            trial_workspace = trial_root / "workspace"
            archive = trial_root / "rollback" / running.trial_id / "workspace"
            if not trial_workspace.is_dir():
                raise PublicationError("rejected trial Workspace is missing before rollback")
            if archive.exists():
                raise PublicationError("rejected trial rollback archive already exists")
            archive.parent.mkdir(parents=True, exist_ok=True)
            replace_with_retry(trial_workspace, archive)
            rollback_receipt = {
                "status": "rolled_back",
                "mode": "isolated_workspace_detached",
                "archive": str(archive),
                "recorded_at": _now(),
            }
        try:
            candidate = complete_trial(
                candidate,
                trial_id=running.trial_id,
                accepted=accepted,
                now=_now(),
                observations=observations,
                rollback_receipt=rollback_receipt,
            )
            self.candidate_store.save(candidate)
            activation = self.trial_activations.load(candidate_id)
            if activation is not None:
                now = _now()
                self.trial_activations.update(
                    candidate_id,
                    status="completed" if accepted else "detached",
                    completed_at=now if accepted else None,
                    detached_at=now if not accepted else None,
                    rollback=rollback_receipt,
                )
        except Exception:
            if (
                not accepted
                and archive is not None
                and archive.exists()
                and trial_workspace is not None
                and not trial_workspace.exists()
            ):
                replace_with_retry(archive, trial_workspace)
            raise
        return candidate

    def get_trial_activation(self, candidate_id: str) -> dict[str, Any] | None:
        try:
            return self.trial_activations.load(str(candidate_id or "").strip())
        except TrialActivationError as exc:
            raise PublicationError(str(exc)) from exc

    def reconcile_trial_activation(self, candidate_id: str) -> dict[str, Any]:
        """Rebuild missing derived Trial runtime state from the exact release."""

        token = str(candidate_id or "").strip()
        record = self.get_trial_activation(token)
        if record is None:
            raise PublicationError("TrialActivation record is missing")
        if str(record.get("status") or "") not in {"active", "reconciling"}:
            raise PublicationError("TrialActivation is not active")
        runtime_binding = (
            dict(record.get("runtime_binding") or {})
            if isinstance(record.get("runtime_binding"), Mapping)
            else {}
        )
        target = runtime_trial_workspace(self.workspace_root, token)
        if target.is_dir() and (target / ".adaos" / "workspace.lock.json").is_file():
            return record
        candidate = self.candidate_store.load(token)
        plan = self.release_cache.get_release(
            candidate.project_id,
            candidate.release_digest,
        )
        try:
            active_lock = load_workspace_lock(
                self.workspace_root / ".adaos" / "workspace.lock.json"
            )
            conflicts = shared_skill_conflicts(plan, active_lock)
        except TrialActivationError as exc:
            raise PublicationError(str(exc)) from exc
        if conflicts:
            raise PublicationError("TrialActivation reconciliation found a shared-skill conflict")
        started = _now()
        self.trial_activations.update(token, status="reconciling")
        manager = WorkspaceActivationManager(
            workspace_root=target,
            package_store=self.package_store,
            state_root=self.state_root / "trials" / token / "state",
        )
        generation = int(runtime_binding.get("reconciliation_generation") or 0) + 1
        activation = manager.activate(
            plan,
            idempotency_key=f"candidate-trial-reconcile:{candidate.digest}:{generation}",
            audience=str(record.get("audience") or "owner"),
            data_mode=str(record.get("data_mode") or "empty"),
            data_ref=str(record.get("data_ref") or "").strip() or None,
            fetch_package=self.remote.fetch_package,
            reload_policy={
                "mode": "skip",
                "approved_by": "artifact_pipeline.runtime_trial_reconcile",
                "reason": "rebuilding derived runtime-only Trial state",
            },
            health_check=lambda lock: {
                "status": "passed",
                "check": "verified_trial_reconstruction",
                "lock_digest": lock.to_dict()["lock_digest"],
            },
        )
        next_binding = {
            **runtime_binding,
            "path": str(target),
            "activation_operation_id": activation.operation_id,
            "workspace_lock_digest": activation.workspace_lock.to_dict()["lock_digest"],
            "reconciliation_generation": generation,
        }
        return self.trial_activations.update(
            token,
            status="active",
            runtime_binding=next_binding,
            reconciled_at=started,
            health_evidence={
                "status": "passed",
                "check": "verified_trial_reconstruction",
            },
        )

    def get_candidate(self, candidate_id: str) -> CandidateRecord:
        """Return one immutable-identity candidate without changing its trial."""

        token = str(candidate_id or "").strip()
        if not token:
            raise PublicationError("candidate_id is required")
        return self.candidate_store.load(token)

    def prepare_rebased_candidate(
        self,
        stale_candidate_id: str,
        *,
        kind: str,
        artifact_id: str,
        artifact_dir: Path,
        validation_evidence: Mapping[str, Any],
        audience: str = "owner",
        data_mode: str = "empty",
        data_ref: str | None = None,
        data_isolation_evidence: Mapping[str, Any] | None = None,
        target_webspace_id: str = "desktop",
        target_space_kind: str = "development",
        target_zone: str | None = None,
        target_subnet_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> PreparedCandidate:
        stale = self.candidate_store.load(stale_candidate_id)
        if stale.status != "stale":
            raise PublicationError("candidate must be stale before it can be reapplied")
        plan = self.load_rebase_plan(stale_candidate_id)
        current = self.current_stable(stale.project_id)
        if current is None:
            raise PublicationError("rebase target stable release is no longer available")
        current_digest = current.release.release_digest or current.release.computed_digest()
        if current_digest != plan.target_base_digest:
            raise PublicationError("rebase target moved again; create a new rebase plan")
        evidence = {
            **dict(validation_evidence),
            "rebase": {
                "replaces_candidate_id": stale_candidate_id,
                "target_base_release": plan.target_base_release,
                "target_base_digest": plan.target_base_digest,
                "change_ids": list(plan.change_ids),
            },
        }
        return self.prepare_candidate(
            kind=kind,
            artifact_id=artifact_id,
            artifact_dir=artifact_dir,
            change_ids=plan.change_ids,
            validation_evidence=evidence,
            current_stable=current,
            audience=audience,
            data_mode=data_mode,
            data_ref=data_ref,
            data_isolation_evidence=data_isolation_evidence,
            target_webspace_id=target_webspace_id,
            target_space_kind=target_space_kind,
            target_zone=target_zone,
            target_subnet_id=target_subnet_id,
            idempotency_key=idempotency_key,
        )

    def promote(
        self,
        candidate_id: str,
        *,
        health_check=None,
        reload_runtime=None,
        health_policy=None,
        reload_policy=None,
        permission_decision=None,
        migration_executor=None,
        migration_rollback=None,
    ) -> PromotionResult:
        try:
            with mutation_lock(self.promotion_lock_path(candidate_id)):
                return self._promote_under_lease(
                    candidate_id,
                    health_check=health_check,
                    reload_runtime=reload_runtime,
                    health_policy=health_policy,
                    reload_policy=reload_policy,
                    permission_decision=permission_decision,
                    migration_executor=migration_executor,
                    migration_rollback=migration_rollback,
                )
        except MutationLockTimeout as exc:
            raise PublicationError("candidate promotion is already running") from exc

    def _promote_under_lease(
        self,
        candidate_id: str,
        *,
        health_check=None,
        reload_runtime=None,
        health_policy=None,
        reload_policy=None,
        permission_decision=None,
        migration_executor=None,
        migration_rollback=None,
    ) -> PromotionResult:
        candidate = self.candidate_store.load(candidate_id)
        plan = self.release_cache.get_release(candidate.project_id, candidate.release_digest)
        operation = self.load_promotion(candidate_id)
        if operation is not None and operation.get("release_digest") != candidate.release_digest:
            raise PublicationError("promotion operation is bound to another release digest")
        terminal_receipts = {
            "channel_moved",
            "workspace_activated",
            "projection_recorded",
            "subscription_saved",
        }
        operation_receipts = (
            operation.get("receipts")
            if isinstance(operation, Mapping)
            and isinstance(operation.get("receipts"), Mapping)
            else {}
        )
        terminal_receipts_complete = terminal_receipts.issubset(operation_receipts)
        if operation is not None and (
            operation.get("status") == "completed"
            or (
                operation.get("phase") == "completed"
                and bool(operation.get("completed_at"))
                and terminal_receipts_complete
            )
        ):
            if operation.get("status") != "completed":
                operation["status"] = "completed"
                operation["reconciled_at"] = _now()
                operation.pop("error", None)
                operation.pop("paused_at", None)
                self._write_promotion(operation)
            return self._completed_promotion_result(candidate, plan, operation)

        if operation is None:
            stable = self.current_stable(candidate.project_id)
            fresh, stale_reason = assess_freshness(
                candidate,
                stable.release if stable is not None else None,
            )
            if not fresh:
                if stable is None:
                    raise PublicationError(
                        f"candidate is stale but no target stable release is available: {stale_reason}"
                    )
                reason = stale_reason or "base_release_moved"
                candidate = mark_stale(candidate, reason=reason, now=_now())
                self.candidate_store.save(candidate)
                rebase_plan = CandidateRebasePlan(
                    candidate_id=candidate.candidate_id,
                    project_id=candidate.project_id,
                    stale_reason=reason,
                    change_ids=candidate.change_ids,
                    previous_base_release=candidate.base_release,
                    previous_base_digest=candidate.base_release_digest,
                    target_base_release=f"{stable.release.project_id}@{stable.release.version}",
                    target_base_digest=(
                        stable.release.release_digest or stable.release.computed_digest()
                    ),
                    target_source_ref=stable.release.source_ref,
                    path_scope=candidate.source_ref.path_scope,
                    created_at=_now(),
                )
                atomic_write_json(self.rebase_plan_path(candidate.candidate_id), rebase_plan.to_dict())
                raise PublicationStaleError(rebase_plan)
            assert_promotable(
                candidate,
                plan.release,
                stable.release if stable is not None else None,
            )
            if not candidate.source_tree:
                raise PublicationError("candidate has no verified public source tree identity")
            actual_tree = self.remote.tree_revision(candidate.source_ref)
            if actual_tree != candidate.source_tree:
                raise PublicationError(
                    f"candidate public source tree changed: {actual_tree} != {candidate.source_tree}"
                )
            expected_base_digest = (
                stable.release.release_digest or stable.release.computed_digest()
                if stable is not None
                else None
            )
            operation = {
                "schema": PROMOTION_OPERATION_SCHEMA,
                "candidate_id": candidate_id,
                "project_id": candidate.project_id,
                "release_digest": candidate.release_digest,
                "expected_base_digest": expected_base_digest,
                "status": "running",
                "phase": "admitted",
                "created_at": _now(),
                "updated_at": _now(),
                "events": [],
                "receipts": {},
            }
            self._promotion_receipt(
                operation,
                "admitted",
                {
                    "base_release": candidate.base_release,
                    "base_release_digest": candidate.base_release_digest,
                    "source_revision": candidate.source_ref.revision,
                    "source_tree": candidate.source_tree,
                },
            )

        operation["status"] = "running"
        operation.pop("error", None)
        operation.pop("paused_at", None)
        self._write_promotion(operation)
        receipts = operation.setdefault("receipts", {})
        try:
            activation_manager = WorkspaceActivationManager(
                workspace_root=self.workspace_root,
                package_store=self.package_store,
                state_root=self.state_root / "activation",
                attestation_admission=self.attestation_admission,
            )
            slot_id = self._workspace_slot_id(
                candidate.project_id,
                activation_manager=activation_manager,
            )
            workflow_receipt = receipts.get("workflow_admitted")
            try:
                if isinstance(workflow_receipt, Mapping):
                    raw_admission = workflow_receipt.get("admission")
                    if not isinstance(raw_admission, Mapping):
                        raise PublicationError(
                            "promotion workflow admission receipt has no admission record"
                        )
                    activation_manager.validate_release_admission(
                        plan,
                        raw_admission,
                        slot_id=slot_id,
                    )
                else:
                    admission = activation_manager.admit_release_candidate(
                        plan,
                        slot_id=slot_id,
                        fetch_package=self.remote.fetch_package,
                    )
                    self._promotion_receipt(
                        operation,
                        "workflow_admitted",
                        {"admission": admission},
                    )
            except ActivationError as exc:
                raise PublicationError(
                    f"workflow publication admission failed: {exc}"
                ) from exc

            published_result: AttestationPublicationResult | None = None
            attestation_receipt = receipts.get("attestations_published")
            if isinstance(attestation_receipt, Mapping):
                if self.attestation_publisher is None:
                    raise PublicationError(
                        "promotion requires its configured attestation publisher to resume"
                    )
                raw_publication = attestation_receipt.get("publication")
                if not isinstance(raw_publication, Mapping):
                    raise PublicationError(
                        "promotion attestation receipt has no publication result"
                    )
                published_result = self.attestation_publisher.load(
                    str(raw_publication.get("operation_id") or "")
                )
                persisted = published_result.to_dict()
                if persisted != dict(raw_publication) or published_result.status != "completed":
                    raise PublicationError(
                        "promotion attestation receipt does not match completed publisher state"
                    )
            elif self.attestation_publisher is not None:
                published_result = self.attestation_publisher.publish(
                    plan,
                    idempotency_key=f"stable-attestations:{candidate.release_digest}",
                )
                if published_result.status != "completed":
                    raise PublicationError("release attestations are not fully published")
                self._promotion_receipt(
                    operation,
                    "attestations_published",
                    {"publication": published_result.to_dict()},
                )

            if published_result is not None:
                exact_set = published_result.release_attestation_set(plan)
                binding_receipt = receipts.get("attestations_bound")
                if isinstance(binding_receipt, Mapping):
                    raw_set = binding_receipt.get("attestation_set")
                    if not isinstance(raw_set, Mapping):
                        raise PublicationError(
                            "promotion attestation binding receipt has no exact set"
                        )
                    expected_set = ReleaseAttestationSet.from_mapping(raw_set).validate_plan(plan)
                    observed_set = self.remote.get_release_attestation_set(
                        candidate.project_id,
                        candidate.release_digest,
                    ).validate_plan(plan)
                    if observed_set != expected_set or observed_set != exact_set:
                        raise PublicationError(
                            "remote release attestation binding differs from promotion receipt"
                        )
                else:
                    binding_state = operation.get("attestation_binding")
                    if isinstance(binding_state, dict):
                        if binding_state.get("status") in {"dispatching", "uncertain"}:
                            if binding_state.get("status") == "dispatching":
                                binding_state["status"] = "uncertain"
                                binding_state["last_error"] = (
                                    "promotion interrupted after binding dispatch intent"
                                )
                                binding_state["updated_at"] = _now()
                                self._write_promotion(operation)
                            raise PublicationError(
                                "release attestation binding outcome is uncertain; "
                                "reconcile it explicitly before resuming promotion"
                            )
                        raise PublicationError(
                            "promotion has an invalid attestation binding state"
                        )
                    binding_state = {
                        "status": "dispatching",
                        "attestation_set": exact_set.to_dict(),
                        "updated_at": _now(),
                    }
                    operation["attestation_binding"] = binding_state
                    self._write_promotion(operation)
                    try:
                        bound = self.remote.put_release_attestation_set(exact_set).validate_plan(plan)
                    except Exception as exc:
                        binding_state["status"] = "uncertain"
                        binding_state["last_error"] = f"{type(exc).__name__}: {exc}"[:1024]
                        binding_state["updated_at"] = _now()
                        self._write_promotion(operation)
                        raise
                    if bound != exact_set:
                        raise PublicationError(
                            "remote registry bound a different release attestation set"
                        )
                    binding_state["status"] = "completed"
                    binding_state["completed_via"] = "write_acknowledgement"
                    binding_state["updated_at"] = _now()
                    self._promotion_receipt(
                        operation,
                        "attestations_bound",
                        {"attestation_set": bound.to_dict()},
                    )

            channel_receipt = receipts.get("channel_moved")
            if isinstance(channel_receipt, Mapping):
                pointer = ChannelPointer.from_mapping(channel_receipt["pointer"])
                observed_pointer = self.remote.get_channel(candidate.project_id, "stable")
                if observed_pointer.release_digest != candidate.release_digest:
                    raise PublicationError(
                        "stable moved again after candidate promotion; local continuation is blocked"
                    )
                pointer = observed_pointer
            else:
                observed_pointer = self._channel_or_none(candidate.project_id, "stable")
                if (
                    observed_pointer is not None
                    and observed_pointer.release_digest == candidate.release_digest
                ):
                    pointer = observed_pointer
                else:
                    expected = operation.get("expected_base_digest")
                    observed_digest = (
                        observed_pointer.release_digest if observed_pointer is not None else None
                    )
                    if observed_digest != expected:
                        raise PublicationError(
                            "stable changed after promotion admission: "
                            f"expected {expected or '<absent>'}, "
                            f"observed {observed_digest or '<absent>'}"
                        )
                    pointer = self.remote.set_channel(
                        plan,
                        "stable",
                        expected_release_digest=expected,
                    )
                self._promotion_receipt(
                    operation,
                    "channel_moved",
                    {"pointer": pointer.to_dict()},
                )

            activation_receipt = receipts.get("workspace_activated")
            if isinstance(activation_receipt, Mapping):
                raw_lock = activation_receipt.get("workspace_lock")
                if not isinstance(raw_lock, Mapping):
                    raise PublicationError("promotion activation receipt has no WorkspaceLock")
                if not isinstance(activation_receipt.get("reload_receipt"), Mapping) or not isinstance(
                    activation_receipt.get("health_receipt"), Mapping
                ):
                    raise PublicationError(
                        "promotion activation predates mandatory reload/health receipts"
                    )
                activation = ActivationResult(
                    operation_id=str(activation_receipt.get("operation_id") or ""),
                    status="completed",
                    workspace_lock=WorkspaceLock.from_mapping(raw_lock),
                    release_digest=candidate.release_digest,
                    idempotent_replay=True,
                )
            else:
                activation_key = f"stable:{candidate.release_digest}"
                recovery_receipt = receipts.get("activation_recovered")
                if isinstance(recovery_receipt, Mapping):
                    activation_key = str(
                        recovery_receipt.get("new_idempotency_key") or ""
                    ).strip()
                    if not activation_key:
                        raise PublicationError(
                            "promotion activation recovery receipt has no next idempotency key"
                        )
                activation = activation_manager.activate(
                    plan,
                    idempotency_key=activation_key,
                    slot_id=slot_id,
                    fetch_package=self.remote.fetch_package,
                    reload_runtime=reload_runtime,
                    health_check=health_check,
                    reload_policy=reload_policy,
                    health_policy=health_policy,
                    permission_decision=permission_decision,
                    migration_executor=migration_executor,
                    migration_rollback=migration_rollback,
                    repair_reporter=self._record_builder_repair,
                )
                activation_operation = json.loads(
                    activation_manager.operation_path(activation.operation_id).read_text(
                        encoding="utf-8"
                    )
                )
                self._promotion_receipt(
                    operation,
                    "workspace_activated",
                    {
                        "operation_id": activation.operation_id,
                        "lock_digest": activation.workspace_lock.to_dict()["lock_digest"],
                        "workspace_lock": activation.workspace_lock.to_dict(),
                        "reload_receipt": activation_operation["reload_receipt"],
                        "health_receipt": activation_operation["health_receipt"],
                    },
                )

            if not isinstance(receipts.get("projection_recorded"), Mapping):
                self.release_cache.put_release(plan)
                self._record_workspace_projection(plan)
                self._promotion_receipt(
                    operation,
                    "projection_recorded",
                    {"release_digest": candidate.release_digest},
                )

            subscription_receipt = receipts.get("subscription_saved")
            if isinstance(subscription_receipt, Mapping):
                raw_subscription = subscription_receipt.get("subscription")
                if not isinstance(raw_subscription, Mapping):
                    raise PublicationError("promotion receipt has no stable subscription")
                subscription = StableSubscription.from_mapping(raw_subscription)
            else:
                subscription = StableSubscription(
                    project_id=candidate.project_id,
                    installed_release=pointer.release,
                    installed_digest=pointer.release_digest,
                )
                self.subscriptions.save(subscription)
                self._promotion_receipt(
                    operation,
                    "subscription_saved",
                    {"subscription": subscription.to_dict()},
                )

            operation["status"] = "completed"
            operation["phase"] = "completed"
            operation["completed_at"] = _now()
            self._write_promotion(operation)
            return PromotionResult(candidate, plan, pointer, activation, subscription)
        except Exception as exc:
            operation["status"] = "paused"
            operation["error"] = f"{type(exc).__name__}: {exc}"
            operation["paused_at"] = _now()
            self._write_promotion(operation)
            raise

    def _completed_promotion_result(
        self,
        candidate: CandidateRecord,
        plan: ReleasePlan,
        operation: Mapping[str, Any],
    ) -> PromotionResult:
        """Materialize a terminal promotion exclusively from durable receipts.

        A completed promotion is immutable.  Revalidating its pre-activation
        admission against a later merged WorkspaceLock can manufacture drift
        (notably after an explicit activation recovery) and must never cause a
        second registry write or activation.  We instead verify that the
        currently installed lock still contains the exact promoted dependency
        closure and return the recorded terminal result.
        """

        receipts = operation.get("receipts")
        if not isinstance(receipts, Mapping):
            raise PublicationError("completed promotion has no durable receipts")
        channel_receipt = receipts.get("channel_moved")
        activation_receipt = receipts.get("workspace_activated")
        subscription_receipt = receipts.get("subscription_saved")
        if not isinstance(channel_receipt, Mapping):
            raise PublicationError("completed promotion has no channel receipt")
        if not isinstance(activation_receipt, Mapping):
            raise PublicationError("completed promotion has no activation receipt")
        if not isinstance(subscription_receipt, Mapping):
            raise PublicationError("completed promotion has no subscription receipt")
        raw_lock = activation_receipt.get("workspace_lock")
        raw_subscription = subscription_receipt.get("subscription")
        if not isinstance(raw_lock, Mapping):
            raise PublicationError("completed promotion activation receipt has no WorkspaceLock")
        if not isinstance(raw_subscription, Mapping):
            raise PublicationError("completed promotion receipt has no stable subscription")
        recorded_lock = WorkspaceLock.from_mapping(raw_lock)
        manager = WorkspaceActivationManager(
            workspace_root=self.workspace_root,
            package_store=self.package_store,
            state_root=self.state_root / "activation",
            attestation_admission=self.attestation_admission,
        )
        active_lock = manager.load_lock()
        active_components = {
            item.key: item for item in (active_lock.components if active_lock else ())
        }
        missing_or_changed = [
            package.key
            for package in plan.packages
            if package.key not in active_components
            or active_components[package.key].digest != package.digest
        ]
        if missing_or_changed:
            raise PublicationError(
                "completed promotion dependency closure is no longer active: "
                + ", ".join(sorted(missing_or_changed))
            )
        return PromotionResult(
            candidate,
            plan,
            ChannelPointer.from_mapping(channel_receipt["pointer"]),
            ActivationResult(
                operation_id=str(activation_receipt.get("operation_id") or ""),
                status="completed",
                workspace_lock=recorded_lock,
                release_digest=candidate.release_digest,
                idempotent_replay=True,
            ),
            StableSubscription.from_mapping(raw_subscription),
        )

    def _record_workspace_projection(self, plan: ReleasePlan) -> None:
        component = next(
            (
                item
                for item in plan.release.components
                if item.artifact_id == plan.release.project_id
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


__all__ = [
    "PUSHED_SOURCE_SCHEMA",
    "PROMOTION_OPERATION_SCHEMA",
    "REBASE_PLAN_SCHEMA",
    "ArtifactPublicationService",
    "CandidateRebasePlan",
    "PreparedCandidate",
    "PromotionResult",
    "PublicationError",
    "PublicationStaleError",
    "PublicationRemote",
    "PushedSourceRecord",
    "SubscriptionUpdateNotice",
    "SubscriptionUpdateResult",
]
