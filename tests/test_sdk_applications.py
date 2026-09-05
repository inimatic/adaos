from __future__ import annotations

import inspect

import pytest

from adaos.sdk import applications
from adaos.sdk.core.exporter import export
from adaos.services.applications import register_development_report_service


class _StubService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def plan_operation(self, *args, **kwargs):
        self.calls.append(("plan_operation", args, kwargs))
        return _Record({"operation_id": "appop.1", "plan_digest": "sha256:" + "a" * 64})

    def apply_operation(self, *args, **kwargs):
        self.calls.append(("apply_operation", args, kwargs))
        return _Record({"operation_id": args[0], "status": "succeeded"})


class _Record:
    def __init__(self, payload):
        self.payload = payload

    def to_dict(self):
        return dict(self.payload)


def test_sdk_application_mutations_forward_complete_review_context(monkeypatch) -> None:
    stub = _StubService()
    monkeypatch.setattr(applications, "_service", lambda: stub)

    plan = applications.plan_install(
        "app_recipes",
        release_digest="sha256:" + "b" * 64,
        expected_revision=3,
        actor_ref="skill:applications",
        subnet_ref="subnet:sn_home",
        capability="applications.plan",
        idempotency_key="install-4",
    )
    result = applications.apply_operation(
        "appop.1",
        plan_digest=plan["plan_digest"],
        actor_ref="skill:applications",
        subnet_ref="subnet:sn_home",
        capability="applications.apply",
        idempotency_key="install-4",
    )

    assert result["status"] == "succeeded"
    assert stub.calls[0] == (
        "plan_operation",
        ("app_recipes", "install"),
        {
            "release_digest": "sha256:" + "b" * 64,
            "expected_revision": 3,
            "actor_ref": "skill:applications",
            "subnet_ref": "subnet:sn_home",
            "capability": "applications.plan",
            "idempotency_key": "install-4",
            "data_policy": "retain",
            "access_redemption_id": None,
        },
    )
    assert stub.calls[1] == (
        "apply_operation",
        ("appop.1",),
        {
            "plan_digest": "sha256:" + "a" * 64,
            "idempotency_key": "install-4",
            "actor_ref": "skill:applications",
            "subnet_ref": "subnet:sn_home",
            "capability": "applications.apply",
        },
    )


def test_sdk_application_surface_has_no_raw_path_or_process_parameters() -> None:
    forbidden = {"path", "filesystem_path", "command", "process", "git_credentials", "registry_path"}
    for name in applications.__all__:
        function = getattr(applications, name)
        assert forbidden.isdisjoint(inspect.signature(function).parameters), name


def test_application_sdk_is_discoverable_for_builder_context() -> None:
    metadata = export(level="std", query="application install prerelease", limit=40)
    names = {item["name"] for item in metadata["tools"]}

    assert "adaos.sdk.applications.plan_install" in names
    assert "adaos.sdk.applications.plan_update_track" in names
    assert "adaos.sdk.applications.resolve_trial_link" in names


def test_sdk_exposes_development_report_status_without_internal_store_access() -> None:
    class Reports:
        def list_reports(self):
            return [{"report_id": "report.1"}]

        def get_report(self, report_id):
            return {"report_id": report_id}

        def public_status(self, report_id):
            return {"report_id": report_id, "status": "accepted"}

        def list_publisher_intakes(self):
            return [{"report_id": "report.1", "status": "quarantined"}]

        def list_local_appeals(self, report_id=None):
            return [{"appeal_id": "appeal.1", "report_id": report_id}]

    register_development_report_service(Reports())
    try:
        assert applications.list_development_reports()[0]["report_id"] == "report.1"
        assert applications.get_development_report_status("report.1")["status"] == "accepted"
        assert applications.list_development_report_intakes()[0]["status"] == "quarantined"
        assert applications.list_development_report_appeals("report.1")[0]["appeal_id"] == "appeal.1"
    finally:
        register_development_report_service(None)


def test_sdk_report_mutations_require_local_narrow_capability(monkeypatch) -> None:
    class Reports:
        def __init__(self):
            self.calls = []

        def create_report(self, **kwargs):
            self.calls.append(("create_report", kwargs))
            return {"report": {"report_id": "report.1"}}

        def triage(self, report_id, **kwargs):
            self.calls.append(("triage", report_id, kwargs))
            return {"event": {"status": kwargs["outcome"]}}

    reports = Reports()
    monkeypatch.setattr(applications, "_local_subnet_ref", lambda: "subnet:sn_home")
    register_development_report_service(reports)
    try:
        submitted = applications.submit_development_report(
            "app_recipes", summary="Failure", details="Expected A, observed B",
            actor_ref="user:owner", subnet_ref="subnet:sn_home",
            capability="applications.report", idempotency_key="report-1",
        )
        assert submitted["report"]["report_id"] == "report.1"
        triaged = applications.triage_development_report(
            "report.1", outcome="declined", reason_code="not_reproduced",
            actor_ref="user:owner", subnet_ref="subnet:sn_home",
            capability="applications.publisher.triage", idempotency_key="triage-1",
        )
        assert triaged["event"]["status"] == "declined"
        with pytest.raises(ValueError, match="applications.report"):
            applications.submit_development_report(
                "app_recipes", summary="Failure", details="Details",
                actor_ref="user:owner", subnet_ref="subnet:sn_home",
                capability="applications.apply", idempotency_key="report-2",
            )
        with pytest.raises(ValueError, match="local identity"):
            applications.submit_development_report(
                "app_recipes", summary="Failure", details="Details",
                actor_ref="user:owner", subnet_ref="subnet:foreign",
                capability="applications.report", idempotency_key="report-3",
            )
    finally:
        register_development_report_service(None)


def test_sdk_release_reads_preserve_identity_and_redact_private_source(monkeypatch) -> None:
    digest = "sha256:" + "a" * 64
    package_digest = "sha256:" + "b" * 64
    raw_release = {
        "schema": "adaos.application.release.v1",
        "application_id": "app_private",
        "publisher_ref": "subnet:publisher",
        "legacy_project_id": "private",
        "version": "1.0.0",
        "release_digest": digest,
        "accepted_candidate_id": "candidate.private.1",
        "acceptance_evidence": [{"token": "secret", "path": "D:/private/source"}],
        "provenance_refs": [digest],
        "addresses_report_ids": [],
        "lifecycle": "stable",
        "project_release": {
            "schema": "adaos.artifact.project_release.v1",
            "project_id": "private",
            "version": "1.0.0",
            "source_ref": {
                "repository": "private/repository", "path_scope": ["secret/"],
            },
            "components": [{
                "kind": "scenario", "artifact_id": "private", "version": "1.0.0",
                "digest": package_digest, "manifest_digest": digest,
                "source_ref": {"repository": "private/repository"},
                "materialization_path": "scenarios/private",
            }],
            "resolved_dependencies": [],
            "permissions": ["network.read"],
            "migrations": [{"command": "private-migration"}],
            "validation_evidence": [{"log_path": "D:/private/log"}],
            "schema_locks": [],
            "migration_locks": [],
            "validation_evidence_refs": [digest],
            "release_digest": digest,
        },
    }

    class Releases:
        def list_releases(self, application_id):
            assert application_id == "app_private"
            return [raw_release]

    monkeypatch.setattr(applications, "_service", lambda: Releases())
    release = applications.list_releases("app_private")[0]
    serialized = str(release)

    assert release["release_digest"] == digest
    assert release["publisher_ref"] == "subnet:publisher"
    assert release["project_release"]["components"][0]["digest"] == package_digest
    assert release["project_release"]["private_source"] == "redacted"
    assert release["project_release"]["migration"] == {"required": True, "count": 1}
    for private_value in (
        "private/repository", "D:/private/source", "D:/private/log",
        "private-migration", "materialization_path", "secret",
    ):
        assert private_value not in serialized
