"""Accept the selected immutable Candidate without mutable Builder authoring.

Core lifecycle admission owns the decision and recovery identity. Source delivery,
data cutover and Workspace adoption retain their existing effect owners.
"""
from __future__ import annotations

from typing import Any

from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore


def finalize_candidate_channel(owner, service, application_id: str, candidate_id: str, *,
                               release_digest: str) -> dict[str, Any]:
    """Join verified Project publication to its exact Application channel."""
    from .runtime import get_application_distribution_service
    try:
        distribution = get_application_distribution_service()
    except RuntimeError as error:
        if "not configured" not in str(error).lower():
            raise
        from adaos.services.project_deployment.default_runtime import configure_default_distributed_runtimes
        configure_default_distributed_runtimes(owner, authoritative=False)
        distribution = get_application_distribution_service()
    application = service.store.get_application(application_id)
    channels = service.store.get_channels(application_id).get("channels") or {}
    if channels.get("stable") == release_digest:
        return {"status": "already_finalized", "release_digest": release_digest}
    distribution.publish_trial(application_id, candidate_id, publisher_ref=application.publisher_ref,
        mode="prerelease" if channels.get("stable") and application.visibility == "public" else "link_only",
        expected_prerelease_digest=channels.get("prerelease") or None)
    result = distribution.promote_stable(application_id, candidate_id,
        publisher_ref=application.publisher_ref, expected_stable_digest=channels.get("stable"))
    if (not result.get("ok", True) or result.get("error")
            or service.store.get_channels(application_id).get("channels", {}).get("stable") != release_digest):
        raise ValueError("Exact Application Stable channel is not confirmed; recover this publication")
    return result


def accept_selected_candidate(owner, application_id: str, *, webspace_id: str,
                              candidate_id: str, candidate_digest: str,
                              actor_ref: str, subnet_ref: str,
                              capability: str = "applications.publish") -> dict[str, Any]:
    from .access_management import ApplicationAccessManagementService
    from .lifecycle_operations import ApplicationLifecycleOperations
    from .publication_readiness import verify_trial_publication
    from .trial_runtime import NativeTrialRuntime
    from .workspace_adoption import adopt_published_workspace
    from adaos.services.root.service import RootDeveloperService

    operations = ApplicationLifecycleOperations(owner)
    operations._authorize(actor_ref, subnet_ref, capability, "applications.publish")
    service, store = operations.service, operations.store
    application = store.get_application(application_id)
    selection = store.get_runtime_selection(webspace_id, application_id)
    release = store.get_release(application_id, selection.release_digest)
    candidates = CandidateStore(operations.state / "artifact_pipeline/candidates")
    candidate = candidates.load(candidate_id)
    if (application.publisher_ref != subnet_ref
            or candidate.project_id != application.legacy_project_id
            or candidate.package_digest != candidate_digest
            or candidate.release_digest != selection.release_digest
            or release.accepted_candidate_id != candidate_id
            or candidate.status not in {"trial", "accepted"}
            or selection.source not in {"local_trial", "stable_installation"}):
        raise ValueError("Exact selected publisher Candidate required for Stable acceptance")

    # Recheck the exact immutable runtime and publication policy before effects.
    # Recovery may observe the completed original cutover, never a different DEV.
    runtime = NativeTrialRuntime.resolve(owner, candidate_id, candidate.release_digest)
    if runtime.project_id != candidate.project_id:
        raise ValueError("TrialIdentityMismatch")
    activation = TrialActivationStore(
        operations.state / "artifact_pipeline/trial-activations").load(candidate_id)
    lock_digest = verify_trial_publication(store, activation or {},
        application_id=application_id, webspace_id=webspace_id,
        candidate_id=candidate_id, package_digest=candidate_digest,
        release_digest=candidate.release_digest, allow_completed=True)
    from .publication_readiness import verify_candidate_publication_tests
    test_acceptance = verify_candidate_publication_tests(owner, candidate)
    management = ApplicationAccessManagementService(service)
    publication_verification = management.promote_trial_verification_for_publication(
        application_id, release_digest=candidate.release_digest,
        publication_evidence=f"release:trial-runtime:{candidate_id}#{lock_digest.removeprefix('sha256:')}",
        actor_ref=actor_ref)
    access_verification = management.admit_release_stage(
        application_id, release_digest=candidate.release_digest, stage="publication")

    root = RootDeveloperService(ctx=owner)
    if candidate.status != "accepted":
        decision = root.decide_artifact_candidate(candidate_id, accepted=True,
            observations=({"actor": actor_ref, "decision": "accepted_for_publication",
                "idempotency_key": f"accept-local-trial:{application_id}:{candidate_id}:decision"},))
        decided = decision.get("candidate") or {}
        if (decided.get("candidate_id") != candidate_id or decided.get("status") != "accepted"
                or decided.get("package_digest") != candidate_digest
                or decided.get("release_digest") != candidate.release_digest):
            raise ValueError("Candidate acceptance is not confirmed")
    publication = root.promote_artifact_candidate(candidate_id, permission_decision={
        "approved": True, "actor": actor_ref, "actor_type": "user",
        "approval_id": f"candidate:{candidate_id}:publication"})
    if (not publication.get("ok", True) or publication.get("error")
            or publication.get("status") == "stale"
            or publication.get("candidate_id") != candidate_id
            or publication.get("release_digest") != candidate.release_digest
            or publication.get("package_digest") != candidate_digest):
        raise ValueError("Workspace publication is not confirmed; Trial selection is retained")

    stable = finalize_candidate_channel(owner, service, application_id, candidate_id,
        release_digest=candidate.release_digest)
    placement = adopt_published_workspace(owner, application_id,
        candidate_id=candidate_id, package_digest=candidate_digest,
        release_digest=candidate.release_digest, webspace_id=webspace_id,
        actor_ref=actor_ref, subnet_ref=subnet_ref, capability=capability,
        management=management)
    return {**placement, "publication": publication, "stable_promotion": stable,
        "test_acceptance": test_acceptance,
        "application_verification": access_verification,
        "publication_verification": publication_verification}
