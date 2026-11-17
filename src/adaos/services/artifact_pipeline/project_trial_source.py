"""Join the ordinary Builder Trial path to whole-Project source custody."""
import yaml

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.development_source_admission import source_write_admission
from .project_source_sync import capture_project, SourceSyncError
from .source_snapshots import SourceSnapshot
from .storage import atomic_write_json, mutation_lock


def prepare_checkpointed_trial(owner, project_id, *, source_kind, source_name,
                               source_revision, change_ids, validation_evidence=None,
                               idempotency_key=None, **admission):
    """Pin once, reconcile the same operation and never recapture later DEV.

    The component checkpoint remains the Automation acceptance basis. The
    complete Project is captured once and its primary source must match that
    basis before any remote write. All packages are built from that snapshot.
    """
    basis = {"project_id": project_id, "source_kind": source_kind, "source_name": source_name,
             "source_revision": source_revision, "change_ids": list(change_ids)}
    if not source_revision or not change_ids:
        raise SourceSyncError("Project Trial requires its accepted Automation checkpoint")
    key = canonical_payload_digest({"project_id": project_id,
        "idempotency_key": idempotency_key or canonical_payload_digest(basis)}).split(":")[1]
    operation_id = "trial-" + key[:48]
    change_id = "trial-" + canonical_payload_digest({"project_id": project_id,
        "change_ids": list(change_ids)}).split(":")[1][:48]
    sync = owner.project_source_sync()
    scope = sync._scope(project_id, change_id)
    intent_path, archive_path = sync._paths(scope, operation_id)
    basis_path = intent_path.parent / "trial-basis.json"
    with mutation_lock(sync.root / project_id / ".trial-source.lock", timeout_s=120):
        previous = sync._read_json(basis_path)
        if previous and previous != basis:
            raise SourceSyncError("Trial idempotency key names another source basis")
        if intent_path.is_file():
            if not previous:
                raise SourceSyncError("Retained Trial source lost its accepted basis")
            frozen = SourceSnapshot.from_archive(archive_path.read_bytes())
        else:
            cfg = owner._load_config()
            publication = owner._artifact_publication_service(cfg)
            pushed = publication.load_pushed_source(source_kind, source_name)
            if pushed.source_ref.revision != source_revision:
                raise SourceSyncError("Project Trial differs from its accepted component checkpoint")
            with source_write_admission(sync.workspace / "projects"):
                frozen = capture_project(sync.workspace, project_id)
                prefix = f"{source_kind}s/{source_name}/"
                component = SourceSnapshot(tuple((n[len(prefix):], raw) for n, raw in frozen.entries if n.startswith(prefix)))
                if component.tree(pushed.source_tree) != pushed.source_tree:
                    raise SourceSyncError("Project Trial primary source changed after Automation acceptance")
                definition = yaml.safe_load(dict(frozen.entries)[f"projects/{project_id}/project.yaml"])
                primary = [m["ref"] for m in definition["components"]["owned"] if m.get("role") == "primary"]
                if primary != [f"{source_kind}:{source_name}"]:
                    raise SourceSyncError("Project Trial primary component differs from its accepted basis")
                atomic_write_json(basis_path, basis)
        # The source writer owns durable capture, CAS, lost-reply and proof reads.
        sync.checkpoint_frozen(project_id, change_id, operation_id=operation_id, snapshot=frozen)
    evidence = {**dict(validation_evidence or {}), "status": "passed",
                "source_snapshot_digest": frozen.digest}
    result = owner.prepare_project_candidate_from_source_checkpoint(project_id,
        change_id=change_id, operation_id=operation_id, validation_evidence=evidence,
        idempotency_key=idempotency_key, **admission)
    return {**result, "automation_source_basis": basis}
