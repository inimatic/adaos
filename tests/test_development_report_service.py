from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from adaos.domain.application import Application, ApplicationInstallation, ApplicationRelease
from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease
from adaos.services.applications.development_reports import DevelopmentReportService
from adaos.services.applications.report_directory import SubnetKeyDirectoryAuthority, SubnetKeyDirectoryClient
from adaos.services.applications.report_keys import SubnetPurposeKeyStore
from adaos.services.applications.report_relay import DurableDevelopmentReportRelay
from adaos.services.applications.report_triage import DevelopmentReportTriageService
from adaos.services.applications.store import ApplicationStore


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _application() -> Application:
    return Application(
        application_id="app_recipes", legacy_project_id="recipes", publisher_ref="subnet:publisher",
        slug="recipes", display={"title": "Recipes", "summary": "Recipes"}, visibility="public",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:recipes"},),
        publisher={"publisher_ref": "subnet:publisher", "display_name": "Publisher", "subnet_short_ref": "publisher", "release_key_ref": "release:1", "release_key_fingerprint": DIGEST_C, "home_zone": "zone_a", "trust_relation": "local"},
    )


def _release(version: str, package_digest: str, *, lifecycle: str = "stable", addresses: tuple[str, ...] = ()) -> ApplicationRelease:
    source = ArtifactSourceRef(forge="github", repository="inimatic/recipes", revision="0123456789abcdef0123456789abcdef01234567", path_scope=("scenarios/recipes/",))
    package = ArtifactPackageRef(kind="scenario", artifact_id="recipes", version=version, digest=package_digest, manifest_digest=DIGEST_C, source_ref=source)
    project = ProjectRelease(project_id="recipes", version=version, source_ref=source, components=(package,), validation_evidence=({"status": "passed"},)).seal()
    return ApplicationRelease(
        application_id="app_recipes", publisher_ref="subnet:publisher", project_release=project,
        accepted_candidate_id=f"candidate.recipes.{version}", acceptance_evidence=({"decision": "accepted"},),
        provenance_refs=(DIGEST_C,), addresses_report_ids=addresses, lifecycle=lifecycle,
    )


def _prepare_application(store: ApplicationStore, release: ApplicationRelease, *, install: bool) -> None:
    store.save_application(_application(), expected_revision=0)
    store.put_release(release)
    if install:
        store.save_installation(ApplicationInstallation(
            installation_id="installation:recipes", application_id="app_recipes",
            installed_release_digest=release.release_digest,
            component_refs=({"component_ref": "scenario:recipes", "package_digest": DIGEST_A, "lifecycle": "bound"},),
            data_policy="retain", status="active", revision=1,
        ), expected_revision=0)


def test_offline_report_accept_release_verify_round_trip(tmp_path: Path) -> None:
    clock = [datetime(2026, 9, 5, 12, tzinfo=timezone.utc)]
    base_release = _release("1.0.0", DIGEST_A)
    guest_store = ApplicationStore(tmp_path / "guest")
    publisher_store = ApplicationStore(tmp_path / "publisher")
    _prepare_application(guest_store, base_release, install=True)
    _prepare_application(publisher_store, base_release, install=False)

    guest_keys = SubnetPurposeKeyStore(tmp_path / "guest", now=lambda: clock[0])
    publisher_keys = SubnetPurposeKeyStore(tmp_path / "publisher", now=lambda: clock[0])
    for store, subnet in ((guest_keys, "subnet:guest"), (publisher_keys, "subnet:publisher")):
        store.ensure_key(subnet, "message_signing")
        store.ensure_key(subnet, "message_encryption")
    authority = SubnetKeyDirectoryAuthority(tmp_path / "directory", zone_id="zone_a", now=lambda: clock[0])
    authority.publish_subnet("subnet:guest", home_zone="zone_a", keys=guest_keys.list_public("subnet:guest"))
    projection = authority.publish_subnet("subnet:publisher", home_zone="zone_a", keys=publisher_keys.list_public("subnet:publisher"))
    guest_directory = SubnetKeyDirectoryClient()
    publisher_directory = SubnetKeyDirectoryClient()
    relay_directory = SubnetKeyDirectoryClient()
    for client in (guest_directory, publisher_directory, relay_directory):
        client.update(projection)
    relay = DurableDevelopmentReportRelay(tmp_path / "root", zone_id="zone_a", directory=relay_directory, now=lambda: clock[0])
    guest = DevelopmentReportService(tmp_path / "guest", subnet_ref="subnet:guest", application_store=guest_store, key_store=guest_keys, directory=guest_directory, relay=relay, now=lambda: clock[0])
    publisher = DevelopmentReportService(tmp_path / "publisher", subnet_ref="subnet:publisher", application_store=publisher_store, key_store=publisher_keys, directory=publisher_directory, relay=relay, now=lambda: clock[0])

    submitted = guest.create_report(
        application_id="app_recipes", summary="Import fails",
        details="Authorization: Bearer abcdefghijklmnopqrstuvwxyz should not enter Builder.",
        idempotency_key="report-import-1",
    )
    report_id = submitted["report"]["report_id"]
    assert guest.create_report(
        application_id="app_recipes", summary="ignored", details="ignored",
        idempotency_key="report-import-1",
    )["duplicate"] is True
    with pytest.raises(ValueError, match="unknown release"):
        guest.create_report(
            application_id="app_recipes", summary="Wrong release", details="Not installed",
            idempotency_key="wrong-release", installed_release_digest=DIGEST_C,
        )
    assert publisher.list_publisher_intakes() == []

    delivered = publisher.receive()
    assert delivered[0]["delivery_disposition"] == "accepted"
    intake = publisher.list_publisher_intakes()[0]
    assert intake["status"] == "quarantined"
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in intake["normalized_details"]
    assert publisher.ticket_service.list_tickets() == []

    guest.receive()
    assert guest.public_status(report_id)["status"] == "received"
    accepted = publisher.accept(report_id, actor="user:publisher")
    assert len(accepted["ticket_refs"]) == 1
    ticket = publisher.ticket_service.list_tickets()[0]
    assert ticket["metadata"]["external_development_report"]["report_id"] == report_id
    assert "Bearer abcdefghijklmnopqrstuvwxyz" not in str(ticket)
    guest.receive()
    assert guest.public_status(report_id)["status"] == "accepted"

    with pytest.raises(ValueError, match="not ready for release"):
        publisher.validate_release_addresses(
            "app_recipes", DIGEST_B, (report_id,)
        )
    publisher.set_public_status(report_id, status="planned")
    validation = publisher.validate_release_addresses(
        "app_recipes", DIGEST_B, (report_id,)
    )
    assert validation["validated_report_ids"] == [report_id]
    guest.receive()
    addressed_release = _release("1.1.0", DIGEST_B, addresses=(report_id,))
    publisher_store.put_release(addressed_release)
    guest_store.put_release(addressed_release)
    publisher.announce_release("app_recipes", addressed_release.release_digest)
    publisher.validate_release_addresses(
        "app_recipes", addressed_release.release_digest, (report_id,)
    )
    with pytest.raises(ValueError, match="not ready for release"):
        publisher.validate_release_addresses("app_recipes", DIGEST_C, (report_id,))
    guest.receive()
    assert guest.public_status(report_id)["release_digest"] == addressed_release.release_digest

    current_installation = guest_store.get_installation("app_recipes")
    guest_store.save_installation(replace(
        current_installation, installed_release_digest=addressed_release.release_digest,
        component_refs=({"component_ref": "scenario:recipes", "package_digest": DIGEST_B, "lifecycle": "bound"},),
        revision=2,
    ), expected_revision=1)
    guest.verify_release(report_id, outcome="verified", release_digest=addressed_release.release_digest)
    publisher.receive()
    guest.receive()
    assert guest.public_status(report_id)["status"] == "verified"

    guest.store.mutate(lambda state: state["events"].__setitem__(report_id, []))
    guest.request_resync(report_id, after_revision=0)
    publisher.receive()
    guest.receive()
    assert guest.public_status(report_id)["status"] == "verified"


def test_publisher_triage_is_explainable_and_appeal_reopens_without_auto_accept(
    tmp_path: Path,
) -> None:
    clock = [datetime(2026, 9, 5, 12, tzinfo=timezone.utc)]
    release = _release("1.0.0", DIGEST_A)
    guest_store = ApplicationStore(tmp_path / "guest")
    publisher_store = ApplicationStore(tmp_path / "publisher")
    _prepare_application(guest_store, release, install=True)
    _prepare_application(publisher_store, release, install=False)
    guest_keys = SubnetPurposeKeyStore(tmp_path / "guest", now=lambda: clock[0])
    publisher_keys = SubnetPurposeKeyStore(tmp_path / "publisher", now=lambda: clock[0])
    for key_store, subnet in (
        (guest_keys, "subnet:guest"),
        (publisher_keys, "subnet:publisher"),
    ):
        key_store.ensure_key(subnet, "message_signing")
        key_store.ensure_key(subnet, "message_encryption")
    authority = SubnetKeyDirectoryAuthority(
        tmp_path / "directory", zone_id="zone_a", now=lambda: clock[0]
    )
    authority.publish_subnet(
        "subnet:guest", home_zone="zone_a", keys=guest_keys.list_public("subnet:guest")
    )
    projection = authority.publish_subnet(
        "subnet:publisher",
        home_zone="zone_a",
        keys=publisher_keys.list_public("subnet:publisher"),
    )
    guest_directory = SubnetKeyDirectoryClient()
    publisher_directory = SubnetKeyDirectoryClient()
    relay_directory = SubnetKeyDirectoryClient()
    for client in (guest_directory, publisher_directory, relay_directory):
        client.update(projection)
    relay = DurableDevelopmentReportRelay(
        tmp_path / "root", zone_id="zone_a", directory=relay_directory,
        now=lambda: clock[0],
    )
    guest = DevelopmentReportService(
        tmp_path / "guest", subnet_ref="subnet:guest", application_store=guest_store,
        key_store=guest_keys, directory=guest_directory, relay=relay, now=lambda: clock[0],
    )
    publisher = DevelopmentReportService(
        tmp_path / "publisher", subnet_ref="subnet:publisher",
        application_store=publisher_store, key_store=publisher_keys,
        directory=publisher_directory, relay=relay, now=lambda: clock[0],
    )

    first = guest.create_report(
        application_id="app_recipes", summary="CSV import fails",
        details="Importing a UTF-8 CSV file fails after selecting the recipe list.",
        idempotency_key="csv-import-1",
    )["report"]
    second = guest.create_report(
        application_id="app_recipes", summary="CSV import failure",
        details="Selecting the recipe list and importing a UTF-8 CSV file fails.",
        idempotency_key="csv-import-2",
    )["report"]
    publisher.receive(limit=10)
    guest.receive(limit=10)

    triage = DevelopmentReportTriageService(publisher, now=lambda: clock[0])
    policy = triage.privacy_policy()
    assert policy["scope"] == "publisher_local_same_application"
    assert policy["automatic_actions"] == []
    before = publisher._publisher_records(first["report_id"])[1]
    candidates = triage.duplicate_candidates(first["report_id"], threshold=0.5)
    assert candidates["authority"] == "advisory_only"
    assert candidates["candidates"][0]["report_id"] == second["report_id"]
    assert candidates["candidates"][0]["shared_terms"]
    assert publisher._publisher_records(first["report_id"])[1] == before
    history = triage.reporter_history(first["report_id"])
    assert history["report_count"] == 2
    assert history["score"] is None and history["rank"] is None

    publisher.triage(first["report_id"], outcome="declined", reason_code="not_reproduced")
    guest.receive()
    submitted = guest.submit_appeal(
        first["report_id"],
        statement="Bearer abcdefghijklmnopqrstuvwxyz was unrelated; new reproduction attached.",
        idempotency_key="appeal-csv-1",
    )
    assert "abcdefghijklmnopqrstuvwxyz" not in submitted["appeal"]["statement"]
    assert guest.submit_appeal(
        first["report_id"], statement=submitted["appeal"]["statement"],
        idempotency_key="appeal-csv-1",
    )["duplicate"] is True
    publisher.receive()
    publisher_appeal = publisher.list_appeals(first["report_id"])[0]
    assert publisher_appeal["status"] == "received"

    resolved = publisher.resolve_appeal(
        publisher_appeal["appeal_id"], resolution="corrected",
        rationale="The added reproduction corrects the earlier assessment.",
    )
    assert resolved["appeal"]["resolution"] == "corrected"
    assert publisher._publisher_records(first["report_id"])[1].status == "triaged"
    assert publisher.ticket_service.list_tickets() == []
    guest.receive(limit=10)
    assert guest.public_status(first["report_id"])["status"] == "triaged"
    assert guest.list_appeals(first["report_id"])[0]["status"] == "resolved"
    assert guest.list_appeals(first["report_id"])[0]["rationale"] == (
        "The added reproduction corrects the earlier assessment."
    )
