"""Project verified local Workspace publication; never consult Builder state."""
import json
from pathlib import Path

from adaos.domain.artifact_release import WorkspaceLock
from adaos.services.artifact_pipeline.candidates import CandidateStore
from adaos.services.artifact_pipeline.storage import mutation_lock
from .access_management import ApplicationAccessManagementService
from .lifecycle_operations import ApplicationLifecycleOperations
from .publisher_owner_access import ensure_publisher_owner_access
from .materialization import request_materialization


def adopt_published_workspace(owner, application_id: str, *, candidate_id: str, package_digest: str,
                              release_digest: str, webspace_id: str, actor_ref: str,
                              subnet_ref: str, capability: str, management=None) -> dict:
    operations = ApplicationLifecycleOperations(owner)
    operations._authorize(actor_ref, subnet_ref, capability, "applications.publish")
    app = operations.store.get_application(application_id)
    candidate = CandidateStore(operations.state / "artifact_pipeline/candidates").load(candidate_id)
    if (app.publisher_ref != subnet_ref or candidate.project_id != app.legacy_project_id
            or candidate.status != "accepted" or candidate.package_digest != package_digest
            or candidate.release_digest != release_digest):
        raise ValueError("Exact accepted publisher Candidate required for Workspace adoption")
    release = operations.store.get_release(application_id, release_digest)
    if release.accepted_candidate_id != candidate_id:
        raise ValueError("Published Application release belongs to another Candidate")
    management = management or ApplicationAccessManagementService(operations.service)
    management.admit_release_stage(application_id, release_digest=release_digest, stage="publication")
    owner_access = ensure_publisher_owner_access(owner, operations.service, application_id,
        release_digest=release_digest, management=management)
    metadata = Path(operations.workspace) / ".adaos"
    with mutation_lock(metadata / ".workspace-writer.lock", timeout_s=30):
        lock = WorkspaceLock.from_mapping(json.loads((metadata / "workspace.lock.json").read_text(encoding="utf-8")))
        if not any(slot.project_id == app.legacy_project_id and slot.release_digest == release_digest for slot in lock.slots):
            raise ValueError("Exact committed publication must be present in the WorkspaceLock")
        installation = operations.service.reconcile_workspace_installation(application_id, release_digest, lock)
        try:
            selection = operations.store.get_runtime_selection(webspace_id, application_id)
        except FileNotFoundError:
            selection = None
        if selection and selection.release_digest != release_digest:
            raise ValueError("RuntimeSelection changed; do not overwrite another selected release")
        if selection is None or selection.source != "stable_installation":
            selection = operations.service.select_runtime(webspace_id=webspace_id, application_id=application_id,
                source="stable_installation", release_digest=release_digest, runtime_root_ref="workspace",
                expected_revision=selection.revision if selection else 0, actor_ref=actor_ref,
                subnet_ref=subnet_ref, capability="applications.apply")
    return {"ok": True, "installation": installation.to_dict(), "runtime_selection": selection.to_dict(),
            "publisher_owner_access": owner_access, "runtime_refresh": request_materialization(owner, application_id)}
