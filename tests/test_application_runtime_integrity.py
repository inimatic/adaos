from types import SimpleNamespace

import pytest

from adaos.services.applications import runtime_integrity as integrity


def _ctx(tmp_path):
    return SimpleNamespace(paths=SimpleNamespace(
        state_dir=lambda: tmp_path / "state", workspace_dir=lambda: tmp_path / "workspace",
        skills_dir=lambda: tmp_path / "workspace/skills"))


@pytest.mark.parametrize("observed,status,reason", [
    ({"version": "1.0.0", "package_digest": "sha256:a", "ready": True}, "aligned", None),
    ({"version": "1.0.0", "package_digest": "sha256:b"}, "drift", "package_digest_mismatch"),
    ({"version": "1.0.0"}, "unknown", "package_identity_unknown"),
    ({}, "unknown", "runtime_unavailable"),
    ({"package_digest": "sha256:a", "source_verified": False}, "drift", "source_drift"),
    ({"package_digest": "sha256:a", "active": False}, "drift", "runtime_not_ready"),
    ({"package_digest": "sha256:a", "active_selection_valid": False}, "drift", "runtime_selection_invalid"),
    ({"version": "2.0.0"}, "drift", "version_mismatch"),
    ({"source_verified": False}, "drift", "source_drift"),
    ({"package_digest": "sha256:a", "source_verification_error": "FileNotFoundError"},
     "unknown", "source_verification_unavailable"),
    ({"source_verification_error": "FileNotFoundError"}, "unknown", "package_identity_unknown"),
])
def test_component_comparison_requires_exact_identity(observed, status, reason):
    result = integrity.compare_runtime_component(
        {"kind": "skill", "artifact_id": "example", "version": "1.0.0", "digest": "sha256:a"}, observed)
    assert result["status"] == status
    if reason:
        assert reason in result["reasons"]


def test_unmanaged_skill_remains_usable(tmp_path):
    integrity.assert_managed_skill_activation(_ctx(tmp_path), "example", None)


def test_alpha_reads_optional_bounded_readme_without_expanding_development_history(tmp_path):
    ctx = _ctx(tmp_path)
    ctx.paths.dev_dir = lambda: tmp_path / "dev"
    project = tmp_path / "dev/projects/example/project.yaml"
    project.parent.mkdir(parents=True)
    project.write_text("version: 1.2.3\ncomponents: {owned: []}\n", encoding="utf-8")
    assert "readme" not in integrity._alpha_observation(ctx, "example", deep=False)
    project.with_name("README.md").write_text("# Help\n" + "x" * 40000, encoding="utf-8")
    result = integrity._alpha_observation(ctx, "example", deep=False)
    assert result["readme"].startswith("# Help\n")
    assert len(result["readme"]) == 32768
    assert result["updated_at"]


@pytest.mark.parametrize("declaration", ["[broken", "[]", "version: 1.2.3\ncomponents: {owned: []}\n"])
def test_invalid_alpha_remains_observable_without_repair(tmp_path, monkeypatch, declaration):
    ctx = _ctx(tmp_path)
    ctx.paths.dev_dir = lambda: tmp_path / "dev"
    project = tmp_path / "dev/projects/example/project.yaml"
    project.parent.mkdir(parents=True)
    project.write_text(declaration, encoding="utf-8")
    def fail(**kwargs):
        raise RuntimeError("compiler-owned outputs differ")
    monkeypatch.setattr("adaos.services.artifact_pipeline.project_build.project_source_snapshot", fail)
    result = integrity._alpha_observation(ctx, "example", deep=True)
    assert result["status"] == "invalid_source"
    assert result["source_error"]
    assert project.read_text(encoding="utf-8") == declaration


def test_committed_lock_allows_only_its_exact_package(tmp_path, monkeypatch):
    locked = SimpleNamespace(key="skill:example", digest="sha256:desired")
    monkeypatch.setattr(integrity, "load_workspace_lock", lambda _: SimpleNamespace(components=(locked,)))
    ctx = _ctx(tmp_path)
    integrity.assert_managed_skill_activation(ctx, "example", "sha256:desired")
    for digest in (None, "sha256:other"):
        with pytest.raises(integrity.ManagedRuntimeConflict):
            integrity.assert_managed_skill_activation(ctx, "example", digest)


def test_legacy_installation_and_shared_ownership_remain_exact(tmp_path, monkeypatch):
    ctx = _ctx(tmp_path)
    (tmp_path / "state/applications/installations").mkdir(parents=True)
    def installation(app, digest):
        return SimpleNamespace(application_id=app, status="active", component_refs=(
            {"component_ref": "skill:example", "package_digest": digest},))
    records = [installation("one", "sha256:a"), installation("two", "sha256:a")]
    monkeypatch.setattr(integrity, "ApplicationStore", lambda _: SimpleNamespace(list_installations=lambda: records))
    integrity.assert_managed_skill_activation(ctx, "example", "sha256:a")
    records[1] = installation("two", "sha256:b")
    with pytest.raises(integrity.ManagedRuntimeConflict):
        integrity.assert_managed_skill_activation(ctx, "example", "sha256:a")


def test_observer_keeps_stable_and_beta_separate_and_selects_by_webspace(tmp_path, monkeypatch):
    from adaos.services.applications.trial_runtime import NativeTrialRuntime

    ctx = _ctx(tmp_path)
    def selection(ws, digest, root):
        return SimpleNamespace(application_id="example", webspace_id=ws, release_digest=digest,
            runtime_root_ref=root, source="local_trial" if root.startswith("trial:") else "stable_installation",
            to_dict=lambda: {"webspace_id": ws, "release_digest": digest, "runtime_root_ref": root})
    rows = [selection("desktop", "beta", "trial:candidate"), selection("other", "stable", "workspace")]
    def release(digest):
        return SimpleNamespace(release_digest=digest, project_release=SimpleNamespace(version=digest))
    store = SimpleNamespace(get_application=lambda _: SimpleNamespace(legacy_project_id="example"),
        list_application_runtime_selections=lambda _: rows,
        get_installation=lambda _: SimpleNamespace(status="active", installed_release_digest="stable"),
        get_release=lambda app, digest: release(digest), get_channels=lambda _: {"channels": {"stable": "stable"}})
    monkeypatch.setattr(integrity, "ApplicationStore", lambda _: store)
    monkeypatch.setattr(NativeTrialRuntime, "resolve", lambda *args: SimpleNamespace(root=tmp_path / "isolated"))
    observed_roots = []
    def observe(ctx, release, *, root, runtime_root_ref, deep):
        observed_roots.append(root)
        return {"version": release.project_release.version, "release_digest": release.release_digest,
            "runtime_root_ref": runtime_root_ref, "status": "aligned" if runtime_root_ref.startswith("trial:") else "drift",
            "components": [{"component_ref": "skill:example"}]}
    monkeypatch.setattr(integrity, "_release_observation", observe)
    result = integrity.observe_application_revisions(ctx, "example")
    assert result["status"] == "aligned"
    assert result["stable"]["status"] == "drift"
    assert result["selection"]["release_digest"] == "beta"
    assert result["beta"][0]["webspace_ids"] == ["desktop"]
    assert result["selected_components"] == [{"component_ref": "skill:example"}]
    assert observed_roots == [tmp_path / "workspace", tmp_path / "isolated"]
    assert result["read_only"] is True


def test_manager_rejects_foreign_package_before_prepare_or_lifecycle(tmp_path, monkeypatch):
    from adaos.services.skill.manager import SkillManager

    ctx = _ctx(tmp_path)
    env = SimpleNamespace(resolve_active_version=lambda: "1.0.0", read_active_slot=lambda _: "A",
        read_deactivation=lambda: {}, prepare_version=lambda _: None,
        read_version_metadata=lambda _: {"slots": {"B": {"package_digest": "sha256:foreign"}}},
        build_slot_paths=lambda *args: SimpleNamespace())
    manager = object.__new__(SkillManager)
    manager.ctx = ctx
    monkeypatch.setattr(manager, "_runtime_env", lambda _: env)
    monkeypatch.setattr(manager, "_resolve_runtime_update_source", lambda *args, **kwargs: (tmp_path / "absent", "workspace"))
    monkeypatch.setattr(manager, "_latest_prepared_version", lambda _: "1.0.0")
    locked = SimpleNamespace(key="skill:example", digest="sha256:exact")
    monkeypatch.setattr(integrity, "load_workspace_lock", lambda _: SimpleNamespace(components=(locked,)))
    monkeypatch.setattr(manager, "prepare_runtime", lambda *args, **kwargs: pytest.fail("must not prepare a foreign package"))
    with pytest.raises(integrity.ManagedRuntimeConflict):
        manager.activate_runtime("example", version="1.0.0", slot="B")


@pytest.mark.parametrize("tamper", [False, True])
def test_legacy_provenance_allows_only_policy_change_not_payload_change(tmp_path, monkeypatch, tamper):
    from adaos.domain.artifact_release import ArtifactSourceRef
    from adaos.services.artifact_pipeline import packages
    from adaos.services.skill.manager import SkillManager

    ctx = _ctx(tmp_path)
    source = tmp_path / "slot/src/skills/example"
    (source / "handlers").mkdir(parents=True)
    (source / "skill.yaml").write_text("name: example\nversion: 1.0.0\n", encoding="utf-8")
    handler = source / "handlers/main.py"
    handler.write_text("def handle():\n    return {}\n", encoding="utf-8")
    ref = ArtifactSourceRef(forge="test", repository="tests/policy", revision="a" * 40)
    original = packages.build_artifact_package(source, kind="skill", source_ref=ref)
    store = packages.ContentAddressedPackageStore(tmp_path / "state/artifact_pipeline/packages")
    store.put(original.archive_bytes, expected_digest=original.ref.digest)
    metadata = {"slots": {"A": {}}}
    writes = []
    env = SimpleNamespace(resolve_active_version=lambda: "1.0.0", read_active_slot=lambda _: "A",
        read_version_metadata=lambda _: metadata,
        build_slot_paths=lambda *args: SimpleNamespace(src_dir=tmp_path / "slot/src"),
        write_version_metadata=lambda version, value: writes.append(value))
    manager = object.__new__(SkillManager)
    manager.ctx = ctx
    monkeypatch.setattr(manager, "_runtime_env", lambda _: env)
    monkeypatch.setattr(packages, "PACKAGE_BUILD_POLICY_DIGEST", "sha256:" + "e" * 64)
    if tamper:
        handler.write_text("def handle():\n    return {'changed': True}\n", encoding="utf-8")
        with pytest.raises(ValueError, match="bytes do not match"):
            manager.adopt_active_runtime_package_identity("example", package=original.ref.to_dict())
        assert not writes
    else:
        result = manager.adopt_active_runtime_package_identity("example", package=original.ref.to_dict())
        assert result["reason"] == "verified_payload_across_build_policy"
        assert result["package_digest"] == original.ref.digest
        assert writes[0]["slots"]["A"]["package_digest"] == original.ref.digest
