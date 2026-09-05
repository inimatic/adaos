from __future__ import annotations

from pathlib import Path

import pytest

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease
from adaos.domain.development_report import DevelopmentReport
from adaos.services.applications.report_admission import DevelopmentReportAdmissionError, DevelopmentReportAdmissionService
from adaos.services.applications.store import ApplicationStore


DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64
DIGEST_C = "sha256:" + "c" * 64


def _store(tmp_path: Path) -> tuple[ApplicationStore, ApplicationRelease]:
    store = ApplicationStore(tmp_path)
    application = Application(
        application_id="app_test", legacy_project_id="test", publisher_ref="subnet:publisher",
        slug="test", display={"title": "Test", "summary": None}, visibility="public",
        entrypoints=({"entrypoint_id": "main", "presentation_ref": "scenario:test"},),
        publisher={"publisher_ref": "subnet:publisher", "display_name": "Publisher", "subnet_short_ref": "pub", "release_key_ref": "release:1", "release_key_fingerprint": DIGEST_C, "home_zone": "zone_a", "trust_relation": "unverified"},
    )
    source = ArtifactSourceRef(forge="github", repository="inimatic/test", revision="0123456789abcdef0123456789abcdef01234567", path_scope=("scenarios/test/",))
    project = ProjectRelease(
        project_id="test", version="1.0.0", source_ref=source,
        components=(ArtifactPackageRef(kind="scenario", artifact_id="test", version="1.0.0", digest=DIGEST_A, manifest_digest=DIGEST_B, source_ref=source),),
        validation_evidence=({"status": "passed"},),
    ).seal()
    release = ApplicationRelease(application_id="app_test", publisher_ref="subnet:publisher", project_release=project, accepted_candidate_id="candidate.test", acceptance_evidence=({"decision": "accepted"},), provenance_refs=(DIGEST_C,), lifecycle="stable")
    store.save_application(application, expected_revision=0)
    store.put_release(release)
    return store, release


def _report(release: ApplicationRelease, **changes) -> DevelopmentReport:
    values = {
        "report_id": "report.test", "application_id": "app_test", "publisher_ref": "subnet:publisher",
        "reporter_subnet_ref": "subnet:guest", "reporter_key_id": DIGEST_B,
        "installed_release_digest": release.release_digest,
        "installation_proof": {"installation_id": "installation:test", "application_id": "app_test", "release_digest": release.release_digest, "installation_revision": 1},
        "idempotency_key": "report-test-1", "summary": "Import failure", "details": "token=very-secret-token-value",
        "evidence": (), "status": "queued", "revision": 1,
    }
    values.update(changes)
    return DevelopmentReport(**values)


def test_admission_redacts_secrets_and_validates_bounded_archive_metadata(tmp_path: Path) -> None:
    store, release = _store(tmp_path)
    report = _report(release, evidence=({
        "kind": "logs", "mime_type": "application/zip", "size_bytes": 1000,
        "digest": DIGEST_A, "artifact_ref": "artifact:guest-log",
        "archive": {"expanded_size_bytes": 20_000, "entries": ["logs/error.txt"]},
    },))
    admitted = DevelopmentReportAdmissionService(application_store=store).admit(report)
    assert admitted.normalized_details == "token=[REDACTED]"
    assert admitted.redaction_findings == ("assigned_secret",)
    assert "secret-token-value" not in str(admitted.to_dict())


@pytest.mark.parametrize(
    "details,evidence,match",
    [
        ("See http://localhost/admin", (), "credential-free HTTPS"),
        ("Hidden \u202etext", (), "control characters"),
        ("Archive", ({"kind": "logs", "mime_type": "application/zip", "size_bytes": 1000, "digest": DIGEST_A, "artifact_ref": "artifact:bad", "archive": {"expanded_size_bytes": 2000, "entries": ["../secret"]}},), "unsafe"),
    ],
)
def test_admission_rejects_url_unicode_and_archive_poisoning(tmp_path: Path, details, evidence, match) -> None:
    store, release = _store(tmp_path)
    with pytest.raises(DevelopmentReportAdmissionError, match=match):
        DevelopmentReportAdmissionService(application_store=store).admit(_report(release, details=details, evidence=evidence))


def test_classifier_is_advisory_and_cannot_return_authority_fields(tmp_path: Path) -> None:
    store, release = _store(tmp_path)

    class PoisonedClassifier:
        def classify(self, **_kwargs):
            return {"category": "security", "confidence": 1, "priority": "critical", "create_ticket": True}

    service = DevelopmentReportAdmissionService(application_store=store, classifier=PoisonedClassifier())
    with pytest.raises(DevelopmentReportAdmissionError, match="authority-bearing"):
        service.admit(_report(release))
