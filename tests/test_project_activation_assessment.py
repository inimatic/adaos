"""New releases cannot seal stale activation evidence after manual source edits."""
from pathlib import Path

import pytest
import yaml

from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore
from adaos.services.artifact_pipeline.project_build import (
    ProjectReleaseBuildError,
    build_workspace_project_release,
)
from adaos.services.skill.activation_assessment import (
    inspect_handler_activation_source,
    recommended_activation,
)


HANDLER = b"def run():\n    return {'ok': True}\n"


def workspace(tmp_path, *, source=HANDLER, assessment=True):
    root = tmp_path / "source"
    skill = root / "skills/worker"
    skill.joinpath("handlers").mkdir(parents=True)
    skill.joinpath("handlers/main.py").write_bytes(source)
    manifest = {"name": "worker", "version": "1.0.0", "entry": "handlers/main.py"}
    if assessment:
        manifest["runtime"] = {"activation": recommended_activation(
            manifest, inspect_handler_activation_source(HANDLER))}
    write_manifest(skill / "skill.yaml", manifest)
    project = root / "projects/example"
    project.mkdir(parents=True)
    write_manifest(project / "project.yaml", {
        "schema": "adaos.project.v1", "kind": "project", "id": "example",
        "version": "1.0.0", "profiles": [],
        "components": {"owned": [{"ref": "skill:worker", "role": "primary"}],
                       "dependencies": []},
        "entrypoints": [], "catalog": {"title": "Example", "description": "",
                                          "categories": [], "tags": []},
        "lifecycle": {"uninstall": {"components": "retain", "runtime_data": "retain",
                                     "source_artifacts": "retain"}},
    })
    return root, manifest


def write_manifest(path, value):
    path.write_text(yaml.safe_dump(value, sort_keys=False), encoding="utf-8")


def build(root, tmp_path):
    return build_workspace_project_release(
        project_dir=root / "projects/example", workspace_root=root,
        source_ref=ArtifactSourceRef(forge="content-addressed-dev", repository="test",
                                    revision="a" * 40, path_scope=("projects/example/",)),
        package_store=ContentAddressedPackageStore(tmp_path / "packages"),
        release_repository=ReleaseRepository(tmp_path / "releases"),
    )


@pytest.mark.parametrize("source", [HANDLER, HANDLER.replace(b"\n", b"\r\n")])
def test_new_release_checks_canonical_packaged_handler_without_source_mutation(tmp_path, source):
    root, _ = workspace(tmp_path, source=source)
    result = build(root, tmp_path)
    assert result.plan.release.project_id == "example"
    assert (root / "skills/worker/handlers/main.py").read_bytes() == source


def test_legacy_unassessed_skill_remains_buildable(tmp_path):
    root, _ = workspace(tmp_path, assessment=False)
    assert build(root, tmp_path).plan.release.project_id == "example"


@pytest.mark.parametrize("newline", [b"\n", b"\r\n"])
def test_builder_producer_and_new_release_gate_agree_on_packaged_handler_bytes(tmp_path, newline):
    from adaos.services.skill_factory_worker import LocalSkillFactoryWorker

    root, _ = workspace(tmp_path, source=HANDLER.replace(b"\n", newline), assessment=False)
    changed = {"skills/worker/handlers/main.py"}
    receipts = LocalSkillFactoryWorker._record_changed_skill_activation(root, changed_paths=changed)
    assert receipts[0]["changed"] is True
    checks, errors = [], []
    LocalSkillFactoryWorker._validate_changed_skill_activation(
        root, checks, errors, changed_paths=changed)
    assert errors == [] and checks[0]["ok"] is True
    assert build(root, tmp_path).plan.release.project_id == "example"


@pytest.mark.parametrize("change", ["handler", "digest", "classification", "policy", "absent-handler"])
def test_new_release_rejects_stale_assessment_before_persisting_packages_or_release(tmp_path, change):
    root, manifest = workspace(tmp_path)
    activation = manifest["runtime"]["activation"]
    if change == "handler":
        (root / "skills/worker/handlers/main.py").write_bytes(HANDLER + b"# changed\n")
    elif change == "digest":
        activation["assessment"]["handler_digest"] = "sha256:" + "0" * 64
    elif change == "classification":
        activation["assessment"]["classification"] = "event_subscriber"
    elif change == "policy":
        activation["mode"] = "lazy"
    else:
        (root / "skills/worker/handlers/main.py").unlink()
    write_manifest(root / "skills/worker/skill.yaml", manifest)
    with pytest.raises(ProjectReleaseBuildError, match="activation assessment.*skill:worker"):
        build(root, tmp_path)
    assert not list((tmp_path / "packages").rglob("*.zip"))
    assert not list((tmp_path / "releases").rglob("*.json"))
