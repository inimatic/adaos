"""Application-authorized local Candidate decisions, independent of Builder."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from adaos.services.artifact_pipeline.candidate_decisions import LocalCandidateDecisionService
from adaos.services.artifact_pipeline.candidates import CandidateStore
from .development import ApplicationDevelopmentCoordinator
from .lifecycle_operations import ApplicationLifecycleOperations, assert_idle_jobs
from .materialization import request_materialization
from .runtime_channel import RuntimeChannelConflict
from adaos.services.artifact_pipeline.trial_activation import trial_workspace_root


def decide_local_candidate(owner, application_id: str, candidate_id: str, *, accepted: bool,
                           expected_candidate_digest: str, actor_ref: str, subnet_ref: str,
                           capability: str, idempotency_key: str,
                           acknowledge_write_loss: bool = False) -> dict:
    operations = ApplicationLifecycleOperations(owner)
    operations._authorize(actor_ref, subnet_ref, capability, "applications.develop")
    app = operations.store.get_application(application_id)
    if app.publisher_ref != subnet_ref:
        raise PermissionError("Only the local publisher may decide its Candidate")
    candidates = CandidateStore(operations.state / "artifact_pipeline/candidates")
    candidate = candidates.load(candidate_id)
    if candidate.project_id != app.legacy_project_id or candidate.digest != expected_candidate_digest:
        raise RuntimeChannelConflict("ReviewedCandidateChanged")
    if not accepted and not acknowledge_write_loss:
        raise ValueError("Reject requires acknowledgement that Beta writes remain retained, not adopted")
    intent = {"candidate_id": candidate_id, "candidate_digest": candidate.digest,
              "release_digest": candidate.release_digest, "accepted": accepted,
              "acknowledge_write_loss": acknowledge_write_loss}

    def execute():
        from adaos.services.artifact_pipeline.storage import mutation_lock
        import hashlib
        token = hashlib.sha256(application_id.encode()).hexdigest()
        with mutation_lock(operations.state / "applications/operation-leases" / f"{token}.lock", timeout_s=.25):
            current = candidates.load(candidate_id)
            if current.digest != expected_candidate_digest:
                raise RuntimeChannelConflict("ReviewedCandidateChanged")
            wanted = "accepted" if accepted else "rejected"
            if current.status != wanted:
                if current.status != "trial":
                    raise RuntimeChannelConflict("CandidateHasAnotherTerminalDecision")
                root = trial_workspace_root(operations.workspace, candidate_id)
                assert_idle_jobs(operations.state, application_id, root)
                snapshot = operations.observer.snapshot(application_id)
                if not accepted:
                    if snapshot["data"]["transition_pending"]:
                        raise RuntimeChannelConflict("RecoverPendingTransitionBeforeCandidateRejection")
                    if snapshot["candidate"]["candidate_id"] == candidate_id and snapshot["runtime"]["source"] == "local_trial":
                        # Core-authorized authoring rejection has the same local
                        # data safety boundary as operator deactivation. The
                        # durable runtime transition is correlated by Candidate;
                        # it completes before any immutable tree is archived.
                        operations._restore_source(SimpleNamespace(application_id=application_id,
                            operation_id=f"candidate-decision:{candidate_id}:{idempotency_key}",
                            plan={"action": "deactivate_beta", "candidate_id": candidate_id,
                                  "release_digest": candidate.release_digest, "webspace_id":
                                  snapshot["runtime"]["webspace_ids"][0],
                                  "expected_selections": snapshot["runtime"]["selections"],
                                  "affected_webspace_ids": snapshot["runtime"]["webspace_ids"]}))
                    references = operations._references(application_id, candidate_id)
                    if references:
                        raise RuntimeChannelConflict("CandidateRuntimeReferenced:" + ",".join(references))
                current = LocalCandidateDecisionService(state_root=operations.state / "artifact_pipeline",
                    workspace_root=operations.workspace).decide_candidate(candidate_id, accepted=accepted,
                    observations=({"actor": actor_ref, "idempotency_key": idempotency_key,
                                   "decision": wanted},))
            refresh = request_materialization(operations.owner, application_id)
            return {"ok": True, "candidate": current.to_dict(), "runtime_refresh": refresh}

    operation = ApplicationDevelopmentCoordinator(operations.state).execute("decide_trial", application_id,
        actor_ref=actor_ref, subnet_ref=subnet_ref, capability=capability, expected_revision=app.revision,
        idempotency_key=idempotency_key, intent=intent, callback=execute)
    return {**operation.get("result", {}), "development_operation_id": operation["operation_id"]}
