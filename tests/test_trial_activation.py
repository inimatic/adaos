from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from adaos.domain.artifact_release import (
    ArtifactPackageRef,
    ArtifactSourceRef,
    DependencyBinding,
    WorkspaceLock,
)
from adaos.services.artifact_pipeline.trial_activation import shared_skill_conflicts


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
