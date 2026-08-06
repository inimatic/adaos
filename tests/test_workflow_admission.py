from __future__ import annotations

import pytest

from adaos.domain.artifact_release import (
    ArtifactContractLock,
    ArtifactPackageRef,
    ArtifactSourceRef,
    ProjectRelease,
    WorkflowAdapterLock,
    WorkspaceLock,
    WorkspaceSlot,
    canonical_payload_digest,
)
from adaos.services.workflow_admission import (
    WorkflowAdmissionError,
    workflow_admission_record,
    workflow_definition_artifact_record,
)


def _digest(token: str) -> str:
    return canonical_payload_digest({"token": token})


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/guided_checklist/",),
    )


def _package(
    version: str,
    *,
    definition_digest: str,
    adapter_locks: tuple[WorkflowAdapterLock, ...] | None = None,
) -> ArtifactPackageRef:
    return ArtifactPackageRef(
        kind="scenario",
        artifact_id="guided_checklist",
        version=version,
        digest=_digest(f"package:{version}:{definition_digest}"),
        manifest_digest=_digest(f"manifest:{version}"),
        source_ref=_source(),
        builder_id="builder.test",
        build_policy_digest=_digest("builder-policy"),
        materialization_path="scenarios/guided_checklist",
        schema_locks=(),
        workflow_lock=ArtifactContractLock(
            lock_id=f"workflow:scenario.guided_checklist@{version}",
            digest=definition_digest,
        ),
        workflow_validation_lock=ArtifactContractLock(
            lock_id=f"workflow-validation:scenario.guided_checklist@{version}",
            digest=_digest(f"validation:{version}:{definition_digest}"),
        ),
        workflow_adapter_locks=adapter_locks
        or (
            WorkflowAdapterLock(
                adapter_id="always",
                kind="guard",
                contract_digest=_digest("adapter:always"),
                owner_scope="platform",
            ),
        ),
        workflow_binding_digest=_digest(f"binding:{version}:{definition_digest}"),
        workflow_role_policy_digest=_digest(
            f"role-policy:{version}:{definition_digest}"
        ),
    )


def _release(
    package: ArtifactPackageRef,
    *,
    migrations: tuple[dict[str, object], ...] = (),
) -> ProjectRelease:
    return ProjectRelease(
        project_id="guided_checklist",
        version=package.version,
        source_ref=_source(),
        components=(package,),
        migrations=migrations,
    ).seal()


def _lock(revision: int, release: ProjectRelease) -> WorkspaceLock:
    return WorkspaceLock(
        lock_revision=revision,
        updated_at="2026-08-03T00:00:00+00:00",
        slots=(
            WorkspaceSlot(
                slot_id="primary",
                project_id=release.project_id,
                release=f"{release.project_id}@{release.version}",
                release_digest=release.release_digest or release.computed_digest(),
            ),
        ),
        components=release.components,
    )


def test_workflow_admission_record_binds_definition_validation_and_adapters() -> None:
    package = _package("1.0.0", definition_digest=_digest("definition:v1"))
    release = _release(package)
    desired = _lock(1, release)

    admission = workflow_admission_record(
        current=None,
        desired=desired,
        release=release,
        candidate_keys={package.key},
    )

    assert admission["schema"] == "adaos.workflow.admission.v1"
    assert admission["status"] == "admitted"
    assert admission["candidate_generation_digest"].startswith("sha256:")
    assert (
        admission["workflows"][0]["role_policy_digest"]
        == package.workflow_role_policy_digest
    )
    assert admission["workflows"] == [
        workflow_definition_artifact_record(package, previous=None)
    ]


def test_workflow_admission_requires_exact_migration_for_definition_change() -> None:
    v1 = _package("1.0.0", definition_digest=_digest("definition:v1"))
    v2 = _package("1.1.0", definition_digest=_digest("definition:v2"))
    current_release = _release(v1)
    current = _lock(1, current_release)
    desired_release_without_migration = _release(v2)
    desired = _lock(2, desired_release_without_migration)

    with pytest.raises(WorkflowAdmissionError, match="exact migration"):
        workflow_admission_record(
            current=current,
            desired=desired,
            release=desired_release_without_migration,
            candidate_keys={v2.key},
        )

    migration = {
        "id": "guided-checklist-workflow-1.1.0",
        "workflow_component": v2.key,
        "from_definition_digest": v1.workflow_lock.digest,
        "to_definition_digest": v2.workflow_lock.digest,
        "rollback": {
            "supported": True,
            "procedure_ref": "guided_checklist.rollback_1_1_0",
        },
    }
    desired_release = _release(v2, migrations=(migration,))
    desired = _lock(2, desired_release)

    admission = workflow_admission_record(
        current=current,
        desired=desired,
        release=desired_release,
        candidate_keys={v2.key},
    )

    assert admission["required_migrations"] == ["guided-checklist-workflow-1.1.0"]
    assert admission["workflows"][0]["definition_changed"] is True
    assert admission["workflows"][0]["migration_id"] == "guided-checklist-workflow-1.1.0"


def test_workflow_admission_rejects_inactive_package_owned_adapter() -> None:
    package = _package(
        "1.0.0",
        definition_digest=_digest("definition:v1"),
        adapter_locks=(
            WorkflowAdapterLock(
                adapter_id="shared.adapter",
                kind="activity",
                contract_digest=_digest("adapter:shared"),
                owner_scope="dependency",
                owner_package="skill:missing",
            ),
        ),
    )
    release = _release(package)
    desired = _lock(1, release)

    with pytest.raises(WorkflowAdmissionError, match="owner package is inactive"):
        workflow_admission_record(
            current=None,
            desired=desired,
            release=release,
            candidate_keys={package.key},
        )
