"""Join the ordinary Builder Trial path to whole-Project source custody."""
import hashlib
import json

import yaml

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.development_source_admission import source_write_admission
from .project_source_sync import capture_project, project_component_source, SourceSyncError
from .source_snapshots import SourceSnapshot, SourceSnapshotError
from .storage import atomic_write_json, mutation_lock


def _accepted_component_source(publication, pushed, *, source_kind, source_name, change_ids):
    """Read the immutable accepted archive, bound to its completed receipt.

    The package is a runtime projection and cannot prove accepted test bytes.
    Neither the current DEV directory nor a newer checkpoint may substitute
    for this archive when normalizing the Project source boundary.
    """
    for change_id in change_ids:
        if change_id not in pushed.change_ids:
            continue
        key = hashlib.sha256(change_id.encode('utf-8')).hexdigest()
        root = publication.state_root / 'checkpoint-intents' / f'{source_kind}s' / source_name
        journal = root / f'{key}.json'
        if not journal.is_file():
            continue
        try:
            intent = json.loads(journal.read_text(encoding='utf-8'))
            raw = (root / f'{key}.zip').read_bytes()
            receipt = intent.get('receipt', {})
            digest = hashlib.sha256(raw).hexdigest()
            if (intent.get('schema') != 'adaos.artifact.checkpoint_intent.v1'
                    or intent.get('status') != 'completed'
                    or intent.get('kind') != source_kind or intent.get('artifact_id') != source_name
                    or intent.get('change_id') != change_id
                    or intent.get('archive_sha256') != digest or receipt.get('sha256') != digest
                    or intent.get('source_tree') != pushed.source_tree
                    or receipt.get('tree_sha') != pushed.source_tree
                    or receipt.get('commit') != pushed.source_ref.revision):
                raise SourceSyncError('Accepted component archive differs from its checkpoint receipt')
            snapshot = SourceSnapshot.from_archive(raw)
            if snapshot.tree(pushed.source_tree) != pushed.source_tree:
                raise SourceSyncError('Accepted component archive differs from its checkpoint tree')
            return project_component_source(snapshot)
        except (OSError, ValueError, AttributeError, SourceSnapshotError) as exc:
            raise SourceSyncError('Accepted component checkpoint archive is unreadable') from exc
    raise SourceSyncError('Accepted component checkpoint archive is unavailable')


def _retained_trial_operation(owner, sync, scope, basis):
    operations = sync.root / scope["project_id"] / "operations"
    matching = {}
    for path in operations.glob("trial-*/intent.json"):
        if sync._read_json(path.with_name("trial-basis.json")) != basis:
            continue
        intent = sync._read_json(path)
        if intent.get("state") != "rejected" and all(
            intent.get("request", {}).get(key) == value for key, value in scope.items()
        ):
            matching[path.parent.name] = intent
    if len(matching) > 1:
        # Older clients keyed source capture to each verification or permission
        # attempt. Recover the original immutable release when it already exists.
        releases = owner._artifact_publication_service(
            owner._load_config(), migrate_legacy_layouts=False,
        ).release_cache
        pinned = set()
        for digest in releases.release_digests_by_version(scope["project_id"]).values():
            release = releases.get_release(scope["project_id"], digest).release
            for evidence in release.validation_evidence:
                custody = evidence.get("source_custody") or {}
                operation = custody.get("operation_id")
                intent = matching.get(operation)
                if intent and (custody.get("snapshot_digest"), custody.get("commit")) == (
                    intent.get("snapshot_digest"), intent.get("commit"),
                ):
                    pinned.add(operation)
        if len(pinned) > 1:
            raise SourceSyncError("Several immutable releases name this Trial checkpoint")
        if pinned:
            return pinned.pop()
    return min(matching, key=lambda operation: matching[operation]["sequence"], default=None)


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
    requested_operation = "trial-" + key[:48]
    change_id = "trial-" + canonical_payload_digest({"project_id": project_id,
        "change_ids": list(change_ids)}).split(":")[1][:48]
    basis_key = canonical_payload_digest(basis).split(":")[1]
    sync = owner.project_source_sync()
    scope = sync._scope(project_id, change_id)
    with mutation_lock(sync.root / project_id / ".trial-source.lock", timeout_s=120):
        request_path = sync._paths(scope, requested_operation)[0].with_name("trial-basis.json")
        requested_basis = sync._read_json(request_path)
        if requested_basis and requested_basis != basis:
            raise SourceSyncError("Trial idempotency key names another source basis")
        binding_path = sync.root / project_id / "trial-sources" / f"{basis_key}.json"
        binding = sync._read_json(binding_path)
        operation_id = binding.get("operation_id")
        if not operation_id:
            operation_id = _retained_trial_operation(owner, sync, scope, basis) or requested_operation
        intent_path, archive_path = sync._paths(scope, operation_id)
        basis_path = intent_path.with_name("trial-basis.json")
        previous = sync._read_json(basis_path)
        if previous and previous != basis:
            raise SourceSyncError("Trial idempotency key names another source basis")
        if intent_path.is_file() or binding:
            if not previous:
                raise SourceSyncError("Retained Trial source lost its accepted basis")
            if not intent_path.is_file():
                raise SourceSyncError("Retained Trial source lost its confirmed intent")
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
                    accepted = _accepted_component_source(publication, pushed,
                        source_kind=source_kind, source_name=source_name, change_ids=change_ids)
                    if component.digest != accepted.digest:
                        raise SourceSyncError("Project Trial primary source changed after Automation acceptance")
                definition = yaml.safe_load(dict(frozen.entries)[f"projects/{project_id}/project.yaml"])
                primary = [m["ref"] for m in definition["components"]["owned"] if m.get("role") == "primary"]
                if primary != [f"{source_kind}:{source_name}"]:
                    raise SourceSyncError("Project Trial primary component differs from its accepted basis")
                atomic_write_json(basis_path, basis)
        if not requested_basis and request_path != basis_path:
            atomic_write_json(request_path, basis)
        # The source writer owns durable capture, CAS, lost-reply and proof reads.
        sync.checkpoint_frozen(project_id, change_id, operation_id=operation_id, snapshot=frozen)
        if not binding:
            atomic_write_json(binding_path, {"operation_id": operation_id})
    evidence = {**dict(validation_evidence or {}), "status": "passed",
                "source_snapshot_digest": frozen.digest}
    result = owner.prepare_project_candidate_from_source_checkpoint(project_id,
        change_id=change_id, operation_id=operation_id, validation_evidence=evidence,
        idempotency_key=idempotency_key, **admission)
    return {**result, "automation_source_basis": basis}
