"""Core local Trial placement over exact immutable release/data contracts."""
from __future__ import annotations

import json
from pathlib import Path

from adaos.domain.application import ApplicationRelease
from adaos.domain.artifact_release import ProjectRelease
from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore
from adaos.services.workspaces.relations import WebspaceRelationshipRegistry

from .access_management import ApplicationAccessManagementService
from .lifecycle_operations import ApplicationLifecycleOperations
from .local_release_transition import bind_local_data_lifecycle
from .materialization import request_materialization
from .publisher_owner_access import ensure_publisher_owner_access
from .release_setup import with_release_setup_contract
from .runtime import get_application_service
from .trial_runtime import NativeTrialRuntime


def prepare_local_trial(
    owner,
    application_id: str,
    candidate_id: str,
    *,
    webspace_id: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    beta_data_source: str | None = None,
    acknowledge_beta_data_reset: bool = False,
) -> dict:
    import hashlib
    from adaos.services.artifact_pipeline.storage import mutation_lock
    state = Path((getattr(owner, "authority_context", None) or owner).paths.state_dir())
    token = hashlib.sha256(application_id.encode()).hexdigest()
    with mutation_lock(state / "applications/operation-leases" / f"{token}.lock", timeout_s=.25):
        return _prepare_local_trial(owner, application_id, candidate_id, webspace_id=webspace_id,
            actor_ref=actor_ref, subnet_ref=subnet_ref, capability=capability,
            beta_data_source=beta_data_source,
            acknowledge_beta_data_reset=acknowledge_beta_data_reset)


def _prepare_local_trial(
    owner,
    application_id: str,
    candidate_id: str,
    *,
    webspace_id: str,
    actor_ref: str,
    subnet_ref: str,
    capability: str,
    beta_data_source: str | None = None,
    acknowledge_beta_data_reset: bool = False,
) -> dict:
    """Admit exact local publisher Trial without a Builder workflow/Scenario."""
    owner = getattr(owner, "authority_context", None) or owner
    operations = ApplicationLifecycleOperations(owner)
    operations._authorize(actor_ref, subnet_ref, capability, "applications.develop")
    state = Path(owner.paths.state_dir())
    service = get_application_service(state)
    application = service.store.get_application(application_id)
    if application.publisher_ref != subnet_ref:
        raise PermissionError("Only the local publisher can prepare a local Trial")
    if WebspaceRelationshipRegistry.from_context().resolve_production_host(webspace_id) != webspace_id:
        raise ValueError("Trial placement must target a production Webspace")
    candidate = CandidateStore(state / "artifact_pipeline/candidates").load(candidate_id)
    if candidate.project_id != application.legacy_project_id:
        raise ValueError("Candidate belongs to another Application")
    runtime = NativeTrialRuntime._resolve_immutable(owner, candidate_id, candidate.release_digest)
    release_path = runtime.root / ".adaos/releases" / f"{runtime.release_digest.split(':')[1]}.json"
    release = ProjectRelease.from_mapping(json.loads(release_path.read_text(encoding="utf-8"))).seal()
    if release.release_digest != candidate.release_digest:
        raise ValueError("Candidate release identity changed")
    envelope = ApplicationRelease(application_id=application_id, publisher_ref=application.publisher_ref,
        project_release=release, accepted_candidate_id=candidate_id,
        acceptance_evidence=candidate.validation_evidence, provenance_refs=(release.release_digest,), lifecycle="trial")
    envelope = with_release_setup_contract(envelope, runtime)
    verification = ApplicationAccessManagementService(service).admit_release_stage(
        application_id, release_digest=envelope.release_digest, stage="trial", candidate_release=envelope)
    service.register_release(envelope)
    owner_access = ensure_publisher_owner_access(owner, service, application_id, release_digest=envelope.release_digest)
    binding_path = runtime.root / ".adaos/data-transition.json"
    effective_beta_data_source = (
        beta_data_source
        if beta_data_source is not None or binding_path.is_file()
        else "continue_beta"
    )
    data = bind_local_data_lifecycle(
        owner,
        runtime,
        release,
        application_id=application_id,
        beta_data_source=effective_beta_data_source,
    )
    selected_beta_data_source = str(
        (getattr(data, "beta_data_transition", {}) or {}).get("requested_source")
        or effective_beta_data_source
        or ""
    ).strip().lower().replace("-", "_")
    activations = TrialActivationStore(state / "artifact_pipeline/trial-activations")

    def preserve_runtime_state(key):
        from .generation_draft_transition import preserve_generation_drafts
        return preserve_generation_drafts(source_runtime=data.beta_source_runtime_root, target_runtime=runtime.root,
            recovery=data.recovery / "sdk-drafts", private=data.private, operation_key=key)

    def activate(_key):
        try:
            for package in runtime.packages:
                if package.kind == "skill":
                    runtime.ready_manager(package.artifact_id)
        except (ValueError, FileNotFoundError, RuntimeError, KeyError):
            runtime.manager(prepare=True)
        for package in runtime.packages:
            if package.kind == "skill":
                runtime.ready_manager(package.artifact_id)
        activation = activations.load(candidate_id)
        proof = {"operation_id": f"application-beta:{application_id}:{candidate_id}",
                 "contract_digest": data._contract()}
        activations.update(candidate_id, data_mode="snapshot",
            target=dict(activation["target"]) | {"webspace_id": webspace_id, "space_kind": "workspace"},
            safety_evidence=dict(activation.get("safety_evidence") or {}) | {"data_transition": proof})
        return {"ok": True, **proof}

    transition = data.prepare_beta(
        webspace_id=webspace_id,
        activate=activate,
        allow_beta_data_reset=(
            selected_beta_data_source == "stable_snapshot"
            and bool(acknowledge_beta_data_reset)
        ),
        prepare_runtime_state=preserve_runtime_state if data.beta_source_runtime_root else None)
    selection = service.store.get_runtime_selection(webspace_id, application_id)
    if selection.runtime_root_ref != f"trial:{candidate_id}" or selection.release_digest != candidate.release_digest:
        return {"ok": True, "status": "superseded_runtime_selection", "data_transition": transition,
                "runtime_selection": selection.to_dict()}
    activation = activations.load(candidate_id)
    proof = {"operation_id": transition.get("operation_id"), "contract_digest": data._contract(),
             "completed": bool(transition.get("completed"))}
    migrated_from_beta = bool(data.beta_source_ref)
    data_transition_selection = dict(getattr(data, "beta_data_transition", {}) or {})
    activation = activations.update(candidate_id, data_mode="snapshot", safety_evidence={
        **dict(activation.get("safety_evidence") or {}), "status": "verified",
        "mode": "beta_snapshot_forward" if migrated_from_beta else "stable_snapshot_forward" if data.stable_digest else "empty_initialization",
        "reason": "Core retained the verified source data and declared configuration in an isolated Trial.",
        "data_transition": proof,
        "data_transition_selection": data_transition_selection,
        **({"beta_source": {"runtime_root_ref": data.beta_source_ref[0], "release_digest": data.beta_source_ref[1]}}
           if migrated_from_beta else {})})
    refresh = request_materialization(owner, application_id, webspace_ids=(webspace_id,))
    from .trial_retention import cleanup_superseded_trials
    retention = cleanup_superseded_trials(owner, application_id)
    return {"ok": True, "runtime_selection": selection.to_dict(), "trial_activation": activation,
            "runtime_refresh": refresh, "data_transition": transition, "verification": verification,
            "data_transition_selection": data_transition_selection,
            "publisher_owner_access": owner_access, "trial_retention": retention}
