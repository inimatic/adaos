from __future__ import annotations

import inspect
from pathlib import Path
from types import SimpleNamespace

from adaos.sdk.builder import applications
from adaos.sdk.core.exporter import export
from adaos.sdk.developer import compositions
from adaos.domain.application import Application
from adaos.services.applications import ApplicationDevelopmentCoordinator, ApplicationService, ApplicationStore


def test_builder_application_create_uses_bounded_composition_and_core(monkeypatch, tmp_path: Path) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    created = []
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(
        applications,
        "publisher_context",
        lambda: {
            "publisher_ref": "subnet:home",
            "display_name": "Home Lab",
            "subnet_short_ref": "home",
            "home_zone": "local",
            "release_key_ref": "artifact-signing:home:key",
            "release_key_fingerprint": "sha256:" + "f" * 64,
            "release_key_algorithm": "ed25519",
            "release_key_issuer": "home",
            "trust_relation": "local",
        },
    )
    monkeypatch.setattr(
        compositions,
        "get",
        lambda _project_id: (_ for _ in ()).throw(
            compositions.ProjectCompositionNotFound("missing")
        ),
    )

    def create(project_id, **kwargs):
        created.append((project_id, kwargs))
        return {"ok": True, "project": {"id": project_id}}

    monkeypatch.setattr(compositions, "create_with_primary_component", create)

    operation = applications.create_application(
        "applications", title="Applications", summary="Application manager",
        actor_ref="user:owner", subnet_ref="subnet:home",
        capability="applications.develop", expected_revision=0,
        idempotency_key="create-applications-1",
    )

    assert operation["status"] == "succeeded"
    assert service.store.get_application("applications").legacy_project_id == "applications"
    assert created[0][1]["kind"] == "scenario"
    assert created[0][1]["entrypoints"][0]["presentation"] == "scenario:applications"


def test_builder_application_sdk_has_no_raw_authority_parameters() -> None:
    forbidden = {
        "path", "filesystem_path", "command", "process", "git_credentials",
        "registry_path", "private_key", "repository_token",
    }
    for name in applications.__all__:
        function = getattr(applications, name)
        assert forbidden.isdisjoint(inspect.signature(function).parameters), name


def test_publisher_context_exposes_only_public_signing_identity(monkeypatch, tmp_path: Path) -> None:
    key = tmp_path / "publisher.ed25519"
    key.write_bytes(b"a" * 32)
    monkeypatch.setenv("ADAOS_ARTIFACT_ATTESTATIONS_MODE", "publish")
    monkeypatch.setenv("ADAOS_ARTIFACT_SIGNING_KEY_FILE", str(key))
    monkeypatch.setenv("ADAOS_ARTIFACT_SIGNING_ISSUER", "subnet-home")
    monkeypatch.setattr(
        applications,
        "_ctx",
        lambda: SimpleNamespace(config=SimpleNamespace(subnet_id="home", zone_id="local")),
    )

    context = applications.publisher_context()

    assert context["publisher_ref"] == "subnet:home"
    assert context["release_key_fingerprint"].startswith("sha256:")
    assert "private" not in " ".join(context).lower()


def test_builder_application_facade_is_discoverable() -> None:
    metadata = export(
        level="std",
        query="builder create application trial publish stable",
        limit=64,
    )
    names = {item["name"] for item in metadata["tools"]}

    assert "adaos.sdk.builder.applications.create_application" in names
    assert "adaos.sdk.builder.applications.publish_prerelease" in names
    assert "adaos.sdk.builder.applications.promote_stable" in names


def test_builder_application_reconciles_lost_create_response(
    monkeypatch, tmp_path: Path
) -> None:
    service = ApplicationService(ApplicationStore(tmp_path))
    coordinator = ApplicationDevelopmentCoordinator(tmp_path)
    publisher = {
        "publisher_ref": "subnet:home",
        "display_name": "Home Lab",
        "subnet_short_ref": "home",
        "home_zone": "local",
        "release_key_ref": "artifact-signing:home:key",
        "release_key_fingerprint": "sha256:" + "f" * 64,
        "release_key_algorithm": "ed25519",
        "release_key_issuer": "home",
        "trust_relation": "local",
    }
    service.register(
        Application(
            application_id="applications",
            legacy_project_id="applications",
            publisher_ref="subnet:home",
            slug="applications",
            display={"title": "Applications", "summary": "Application manager"},
            visibility="private",
            entrypoints=(
                {
                    "entrypoint_id": "main",
                    "presentation_ref": "scenario:applications",
                },
            ),
            publisher={
                key: publisher[key]
                for key in (
                    "publisher_ref",
                    "display_name",
                    "subnet_short_ref",
                    "release_key_ref",
                    "release_key_fingerprint",
                    "home_zone",
                    "trust_relation",
                )
            },
        )
    )
    intent = {
        "title": "Applications",
        "summary": "Application manager",
        "template": "empty",
        "visibility": "private",
        "publisher_key_fingerprint": publisher["release_key_fingerprint"],
        "publisher": publisher,
    }
    try:
        coordinator.execute(
            "create",
            "applications",
            actor_ref="user:owner",
            subnet_ref="subnet:home",
            capability="applications.develop",
            expected_revision=0,
            idempotency_key="create-applications-lost-response",
            intent=intent,
            callback=lambda: (_ for _ in ()).throw(TimeoutError("response lost")),
        )
    except TimeoutError:
        pass
    monkeypatch.setattr(applications, "_application_service", lambda: service)
    monkeypatch.setattr(applications, "_coordinator", lambda: coordinator)
    monkeypatch.setattr(applications, "publisher_context", lambda: publisher)

    operation = applications.reconcile_development_operation(
        coordinator.list()[0]["operation_id"],
        actor_ref="user:owner",
        subnet_ref="subnet:home",
        capability="applications.recover",
    )

    assert operation["status"] == "succeeded"
    assert operation["result"]["duplicate"] is True
