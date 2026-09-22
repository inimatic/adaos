"""Resolution planning and CBS-aware integration with workspace activation."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, Mapping

from adaos.domain.artifact_release import WorkspaceLock, canonical_payload_digest
from adaos.domain.capability_binding_state import (
    ApplicationResolution,
    BindingInstance,
    ResolutionPlan,
    StateSpace,
)
from adaos.services.artifact_pipeline.activation import (
    ActivationConflictError,
    ActivationResult,
    WorkspaceActivationManager,
)
from adaos.services.artifact_pipeline.releases import ReleasePlan
from adaos.services.capability_binding_state.local_state import LocalIdentityStore
from adaos.services.resources.local import LocalCrudResourceService


class ResolutionPlanError(ValueError):
    pass


class ResolutionPlanExpired(ResolutionPlanError):
    pass


class StaleWriterError(PermissionError):
    pass


def _parse_time(value: Any) -> datetime:
    token = str(value or "").strip()
    try:
        parsed = datetime.fromisoformat(token.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ResolutionPlanError(f"invalid plan timestamp: {token}") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


@dataclass(slots=True)
class ResolutionPlanner:
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)

    def build(
        self,
        resolution: ApplicationResolution,
        *,
        current_lock: WorkspaceLock | None,
        ttl: timedelta = timedelta(minutes=15),
        provisioning: Iterable[Mapping[str, Any]] = (),
        migrations: Iterable[Mapping[str, Any]] = (),
    ) -> ResolutionPlan:
        created = self.now().astimezone(timezone.utc).replace(microsecond=0)
        expires = created + ttl
        if ttl.total_seconds() <= 0:
            raise ResolutionPlanError("ResolutionPlan ttl must be positive")
        value = resolution.to_dict()
        migration_values = [dict(item) for item in migrations]
        steps: list[dict[str, str]] = [
            {"step_id": "validate-preconditions", "kind": "read_only", "retry": "safe"},
            {"step_id": "stage-packages", "kind": "idempotent", "retry": "safe"},
        ]
        if provisioning:
            steps.append(
                {"step_id": "provision-state", "kind": "compensatable", "retry": "after_reconcile"}
            )
        if migration_values:
            steps.append(
                {"step_id": "migrate-state", "kind": "compensatable", "retry": "after_reconcile"}
            )
        steps.extend(
            (
                {"step_id": "switch-authority", "kind": "reconcile_only", "retry": "after_reconcile"},
                {"step_id": "verify-health", "kind": "read_only", "retry": "safe"},
            )
        )
        base_lock: dict[str, Any] = {
            "revision": current_lock.lock_revision if current_lock is not None else 0
        }
        if current_lock is not None:
            base_lock["digest"] = current_lock.to_dict()["lock_digest"]
        desired_bindings = [dict(item) for item in value["binding_instances"]]
        desired_states = [dict(item) for item in value["state_attachments"]]
        expected_generations = [
            {
                "state_space_ref": item["state_space_ref"],
                "revision_digest": item["revision_digest"],
                "generation": item["generation"],
            }
            for item in desired_states
        ]
        expected_epochs = [
            {
                "subject_ref": item["ref"],
                "revision_digest": item["revision_digest"],
                "authority_epoch": item["authority_epoch"],
            }
            for item in desired_bindings
        ] + [
            {
                "subject_ref": item["state_space_ref"],
                "revision_digest": item["revision_digest"],
                "authority_epoch": item["authority_epoch"],
            }
            for item in desired_states
        ]
        seed = {
            "resolution_digest": resolution.digest,
            "base_lock": base_lock,
            "created_at": created.isoformat(),
            "expires_at": expires.isoformat(),
        }
        plan_token = canonical_payload_digest(seed).split(":", 1)[1][:24]
        return ResolutionPlan.create(
            plan_ref=f"resolution-plan:{plan_token}",
            application_resolution_ref=resolution.resolution_ref,
            application_resolution_digest=resolution.digest,
            base_lock=base_lock,
            desired_bindings=desired_bindings,
            desired_state_attachments=desired_states,
            provisioning=provisioning,
            migrations=migration_values,
            evidence=value["evidence"],
            steps=steps,
            expected_generations=expected_generations,
            expected_authority_epochs=expected_epochs,
            compensation=(
                {
                    "action": "retain_previous_workspace_lock",
                    "until": "authority_commit",
                },
            ),
            recovery={
                "pre_commit": "discard_or_reconcile_staging_and_keep_base_lock",
                "post_commit": "reconcile_exact_committed_plan",
            },
            created_at=created.isoformat(),
            expires_at=expires.isoformat(),
        )


@dataclass(frozen=True, slots=True)
class CBSActivationResult:
    activation: ActivationResult
    resolution_plan_digest: str
    application_resolution_digest: str


@dataclass(slots=True)
class CBSActivationCoordinator:
    activation_manager: WorkspaceActivationManager
    identity_store: LocalIdentityStore
    now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)
    state_generation_observer: Callable[[str], int] | None = None
    evidence_status_observer: Callable[
        [tuple[Mapping[str, Any], ...]], Mapping[str, str]
    ] | None = None

    def activate(
        self,
        resolution_plan: ResolutionPlan,
        *,
        resolution: ApplicationResolution,
        release_plan: ReleasePlan,
        idempotency_key: str,
        phase_hook: Callable[[str], None] | None = None,
        reload_runtime: Callable[[WorkspaceLock], Any] | None = None,
        health_check: Callable[[WorkspaceLock], Any] | None = None,
        reload_policy: Mapping[str, Any] | None = None,
        health_policy: Mapping[str, Any] | None = None,
    ) -> CBSActivationResult:
        self._validate_static(resolution_plan, resolution, release_plan)
        self._validate_preconditions(resolution_plan)
        self._validate_evidence_preconditions(resolution_plan)
        plan_value = resolution_plan.to_dict()
        resolution_value = resolution.to_dict()
        evidence_set_digest = canonical_payload_digest(resolution_value["evidence"])
        cbs_lock = {
            "application_resolution_ref": resolution.resolution_ref,
            "application_resolution_digest": resolution.digest,
            "resolution_plan_ref": resolution_plan.plan_ref,
            "resolution_plan_digest": resolution_plan.digest,
            "writer_protocol": "cbs-single-writer-v1",
            "binding_instances": [dict(item) for item in plan_value["desired_bindings"]],
            "state_spaces": [dict(item) for item in plan_value["desired_state_attachments"]],
            "state_attachments": [dict(item) for item in plan_value["desired_state_attachments"]],
            "evidence_set_digest": evidence_set_digest,
        }

        def checked_phase(phase: str) -> None:
            if phase == "switch-lock":
                self._validate_preconditions(resolution_plan)
                self._validate_evidence_preconditions(resolution_plan)
            if phase_hook is not None:
                phase_hook(phase)

        base_digest = plan_value["base_lock"].get("digest")
        activation = self.activation_manager.activate(
            release_plan,
            idempotency_key=idempotency_key,
            phase_hook=checked_phase,
            reload_runtime=reload_runtime,
            health_check=health_check,
            reload_policy=reload_policy,
            health_policy=health_policy,
            expected_lock_digest=base_digest,
            cbs_lock=cbs_lock,
            application_resolution_digest=resolution.digest,
            resolution_plan_digest=resolution_plan.digest,
        )
        return CBSActivationResult(
            activation=activation,
            resolution_plan_digest=resolution_plan.digest,
            application_resolution_digest=resolution.digest,
        )

    def _validate_static(
        self,
        plan: ResolutionPlan,
        resolution: ApplicationResolution,
        release_plan: ReleasePlan,
    ) -> None:
        value = plan.to_dict()
        if value["application_resolution_ref"] != resolution.resolution_ref or value[
            "application_resolution_digest"
        ] != resolution.digest:
            raise ResolutionPlanError("ResolutionPlan is bound to another ApplicationResolution")
        resolution_value = resolution.to_dict()
        if value["desired_bindings"] != resolution_value["binding_instances"]:
            raise ResolutionPlanError("ResolutionPlan changes admitted BindingInstances")
        if value["desired_state_attachments"] != resolution_value["state_attachments"]:
            raise ResolutionPlanError("ResolutionPlan changes admitted StateSpace attachments")
        if value["evidence"] != resolution_value["evidence"]:
            raise ResolutionPlanError("ResolutionPlan changes admitted evidence")
        if _parse_time(value["expires_at"]) <= self.now().astimezone(timezone.utc):
            raise ResolutionPlanExpired("ResolutionPlan has expired")
        release_digest = release_plan.release.release_digest or release_plan.release.computed_digest()
        if release_digest != resolution_value["project_release_digest"]:
            raise ResolutionPlanError("ResolutionPlan release differs from ApplicationResolution")
        irreversible = [
            item
            for item in value["migrations"]
            if item["classification"] == "irreversible" and not item["proven"]
        ]
        irreversible_steps = [item for item in value["steps"] if item["kind"] == "irreversible"]
        if irreversible or irreversible_steps:
            raise ResolutionPlanError(
                "unproven irreversible actions require an attended external workflow"
            )
        if any(item.get("status") != "admissible" for item in value["evidence"]):
            raise ResolutionPlanError(
                "ResolutionPlan activation requires currently admissible evidence"
            )

    def _validate_evidence_preconditions(self, plan: ResolutionPlan) -> None:
        if self.evidence_status_observer is None:
            return
        evidence = tuple(dict(item) for item in plan.to_dict()["evidence"])
        observed = {
            str(key): str(value)
            for key, value in self.evidence_status_observer(evidence).items()
        }
        invalid = [
            item["claim_digest"]
            for item in evidence
            if observed.get(str(item["claim_digest"])) != "admissible"
        ]
        if invalid:
            raise ActivationConflictError(
                "evidence freshness changed after planning: " + ", ".join(invalid)
            )

    def _validate_preconditions(self, plan: ResolutionPlan) -> None:
        value = plan.to_dict()
        current = self.activation_manager.load_lock()
        observed_digest = current.to_dict()["lock_digest"] if current is not None else None
        observed_revision = current.lock_revision if current is not None else 0
        expected = value["base_lock"]
        if expected.get("digest") != observed_digest or expected["revision"] != observed_revision:
            raise ActivationConflictError(
                "ResolutionPlan base WorkspaceLock no longer matches active authority"
            )
        epochs = {item["subject_ref"]: item for item in value["expected_authority_epochs"]}
        for item in value["desired_bindings"]:
            record = self.identity_store.by_digest(
                item["ref"], item["revision_digest"], BindingInstance
            )
            if record.authority_epoch != item["authority_epoch"]:
                raise ActivationConflictError("BindingInstance authority epoch changed")
            if epochs.get(item["ref"]) != {
                "subject_ref": item["ref"],
                "revision_digest": item["revision_digest"],
                "authority_epoch": item["authority_epoch"],
            }:
                raise ActivationConflictError("BindingInstance epoch precondition is incomplete")
        generations = {item["state_space_ref"]: item for item in value["expected_generations"]}
        for item in value["desired_state_attachments"]:
            record = self.identity_store.by_digest(
                item["state_space_ref"], item["revision_digest"], StateSpace
            )
            if record.generation != item["generation"] or record.authority_epoch != item[
                "authority_epoch"
            ]:
                raise ActivationConflictError("StateSpace generation or authority epoch changed")
            if generations.get(item["state_space_ref"]) != {
                "state_space_ref": item["state_space_ref"],
                "revision_digest": item["revision_digest"],
                "generation": item["generation"],
            }:
                raise ActivationConflictError("StateSpace generation precondition is incomplete")
            if self.state_generation_observer is not None and self.state_generation_observer(
                item["state_space_ref"]
            ) != item["generation"]:
                raise ActivationConflictError("materialized state generation changed after planning")


@dataclass(slots=True)
class FencedLocalCrudWriter:
    service: LocalCrudResourceService
    activation_manager: WorkspaceActivationManager
    state_space_ref: str

    def operate(
        self,
        resource_type: str,
        operation_id: str,
        *,
        writer_epoch: int,
        record_id: str,
        payload: Mapping[str, Any],
        expected_revision: Any = None,
    ) -> dict[str, Any]:
        lock = self.activation_manager.load_lock()
        cbs = lock.cbs if lock is not None else None
        if not isinstance(cbs, Mapping):
            raise StaleWriterError("workspace has no CBS writer authority")
        spaces = [
            item
            for item in cbs.get("state_spaces") or ()
            if isinstance(item, Mapping) and item.get("state_space_ref") == self.state_space_ref
        ]
        if len(spaces) != 1 or int(spaces[0].get("authority_epoch") or 0) != int(writer_epoch):
            raise StaleWriterError(
                f"writer epoch {writer_epoch} is not active for {self.state_space_ref}"
            )
        return self.service.operate(
            resource_type,
            operation_id,
            record_id=record_id,
            payload=payload,
            expected_revision=expected_revision,
        )


__all__ = [
    "CBSActivationCoordinator",
    "CBSActivationResult",
    "FencedLocalCrudWriter",
    "ResolutionPlanError",
    "ResolutionPlanExpired",
    "ResolutionPlanner",
    "StaleWriterError",
]
