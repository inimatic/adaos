from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    WorkspaceLock,
)
from adaos.services.workspace_release_guard import (
    WorkspaceSourceMutationBlocked,
    assert_workspace_component_mutable,
)


_DIGEST_A = "sha256:" + "a" * 64
_DIGEST_B = "sha256:" + "b" * 64


def _write_lock(workspace: Path) -> None:
    source = ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision="0123456789abcdef",
        path_scope=("scenarios/builder/",),
    )
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="builder",
        version="0.2.55",
        digest=_DIGEST_A,
        manifest_digest=_DIGEST_B,
        source_ref=source,
    )
    lock = WorkspaceLock(
        lock_revision=17,
        updated_at="2026-09-02T00:00:00Z",
        components=(package,),
    )
    path = workspace / ".adaos" / "workspace.lock.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(lock.to_dict()), encoding="utf-8")


def test_workspace_guard_allows_components_outside_active_lock(tmp_path: Path) -> None:
    _write_lock(tmp_path)

    assert_workspace_component_mutable(
        tmp_path,
        kind="skill",
        artifact_id="demo_metrics_skill",
    )


def test_workspace_guard_blocks_direct_mutation_of_locked_component(tmp_path: Path) -> None:
    _write_lock(tmp_path)

    with pytest.raises(
        WorkspaceSourceMutationBlocked,
        match=r"scenario:builder.*revision 17.*\.adaos/dev.*Trial and Publication",
    ):
        assert_workspace_component_mutable(
            tmp_path,
            kind="scenarios",
            artifact_id="builder",
        )


def test_workspace_guard_fails_closed_for_untrusted_lock(tmp_path: Path) -> None:
    path = tmp_path / ".adaos" / "workspace.lock.json"
    path.parent.mkdir(parents=True)
    path.write_text("{not-json", encoding="utf-8")

    with pytest.raises(WorkspaceSourceMutationBlocked, match="cannot trust"):
        assert_workspace_component_mutable(
            tmp_path,
            kind="scenario",
            artifact_id="builder",
        )
