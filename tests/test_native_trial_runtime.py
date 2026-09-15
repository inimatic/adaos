from types import SimpleNamespace
from dataclasses import replace

import pytest

from adaos.services.agent_context import get_ctx, use_ctx
from adaos.services.applications.trial_runtime import NativeTrialRuntime, TrialRuntimeUnavailable
from adaos.services.policy.skill_capabilities import _profile_path
from adaos.services.skill.declarations import (
    load_runtime_skill_declarations, runtime_skill_declarations_snapshot,
)


@pytest.fixture(autouse=True)
def native_paths():
    from adaos.adapters.fs.path_provider import PathProvider

    owner = get_ctx()
    with use_ctx(replace(owner, paths=PathProvider(owner.settings))):
        yield


def test_trial_context_separates_data_and_keeps_node_authority(tmp_path):
    owner = get_ctx()
    root = tmp_path / "trials/candidate-1"
    runtime = NativeTrialRuntime(owner, "candidate-1", "sha256:" + "a" * 64, root, ())
    ctx = runtime.context()
    assert ctx.paths.workspace_dir() == root
    assert ctx.paths.skills_dir() == root / "skills"
    assert ctx.paths.state_dir().is_relative_to(root)
    assert ctx.authority_state_dir == owner.paths.state_dir()
    assert _profile_path(ctx) == _profile_path(owner)
    assert ctx.kv is not owner.kv and ctx.sql is not owner.sql
    assert ctx.projections is not owner.projections
    assert ctx.skill_ctx is not owner.skill_ctx
    assert ctx.config is owner.config
    with pytest.raises(TrialRuntimeUnavailable, match="DEV"):
        ctx.paths.dev_skills_dir()
    assert get_ctx() is owner


def test_trial_declarations_do_not_replace_same_named_workspace_skill(tmp_path):
    owner = get_ctx()
    runtime = NativeTrialRuntime(owner, "candidate-1", "sha256:" + "a" * 64, tmp_path / "trial", ())
    manifest = {"data_routes": [{"route": "stream", "receiver": "workspace.books"}]}
    load_runtime_skill_declarations("same", manifest, artifact_root=tmp_path / "workspace")
    before = runtime_skill_declarations_snapshot("same")
    with use_ctx(runtime.context()):
        assert runtime_skill_declarations_snapshot("same") == {}
        load_runtime_skill_declarations("same", {}, artifact_root=tmp_path / "trial")
        assert runtime_skill_declarations_snapshot("same")["receiver_patterns"] == ()
    assert runtime_skill_declarations_snapshot("same") == before


def test_production_host_uses_registered_relation_not_suffix(tmp_path, monkeypatch):
    from adaos.services.workspaces.relations import WebspaceRelationshipRegistry
    from adaos.services.workspaces import index

    registry = WebspaceRelationshipRegistry()
    rows = {"office-dev": SimpleNamespace(is_dev=False), "custom-preview": SimpleNamespace(is_dev=True),
            "orphan": SimpleNamespace(is_dev=True)}
    monkeypatch.setattr(index, "get_workspace", rows.get)
    monkeypatch.setattr(registry, "get_incoming", lambda value: (
        SimpleNamespace(source_webspace_id="office-dev") if value == "custom-preview" else None))
    assert registry.resolve_production_host("custom-preview") == "office-dev"
    assert registry.resolve_production_host("office-dev") == "office-dev"
    for value in ("orphan", "missing"):
        with pytest.raises(ValueError):
            registry.resolve_production_host(value)


def test_missing_trial_does_not_resolve_dev(tmp_path):
    with pytest.raises(TrialRuntimeUnavailable, match="missing"):
        NativeTrialRuntime.resolve(get_ctx(), "missing", "sha256:" + "a" * 64)


@pytest.fixture
def activation_record():
    from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease, WorkspaceLock
    from adaos.services.artifact_pipeline.storage import atomic_write_json
    from adaos.services.artifact_pipeline.trial_activation import TrialActivationStore, trial_workspace_root

    owner = get_ctx()
    source = ArtifactSourceRef(forge="github", repository="example/application", revision="a" * 40,
                               path_scope=("skills/example/",))
    package = ArtifactPackageRef(kind="skill", artifact_id="example", version="0.1.0",
                                digest="sha256:" + "b" * 64, manifest_digest="sha256:" + "c" * 64, source_ref=source)
    release = ProjectRelease(project_id="example", version="0.1.0", source_ref=source, components=(package,)).seal()
    lock = WorkspaceLock(lock_revision=1, updated_at="2026-09-15T00:00:00+00:00", components=(package,))
    root = trial_workspace_root(owner.paths.workspace_dir(), "candidate-example").resolve()
    atomic_write_json(root / ".adaos/workspace.lock.json", lock.to_dict())
    atomic_write_json(root / ".adaos/releases" / (release.release_digest.split(":")[1] + ".json"), release.to_dict())
    store = TrialActivationStore(owner.paths.state_dir() / "artifact_pipeline/trial-activations")
    store.save({"schema": "adaos.trial.activation.v1", "status": "active", "data_mode": "empty",
                "candidate_ref": {"candidate_id": "candidate-example", "release_digest": release.release_digest},
                "runtime_binding": {"authority": "immutable_candidate", "kind": "isolated_trial_workspace",
                                    "path": str(root), "workspace_lock_digest": lock.to_dict()["lock_digest"]},
                "package_refs": [package.to_dict()]})
    return store, release, package


def test_native_runtime_requires_exact_release_and_lock(activation_record):
    store, release, package = activation_record
    runtime = NativeTrialRuntime.resolve(get_ctx(), "candidate-example", release.release_digest)
    assert runtime.packages == (package,)
    store.update("candidate-example", package_refs=[replace(package, digest="sha256:" + "d" * 64).to_dict()])
    with pytest.raises(TrialRuntimeUnavailable, match="WorkspaceLock"):
        NativeTrialRuntime.resolve(get_ctx(), "candidate-example", release.release_digest)


@pytest.mark.parametrize("change, reason", [
    ({"data_mode": "real"}, "empty isolated"),
    ({"expires_at": "2000-01-01T00:00:00+00:00"}, "expired"),
    ({"status": "detached"}, "inactive"),
])
def test_native_trial_fails_closed_for_unadmitted_modes_and_lifecycle(activation_record, change, reason):
    store, release, _ = activation_record
    store.update("candidate-example", **change)
    with pytest.raises(TrialRuntimeUnavailable, match=reason):
        NativeTrialRuntime.resolve(get_ctx(), "candidate-example", release.release_digest)


@pytest.mark.parametrize("stage", ["trial", "publication"])
def test_candidate_materialization_route_rejects_delivery_before_side_effects(stage):
    import asyncio
    from adaos.services.scenario.webspace_components.builder_publication import WebspaceBuilderPublicationService

    service = WebspaceBuilderPublicationService()
    with pytest.raises(ValueError, match="DEV-only"):
        asyncio.run(service.apply_revision_materialization(
            "desktop", scenario_id="example", preview_stage=stage, operations=None))
