import json
from dataclasses import replace

from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, WorkspaceLock
from adaos.domain.project_deployment import DeploymentPlanChange
from adaos.services.project_deployment.workspace_admission import workspace_admission_warnings


def test_local_pins_require_exact_package_but_do_not_admit_or_block_remote_delivery(tmp_path):
    package = ArtifactPackageRef(kind="skill", artifact_id="worker", version="1.0.0",
        digest="sha256:" + "a" * 64, manifest_digest="sha256:" + "b" * 64,
        source_ref=ArtifactSourceRef(forge="github", repository="owner/registry", revision="1" * 40,
                                    path_scope=("skills/worker/",)))
    lock = WorkspaceLock(lock_revision=1, updated_at="2026-10-18T00:00:00+00:00", components=(package,))
    (tmp_path / ".adaos").mkdir()
    (tmp_path / ".adaos/workspace.lock.json").write_text(json.dumps(lock.to_dict()))
    change = DeploymentPlanChange(action="update", component_ref="skill:worker", node_id="local",
                                  target_package_digest="sha256:" + "c" * 64,
                                  phases=("fetch", "activate"), reason="test")
    assert workspace_admission_warnings(tmp_path, (change,), "local") == (
        "blocked:skill:worker:workspace_lock_package_conflict",)
    assert workspace_admission_warnings(tmp_path, (replace(change, node_id="remote"),), "local") == ()
    assert workspace_admission_warnings(tmp_path, (replace(change, target_package_digest=package.digest),), "local") == ()


def test_missing_workspace_lock_preserves_legacy_delivery_but_invalid_lock_fails_closed(tmp_path):
    change = DeploymentPlanChange(action="install", component_ref="skill:worker", node_id="local",
                                  target_package_digest="sha256:" + "a" * 64,
                                  phases=("fetch", "activate"), reason="test")
    assert workspace_admission_warnings(tmp_path, (change,), "local") == ()
    (tmp_path / ".adaos").mkdir()
    (tmp_path / ".adaos/workspace.lock.json").write_text('{"private":"must-not-appear"}')
    assert workspace_admission_warnings(tmp_path, (change,), "local") == (
        "blocked:workspace_lock:unavailable",)
