from __future__ import annotations

from typing import Any, Mapping

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ProjectRelease,
    WorkspaceLock,
    canonical_payload_digest,
)
from adaos.services.governed_workflow import validate_workflow_record


WORKFLOW_DEFINITION_ARTIFACT_SCHEMA = "adaos.workflow.definition_artifact.v1"
WORKFLOW_ADMISSION_SCHEMA = "adaos.workflow.admission.v1"


class WorkflowAdmissionError(ValueError):
    """Raised when code, workflow definitions, adapters, and migrations diverge."""


def workflow_definition_artifact_record(
    package: ArtifactPackageRef,
    *,
    previous: ArtifactPackageRef | None = None,
    migration_id: str | None = None,
) -> dict[str, Any]:
    if package.workflow_lock is None:
        raise WorkflowAdmissionError(f"package has no workflow definition: {package.key}")
    definition_changed = bool(
        previous is not None
        and previous.workflow_lock is not None
        and previous.workflow_lock.digest != package.workflow_lock.digest
    )
    binding_changed = bool(
        previous is not None
        and previous.workflow_binding_digest != package.workflow_binding_digest
    )
    record = {
        "schema": WORKFLOW_DEFINITION_ARTIFACT_SCHEMA,
        "component": package.key,
        "package_digest": package.digest,
        "definition": package.workflow_lock.to_dict(),
        "validation": (
            package.workflow_validation_lock.to_dict()
            if package.workflow_validation_lock is not None
            else None
        ),
        "binding_digest": package.workflow_binding_digest,
        "role_policy_digest": package.workflow_role_policy_digest,
        "adapter_locks": [item.to_dict() for item in package.workflow_adapter_locks],
        "definition_changed": definition_changed,
        "binding_changed": binding_changed,
        "migration_id": migration_id,
    }
    validate_workflow_record(WORKFLOW_DEFINITION_ARTIFACT_SCHEMA, record)
    return record


def workflow_admission_record(
    *,
    current: WorkspaceLock | None,
    desired: WorkspaceLock,
    release: ProjectRelease,
    candidate_keys: set[str] | frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Bind workflow definitions, adapter contracts, and migrations as one generation."""

    before = {item.key: item for item in (current.components if current else ())}
    entries: list[dict[str, Any]] = []
    required_migrations: list[str] = []
    active_components = {item.key for item in desired.components}
    for package in sorted(desired.components, key=lambda item: item.key):
        if package.workflow_lock is None:
            continue
        if package.key in candidate_keys and (
            package.workflow_validation_lock is None
            or package.workflow_binding_digest is None
        ):
            raise WorkflowAdmissionError(
                f"candidate workflow package has no complete binding: {package.key}"
            )
        previous = before.get(package.key)
        definition_changed = bool(
            previous is not None
            and previous.workflow_lock is not None
            and previous.workflow_lock.digest != package.workflow_lock.digest
        )
        migration_id = None
        if definition_changed:
            previous_lock = previous.workflow_lock
            previous_identity = str(previous_lock.lock_id)
            target_identity = str(package.workflow_lock.lock_id)
            if previous_identity == target_identity:
                raise WorkflowAdmissionError(
                    "workflow definition bytes changed without a definition version bump: "
                    f"{package.key}"
                )
            migration = _find_exact_migration(
                release.migrations,
                component_key=package.key,
                from_digest=previous_lock.digest,
                to_digest=package.workflow_lock.digest,
            )
            if migration is None:
                raise WorkflowAdmissionError(
                    "workflow definition upgrade requires an exact migration contract: "
                    f"{package.key}"
                )
            migration_id = str(
                migration.get("id") or migration.get("name") or ""
            ).strip()
            if not migration_id:
                raise WorkflowAdmissionError(
                    f"workflow migration has no stable id: {package.key}"
                )
            required_migrations.append(migration_id)
        for adapter in package.workflow_adapter_locks:
            if adapter.owner_scope == "platform":
                continue
            if adapter.owner_package not in active_components:
                raise WorkflowAdmissionError(
                    f"workflow adapter owner package is inactive: {adapter.owner_package}"
                )
        entries.append(
            workflow_definition_artifact_record(
                package,
                previous=previous,
                migration_id=migration_id,
            )
        )
    unsigned = {
        "schema": WORKFLOW_ADMISSION_SCHEMA,
        "workspace_lock_digest": desired.to_dict()["lock_digest"],
        "release_digest": release.release_digest or release.computed_digest(),
        "workflows": entries,
        "required_migrations": sorted(required_migrations),
    }
    admission = {
        **unsigned,
        "candidate_generation_digest": canonical_payload_digest(unsigned),
        "status": "admitted" if entries else "not_required",
        "diagnostics": [],
    }
    validate_workflow_record(WORKFLOW_ADMISSION_SCHEMA, admission)
    return admission


def _find_exact_migration(
    migrations: tuple[Mapping[str, Any], ...],
    *,
    component_key: str,
    from_digest: str,
    to_digest: str,
) -> dict[str, Any] | None:
    for item in migrations:
        if (
            str(item.get("workflow_component") or "") == component_key
            and str(item.get("from_definition_digest") or "") == from_digest
            and str(item.get("to_definition_digest") or "") == to_digest
        ):
            return dict(item)
    return None


__all__ = [
    "WORKFLOW_ADMISSION_SCHEMA",
    "WORKFLOW_DEFINITION_ARTIFACT_SCHEMA",
    "WorkflowAdmissionError",
    "workflow_admission_record",
    "workflow_definition_artifact_record",
]
