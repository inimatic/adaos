from __future__ import annotations

from pathlib import Path

import pytest

from adaos.domain.application import Application
from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.applications import (
    ApplicationDistributionError,
    ApplicationDistributionService,
    ApplicationService,
    ApplicationStore,
    DistributionOutcomeUnknown,
)
from adaos.services.artifact_pipeline.attestation_sets import (
    ArtifactAttestationRef,
    ReleaseAttestationSet,
)
from adaos.services.artifact_pipeline.attestations import (
    PACKAGE_PROVENANCE_PREDICATE,
    RELEASE_PROVENANCE_PREDICATE,
    package_provenance_digest,
    release_provenance_digest,
)
from adaos.services.artifact_pipeline.candidates import (
    CandidateStore,
    begin_trial,
    candidate_from_release,
    complete_trial,
    record_validation,
)
from adaos.services.artifact_pipeline.channels import ReleaseRepository
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore, build_artifact_package
from adaos.services.artifact_pipeline.releases import PackageCatalog, ReleasePlan, build_project_release


NOW = "2026-09-05T12:00:00+00:00"
KEY_ID = "sha256:" + "1" * 64


class _Remote:
    def __init__(self, root: Path) -> None:
        self.repository = ReleaseRepository(root)
        self.archives: dict[str, bytes] = {}
        self.fail_after_upload_once = False
        self.fail_after_channel_once = False
        self.fail_after_clear_once = False
        self.upload_writes = 0
        self.channel_writes = 0

    def put_release(self, plan: ReleasePlan, archives: dict[str, bytes]) -> None:
        self.upload_writes += 1
        self.archives.update(archives)
        self.repository.put_release(plan)
        if self.fail_after_upload_once:
            self.fail_after_upload_once = False
            raise TimeoutError("upload response lost")

    def get_release(self, project_id: str, release_digest: str) -> ReleasePlan:
        return self.repository.get_release(project_id, release_digest)

    def fetch_package(self, package) -> bytes:
        return self.archives[package.digest]

    def get_channel(self, project_id: str, channel: str = "stable"):
        return self.repository.get_channel(project_id, channel)

    def set_channel(self, plan: ReleasePlan, channel: str = "stable", *, expected_release_digest: str | None):
        self.channel_writes += 1
        pointer = self.repository.set_channel(
            plan.release.project_id,
            channel,
            str(plan.release.release_digest),
            expected_release_digest=expected_release_digest,
        )
        if self.fail_after_channel_once:
            self.fail_after_channel_once = False
            raise TimeoutError("channel response lost")
        return pointer

    def clear_channel(self, project_id: str, channel: str, *, expected_release_digest: str):
        self.channel_writes += 1
        pointer = self.repository.clear_channel(
            project_id,
            channel,
            expected_release_digest=expected_release_digest,
        )
        if self.fail_after_clear_once:
            self.fail_after_clear_once = False
            raise TimeoutError("clear response lost")
        return pointer


class _ReleaseSets:
    def __init__(self) -> None:
        self.bindings: dict[str, ReleaseAttestationSet] = {}

    def get_release_attestation_set(self, project_id: str, release_digest: str):
        binding = self.bindings[release_digest]
        assert binding.project_id == project_id
        return binding


class _Admission:
    def __init__(self) -> None:
        self.release_sets = _ReleaseSets()

    def verify_release_plan(self, plan: ReleasePlan):
        digest = str(plan.release.release_digest)
        assert digest in self.release_sets.bindings
        return {"status": "verified", "release_digest": digest}


def _application() -> Application:
    return Application(
        application_id="app_recipes",
        legacy_project_id="recipes",
        publisher_ref="subnet:publisher",
        slug="recipes",
        display={"title": "Recipes", "summary": None},
        visibility="public",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:recipes"},),
        publisher={
            "publisher_ref": "subnet:publisher",
            "display_name": "Publisher",
            "subnet_short_ref": "pub-1234",
            "release_key_ref": "subnet-key:release-1",
            "release_key_fingerprint": "sha256:" + "2" * 64,
            "home_zone": "zone-a",
            "trust_relation": "local",
        },
    )


def _accepted_candidate(
    tmp_path: Path,
    *,
    version: str,
    base: ReleasePlan | None,
    candidates: CandidateStore,
    releases: ReleaseRepository,
    packages: ContentAddressedPackageStore,
    admission: _Admission,
):
    source_dir = tmp_path / "sources" / version
    source_dir.mkdir(parents=True)
    (source_dir / "scenario.yaml").write_text(
        f"id: recipes\nversion: {version}\n", encoding="utf-8"
    )
    source_ref = ArtifactSourceRef(
        forge="adaos-root",
        repository="publisher/applications",
        revision=(version.replace(".", "") * 40)[:40],
    )
    built = build_artifact_package(source_dir, kind="scenario", source_ref=source_ref)
    plan = build_project_release(
        project_id="recipes",
        version=version,
        source_ref=source_ref,
        components=(built.ref,),
        catalog=PackageCatalog(),
        validation_evidence=({"status": "passed", "suite": "distribution"},),
    )
    candidate = candidate_from_release(
        candidate_id=f"recipes-{version.replace('.', '-')}",
        release=plan.release,
        base_release=base.release if base else None,
        package_digest=built.ref.digest,
        change_ids=(f"change-{version}",),
        now=NOW,
    )
    candidate = record_validation(candidate, {"status": "passed"}, now=NOW)
    candidate = begin_trial(
        candidate,
        trial_id=f"trial-{version}",
        audience="publisher",
        data_mode="empty",
        lock_digest="sha256:" + "3" * 64,
        now=NOW,
        health_receipt={"status": "passed"},
    )
    candidate = complete_trial(
        candidate,
        trial_id=f"trial-{version}",
        accepted=True,
        now="2026-09-05T12:01:00+00:00",
        rollback_receipt={"status": "not_required"},
    )
    candidates.save(candidate)
    releases.put_release(plan)
    packages.put(built.archive_bytes, expected_digest=built.ref.digest)
    refs = [
        ArtifactAttestationRef(
            subject_kind="release",
            subject_digest=str(plan.release.release_digest),
            project_id="recipes",
            attestation_digest="sha256:" + "4" * 64,
            issuer="publisher",
            key_id=KEY_ID,
            predicate_type=RELEASE_PROVENANCE_PREDICATE,
            predicate_digest=release_provenance_digest(plan.release),
        ),
        ArtifactAttestationRef(
            subject_kind="package",
            subject_digest=built.ref.digest,
            project_id="recipes",
            attestation_digest="sha256:" + "5" * 64,
            issuer="publisher",
            key_id=KEY_ID,
            predicate_type=PACKAGE_PROVENANCE_PREDICATE,
            predicate_digest=package_provenance_digest(built.ref),
        ),
    ]
    admission.release_sets.bindings[str(plan.release.release_digest)] = ReleaseAttestationSet.from_references(
        plan, refs
    )
    return candidate, plan


def _service(tmp_path: Path):
    applications = ApplicationService(ApplicationStore(tmp_path / "state"))
    applications.register(_application())
    candidates = CandidateStore(tmp_path / "candidates")
    releases = ReleaseRepository(tmp_path / "release-cache")
    packages = ContentAddressedPackageStore(tmp_path / "packages")
    remote = _Remote(tmp_path / "remote")
    admission = _Admission()
    distribution = ApplicationDistributionService(
        applications=applications,
        candidates=candidates,
        releases=releases,
        packages=packages,
        remote=remote,
        admission=admission,
    )
    return distribution, candidates, releases, packages, remote, admission


def test_link_trial_bootstraps_first_stable_without_rebuild(tmp_path: Path) -> None:
    distribution, candidates, releases, packages, remote, admission = _service(tmp_path)
    candidate, plan = _accepted_candidate(
        tmp_path,
        version="1.0.0",
        base=None,
        candidates=candidates,
        releases=releases,
        packages=packages,
        admission=admission,
    )

    trial = distribution.publish_trial(
        "app_recipes", candidate.candidate_id, publisher_ref="subnet:publisher", mode="link_only"
    )
    promoted = distribution.promote_stable(
        "app_recipes",
        candidate.candidate_id,
        publisher_ref="subnet:publisher",
        expected_stable_digest=None,
    )

    assert "channel" not in trial
    assert promoted["channel"]["pointer"]["release_digest"] == candidate.release_digest
    assert remote.get_channel("recipes", "stable").release_digest == candidate.release_digest
    assert remote.upload_writes == 1
    assert releases.get_release("recipes", candidate.release_digest) == plan


def test_later_stable_requires_current_exact_prerelease(tmp_path: Path) -> None:
    distribution, candidates, releases, packages, _, admission = _service(tmp_path)
    first, first_plan = _accepted_candidate(
        tmp_path,
        version="1.0.0",
        base=None,
        candidates=candidates,
        releases=releases,
        packages=packages,
        admission=admission,
    )
    distribution.publish_trial("app_recipes", first.candidate_id, publisher_ref="subnet:publisher", mode="link_only")
    distribution.promote_stable(
        "app_recipes", first.candidate_id, publisher_ref="subnet:publisher", expected_stable_digest=None
    )
    second, _ = _accepted_candidate(
        tmp_path,
        version="1.1.0",
        base=first_plan,
        candidates=candidates,
        releases=releases,
        packages=packages,
        admission=admission,
    )
    with pytest.raises(ApplicationDistributionError, match="Trial publication"):
        distribution.promote_stable(
            "app_recipes",
            second.candidate_id,
            publisher_ref="subnet:publisher",
            expected_stable_digest=first.release_digest,
        )
    distribution.publish_trial(
        "app_recipes", second.candidate_id, publisher_ref="subnet:publisher", mode="prerelease"
    )
    promoted = distribution.promote_stable(
        "app_recipes",
        second.candidate_id,
        publisher_ref="subnet:publisher",
        expected_stable_digest=first.release_digest,
    )
    assert promoted["release"]["release_digest"] == second.release_digest
    with pytest.raises(FileNotFoundError):
        distribution.remote.get_channel("recipes", "prerelease")
    assert distribution.applications.store.get_channels("app_recipes")["channels"] == {
        "stable": second.release_digest
    }


def test_unknown_upload_is_observed_before_retry(tmp_path: Path) -> None:
    distribution, candidates, releases, packages, remote, admission = _service(tmp_path)
    candidate, _ = _accepted_candidate(
        tmp_path,
        version="1.0.0",
        base=None,
        candidates=candidates,
        releases=releases,
        packages=packages,
        admission=admission,
    )
    remote.fail_after_upload_once = True
    with pytest.raises(DistributionOutcomeUnknown, match="reconcile"):
        distribution.publish_trial(
            "app_recipes", candidate.candidate_id, publisher_ref="subnet:publisher", mode="link_only"
        )
    with pytest.raises(DistributionOutcomeUnknown, match="reconcile"):
        distribution.publish_trial(
            "app_recipes", candidate.candidate_id, publisher_ref="subnet:publisher", mode="link_only"
        )

    reconciled = distribution.reconcile(candidate.candidate_id)
    completed = distribution.publish_trial(
        "app_recipes", candidate.candidate_id, publisher_ref="subnet:publisher", mode="link_only"
    )

    assert reconciled["upload"]["completed_via"] == "reconciliation"
    assert completed["operation"]["trial_publication"]["status"] == "completed"
    assert remote.upload_writes == 1


def test_public_prerelease_is_not_allowed_before_first_stable(tmp_path: Path) -> None:
    distribution, candidates, releases, packages, _, admission = _service(tmp_path)
    candidate, _ = _accepted_candidate(
        tmp_path,
        version="1.0.0",
        base=None,
        candidates=candidates,
        releases=releases,
        packages=packages,
        admission=admission,
    )
    with pytest.raises(ApplicationDistributionError, match="first stable"):
        distribution.publish_trial(
            "app_recipes", candidate.candidate_id, publisher_ref="subnet:publisher", mode="prerelease"
        )


def test_unknown_channel_move_and_retirement_reconcile_without_second_write(tmp_path: Path) -> None:
    distribution, candidates, releases, packages, remote, admission = _service(tmp_path)
    first, first_plan = _accepted_candidate(
        tmp_path,
        version="1.0.0",
        base=None,
        candidates=candidates,
        releases=releases,
        packages=packages,
        admission=admission,
    )
    distribution.publish_trial("app_recipes", first.candidate_id, publisher_ref="subnet:publisher", mode="link_only")
    remote.fail_after_channel_once = True
    with pytest.raises(DistributionOutcomeUnknown, match="stable channel"):
        distribution.promote_stable(
            "app_recipes", first.candidate_id, publisher_ref="subnet:publisher", expected_stable_digest=None
        )
    writes = remote.channel_writes
    distribution.reconcile(first.candidate_id)
    distribution.promote_stable(
        "app_recipes", first.candidate_id, publisher_ref="subnet:publisher", expected_stable_digest=None
    )
    assert remote.channel_writes == writes

    second, _ = _accepted_candidate(
        tmp_path,
        version="1.1.0",
        base=first_plan,
        candidates=candidates,
        releases=releases,
        packages=packages,
        admission=admission,
    )
    distribution.publish_trial(
        "app_recipes", second.candidate_id, publisher_ref="subnet:publisher", mode="prerelease"
    )
    remote.fail_after_clear_once = True
    with pytest.raises(DistributionOutcomeUnknown, match="retirement"):
        distribution.promote_stable(
            "app_recipes",
            second.candidate_id,
            publisher_ref="subnet:publisher",
            expected_stable_digest=first.release_digest,
        )
    writes = remote.channel_writes
    distribution.reconcile(second.candidate_id)
    promoted = distribution.promote_stable(
        "app_recipes",
        second.candidate_id,
        publisher_ref="subnet:publisher",
        expected_stable_digest=first.release_digest,
    )
    replay = distribution.promote_stable(
        "app_recipes",
        second.candidate_id,
        publisher_ref="subnet:publisher",
        expected_stable_digest=first.release_digest,
    )
    assert promoted["release"]["release_digest"] == second.release_digest
    assert replay["operation"]["stable_promotion"]["status"] == "completed"
    assert remote.channel_writes == writes
