from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.domain.project_deployment import ComponentActivation
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore,
    build_artifact_package,
)
from adaos.services.project_deployment.materialization import (
    ProjectOwnedComponentMutationError,
    active_project_component,
    ensure_standalone_component_mutation_allowed,
    restore_project_owned_materializations,
)
from adaos.services.project_deployment.store import ProjectDeploymentStore


_NOW = "2026-08-22T07:00:00+00:00"


def test_restore_project_owned_materialization_removes_workspace_sync_drift(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    source = tmp_path / "source" / "media_center"
    source.mkdir(parents=True)
    (source / "scenario.yaml").write_text(
        "id: media_center\nversion: 0.6.8\n",
        encoding="utf-8",
    )
    (source / "webui.json").write_text("{}\n", encoding="utf-8")
    tests_dir = source / "tests"
    tests_dir.mkdir()
    (tests_dir / "test_source_only.py").write_text("assert True\n", encoding="utf-8")
    built = build_artifact_package(
        source,
        kind="scenario",
        source_ref=ArtifactSourceRef(
            forge="github",
            repository="inimatic/adaos-registry",
            revision="0123456789abcdef0123456789abcdef01234567",
            path_scope=("scenarios/media_center/",),
        ),
    )
    package_store = ContentAddressedPackageStore(
        state_dir / "artifact_pipeline" / "packages"
    )
    package_store.put(built.archive_bytes, expected_digest=built.ref.digest)
    target = workspace / "scenarios" / "media_center"
    package_store.materialize(built.ref.digest, target)
    restored_test = target / "tests" / "test_source_only.py"
    restored_test.parent.mkdir()
    restored_test.write_text("assert True\n", encoding="utf-8")

    ProjectDeploymentStore(state_dir=state_dir).put_activation(
        ComponentActivation(
            activation_id="activation.media-center",
            deployment_id="media-center-home",
            component_ref="scenario:media_center",
            node_id="node-a",
            release_digest="sha256:" + "a" * 64,
            package_digest=built.ref.digest,
            generation=43,
            status="active",
            health={"ready": True},
            evidence={},
            created_at=_NOW,
            updated_at=_NOW,
        )
    )
    ctx = SimpleNamespace(
        config=SimpleNamespace(node_id="node-a"),
        paths=SimpleNamespace(
            state_dir=lambda: state_dir,
            workspace_dir=lambda: workspace,
        ),
    )

    first = restore_project_owned_materializations(ctx)
    second = restore_project_owned_materializations(ctx)

    assert first["ok"] is True
    assert first["checked"] == ["scenario:media_center"]
    assert first["repaired"] == ["scenario:media_center"]
    assert not restored_test.exists()
    assert second["ok"] is True
    assert second["repaired"] == []
    assert active_project_component(ctx, "scenario:media_center") is True
    assert active_project_component(ctx, "skill:media_center_skill") is False
    with pytest.raises(ProjectOwnedComponentMutationError, match="project_owned_component"):
        ensure_standalone_component_mutation_allowed(
            ctx,
            "scenario:media_center",
            operation="scenario install",
        )
    ensure_standalone_component_mutation_allowed(
        ctx,
        "skill:media_center_skill",
        operation="skill install",
    )


def test_restore_allows_shared_ownership_of_the_same_immutable_package(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    source = tmp_path / "source" / "voice_chat_skill"
    source.mkdir(parents=True)
    (source / "skill.yaml").write_text(
        "name: voice_chat_skill\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    built = build_artifact_package(
        source,
        kind="skill",
        source_ref=ArtifactSourceRef(
            forge="github",
            repository="inimatic/adaos-registry",
            revision="0123456789abcdef0123456789abcdef01234567",
            path_scope=("skills/voice_chat_skill/",),
        ),
    )
    package_store = ContentAddressedPackageStore(
        state_dir / "artifact_pipeline" / "packages"
    )
    package_store.put(built.archive_bytes, expected_digest=built.ref.digest)
    target = workspace / "skills" / "voice_chat_skill"
    package_store.materialize(built.ref.digest, target)
    extra = target / "tests" / "test_source_only.py"
    extra.parent.mkdir()
    extra.write_text("assert True\n", encoding="utf-8")

    store = ProjectDeploymentStore(state_dir=state_dir)
    for generation, deployment_id in enumerate(
        ("application-deployment:voice", "application-deployment:adaos_builder"),
        start=1,
    ):
        store.put_activation(
            ComponentActivation(
                activation_id=f"activation.shared-{generation}",
                deployment_id=deployment_id,
                component_ref="skill:voice_chat_skill",
                node_id="node-a",
                release_digest="sha256:" + str(generation) * 64,
                package_digest=built.ref.digest,
                generation=generation,
                status="active",
                health={"ready": True},
                evidence={},
                created_at=_NOW,
                updated_at=_NOW,
            )
        )
    ctx = SimpleNamespace(
        config=SimpleNamespace(node_id="node-a"),
        paths=SimpleNamespace(
            state_dir=lambda: state_dir,
            workspace_dir=lambda: workspace,
        ),
    )

    result = restore_project_owned_materializations(ctx)

    assert result["ok"] is True
    assert result["checked"] == ["skill:voice_chat_skill"]
    assert result["repaired"] == ["skill:voice_chat_skill"]
    assert not extra.exists()


def test_restore_reconciles_stale_package_owner_to_exact_materialization(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    workspace = tmp_path / "workspace"
    source = tmp_path / "source" / "voice_chat_skill"
    source.mkdir(parents=True)
    manifest = source / "skill.yaml"
    manifest.write_text(
        "name: voice_chat_skill\nversion: 0.7.5\n", encoding="utf-8"
    )
    source_ref = ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef0123456789abcdef01234567",
        path_scope=("skills/voice_chat_skill/",),
    )
    old = build_artifact_package(source, kind="skill", source_ref=source_ref)
    manifest.write_text(
        "name: voice_chat_skill\nversion: 0.7.7\n", encoding="utf-8"
    )
    new = build_artifact_package(source, kind="skill", source_ref=source_ref)
    package_store = ContentAddressedPackageStore(
        state_dir / "artifact_pipeline" / "packages"
    )
    package_store.put(old.archive_bytes, expected_digest=old.ref.digest)
    package_store.put(new.archive_bytes, expected_digest=new.ref.digest)
    package_store.materialize(
        new.ref.digest,
        workspace / "skills" / "voice_chat_skill",
    )

    store = ProjectDeploymentStore(state_dir=state_dir)
    old_activation = ComponentActivation(
        activation_id="activation.voice-old",
        deployment_id="application-deployment:builder",
        component_ref="skill:voice_chat_skill",
        node_id="node-a",
        release_digest="sha256:" + "a" * 64,
        package_digest=old.ref.digest,
        generation=1,
        status="active",
        health={"ready": True},
        evidence={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    new_activation = ComponentActivation(
        activation_id="activation.voice-new",
        deployment_id="application-deployment:desktop",
        component_ref="skill:voice_chat_skill",
        node_id="node-a",
        release_digest="sha256:" + "b" * 64,
        package_digest=new.ref.digest,
        generation=2,
        status="active",
        health={"ready": True},
        evidence={},
        created_at=_NOW,
        updated_at=_NOW,
    )
    store.put_activation(old_activation)
    store.put_activation(new_activation)
    ctx = SimpleNamespace(
        config=SimpleNamespace(node_id="node-a"),
        paths=SimpleNamespace(
            state_dir=lambda: state_dir,
            workspace_dir=lambda: workspace,
        ),
    )

    result = restore_project_owned_materializations(ctx)

    assert result["ok"] is True
    assert result["reconciled"] == ["skill:voice_chat_skill"]
    assert store.get_activation(old_activation.activation_id).status == "inactive"
    assert store.get_activation(new_activation.activation_id).status == "active"
