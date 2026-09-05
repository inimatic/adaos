from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.domain.application import Application, ApplicationRelease
from adaos.domain.artifact_release import ArtifactPackageRef, ArtifactSourceRef, ProjectRelease
from adaos.domain.development_report import DevelopmentReport
from adaos.services.applications.report_admission import DevelopmentReportAdmissionError, DevelopmentReportAdmissionService
from adaos.services.applications.report_classifier import OciDevelopmentReportClassifier
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


def test_oci_classifier_is_digest_pinned_scratch_only_and_advisory(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, release = _store(tmp_path)
    commands: list[list[str]] = []
    observed_inputs: list[dict] = []

    class CompletedProcess:
        returncode = 0

        def poll(self):
            return 0

    def popen(command, **_kwargs):
        commands.append(command)
        mounts = [item for item in command if item.startswith("type=bind,src=")]
        input_path = Path(mounts[0].split(",dst=", 1)[0].removeprefix("type=bind,src="))
        output_path = Path(mounts[1].split(",dst=", 1)[0].removeprefix("type=bind,src="))
        observed_inputs.append(json.loads(input_path.read_text(encoding="utf-8")))
        output_path.write_text(
            json.dumps(
                {
                    "category": "bug",
                    "confidence": 0.9,
                    "tags": ["import"],
                    "summary": "Import fails",
                }
            ),
            encoding="utf-8",
        )
        return CompletedProcess()

    monkeypatch.setattr(
        "adaos.services.applications.report_classifier.shutil.which",
        lambda _runtime: "docker",
    )
    monkeypatch.setattr(
        "adaos.services.applications.report_classifier.subprocess.Popen",
        popen,
    )
    image = "registry.example/adaos/report-classifier@sha256:" + "d" * 64
    classifier = OciDevelopmentReportClassifier(
        state_root=tmp_path / "classifier-state",
        image=image,
    )

    admitted = DevelopmentReportAdmissionService(
        application_store=store,
        classifier=classifier,
    ).admit(_report(release))

    assert observed_inputs[0]["details"] == "token=[REDACTED]"
    assert all("very-secret-token-value" not in item for item in commands[0])
    for required in (
        "--pull", "never", "--network", "none", "--read-only", "--cap-drop",
        "ALL", "--security-opt", "no-new-privileges:true", "--user", "65532:65532",
    ):
        assert required in commands[0]
    classification = admitted.model_classification
    assert classification is not None
    assert classification["authority"] == "advisory_only"
    assert classification["status"] == "completed"
    assert classification["model"] == image
    assert classification["provenance"]["input_digest"] == classification["input_digest"]
    assert list((tmp_path / "classifier-state" / "report-classifier").iterdir()) == []


def test_oci_classifier_unavailability_does_not_reject_admitted_report(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store, release = _store(tmp_path)
    monkeypatch.setattr(
        "adaos.services.applications.report_classifier.shutil.which",
        lambda _runtime: None,
    )
    classifier = OciDevelopmentReportClassifier(
        state_root=tmp_path / "classifier-state",
        image="registry.example/classifier@sha256:" + "e" * 64,
    )

    admitted = DevelopmentReportAdmissionService(
        application_store=store,
        classifier=classifier,
    ).admit(_report(release))

    assert admitted.model_classification is not None
    assert admitted.model_classification["status"] == "unavailable"
    assert admitted.model_classification["reason_code"] == "oci_runtime_unavailable"
