"""Core recovery of exact local transitions, including non-executable expired data."""
from pathlib import Path
from typing import Any, Mapping
from adaos.domain.application import utc_now
from .runtime import get_application_service
from .lifecycle_operations import ApplicationLifecycleOperations
from .materialization import request_materialization


def _application_for_project(service, project_id):
    matches = [a for a in service.store.list_applications() if a.legacy_project_id == project_id]
    if len(matches) != 1:
        raise ValueError("Recovery requires exactly one Application for the immutable Project")
    return matches[0]


def abort_local_trial_preparation(owner, candidate_id: str, *, release_digest: str, actor_ref: str,
                         subnet_ref: str, capability: str) -> dict[str, Any]:
    """Recover only the exact admitted local transition; never execute expired code."""
    owner = getattr(owner, "authority_context", None) or owner
    service = get_application_service(Path(owner.paths.state_dir()))
    operations = ApplicationLifecycleOperations(owner)
    import json

    from adaos.domain.artifact_release import ProjectRelease, WorkspaceLock
    from adaos.services.applications.local_release_transition import bind_local_data_lifecycle
    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.artifact_pipeline.storage import mutation_lock

    if not actor_ref.strip():
        raise ValueError("Recovery requires an actor")
    runtime = NativeTrialRuntime._resolve_immutable(owner, candidate_id, release_digest, for_data_recovery=True)
    path = runtime.root / ".adaos/releases" / f"{runtime.release_digest.split(':')[1]}.json"
    release = ProjectRelease.from_mapping(json.loads(path.read_text(encoding="utf-8"))).seal()
    application = _application_for_project(service, release.project_id)
    subnet = subnet_ref
    operations._authorize(actor_ref, subnet_ref, capability, "applications.recover")
    if application.publisher_ref.lower() != subnet.lower():
        raise ValueError("Only the local publisher may recover its Beta preparation")
    binding_path = runtime.root / ".adaos/data-transition.json"
    if not binding_path.is_file():
        # The immutable Trial may have been materialized and registered before
        # ownership/data admission rejected it.  In that case no migration
        # intent, runtime selection, or Workspace mutation exists to roll back.
        # Prove those negatives and retire only the exact activation record;
        # attempting to reconstruct a lifecycle here would repeat the original
        # admission failure and make the failed Candidate unrecoverable.
        selections = service.store.list_runtime_selections()
        if any(
            item.application_id == application.application_id
            and (
                item.release_digest == release_digest
                or item.runtime_root_ref == f"trial:{candidate_id}"
            )
            for item in selections
        ):
            raise ValueError(
                "Trial runtime selection exists without a data-transition binding"
            )
        lock_path = Path(owner.paths.workspace_dir()) / ".adaos/workspace.lock.json"
        if lock_path.is_file():
            lock = WorkspaceLock.from_mapping(
                json.loads(lock_path.read_text(encoding="utf-8"))
            )
            if any(
                slot.project_id == release.project_id
                and slot.release_digest == release_digest
                for slot in lock.slots
            ):
                raise ValueError(
                    "Workspace contains the failed release without a data-transition binding"
                )
        from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore

        activations = TrialActivationStore(
            Path(owner.paths.state_dir()) / "artifact_pipeline/trial-activations"
        )
        activation = activations.load(candidate_id)
        candidate_ref = (
            activation.get("candidate_ref") if isinstance(activation, dict) else None
        )
        if (
            not isinstance(candidate_ref, Mapping)
            or candidate_ref.get("candidate_id") != candidate_id
            or candidate_ref.get("release_digest") != release_digest
        ):
            raise ValueError("Exact failed TrialActivation record is missing")
        activations.update(
            candidate_id,
            status="failed",
            detached_at=utc_now(),
            safety_evidence={
                **dict(activation.get("safety_evidence") or {}),
                "status": "aborted_before_data_transition",
                "reason": "ownership_or_data_admission_rejected_before_mutation",
                "actor_ref": actor_ref,
            },
        )
        return {
            "ok": True,
            "status": "aborted_before_data_transition",
            "operation_id": None,
            "runtime_refresh": request_materialization(owner, application.application_id),
        }
    lifecycle = bind_local_data_lifecycle(owner, runtime, release, application_id=application.application_id)
    metadata = Path(owner.paths.workspace_dir()) / ".adaos"

    def verify(_key):
        if lifecycle.beta_source_ref:
            NativeTrialRuntime._resolve_immutable(owner, lifecycle.beta_source_ref[0].split(":", 1)[1],
                lifecycle.beta_source_ref[1])
            return {"ok": True, "release_digest": lifecycle.beta_source_ref[1]}
        lock = WorkspaceLock.from_mapping(json.loads((metadata / "workspace.lock.json").read_text(encoding="utf-8")))
        slots = [slot for slot in lock.slots if slot.project_id == release.project_id]
        if lifecycle.stable_digest:
            installation = service.store.get_installation(application.application_id)
            if (installation.status != "active" or installation.installed_release_digest != lifecycle.stable_digest
                    or len(slots) != 1 or slots[0].release_digest != lifecycle.stable_digest):
                raise ValueError("Stable code/installation changed; explicit publication recovery required")
        elif slots:
            raise ValueError("Workspace installation appeared after Beta preparation")
        return {"ok": True, "release_digest": lifecycle.stable_digest}

    result = lifecycle.abort_beta_preparation(verify_source=verify,
        source_guard=lambda: mutation_lock(metadata / ".workspace-writer.lock", timeout_s=30))
    return {"ok": True, "status": "aborted", "operation_id": result["operation_id"],
            "runtime_refresh": request_materialization(owner, application.application_id)}


def retire_failed_local_publication_preflight(owner, candidate_id: str, *, release_digest: str, actor_ref: str,
                         subnet_ref: str, capability: str) -> dict[str, Any]:
    """Recover only the exact admitted local transition; never execute expired code."""
    owner = getattr(owner, "authority_context", None) or owner
    service = get_application_service(Path(owner.paths.state_dir()))
    operations = ApplicationLifecycleOperations(owner)
    import json

    from adaos.domain.artifact_release import ProjectRelease, WorkspaceLock
    from adaos.services.applications.local_release_transition import bind_local_data_lifecycle
    from adaos.services.applications.trial_runtime import NativeTrialRuntime
    from adaos.services.artifact_pipeline.storage import mutation_lock

    if not actor_ref.strip():
        raise ValueError("Recovery requires an actor")
    runtime = NativeTrialRuntime._resolve_immutable(owner, candidate_id, release_digest, for_data_recovery=True)
    release_path = (
        runtime.root
        / ".adaos/releases"
        / f"{runtime.release_digest.split(':')[1]}.json"
    )
    release = ProjectRelease.from_mapping(
        json.loads(release_path.read_text(encoding="utf-8"))
    ).seal()
    application = _application_for_project(service, release.project_id)
    subnet = subnet_ref
    operations._authorize(actor_ref, subnet_ref, capability, "applications.recover")
    if application.publisher_ref.lower() != subnet.lower():
        raise ValueError("Only the local publisher may recover its Stable preflight")
    lifecycle = bind_local_data_lifecycle(owner, runtime, release, application_id=application.application_id)
    metadata = Path(owner.paths.workspace_dir()) / ".adaos"

    def verify(_key):
        lock = WorkspaceLock.from_mapping(
            json.loads((metadata / "workspace.lock.json").read_text(encoding="utf-8"))
        )
        slots = [slot for slot in lock.slots if slot.project_id == release.project_id]
        if lifecycle.stable_digest:
            installation = service.store.get_installation(
                application.application_id
            )
            if (
                installation.status != "active"
                or installation.installed_release_digest != lifecycle.stable_digest
                or len(slots) != 1
                or slots[0].release_digest != lifecycle.stable_digest
            ):
                raise ValueError(
                    "Stable code/installation changed; explicit publication recovery required"
                )
        elif slots:
            raise ValueError("Workspace installation appeared after Beta preparation")
        return {"ok": True, "release_digest": lifecycle.stable_digest}

    def guard():
        return mutation_lock(metadata / ".workspace-writer.lock", timeout_s=30)

    retired = lifecycle.abort_stable_adoption_before_effects(
        verify_source=verify,
        source_guard=guard,
    )
    selections = [
        item
        for item in lifecycle.channel.read() or ()
        if item.application_id == application.application_id
        and item.runtime_root_ref == f"trial:{candidate_id}"
        and item.release_digest == release_digest
    ]
    if len(selections) != 1:
        raise ValueError("Exact failed Trial selection is unavailable")
    rejected = lifecycle.reject_beta(
        webspace_id=selections[0].webspace_id,
        verify_source=verify,
        source_guard=guard,
    )
    return {
        "ok": True,
        "status": "retired_before_effects",
        "stable_recovery": retired,
        "beta_rejection": rejected,
        "runtime_refresh": request_materialization(owner, application.application_id),
    }
