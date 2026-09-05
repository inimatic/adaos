from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    DependencyBinding,
    WorkspaceLock,
)
from adaos.services.artifact_pipeline.trial_activation import (
    TRIAL_WORKSPACE_LAYOUT_SCHEMA,
    TrialActivationError,
    TrialWorkspaceLayout,
    ensure_trial_workspace_shape,
    legacy_runtime_trial_root,
    legacy_runtime_trial_workspace,
    shared_skill_conflicts,
    trial_workspace_root,
)


def _skill(digest_char: str) -> ArtifactPackageRef:
    return ArtifactPackageRef(
        kind="skill",
        artifact_id="shared_skill",
        version="1.0.0" if digest_char == "a" else "2.0.0",
        digest="sha256:" + digest_char * 64,
        manifest_digest="sha256:" + "c" * 64,
        source_ref=ArtifactSourceRef(
            forge="github",
            repository="inimatic/adaos-registry",
            revision="d" * 40,
            path_scope=("skills/shared_skill/",),
        ),
    )


def test_runtime_trial_rejects_changed_skill_used_by_another_active_scenario() -> None:
    active = _skill("a")
    candidate = _skill("b")
    lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-08-06T00:00:00+00:00",
        components=(active,),
        bindings=(
            DependencyBinding(
                consumer="scenario:other",
                dependency=active.key,
                package_digest=active.digest,
            ),
        ),
    )

    conflicts = shared_skill_conflicts(
        SimpleNamespace(packages=(candidate,)),
        lock,
    )

    assert conflicts == [
        {
            "skill": "skill:shared_skill",
            "active_digest": active.digest,
            "candidate_digest": candidate.digest,
            "active_consumers": ["scenario:other"],
            "reason": "shared_skill_version_conflict",
        }
    ]


def test_runtime_trial_allows_same_skill_digest_or_closed_candidate_consumer() -> None:
    active = _skill("a")
    candidate = replace(active, version="1.0.1")
    same_digest_lock = WorkspaceLock(
        lock_revision=1,
        updated_at="2026-08-06T00:00:00+00:00",
        components=(active,),
        bindings=(
            DependencyBinding(
                consumer="scenario:recipes",
                dependency=active.key,
                package_digest=active.digest,
            ),
        ),
    )
    assert not shared_skill_conflicts(
        SimpleNamespace(packages=(candidate,)),
        same_digest_lock,
    )

    changed = _skill("b")
    scenario = SimpleNamespace(key="scenario:recipes", kind="scenario")
    assert not shared_skill_conflicts(
        SimpleNamespace(packages=(changed, scenario)),
        same_digest_lock,
    )


def test_trial_workspace_uses_workspace_shaped_sibling_root(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"

    assert trial_workspace_root(workspace, "candidate-1") == (
        workspace / "trials" / "candidate-1"
    ).resolve()
    with pytest.raises(TrialActivationError, match="safe Trial path"):
        trial_workspace_root(workspace, "candidate:1")


def test_trial_workspace_shape_includes_source_and_runtime_roots(tmp_path: Path) -> None:
    trial = ensure_trial_workspace_shape(
        tmp_path / "workspace" / "trials" / "candidate-1"
    )

    assert trial.is_dir()
    for relative in (
        ".adaos",
        ".runtime",
        "projects",
        "scenarios",
        "skills",
        "skills/.runtime",
    ):
        assert (trial / relative).is_dir()


def test_trial_workspace_layout_migrates_legacy_runtime_tree(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    candidate_id = "candidate-1"
    legacy_workspace = legacy_runtime_trial_workspace(workspace, candidate_id)
    (legacy_workspace / "skills" / "demo").mkdir(parents=True)
    (legacy_workspace / "skills" / "demo" / "skill.yaml").write_text(
        "name: demo\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    legacy_wrapper = legacy_runtime_trial_root(workspace, candidate_id)
    (legacy_wrapper / "operation.json").write_text("{}\n", encoding="utf-8")

    canonical, receipt = TrialWorkspaceLayout(
        workspace_root=workspace,
        state_root=state,
    ).ensure(candidate_id)

    assert canonical == trial_workspace_root(workspace, candidate_id)
    assert (canonical / "skills" / "demo" / "skill.yaml").is_file()
    assert not legacy_wrapper.exists()
    assert receipt is not None
    assert receipt["schema"] == TRIAL_WORKSPACE_LAYOUT_SCHEMA
    assert receipt["status"] == "completed"
    assert receipt["source_digest"] == receipt["target_digest"]
    assert (Path(receipt["legacy_archive"]) / "operation.json").is_file()


def test_trial_workspace_layout_rejects_divergent_duplicate(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    candidate_id = "candidate-1"
    legacy = legacy_runtime_trial_workspace(workspace, candidate_id)
    canonical = trial_workspace_root(workspace, candidate_id)
    legacy.mkdir(parents=True)
    canonical.mkdir(parents=True)
    (legacy / "value.txt").write_text("legacy", encoding="utf-8")
    (canonical / "value.txt").write_text("canonical", encoding="utf-8")

    with pytest.raises(TrialActivationError, match="diverge"):
        TrialWorkspaceLayout(
            workspace_root=workspace,
            state_root=tmp_path / "state",
        ).ensure(candidate_id)


def test_trial_workspace_layout_resumes_verified_interrupted_move(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from adaos.services.artifact_pipeline import trial_activation

    workspace = tmp_path / "workspace"
    state = tmp_path / "state"
    candidate_id = "candidate-1"
    legacy = legacy_runtime_trial_workspace(workspace, candidate_id)
    legacy.mkdir(parents=True)
    (legacy / "value.txt").write_text("candidate", encoding="utf-8")
    wrapper = legacy_runtime_trial_root(workspace, candidate_id)
    (wrapper / "operation.json").write_text("{}\n", encoding="utf-8")
    original_replace = trial_activation.replace_with_retry
    calls = 0

    def interrupt_archive(source: Path, target: Path, **kwargs) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated interruption")
        original_replace(source, target, **kwargs)

    monkeypatch.setattr(trial_activation, "replace_with_retry", interrupt_archive)
    layout = TrialWorkspaceLayout(workspace_root=workspace, state_root=state)

    with pytest.raises(OSError, match="simulated interruption"):
        layout.ensure(candidate_id)

    canonical, receipt = layout.ensure(candidate_id)

    assert (canonical / "value.txt").read_text(encoding="utf-8") == "candidate"
    assert receipt is not None
    assert receipt["status"] == "completed"
    assert receipt["source_digest"] == receipt["target_digest"]
    assert (Path(receipt["legacy_archive"]) / "operation.json").is_file()


def test_trial_workspace_layout_adds_local_git_exclude_idempotently(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    exclude = workspace / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True)
    exclude.write_text("# local rules\n.cache/\n", encoding="utf-8")
    layout = TrialWorkspaceLayout(
        workspace_root=workspace,
        state_root=tmp_path / "state",
    )

    layout.ensure("candidate-1")
    layout.ensure("candidate-2")

    assert exclude.read_text(encoding="utf-8") == (
        "# local rules\n.cache/\n/trials/\n"
    )
