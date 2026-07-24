from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease
from adaos.services.artifact_pipeline import (
    CandidateError,
    CandidateStore,
    assert_promotable,
    assess_freshness,
    begin_trial,
    candidate_from_release,
    complete_trial,
    mark_stale,
    record_validation,
)


def _source(revision: str) -> ArtifactSourceRef:
    return ArtifactSourceRef(
        forge="github",
        repository="inimatic/adaos-registry",
        revision=revision,
        path_scope=("scenarios/recipes/",),
    )


def _release(version: str, revision_token: str, digest_token: str) -> ProjectRelease:
    source = _source(revision_token * 40)
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version=version,
        digest="sha256:" + digest_token * 64,
        manifest_digest="sha256:" + "f" * 64,
        source_ref=source,
    )
    return ProjectRelease(
        project_id="recipes",
        version=version,
        source_ref=source,
        components=(package,),
    ).seal()


def test_candidate_validation_trial_and_freshness_gate() -> None:
    stable = _release("1.0.0", "1", "a")
    release = _release("1.1.0", "2", "b")
    candidate = candidate_from_release(
        candidate_id="recipes-1-1-0",
        release=release,
        base_release=stable,
        package_digest=release.components[0].digest,
        change_ids=("change-favorites",),
        source_tree="3" * 40,
        now="2026-07-24T00:00:00Z",
    )
    identity_digest = candidate.digest

    candidate = record_validation(
        candidate,
        {"suite": "pytest", "status": "passed", "digest": release.release_digest},
        now="2026-07-24T00:10:00Z",
    )
    candidate = begin_trial(
        candidate,
        trial_id="trial-recipes",
        audience="owner",
        data_mode="snapshot",
        lock_digest="sha256:" + "c" * 64,
        now="2026-07-24T00:20:00Z",
    )
    candidate = complete_trial(
        candidate,
        trial_id="trial-recipes",
        accepted=True,
        now="2026-07-24T01:20:00Z",
        observations=({"status": "accepted", "duration_seconds": 3600},),
    )

    assert candidate.digest == identity_digest
    assert candidate.status == "accepted"
    assert assess_freshness(candidate, stable) == (True, None)
    assert_promotable(candidate, release, stable)

    moved = _release("1.0.1", "4", "d")
    assert assess_freshness(candidate, moved) == (False, "base_release_moved")
    with pytest.raises(CandidateError, match="stale"):
        assert_promotable(candidate, release, moved)
    stale = mark_stale(
        candidate,
        reason="base_release_moved",
        now="2026-07-24T02:00:00Z",
    )
    assert stale.status == "stale"


def test_real_data_trial_requires_safety_proof() -> None:
    stable = _release("1.0.0", "1", "a")
    release = _release("1.1.0", "2", "b")
    candidate = candidate_from_release(
        candidate_id="recipes-real",
        release=release,
        base_release=stable,
        package_digest=release.components[0].digest,
        change_ids=("change-real",),
        now="2026-07-24T00:00:00Z",
    )
    candidate = record_validation(candidate, {"status": "passed"}, now="2026-07-24T00:01:00Z")

    with pytest.raises(CandidateError, match="read-only or reversible"):
        begin_trial(
            candidate,
            trial_id="unsafe",
            audience="owner",
            data_mode="real",
            lock_digest="sha256:" + "c" * 64,
            now="2026-07-24T00:02:00Z",
        )

    allowed = begin_trial(
        candidate,
        trial_id="safe",
        audience="owner",
        data_mode="real",
        lock_digest="sha256:" + "c" * 64,
        now="2026-07-24T00:02:00Z",
        real_data_read_only=True,
    )
    assert allowed.status == "trial"


def test_candidate_store_rejects_tampered_identity(tmp_path: Path) -> None:
    stable = _release("1.0.0", "1", "a")
    release = _release("1.1.0", "2", "b")
    candidate = candidate_from_release(
        candidate_id="recipes-candidate",
        release=release,
        base_release=stable,
        package_digest=release.components[0].digest,
        change_ids=("change-one", "change-two"),
        now="2026-07-24T00:00:00Z",
    )
    store = CandidateStore(tmp_path / "candidates")
    path = store.save(candidate)

    assert store.load(candidate.candidate_id) == candidate
    schema = json.loads(
        (Path(__file__).parents[1] / "src" / "adaos" / "abi" / "artifact.candidate.v1.schema.json").read_text(
            encoding="utf-8"
        )
    )
    jsonschema.Draft202012Validator(schema).validate(candidate.to_dict())
    payload = json.loads(path.read_text(encoding="utf-8"))
    payload["change_ids"] = ["different-change"]
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(CandidateError, match="digest does not match"):
        store.load(candidate.candidate_id)


def test_first_publication_uses_explicit_genesis_base() -> None:
    release = _release("1.0.0", "2", "b")
    candidate = candidate_from_release(
        candidate_id="recipes-genesis",
        release=release,
        base_release=None,
        package_digest=release.components[0].digest,
        change_ids=("create-recipes",),
        now="2026-07-24T00:00:00Z",
    )

    assert candidate.base_release == "unpublished"
    assert candidate.base_release_digest == "sha256:" + "0" * 64
    assert assess_freshness(candidate, None) == (True, None)
