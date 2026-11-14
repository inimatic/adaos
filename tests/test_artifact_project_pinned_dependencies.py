"""An isolated application must retain its immutable shared dependency closure."""
from pathlib import Path

import pytest
import yaml

from adaos.domain.artifact_release import ArtifactSourceRef, WorkspaceLock
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import (
    ContentAddressedPackageStore, PackageVerificationError, build_artifact_package,
)
from adaos.services.artifact_pipeline.project_build import (
    ProjectReleaseBuildError, build_workspace_project_release,
)


def write_yaml(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def skill(workspace, name, version, *, requires=(), stale=False):
    root = workspace / "skills" / name
    manifest = {"name": name, "version": version, "entry": "handlers/main.py", "tools": []}
    if requires:
        manifest["runtime"] = {"skills": {"required": list(requires)}}
    handler = root / "handlers/main.py"
    handler.parent.mkdir(parents=True, exist_ok=True)
    handler.write_text("def run(): return {'ok': True}\n", encoding="utf-8")
    if stale:
        from adaos.services.skill.activation_assessment import inspect_handler_activation, recommended_activation
        activation = recommended_activation(manifest, inspect_handler_activation(handler))
        activation["assessment"]["handler_digest"] = "sha256:" + "0" * 64
        manifest.setdefault("runtime", {})["activation"] = activation
    write_yaml(root / "skill.yaml", manifest)
    return root


def setup_release(tmp_path, *, transitive=False):
    workspace = tmp_path / "workspace"
    source = ArtifactSourceRef(forge="github", repository="example/pinned",
                              revision="a" * 40, path_scope=("projects/viewer/",))
    store = ContentAddressedPackageStore(tmp_path / "packages")
    repository = ReleaseRepository(tmp_path / "releases")
    skill(workspace, "worker", "1.0.0")
    skill(workspace, "shared", "1.0.0", requires=[{"id": "leaf", "version": "==1.0.0"}] if transitive else [])
    if transitive:
        skill(workspace, "leaf", "1.0.0")
    definition = {
        "schema": "adaos.project.v1", "kind": "project", "id": "viewer", "version": "1.0.0",
        "profiles": [], "components": {"owned": [{"ref": "skill:worker", "role": "primary"}],
            "dependencies": [{"ref": "skill:shared", "version": "==1.0.0", "lifecycle": "shared"}]},
        "entrypoints": [], "catalog": {"title": "Viewer", "description": "", "categories": [], "tags": []},
        "lifecycle": {"uninstall": {"components": "retain", "runtime_data": "retain", "source_artifacts": "retain"}},
    }
    project = workspace / "projects/viewer/project.yaml"
    write_yaml(project, definition)
    arguments = dict(project_dir=project.parent, workspace_root=workspace, source_ref=source,
                     package_store=store, release_repository=repository)
    previous = build_workspace_project_release(**arguments).plan
    # Another application's/global update does not update this Project's pin.
    active = []
    for name in ("shared", "leaf") if transitive else ("shared",):
        root = skill(workspace, name, "2.0.0", stale=True)
        package = build_artifact_package(root, kind="skill", source_ref=source)
        store.put(package.archive_bytes, expected_digest=package.ref.digest)
        active.append(package.ref)
    arguments["active_workspace_lock"] = WorkspaceLock(
        lock_revision=1, updated_at="2026-11-13T00:00:00+00:00", components=tuple(active))
    definition["version"] = "2.0.0"
    skill(workspace, "worker", "2.0.0")
    write_yaml(project, definition)
    return arguments, previous, definition, project


@pytest.mark.parametrize("transitive", [False, True])
def test_exact_pin_reuses_verified_prior_project_closure(tmp_path, transitive):
    arguments, previous, _, _ = setup_release(tmp_path, transitive=transitive)
    active = arguments["active_workspace_lock"]
    result = build_workspace_project_release(**arguments).plan
    old = {p.key:p for p in previous.packages}
    actual = {p.key:p for p in result.packages}
    for key in ("skill:shared", "skill:leaf") if transitive else ("skill:shared",):
        assert actual[key] == old[key]
        assert arguments["package_store"].verify(actual[key].digest).ref == old[key]
    assert actual["skill:worker"].version == "2.0.0"
    assert {p.version for p in active.components} == {"2.0.0"}


@pytest.mark.parametrize("fault", ["missing", "corrupt"])
def test_retained_pin_never_rebuilds_missing_or_corrupt_archive(tmp_path, fault):
    arguments, previous, _, _ = setup_release(tmp_path)
    pinned = next(p for p in previous.packages if p.key == "skill:shared")
    archive = arguments["package_store"].package_path(pinned.digest)
    if fault == "missing":
        archive.unlink()
    else:
        archive.write_bytes(b"corrupt package")
    with pytest.raises((ProjectReleaseBuildError, PackageVerificationError)):
        build_workspace_project_release(**arguments)
    assert "2.0.0" not in arguments["release_repository"].release_digests_by_version("viewer")


def test_retained_closure_respects_explicit_transitive_version_constraint(tmp_path):
    arguments, _, definition, project = setup_release(tmp_path, transitive=True)
    definition["components"]["dependencies"].append(
        {"ref": "skill:leaf", "version": "==2.0.0", "lifecycle": "shared"})
    write_yaml(project, definition)
    with pytest.raises(ProjectReleaseBuildError, match="conflicts with declared version"):
        build_workspace_project_release(**arguments)


def test_new_owned_component_still_requires_fresh_activation_assessment(tmp_path):
    arguments, _, _, _ = setup_release(tmp_path)
    skill(arguments["workspace_root"], "worker", "2.0.0", stale=True)
    with pytest.raises(ProjectReleaseBuildError, match="stale activation assessment for skill:worker"):
        build_workspace_project_release(**arguments)
