"""Recover duplicate legacy declarations from authoritative Application identity.

No model selects a winner. Only an unbound legacy declaration wholly contained
in one existing Application's Project can be retired automatically. Its complete
source is confirmed remotely first; its folder is retained intact in recovery.
"""
from pathlib import Path

import yaml

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.development_source_admission import (
    SourceAdmissionError, ownership_inventory, project_owned_refs, source_write_admission,
)
from adaos.services.artifact_pipeline.project_source_sync import _capture_project
from adaos.services.artifact_pipeline.source_snapshots import SourceSnapshot
from adaos.services.artifact_pipeline.storage import atomic_write_json, mutation_lock


def retire_legacy_project(*, sync, applications, canonical_project_id: str,
                          legacy_project_id: str, recovery_root: Path) -> dict:
    """Repeatable, preserving repair; refuse competing Applications or ownership.

    The supplied store is the native authoritative ApplicationStore. The caller
    already owns development authority. This function never removes components,
    creates an Application, rewrites manifests or changes a runtime selection.
    """
    scope = sync._scope(legacy_project_id, "legacy-ownership-recovery")
    canonical = sync._scope(canonical_project_id, "legacy-ownership-recovery")["project_id"]
    if canonical == legacy_project_id:
        raise SourceAdmissionError("Legacy and canonical Projects must differ")
    projects = sync.workspace / "projects"
    source = projects / legacy_project_id
    recovery = Path(recovery_root).resolve()
    if recovery.is_relative_to(sync.workspace) or sync.workspace.is_relative_to(recovery):
        raise SourceAdmissionError("Recovery must be outside the active DEV context")
    record_path = sync.root / legacy_project_id / "ownership-recovery.json"

    def authority():
        bindings = {}
        for app in applications.list_applications():
            bindings.setdefault(app.legacy_project_id, []).append(app.application_id)
        if len(bindings.get(canonical, [])) != 1 or bindings.get(legacy_project_id):
            raise SourceAdmissionError("Duplicate declaration has competing or missing Application authority")
        return bindings[canonical][0]

    with mutation_lock(sync.root / legacy_project_id / ".ownership-recovery.lock", timeout_s=120):
        application_id = authority()
        retained = sync._read_json(record_path)
        if retained and (retained.get("canonical_project_id") != canonical
                         or retained.get("application_id") != application_id):
            raise SourceAdmissionError("Ownership recovery belongs to another Application")
        if not retained:
            with source_write_admission(projects):
                authority()
                if (source / ".git").exists():
                    raise SourceAdmissionError("Operational Git checkouts require their source owner")
                definition = yaml.safe_load((source / "project.yaml").read_text(encoding="utf-8-sig"))
                canonical_definition = yaml.safe_load((projects / canonical / "project.yaml").read_text(encoding="utf-8-sig"))
                if canonical_definition.get("id") != canonical:
                    raise SourceAdmissionError("Canonical Project identity changed")
                legacy_refs = set(project_owned_refs(definition))
                canonical_refs = set(project_owned_refs(canonical_definition))
                primary = [m["ref"] for m in definition["components"]["owned"] if m.get("role") == "primary"]
                canonical_primary = [m["ref"] for m in canonical_definition["components"]["owned"] if m.get("role") == "primary"]
                owners = ownership_inventory(projects)
                if (not legacy_refs or not legacy_refs.issubset(canonical_refs)
                        or len(primary) != 1 or primary != canonical_primary
                        or any(set(owners.get(ref, [])) != {legacy_project_id, canonical} for ref in legacy_refs)):
                    raise SourceAdmissionError("Legacy ownership is not wholly contained in one canonical Project")
                frozen = _capture_project(sync.workspace, legacy_project_id, admit_ownership=False)
                operation_id = "custody-" + frozen.digest.split(":")[1][:48]
                retained = {"schema": "adaos.development.ownership_recovery.v1", "status": "prepared",
                    "legacy_project_id": legacy_project_id, "canonical_project_id": canonical,
                    "application_id": application_id, "operation_id": operation_id,
                    "snapshot_digest": frozen.digest, "canonical_manifest_digest": canonical_payload_digest(canonical_definition),
                    "destination": str(recovery / legacy_project_id / operation_id / "project")}
                # Freeze bytes before even a failed transport attempt. Later DEV
                # edits cannot redefine the recovery operation after restart.
                from adaos.services.artifact_pipeline.storage import atomic_write_bytes
                atomic_write_bytes(record_path.with_suffix(".zip"), frozen.archive())
                atomic_write_json(record_path, retained)
        frozen = SourceSnapshot.from_archive(record_path.with_suffix(".zip").read_bytes())
        if frozen.digest != retained["snapshot_digest"]:
            raise SourceAdmissionError("Ownership recovery snapshot changed")
        if retained["operation_id"].startswith("ownership-"):
            old_path, _ = sync._paths(scope, retained["operation_id"])
            old = sync._read_json(old_path)
            if old and old.get('state') != 'rejected':
                try:
                    sync.checkpoint_frozen(legacy_project_id, scope['change_id'],
                        operation_id=retained['operation_id'], snapshot=frozen)
                except Exception:
                    old = sync._read_json(old_path)
                    if old.get('state') != 'rejected':
                        raise
            if old.get('state') != 'rejected' or old.get('rejection_code') != 'source_component_owner_transfer_required':
                raise SourceAdmissionError('Prior ownership attempt needs explicit reconciliation')
            retained = {**retained, 'rejected_operation_id': retained['operation_id'],
                        'operation_id': 'custody-' + frozen.digest.split(':')[1][:48]}
            atomic_write_json(record_path, retained)
        sync.checkpoint_frozen(legacy_project_id, scope["change_id"],
            operation_id=retained["operation_id"], snapshot=frozen,
            custody={'canonical_project_id': canonical})
        intent_path, _ = sync._paths(scope, retained["operation_id"])
        proof = sync._read_json(intent_path)["receipt"]
        destination = Path(retained["destination"])
        # Validate both absolute resolved targets before a directory move. Only
        # this exact Project folder may leave DEV; no recursive deletion occurs.
        if (source.resolve() != projects.resolve() / legacy_project_id
                or not destination.resolve().is_relative_to(recovery)
                or source.is_symlink() or destination.is_symlink()):
            raise SourceAdmissionError("Ownership recovery paths changed")
        with source_write_admission(projects):
            authority()
            current = yaml.safe_load((projects / canonical / "project.yaml").read_text(encoding="utf-8-sig"))
            if canonical_payload_digest(current) != retained["canonical_manifest_digest"]:
                raise SourceAdmissionError("Canonical Project changed during ownership recovery")
            if source.exists():
                if _capture_project(sync.workspace, legacy_project_id, admit_ownership=False).digest != frozen.digest:
                    raise SourceAdmissionError("Legacy source changed; newer work is preserved for reconciliation")
                if destination.exists():
                    raise SourceAdmissionError("Both legacy and recovery directories exist; preserve both")
                destination.parent.mkdir(parents=True, exist_ok=True)
                # os.rename deliberately refuses cross-volume movement. A node
                # storage migration needs its own verified copy/cutover owner.
                source.rename(destination)
            elif not (destination / "project.yaml").is_file():
                raise SourceAdmissionError("Legacy declaration and recovery copy are both unavailable")
            retained = {**retained, "status": "completed", "source_receipt": proof}
            atomic_write_json(record_path, retained)
        return retained
