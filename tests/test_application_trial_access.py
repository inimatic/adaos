from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease
from adaos.services.applications import ApplicationService, ApplicationStore, TrialAccessError, TrialAccessService


PROVENANCE = "sha256:" + "b" * 64


def _core(tmp_path: Path) -> tuple[ApplicationService, TrialAccessService, str]:
    service = ApplicationService(ApplicationStore(tmp_path / "state"))
    application = Application(
        application_id="app_recipes",
        legacy_project_id="recipes",
        publisher_ref="subnet:publisher",
        slug="recipes",
        display={"title": "Recipes", "summary": None},
        visibility="link",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:recipes"},),
        publisher={
            "publisher_ref": "subnet:publisher",
            "display_name": "Publisher",
            "subnet_short_ref": "pub-1234",
            "release_key_ref": "subnet-key:release-1",
            "release_key_fingerprint": "sha256:" + "c" * 64,
            "home_zone": "zone-a",
            "trust_relation": "local",
        },
    )
    service.register(application)
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version="1.0.0",
        digest="sha256:" + "d" * 64,
        source_ref=ArtifactSourceRef(
            forge="adaos-root",
            repository="publisher/applications",
            revision="1" * 40,
        ),
        manifest_digest="sha256:" + "e" * 64,
    )
    project_release = ProjectRelease(
        project_id="recipes",
        version="1.0.0",
        source_ref=package.source_ref,
        components=(package,),
        resolved_dependencies=(),
        permissions=(),
        migrations=(),
        validation_evidence=({"status": "passed"},),
    ).seal()
    release_digest = str(project_release.release_digest)
    service.register_release(
        ApplicationRelease(
            application_id="app_recipes",
            publisher_ref="subnet:publisher",
            project_release=project_release,
            accepted_candidate_id="candidate-1",
            acceptance_evidence=({"status": "accepted"},),
            provenance_refs=(PROVENANCE,),
            lifecycle="trial",
        )
    )
    return service, TrialAccessService(service), release_digest


def _expiry(hours: int = 1) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def test_exact_trial_link_is_targeted_bounded_and_idempotent(tmp_path: Path) -> None:
    _, access, release_digest = _core(tmp_path)
    issued = access.issue(
        "app_recipes",
        publisher_ref="subnet:publisher",
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        scope="exact_release",
        release_digest=release_digest,
        expires_at=_expiry(),
        allowed_zones=("zone-a",),
        max_uses=1,
        idempotency_key="invite-1",
    )
    repeated = access.issue(
        "app_recipes",
        publisher_ref="subnet:publisher",
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        scope="exact_release",
        release_digest=release_digest,
        expires_at=issued["grant"]["expires_at"],
        allowed_zones=("zone-a",),
        max_uses=1,
        idempotency_key="invite-1",
    )
    assert repeated["link"] == issued["link"]
    receipt = access.resolve(
        issued["link"],
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        zone="zone-a",
        redemption_id="install-attempt-1",
    )
    replay = access.resolve(
        issued["link"],
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        zone="zone-a",
        redemption_id="install-attempt-1",
    )
    assert receipt["release_digest"] == release_digest
    assert replay["idempotent_replay"] is True
    assert access.store.get_grant(issued["grant"]["grant_id"]).status == "consumed"


def test_trial_link_rejects_wrong_recipient_replay_and_revocation(tmp_path: Path) -> None:
    _, access, release_digest = _core(tmp_path)
    issued = access.issue(
        "app_recipes",
        publisher_ref="subnet:publisher",
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        scope="exact_release",
        release_digest=release_digest,
        expires_at=_expiry(),
        allowed_zones=("zone-a",),
        max_uses=2,
        idempotency_key="invite-2",
    )
    with pytest.raises(TrialAccessError, match="another subnet"):
        access.resolve(
            issued["link"],
            recipient_subnet_ref="subnet:attacker",
            recipient_key_ref="subnet-key:guest-encryption-1",
            zone="zone-a",
            redemption_id="attack-1",
        )
    access.resolve(
        issued["link"],
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        zone="zone-a",
        redemption_id="install-1",
    )
    grant = access.store.get_grant(issued["grant"]["grant_id"])
    access.revoke(grant.grant_id, publisher_ref="subnet:publisher", expected_revision=grant.revision)
    with pytest.raises(TrialAccessError, match="revoked"):
        access.resolve(
            issued["link"],
            recipient_subnet_ref="subnet:guest",
            recipient_key_ref="subnet-key:guest-encryption-1",
            zone="zone-a",
            redemption_id="install-2",
        )


def test_follow_prerelease_resolves_current_pointer_without_changing_grant(tmp_path: Path) -> None:
    service, access, release_digest = _core(tmp_path)
    service.store.set_channel("app_recipes", "prerelease", release_digest, expected_release_digest=None)
    issued = access.issue(
        "app_recipes",
        publisher_ref="subnet:publisher",
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        scope="follow_prerelease",
        expires_at=_expiry(),
        allowed_zones=("zone-a",),
        max_uses=2,
        idempotency_key="follow-1",
    )
    receipt = access.resolve(
        issued["link"],
        recipient_subnet_ref="subnet:guest",
        recipient_key_ref="subnet-key:guest-encryption-1",
        zone="zone-a",
        redemption_id="follow-install-1",
    )
    assert receipt["release_digest"] == release_digest
    assert issued["grant"]["release_digest"] is None
