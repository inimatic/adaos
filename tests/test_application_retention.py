from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactSourceRef
from adaos.services.applications import (
    ApplicationRetentionService,
    ApplicationService,
    ApplicationStore,
)
from adaos.services.artifact_pipeline.packages import ContentAddressedPackageStore, build_artifact_package
from adaos.services.artifact_pipeline.releases import PackageCatalog, build_project_release
from adaos.services.artifact_pipeline.retention import (
    ArtifactPipelineRetentionManager,
    ArtifactRetentionError,
    ArtifactRetentionPolicy,
)


NOW = datetime(2026, 9, 5, 12, 0, tzinfo=timezone.utc)


def _fixture(tmp_path: Path):
    source_dir = tmp_path / "source" / "recipes"
    source_dir.mkdir(parents=True)
    (source_dir / "scenario.yaml").write_text("id: recipes\nversion: 1.0.0\n", encoding="utf-8")
    source_ref = ArtifactSourceRef(
        forge="adaos-root", repository="publisher/applications", revision="1" * 40
    )
    built = build_artifact_package(source_dir, kind="scenario", source_ref=source_ref)
    plan = build_project_release(
        project_id="recipes",
        version="1.0.0",
        source_ref=source_ref,
        components=(built.ref,),
        catalog=PackageCatalog(),
        validation_evidence=({"status": "passed"},),
    )
    service = ApplicationService(ApplicationStore(tmp_path / "state"))
    service.register(
        Application(
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
                "release_key_fingerprint": "sha256:" + "a" * 64,
                "home_zone": "zone-a",
                "trust_relation": "local",
            },
        )
    )
    release = service.register_release(
        ApplicationRelease(
            application_id="app_recipes",
            publisher_ref="subnet:publisher",
            project_release=plan.release,
            accepted_candidate_id="candidate-1",
            acceptance_evidence=({"status": "accepted"},),
            provenance_refs=("sha256:" + "b" * 64,),
            lifecycle="trial",
        )
    )
    service.move_channel(
        "app_recipes",
        "stable",
        release.release_digest,
        publisher_ref="subnet:publisher",
        expected_release_digest=None,
    )
    artifact_root = tmp_path / "state" / "artifact_pipeline"
    package_store = ContentAddressedPackageStore(artifact_root / "packages")
    package_store.put(built.archive_bytes, expected_digest=built.ref.digest)
    os.utime(package_store.package_path(built.ref.digest), (1, 1))
    return service, release, built.ref.digest, artifact_root


def _manager(artifact_root: Path, retention: ApplicationRetentionService, observed: datetime):
    return ArtifactPipelineRetentionManager(
        state_root=artifact_root,
        workspace_root=artifact_root.parent / "workspace",
        policy=ArtifactRetentionPolicy(
            orphan_grace_seconds=0,
            package_retention_seconds=0,
            record_retention_seconds=0,
            lock_history_retention_seconds=0,
        ),
        protected_digests_provider=lambda: retention.protected_digests(now=observed),
    )


def test_channels_holds_and_grace_tombstones_protect_package_cas(tmp_path: Path) -> None:
    service, release, package_digest, artifact_root = _fixture(tmp_path)
    retention = ApplicationRetentionService(service)
    active = _manager(artifact_root, retention, NOW).run(dry_run=True, now=NOW.timestamp())
    assert package_digest in active["protected_package_digests"]

    service.store.set_channel(
        "app_recipes", "stable", None, expected_release_digest=release.release_digest
    )
    retention.retire_release(
        "app_recipes",
        release.release_digest,
        reason="superseded by verified stable",
        grace_until=(NOW + timedelta(days=7)).isoformat(),
        disposition="superseded",
    )
    grace = _manager(artifact_root, retention, NOW + timedelta(days=1)).run(
        dry_run=True, now=(NOW + timedelta(days=1)).timestamp()
    )
    assert package_digest in grace["protected_package_digests"]

    expired = _manager(artifact_root, retention, NOW + timedelta(days=8)).run(
        dry_run=True, now=(NOW + timedelta(days=8)).timestamp()
    )
    assert package_digest not in expired["protected_package_digests"]
    assert any(
        item["reason"] == "unreferenced_package" and item["path"].endswith(f"{package_digest[7:]}.zip")
        for item in expired["actions"]
    )


def test_corrupt_application_hold_blocks_cas_gc_fail_closed(tmp_path: Path) -> None:
    service, _, _, artifact_root = _fixture(tmp_path)
    retention = ApplicationRetentionService(service)
    hold_root = retention.root / "holds"
    hold_root.mkdir(parents=True)
    (hold_root / "corrupt.json").write_text("not-json", encoding="utf-8")

    with pytest.raises(ArtifactRetentionError, match="GC is blocked"):
        _manager(artifact_root, retention, NOW).run(dry_run=True, now=NOW.timestamp())
