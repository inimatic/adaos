from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease
from adaos.services.applications import (
    MetadataSigner,
    TrustedMetadataAuthority,
    TrustedMetadataClient,
    TrustedMetadataError,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)
PACKAGE_DIGEST = "sha256:" + "a" * 64
ATTESTATION_DIGEST = "sha256:" + "b" * 64


def _records() -> tuple[Application, ApplicationRelease]:
    source = ArtifactSourceRef(
        forge="adaos-root",
        repository="publisher/applications",
        revision="1" * 40,
    )
    package = ArtifactPackageRef(
        kind="scenario",
        artifact_id="recipes",
        version="1.0.0",
        digest=PACKAGE_DIGEST,
        manifest_digest="sha256:" + "c" * 64,
        source_ref=source,
    )
    project_release = ProjectRelease(
        project_id="recipes",
        version="1.0.0",
        source_ref=source,
        components=(package,),
        validation_evidence=({"status": "passed"},),
    ).seal()
    application = Application(
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
            "release_key_fingerprint": "sha256:" + "d" * 64,
            "home_zone": "zone-a",
            "trust_relation": "trusted",
        },
    )
    release = ApplicationRelease(
        application_id=application.application_id,
        publisher_ref=application.publisher_ref,
        project_release=project_release,
        accepted_candidate_id="candidate-1",
        acceptance_evidence=({"status": "accepted"},),
        provenance_refs=(ATTESTATION_DIGEST,),
        lifecycle="trial",
    )
    return application, release


def _authority(tmp_path: Path) -> TrustedMetadataAuthority:
    return TrustedMetadataAuthority(
        tmp_path / "metadata",
        signers={role: MetadataSigner.generate(role) for role in ("root", "targets", "snapshot", "freshness")},
    )


def _target(authority: TrustedMetadataAuthority, *, status: str = "active"):
    application, release = _records()
    return authority.release_target(
        application,
        release,
        channels=("stable",),
        package_sizes={PACKAGE_DIGEST: 4096},
        attestation_set_digest=ATTESTATION_DIGEST,
        status=status,
    )


def _client(tmp_path: Path, authority: TrustedMetadataAuthority) -> TrustedMetadataClient:
    return TrustedMetadataClient(
        tmp_path / "client" / "trusted-metadata.json",
        pinned_root_key_id=authority.signers["root"].key_id,
    )


def _verify(client: TrustedMetadataClient, bundle, *, now: datetime = NOW):
    _, release = _records()
    return client.verify_release(
        bundle,
        application_id="app_recipes",
        publisher_ref="subnet:publisher",
        release_digest=release.release_digest,
        observed_packages={PACKAGE_DIGEST: 4096},
        now=now,
    )


def test_signed_metadata_bundle_verifies_and_rejects_rollback_and_mix(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    client = _client(tmp_path, authority)
    first = authority.publish((_target(authority),), now=NOW)
    second = authority.publish((_target(authority),), now=NOW + timedelta(minutes=1))

    assert _verify(client, first)["status"] == "verified"
    assert _verify(client, second, now=NOW + timedelta(minutes=1))["versions"]["targets"] == 2
    with pytest.raises(TrustedMetadataError, match="rollback"):
        _verify(client, first, now=NOW + timedelta(minutes=2))

    mixed = dict(second)
    mixed["targets"] = first["targets"]
    with pytest.raises(TrustedMetadataError, match="rollback|mix-and-match"):
        _verify(client, mixed, now=NOW + timedelta(minutes=2))


def test_metadata_rejects_wrong_publisher_size_yank_revocation_and_disable(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    application, release = _records()
    active = authority.publish((_target(authority),), now=NOW)

    with pytest.raises(TrustedMetadataError, match="publisher"):
        TrustedMetadataClient(
            tmp_path / "unknown.json", pinned_root_key_id=authority.signers["root"].key_id
        ).verify_release(
            active,
            application_id=application.application_id,
            publisher_ref="subnet:unknown",
            release_digest=release.release_digest,
            observed_packages={PACKAGE_DIGEST: 4096},
            now=NOW,
        )
    with pytest.raises(TrustedMetadataError, match="size/digest"):
        _client(tmp_path / "size", authority).verify_release(
            active,
            application_id=application.application_id,
            publisher_ref=application.publisher_ref,
            release_digest=release.release_digest,
            observed_packages={PACKAGE_DIGEST: 4095},
            now=NOW,
        )

    yanked = authority.publish((_target(authority, status="yanked"),), now=NOW + timedelta(minutes=1))
    with pytest.raises(TrustedMetadataError, match="yanked"):
        _client(tmp_path / "yanked", authority).verify_release(
            yanked,
            application_id=application.application_id,
            publisher_ref=application.publisher_ref,
            release_digest=release.release_digest,
            observed_packages={PACKAGE_DIGEST: 4096},
            now=NOW + timedelta(minutes=1),
        )
    revoked = authority.publish(
        (_target(authority),),
        now=NOW + timedelta(minutes=2),
        revoked_publisher_keys=(application.publisher["release_key_fingerprint"],),
    )
    with pytest.raises(TrustedMetadataError, match="revoked"):
        _client(tmp_path / "revoked", authority).verify_release(
            revoked,
            application_id=application.application_id,
            publisher_ref=application.publisher_ref,
            release_digest=release.release_digest,
            observed_packages={PACKAGE_DIGEST: 4096},
            now=NOW + timedelta(minutes=2),
        )
    disabled = authority.publish(
        (_target(authority),),
        now=NOW + timedelta(minutes=3),
        emergency_disabled_applications=(application.application_id,),
    )
    with pytest.raises(TrustedMetadataError, match="disabled"):
        _client(tmp_path / "disabled", authority).verify_release(
            disabled,
            application_id=application.application_id,
            publisher_ref=application.publisher_ref,
            release_digest=release.release_digest,
            observed_packages={PACKAGE_DIGEST: 4096},
            now=NOW + timedelta(minutes=3),
        )


def test_stale_metadata_allows_existing_runtime_only(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    bundle = authority.publish((_target(authority),), now=NOW, freshness_hours=1)
    application, release = _records()
    stale_time = NOW + timedelta(hours=3)
    client = _client(tmp_path, authority)

    with pytest.raises(TrustedMetadataError, match="stale"):
        client.verify_release(
            bundle,
            application_id=application.application_id,
            publisher_ref=application.publisher_ref,
            release_digest=release.release_digest,
            observed_packages={PACKAGE_DIGEST: 4096},
            now=stale_time,
        )
    result = client.verify_release(
        bundle,
        application_id=application.application_id,
        publisher_ref=application.publisher_ref,
        release_digest=release.release_digest,
        observed_packages={PACKAGE_DIGEST: 4096},
        now=stale_time,
        allow_stale_installed=True,
    )
    assert result["status"] == "stale_metadata_installed_only"
    assert "freshness" in result["expired_roles"]


def test_root_and_online_role_rotation_requires_old_and_new_trust(tmp_path: Path) -> None:
    authority = _authority(tmp_path)
    client = _client(tmp_path, authority)
    first = authority.publish((_target(authority),), now=NOW)
    _verify(client, first)
    replacement = {
        role: MetadataSigner.generate(role)
        for role in ("root", "targets", "snapshot", "freshness")
    }
    pending = authority.rotate_keys(replacement, now=NOW + timedelta(minutes=1))
    assert len(pending["signatures"]) == 2
    rotated = authority.publish((_target(authority),), now=NOW + timedelta(minutes=2))

    verified = _verify(client, rotated, now=NOW + timedelta(minutes=2))

    assert verified["versions"]["root"] == 2
    assert client._state()["trusted_root"]["signed"]["roles"]["root"]["key_ids"] == [
        replacement["root"].key_id
    ]
