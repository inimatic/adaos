from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactReleaseContractError,
    ArtifactSourceRef,
    DependencyBinding,
    ProjectRef,
    ProjectRelease,
    ResolvedDependency,
    StableSubscription,
    WorkspaceLock,
    WorkspaceSlot,
)


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64
_DIGEST_C = "sha256:" + "c" * 64


def _source() -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("scenarios/recipes/", "skills/shopping_list/"),
    )


def _package(
    *,
    kind: str = "scenario",
    artifact_id: str = "recipes",
    version: str = "1.2.3",
    digest: str = _DIGEST_A,
) -> ArtifactPackageRef:
    return ArtifactPackageRef(
        kind=kind,
        artifact_id=artifact_id,
        version=version,
        digest=digest,
        manifest_digest=_DIGEST_C,
        source_ref=_source(),
    )


def _schema(name: str) -> dict:
    path = Path(__file__).parents[1] / "src" / "adaos" / "abi" / name
    return json.loads(path.read_text(encoding="utf-8"))


def test_source_ref_normalizes_scope_and_rejects_escape() -> None:
    source = ArtifactSourceRef(
        forge=" GitHub ",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef",
        path_scope=("scenarios\\recipes\\", "scenarios/recipes/"),
    )

    assert source.forge == "github"
    assert source.path_scope == ("scenarios/recipes/",)

    with pytest.raises(ArtifactReleaseContractError, match="safe relative path"):
        ArtifactSourceRef(
            forge="github",
            repository="inimatic/adaos-registry",
            revision="0123456789abcdef",
            path_scope=("../secrets",),
        )


def test_project_release_digest_is_canonical_and_detects_tampering() -> None:
    component = _package()
    dependency = ResolvedDependency(
        kind="skill",
        artifact_id="shopping_list",
        version="1.6.2",
        package_digest=_DIGEST_B,
        version_spec=">=1.4,<2",
    )
    release = ProjectRelease(
        project_id="recipes",
        version="1.2.3",
        source_ref=_source(),
        components=(component,),
        resolved_dependencies=(dependency,),
        permissions=("shopping.read", "shopping.read"),
    ).seal()

    assert release.release_digest == release.computed_digest()
    assert release.to_dict()["permissions"] == ["shopping.read"]
    assert ProjectRelease.from_mapping(release.to_dict()) == release

    tampered = release.to_dict()
    tampered["permissions"] = ["shopping.write"]
    with pytest.raises(ArtifactReleaseContractError, match="does not match"):
        ProjectRelease.from_mapping(tampered)


def test_workspace_lock_rejects_two_active_versions_of_same_skill() -> None:
    one = _package(kind="skill", artifact_id="shopping_list", version="1.0.0", digest=_DIGEST_A)
    two = _package(kind="skill", artifact_id="shopping_list", version="2.0.0", digest=_DIGEST_B)

    with pytest.raises(ArtifactReleaseContractError, match="one active package"):
        WorkspaceLock(
            lock_revision=2,
            previous_lock_revision=1,
            updated_at="2026-07-24T00:00:00Z",
            components=(one, two),
        )


def test_workspace_lock_requires_binding_to_active_package() -> None:
    scenario = _package()
    skill = _package(
        kind="skill",
        artifact_id="shopping_list",
        version="1.6.2",
        digest=_DIGEST_B,
    )
    lock = WorkspaceLock(
        lock_revision=2,
        previous_lock_revision=1,
        updated_at="2026-07-24T00:00:00Z",
        slots=(
            WorkspaceSlot(
                slot_id="primary",
                project_id="recipes",
                release="recipes@1.2.3",
                release_digest=_DIGEST_B,
            ),
        ),
        components=(scenario, skill),
        bindings=(
            DependencyBinding(
                consumer="scenario:recipes",
                dependency="skill:shopping_list",
                package_digest=_DIGEST_B,
            ),
        ),
    )

    payload = lock.to_dict()
    assert payload["schema"] == "adaos.workspace.lock.v1"
    assert payload["lock_digest"].startswith("sha256:")
    assert WorkspaceLock.from_mapping(payload) == lock

    tampered = dict(payload)
    tampered["updated_at"] = "2026-07-25T00:00:00Z"
    with pytest.raises(ArtifactReleaseContractError, match="lock_digest does not match"):
        WorkspaceLock.from_mapping(tampered)

    with pytest.raises(ArtifactReleaseContractError, match="inactive package"):
        WorkspaceLock(
            lock_revision=2,
            previous_lock_revision=1,
            updated_at="2026-07-24T00:00:00Z",
            components=(scenario, skill),
            bindings=(
                DependencyBinding(
                    consumer="scenario:recipes",
                    dependency="skill:shopping_list",
                    package_digest=_DIGEST_A,
                ),
            ),
        )


def test_contract_payloads_validate_against_abi_schemas() -> None:
    source = _source()
    package = _package()
    release = ProjectRelease(
        project_id="recipes",
        version="1.2.3",
        source_ref=source,
        components=(package,),
    ).seal()
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-07-24T00:00:00Z",
        slots=(
            WorkspaceSlot(
                slot_id="primary",
                project_id="recipes",
                release="recipes@1.2.3",
                release_digest=release.release_digest,
            ),
        ),
        components=(package,),
    )
    subscription = StableSubscription(
        project_id="recipes",
        installed_release="recipes@1.2.3",
        installed_digest=release.release_digest,
    )

    fixtures = [
        ("artifact.project-ref.v1.schema.json", ProjectRef("recipes").to_dict()),
        ("artifact.source-ref.v1.schema.json", source.to_dict()),
        ("artifact.package-ref.v1.schema.json", package.to_dict()),
        ("artifact.project-release.v1.schema.json", release.to_dict()),
        ("workspace.lock.v1.schema.json", lock.to_dict()),
        ("artifact.subscription.v1.schema.json", subscription.to_dict()),
    ]
    for schema_name, payload in fixtures:
        jsonschema.Draft202012Validator(_schema(schema_name)).validate(payload)
