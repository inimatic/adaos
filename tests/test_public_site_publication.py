from __future__ import annotations

import json
from pathlib import Path
import shutil

import pytest

from adaos.services import public_site_publication as publication_module
from adaos.services.public_site_publication import (
    CloudflarePublicationDelivery,
    PublicSitePublicationService,
)
from adaos.services.public_site_components import PublicSiteComponentRegistry
from adaos.services.site_studio.contracts import digest
from adaos.services.root_mcp import public_sites_plane


SOURCE_DIGEST = "sha256:" + "1" * 64
DEPLOYMENT_DIGEST = "sha256:" + "2" * 64
POINTER_DIGEST = "sha256:" + "3" * 64
PLAN_DIGEST = "sha256:" + "4" * 64
PLATFORM_PATH = "st_0123456789ab"
PUBLIC_URL = f"https://p.adaos.io/{PLATFORM_PATH}/"


def test_default_delivery_never_uses_node_operator_credentials(tmp_path, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("a node must not construct a provider adapter or execute provider commands")

    monkeypatch.setattr(publication_module, "CloudflarePublicationDelivery", forbidden)
    monkeypatch.setattr(publication_module.subprocess, "run", forbidden)
    service = PublicSitePublicationService(state_dir=tmp_path / "state", adaos_root=tmp_path)
    for operation in ("prepare", "publish", "request_domain", "status_domain", "activate_domain", "activate_managed_domain"):
        with pytest.raises(RuntimeError, match="Root-hosted public Site delivery is not configured"):
            getattr(service.delivery, operation)()

    active = {"site_id": "adaos", "revision_id": "draft-1", "source_digest": SOURCE_DIGEST}
    candidate = {**active, "status": "prepared", "platform_path": PLATFORM_PATH}
    status = service._status_view(active, {}, candidate, PLATFORM_PATH)
    assert status["can_prepare"] is False
    assert status["can_publish"] is False
    assert status["delivery"]["status"] == "root_publication_delivery_unavailable"


class FakeDelivery:
    def __init__(self) -> None:
        self.prepared = 0
        self.published = 0

    def prepare(
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        environment: str = "prod",
    ) -> dict:
        self.prepared += 1
        assert platform_path.startswith("st_")
        origin = "https://p.adaos.io" if environment == "prod" else "https://dev.p.adaos.io"
        workspace.mkdir(parents=True)
        (workspace / "publication-plan.json").write_text(
            json.dumps(
                {
                    "schema": "inimatic.public_site_publication_plan.v1",
                    "environment": environment,
                    "host": "p.adaos.io" if environment == "prod" else "dev.p.adaos.io",
                    "platform_path": platform_path,
                    "public_url": f"{origin}/{platform_path}/",
                    "pointer": {
                        "deployment_digest": DEPLOYMENT_DIGEST,
                        "pointer_digest": POINTER_DIGEST,
                        "generation": 6,
                        "published_at": "2026-11-04T12:00:00Z",
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "ok": True,
            "plan_sha256": PLAN_DIGEST,
            "deployment_digest": DEPLOYMENT_DIGEST,
            "pointer_digest": POINTER_DIGEST,
        }

    def publish(
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        plan_digest: str,
        environment: str = "prod",
    ) -> dict:
        assert plan_digest == PLAN_DIGEST
        self.published += 1
        return {
            "ok": True,
            "deployment_digest": DEPLOYMENT_DIGEST,
            "pointer_digest": POINTER_DIGEST,
            "url": (
                f"https://p.adaos.io/{platform_path}/"
                if environment == "prod"
                else f"https://dev.p.adaos.io/{platform_path}/"
            ),
            "receipt": str(workspace / "receipt.json"),
        }

    def request_domain(self, hostname: str) -> dict:
        return {
            "status": "pending",
            "ssl_status": "pending_validation",
            "ownership_verification": {
                "name": "_cf-custom-hostname." + hostname,
                "type": "txt",
                "value": "ownership-proof",
            },
            "certificate_validation": [
                {"txt_name": "_acme-challenge." + hostname, "txt_value": "cert-proof"}
            ],
        }

    def activate_domain(self, hostname: str, site_id: str) -> dict:
        assert site_id == "adaos"
        return {"status": "confirmed", "hostname": hostname}


class FakeComponentRegistry:
    def admit_bundle(self, site_dir: Path, *, actor: str, operation_id: str) -> dict:
        return {
            "set_digest": "sha256:" + "6" * 64,
            "channel": "dev2027",
            "deployment": {
                "deployment_digest": DEPLOYMENT_DIGEST,
                "projection_digest": "sha256:" + "7" * 64,
                "renderer_abi": "adaos.public-renderer.v1",
            },
        }

    def record_publication(self, **request) -> dict:
        return {"site_id": request["site_id"]}

    def site_status(self, site_id: str, **kwargs) -> dict:
        return {
            "site_id": site_id,
            "environment": kwargs.get("environment", "prod"),
            "current_set": None,
        }

    def release_set_status(self, set_digest: str) -> dict:
        return {"set_digest": set_digest}


class IdentityDelivery:
    def __init__(self) -> None:
        self.generation = 0

    def prepare(
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        environment: str = "prod",
    ) -> dict:
        self.generation += 1
        deployment = json.loads(
            (site_dir / "site.deployment.json").read_text(encoding="utf-8")
        )
        origin = "https://p.adaos.io" if environment == "prod" else "https://dev.p.adaos.io"
        pointer_body = {
            "deployment_digest": deployment["deployment_digest"],
            "generation": self.generation,
            "published_at": f"2026-11-06T12:{self.generation:02d}:00+00:00",
        }
        pointer = {**pointer_body, "pointer_digest": digest(pointer_body)}
        plan = {
            "schema": "inimatic.public_site_publication_plan.v1",
            "environment": environment,
            "host": "p.adaos.io" if environment == "prod" else "dev.p.adaos.io",
            "platform_path": platform_path,
            "public_url": f"{origin}/{platform_path}/",
            "pointer": pointer,
        }
        workspace.mkdir(parents=True)
        (workspace / "publication-plan.json").write_text(
            json.dumps(plan), encoding="utf-8"
        )
        return {
            "ok": True,
            "plan_sha256": digest(plan),
            "deployment_digest": deployment["deployment_digest"],
            "pointer_digest": pointer["pointer_digest"],
        }

    def publish(
        self,
        *,
        site_dir: Path,
        workspace: Path,
        platform_path: str,
        plan_digest: str,
        environment: str = "prod",
    ) -> dict:
        plan = json.loads(
            (workspace / "publication-plan.json").read_text(encoding="utf-8")
        )
        assert digest(plan) == plan_digest
        return {
            "ok": True,
            "deployment_digest": plan["pointer"]["deployment_digest"],
            "pointer_digest": plan["pointer"]["pointer_digest"],
            "url": (
                f"https://p.adaos.io/{platform_path}/"
                if environment == "prod"
                else f"https://dev.p.adaos.io/{platform_path}/"
            ),
        }


def _active() -> dict:
    return {
        "project_id": "adaos",
        "site_id": "adaos",
        "revision_id": "draft-000117",
        "source_digest": SOURCE_DIGEST,
        "artifact_digest": "sha256:" + "5" * 64,
        "preview_deployment_digest": DEPLOYMENT_DIGEST,
    }


@pytest.mark.parametrize("same_release,admitted", [(True, True), (False, True), (True, False)])
def test_authoring_uses_the_same_selected_stable_after_finalize(tmp_path, monkeypatch, same_release, admitted):
    from types import SimpleNamespace
    from adaos.services.applications import lifecycle

    workspace = tmp_path / "workspace"
    skill_root = workspace / "skills/.runtime/studio_skill/v0.1/data"
    (skill_root / "files").mkdir(parents=True)
    (skill_root / "authoring.sqlite3").write_bytes(b"retained authoring database")
    # Retained data from another version is not the selected data bucket.
    old = workspace / "skills/.runtime/studio_skill/v0.0/data"
    old.mkdir(parents=True)
    (old / "authoring.sqlite3").write_bytes(b"old retained data")
    (skill_root.parent.parent / "current_runtime.json").write_text(
        json.dumps({"runtime_bucket": "v0.1", "version": "0.1.2"}), encoding="utf-8")
    paths = SimpleNamespace(state_dir=lambda: tmp_path / "state", workspace_dir=lambda: workspace)
    monkeypatch.setattr(publication_module, "get_ctx", lambda: SimpleNamespace(paths=paths))
    selection = SimpleNamespace(application_id="studio", release_digest=SOURCE_DIGEST, runtime_root_ref="workspace")
    def selected(ctx, webspace, kind, component, **kwargs):
        assert webspace == "desktop"
        if kind == "skill" and not same_release:
            return SimpleNamespace(application_id="foreign", release_digest=DEPLOYMENT_DIGEST)
        return selection
    monkeypatch.setattr(publication_module, "selected_application", selected)
    monkeypatch.setattr(lifecycle, "observe_runtime_admission", lambda *a, **k: {"admission": "admitted" if admitted else "recovery_required"})
    monkeypatch.setattr(publication_module, "read_active_revision", lambda database: _active())
    service = PublicSitePublicationService(state_dir=tmp_path / "state", adaos_root=tmp_path,
                                         trial_resolver=lambda *a: None)
    args = dict(scenario_id="studio", skill_id="studio_skill", webspace_id="desktop", project_id="adaos")
    if not same_release or not admitted:
        with pytest.raises(ValueError, match="runtime is unavailable"):
            service._active_authoring(**args)
    else:
        active, database, files = service._active_authoring(**args)
        assert active == _active()
        assert database == skill_root / "authoring.sqlite3"
        assert files == skill_root / "files"


def test_authoring_resolves_the_selected_project_database(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from adaos.services.applications import lifecycle

    project_id = "project-" + "a" * 32
    workspace = tmp_path / "workspace"
    data_root = workspace / "skills/.runtime/studio_skill/v0.2/data"
    (data_root / "files").mkdir(parents=True)
    (data_root / (project_id + ".sqlite3")).write_bytes(b"retained project database")
    (data_root.parent.parent / "current_runtime.json").write_text(
        json.dumps({"runtime_bucket": "v0.2", "version": "0.2.0"}),
        encoding="utf-8",
    )
    paths = SimpleNamespace(
        state_dir=lambda: tmp_path / "state", workspace_dir=lambda: workspace
    )
    monkeypatch.setattr(publication_module, "get_ctx", lambda: SimpleNamespace(paths=paths))
    selection = SimpleNamespace(
        application_id="studio",
        release_digest=SOURCE_DIGEST,
        runtime_root_ref="workspace",
    )
    monkeypatch.setattr(
        publication_module,
        "selected_application",
        lambda *args, **kwargs: selection,
    )
    monkeypatch.setattr(
        lifecycle,
        "observe_runtime_admission",
        lambda *args, **kwargs: {"admission": "admitted"},
    )
    active = {**_active(), "project_id": project_id, "site_id": project_id}
    monkeypatch.setattr(publication_module, "read_active_revision", lambda database: active)
    service = PublicSitePublicationService(
        state_dir=tmp_path / "state",
        adaos_root=tmp_path,
        trial_resolver=lambda *args: None,
    )

    observed, database, files = service._active_authoring(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id=project_id,
    )

    assert observed == active
    assert database == data_root / (project_id + ".sqlite3")
    assert files == data_root / "files"


def test_firebase_domain_request_projects_owner_dns_records(tmp_path, monkeypatch):
    from types import SimpleNamespace

    delivery = CloudflarePublicationDelivery(tmp_path, state_dir=tmp_path / "state")
    delivery.firebase_domain_tool = tmp_path / "domain.mjs"
    delivery.firebase_domain_tool.write_text("// test", encoding="utf-8")
    tools_root = tmp_path / "firebase-tools"
    (tools_root / "lib/bin").mkdir(parents=True)
    (tools_root / "lib/apiv2.js").write_text("// test", encoding="utf-8")
    delivery.firebase_cli = tools_root / "lib/bin/firebase.js"
    delivery.firebase_cli.write_text("// test", encoding="utf-8")
    payload = {
        "exists": True,
        "hostState": "HOST_PENDING",
        "ownershipState": "OWNERSHIP_PENDING",
        "requiredDnsUpdates": {
            "desired": [
                {
                    "records": [
                        {
                            "domainName": "beta.example.test",
                            "type": "TXT",
                            "rdata": "hosting-site=adaos-public-bootstrap",
                        },
                        {
                            "domainName": "beta.example.test",
                            "type": "CNAME",
                            "rdata": "adaos-public-bootstrap.web.app",
                        },
                    ]
                }
            ]
        },
        "cert": {
            "state": "CERT_PENDING",
            "verification": {
                "dns": {
                    "desired": [
                        {
                            "records": [
                                {
                                    "domainName": "_acme-challenge.beta.example.test",
                                    "type": "TXT",
                                    "rdata": "certificate-proof",
                                }
                            ]
                        }
                    ]
                }
            },
        },
    }
    monkeypatch.setattr(
        publication_module.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr=""
        ),
    )

    result = delivery.request_domain("beta.example.test")

    assert result["provider"] == "firebase"
    assert result["status"] == "pending"
    assert result["ownership_verification"]["value"] == "hosting-site=adaos-public-bootstrap"
    assert result["routing_records"][1]["type"] == "CNAME"
    assert result["certificate_validation"][0]["txt_value"] == "certificate-proof"


def test_firebase_domain_activation_deploys_the_selected_site_binding(tmp_path, monkeypatch):
    state = tmp_path / "state"
    delivery = CloudflarePublicationDelivery(tmp_path, state_dir=state)
    operation_id = "pspub_" + "a" * 32
    site_id = "st_0123456789ab"
    registry = state / "public-sites/registry.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(
        json.dumps(
            {
                "sites": {
                    site_id: {
                        "operation_id": operation_id,
                        "platform_path": site_id,
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    provider_workspace = tmp_path / "provider"
    provider_plan = provider_workspace / "cloudflare/publication-plan.json"
    provider_plan.parent.mkdir(parents=True)
    provider_plan.write_text("{}", encoding="utf-8")
    operation = (
        state
        / "private_recovery/public-sites/operations"
        / operation_id
        / "operation.json"
    )
    operation.parent.mkdir(parents=True)
    operation.write_text(
        json.dumps({"provider_workspace": str(provider_workspace)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        delivery,
        "_firebase_domain",
        lambda command, hostname: {"status": "active", "ssl_status": "active"},
    )
    calls = []

    def run_firebase(command, **kwargs):
        calls.append((command, kwargs))
        if command == "routing-plan":
            return {
                "plan_sha256": PLAN_DIGEST,
                "descriptor_digest": DEPLOYMENT_DIGEST,
            }
        return {"descriptor_digest": DEPLOYMENT_DIGEST}

    monkeypatch.setattr(delivery, "_run_firebase", run_firebase)

    result = delivery.activate_domain("beta.example.test", site_id)

    assert result == {
        "status": "active",
        "provider": "firebase",
        "hostname": "beta.example.test",
    }
    assert [call[0] for call in calls] == ["routing-plan", "routing-apply"]
    assert calls[0][1]["domain_bindings"] == (f"beta.example.test={site_id}",)


def test_domain_listing_refreshes_firebase_status_without_reassigning_the_domain(
    tmp_path, monkeypatch
):
    class DomainDelivery(FakeDelivery):
        def status_domain(self, hostname):
            assert hostname == "beta.example.test"
            return {
                "status": "active",
                "ssl_status": "active",
                "provider": "firebase",
                "ownership_verification": {},
                "certificate_validation": [],
                "routing_records": [],
            }

    service = PublicSitePublicationService(
        state_dir=tmp_path / "state",
        adaos_root=tmp_path,
        delivery=DomainDelivery(),
        trial_resolver=lambda *args: None,
    )
    monkeypatch.setattr(
        service,
        "_active_authoring",
        lambda **kwargs: (_active(), tmp_path / "authoring.sqlite3", tmp_path / "files"),
    )
    service.public_root.mkdir(parents=True)
    service.registry_path.write_text(
        json.dumps(
            {
                "schema": "adaos.public_site_publication_registry.v1",
                "sites": {},
                "domains": {
                    "beta.example.test": {
                        "schema": "adaos.public_site_domain.v1",
                        "site_id": "adaos",
                        "hostname": "beta.example.test",
                        "status": "pending",
                        "ssl_status": "pending_validation",
                        "requested_at": "2026-11-06T00:00:00+00:00",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    result = service.domains(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id="adaos",
    )

    assert result["items"][0]["status"] == "active"
    assert result["items"][0]["provider"] == "firebase"
    retained = json.loads(service.registry_path.read_text(encoding="utf-8"))
    assert retained["domains"]["beta.example.test"]["status"] == "pending"


def test_prepare_and_publish_keep_provider_paths_private(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = FakeDelivery()
    service = PublicSitePublicationService(
        state_dir=tmp_path / "state",
        adaos_root=tmp_path,
        delivery=delivery,
        trial_resolver=lambda *args: None,
        component_registry=FakeComponentRegistry(),
    )
    monkeypatch.setattr(
        service,
        "_active_authoring",
        lambda **kwargs: (_active(), tmp_path / "authoring.sqlite3", tmp_path / "files"),
    )

    def fake_export(database, files, output, *, canonical_base_url):
        assert canonical_base_url.startswith("https://p.adaos.io/st_")
        output.mkdir(parents=True)
        return {
            "revision_id": "draft-000117",
            "source_digest": SOURCE_DIGEST,
            "deployment_digest": DEPLOYMENT_DIGEST,
        }

    monkeypatch.setattr(publication_module, "export_publication", fake_export)

    candidate = service.prepare(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id="adaos",
        expected_revision_id="draft-000117",
        expected_source_digest=SOURCE_DIGEST,
    )

    assert candidate["status"] == "candidate_ready"
    assert candidate["can_publish"] is True
    operation = candidate["candidate"]
    assert operation["plan_digest"] == PLAN_DIGEST
    assert "workspace" not in json.dumps(candidate)
    assert "site_dir" not in json.dumps(candidate)

    published = service.publish(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id="adaos",
        operation_id=operation["operation_id"],
        plan_digest=PLAN_DIGEST,
        expected_revision_id="draft-000117",
        expected_source_digest=SOURCE_DIGEST,
        confirmed=True,
    )

    assert published["status"] == "published"
    assert published["changes_pending"] is False
    assert published["published"]["revision_id"] == "draft-000117"
    assert published["public_url"].startswith("https://p.adaos.io/st_")
    assert delivery.prepared == delivery.published == 1


def test_development_release_keeps_production_pointer_untouched(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = FakeDelivery()
    service = PublicSitePublicationService(
        state_dir=tmp_path / "state",
        adaos_root=tmp_path,
        delivery=delivery,
        trial_resolver=lambda *args: None,
        component_registry=FakeComponentRegistry(),
    )
    monkeypatch.setattr(
        service,
        "_active_authoring",
        lambda **kwargs: (_active(), tmp_path / "authoring.sqlite3", tmp_path / "files"),
    )

    def fake_export(database, files, output, *, canonical_base_url):
        assert canonical_base_url.startswith("https://dev.p.adaos.io/st_")
        output.mkdir(parents=True)
        return {
            "revision_id": _active()["revision_id"],
            "source_digest": SOURCE_DIGEST,
            "deployment_digest": DEPLOYMENT_DIGEST,
        }

    monkeypatch.setattr(publication_module, "export_publication", fake_export)
    candidate = service.prepare(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id="adaos",
        expected_revision_id=_active()["revision_id"],
        expected_source_digest=SOURCE_DIGEST,
        release_target="development",
    )
    published = service.publish(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id="adaos",
        operation_id=candidate["candidate"]["operation_id"],
        plan_digest=PLAN_DIGEST,
        expected_revision_id=_active()["revision_id"],
        expected_source_digest=SOURCE_DIGEST,
        confirmed=True,
    )

    assert published["environment"] == "dev"
    assert published["public_url"].startswith("https://dev.p.adaos.io/")
    retained = json.loads(service.registry_path.read_text(encoding="utf-8"))
    assert retained["sites"] == {}
    assert retained["channels"]["dev"]["adaos"]["deployment_digest"] == DEPLOYMENT_DIGEST
    observed = service.status(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id="adaos",
    )
    assert observed["release_channels"]["production"]["status"] == "unpublished"
    assert observed["release_channels"]["development"]["status"] == "published"


def test_publication_status_projects_identity_free_delivery_telemetry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from adaos.services.public_site_delivery_telemetry import (
        PublicSiteDeliveryTelemetryService,
    )

    telemetry = PublicSiteDeliveryTelemetryService(state_dir=tmp_path / "telemetry")
    telemetry.record(
        {
            "schema": "adaos.public_site_delivery_observation.v1",
            "observed_at": "2026-11-06T12:00:00+00:00",
            "kind": "publication_probe",
            "status": "healthy",
            "bootstrap": "firebase",
            "descriptor_generation": 12,
            "selected_zone": "cloudflare-global",
            "fallback_zone": None,
            "provider": "cloudflare",
            "cache_state": "hit",
            "site_id": "adaos",
            "deployment_digest": DEPLOYMENT_DIGEST,
            "request_count": 1,
            "bytes_delivered": 2048,
            "latency_ms": 84,
        },
        source="regional_probe",
        observation_id="regional-probe-one",
    )
    service = PublicSitePublicationService(
        state_dir=tmp_path / "state",
        adaos_root=tmp_path,
        delivery=FakeDelivery(),
        trial_resolver=lambda *args: None,
        component_registry=FakeComponentRegistry(),
        delivery_telemetry=telemetry,
    )
    monkeypatch.setattr(
        service,
        "_active_authoring",
        lambda **kwargs: (_active(), tmp_path / "authoring.sqlite3", tmp_path / "files"),
    )
    service.public_root.mkdir(parents=True)
    service.registry_path.write_text(
        json.dumps(
            {
                "schema": "adaos.public_site_publication_registry.v1",
                "sites": {
                    "adaos": {
                        "site_id": "adaos",
                        "project_id": "adaos",
                        "environment": "prod",
                        "platform_path": "st_0123456789ab",
                        "revision_id": _active()["revision_id"],
                        "source_digest": SOURCE_DIGEST,
                        "deployment_digest": DEPLOYMENT_DIGEST,
                    }
                },
                "channels": {},
                "addresses": {
                    "adaos": {"platform_path": "st_0123456789ab"}
                },
            }
        ),
        encoding="utf-8",
    )

    observed = service.status(
        scenario_id="studio",
        skill_id="studio_skill",
        webspace_id="desktop",
        project_id="adaos",
    )

    delivery = observed["delivery_telemetry"]
    assert delivery["totals"]["observations"] == 1
    assert delivery["privacy"]["visitor_identity"] == "not_collected"
    assert "regional_probe" not in json.dumps(delivery)


def test_component_upgrade_and_rollback_move_complete_deployments(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fixture = (
        Path(__file__).resolve().parents[1]
        / "src/adaos/integrations/adaos-client/src/assets/public-sites/adaos"
    )
    state = tmp_path / "state"
    components = PublicSiteComponentRegistry(state_dir=state / "root_mcp")
    current_site = tmp_path / "current-site"
    shutil.copytree(fixture, current_site)
    current = components.admit_bundle(
        current_site, actor="root:release", operation_id="pspub_current"
    )
    current_deployment = current["deployment"]
    components.record_publication(
        site_id="adaos",
        project_id="adaos",
        deployment_digest=current_deployment["deployment_digest"],
        component_set_digest=current["set_digest"],
        projection_digest=current_deployment["projection_digest"],
        renderer_abi=current_deployment["renderer_abi"],
        actor="skill:studio_skill",
        operation_id="pspub_current",
        published_at="2026-11-06T12:00:00+00:00",
    )
    candidate_site = tmp_path / "candidate-site"
    shutil.copytree(fixture, candidate_site)
    release_path = candidate_site / "components/release-set.json"
    release = json.loads(release_path.read_text(encoding="utf-8"))
    release.pop("set_digest")
    release["set_id"] = "site-set-adaos-static-r2"
    release["qualified_at"] = "2026-11-06T12:30:00+00:00"
    release["set_digest"] = digest(release)
    release_path.write_text(json.dumps(release), encoding="utf-8")
    candidate_deployment_path = candidate_site / "site.deployment.json"
    candidate_deployment = json.loads(
        candidate_deployment_path.read_text(encoding="utf-8")
    )
    candidate_deployment.pop("deployment_digest")
    candidate_deployment["component_release_set"] = {
        "id": release["set_id"],
        "digest": release["set_digest"],
    }
    candidate_deployment["deployment_id"] = "adaos:components:r2"
    candidate_deployment["release_id"] = "components-r2"
    candidate_deployment["deployment_digest"] = digest(candidate_deployment)
    candidate_deployment_path.write_text(
        json.dumps(candidate_deployment), encoding="utf-8"
    )
    candidate = components.admit_bundle(
        candidate_site, actor="root:release", operation_id="candidate"
    )
    components.set_support_state(
        candidate["set_digest"],
        support_state="supported",
        recommended=True,
        actor="root:release",
        reason="Qualified equivalent family set.",
    )

    delivery = IdentityDelivery()
    service = PublicSitePublicationService(
        state_dir=state,
        adaos_root=tmp_path,
        delivery=delivery,
        trial_resolver=lambda *args: None,
        component_registry=components,
    )
    monkeypatch.setattr(
        service,
        "_active_authoring",
        lambda **kwargs: (_active(), tmp_path / "authoring.sqlite3", tmp_path / "files"),
    )
    original_operation = service.private_root / "operations/pspub_current"
    original_operation.mkdir(parents=True)
    original_site = original_operation / "site"
    shutil.copytree(current_site, original_site)
    (original_operation / "operation.json").write_text(
        json.dumps(
            {
                "operation_id": "pspub_current",
                "active": _active(),
                "site_dir": str(original_site),
            }
        ),
        encoding="utf-8",
    )
    service.public_root.mkdir(parents=True)
    initial_publication = {
        "schema": "adaos.public_site_publication.v1",
        "site_id": "adaos",
        "project_id": "adaos",
        "environment": "prod",
        "platform_path": PLATFORM_PATH,
        "public_url": PUBLIC_URL,
        "revision_id": _active()["revision_id"],
        "source_digest": SOURCE_DIGEST,
        "deployment_digest": current_deployment["deployment_digest"],
        "pointer_digest": POINTER_DIGEST,
        "generation": 1,
        "published_at": "2026-11-06T12:00:00+00:00",
        "operation_id": "pspub_current",
        "component_set_digest": current["set_digest"],
        "renderer_abi": current_deployment["renderer_abi"],
        "projection_digest": current_deployment["projection_digest"],
    }
    service.registry_path.write_text(
        json.dumps(
            {
                "schema": "adaos.public_site_publication_registry.v1",
                "sites": {"adaos": initial_publication},
                "addresses": {
                    "adaos": {
                        "site_id": "adaos",
                        "platform_path": PLATFORM_PATH,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    request = {
        "scenario_id": "studio",
        "skill_id": "studio_skill",
        "webspace_id": "desktop",
        "project_id": "adaos",
    }

    prepared = service.prepare_component_upgrade(**request)
    review = prepared["review"]
    assert prepared["owner_only"] is True
    assert prepared["cache_policy"] == "private,no-store"
    assert "?release_preview=" not in prepared["preview_url"]
    assert "#release-preview=" in prepared["preview_url"]
    grant = prepared["preview_grant"]
    owner_preview = service.resolve_component_upgrade_preview(
        **request,
        review_id=review["review_id"],
        grant_id=grant["grant_id"],
        token=grant["token"],
    )
    assert owner_preview["cache_policy"] == "private,no-store"
    assert owner_preview["preview"]["projection"]["projection_digest"] == (
        current_deployment["projection_digest"]
    )
    decided = service.decide_component_upgrade(
        **request,
        review_id=review["review_id"],
        plan_digest=review["plan_digest"],
        decision="approve",
        confirmed=True,
    )
    operation = decided["publication"]
    upgraded = service.publish_component_upgrade(
        **request,
        operation_id=operation["operation_id"],
        plan_digest=operation["plan_digest"],
        confirmed=True,
    )
    assert upgraded["published"]["component_set_digest"] == candidate["set_digest"]
    assert upgraded["published"]["projection_digest"] == current_deployment["projection_digest"]
    assert components.upgrade_review(review["review_id"])["status"] == "published"

    rollback = service.prepare_rollback(**request)
    restored = service.publish_rollback(
        **request,
        operation_id=rollback["operation_id"],
        plan_digest=rollback["plan_digest"],
        confirmed=True,
    )
    assert restored["published"]["deployment_digest"] == current_deployment["deployment_digest"]
    assert restored["published"]["component_set_digest"] == current["set_digest"]


def test_prepare_rejects_stale_revision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    service = PublicSitePublicationService(
        state_dir=tmp_path / "state",
        adaos_root=tmp_path,
        delivery=FakeDelivery(),
        trial_resolver=lambda *args: None,
    )
    monkeypatch.setattr(
        service,
        "_active_authoring",
        lambda **kwargs: (_active(), tmp_path / "db", tmp_path / "files"),
    )

    with pytest.raises(ValueError, match="revision changed"):
        service.prepare(
            scenario_id="studio",
            skill_id="studio_skill",
            webspace_id="desktop",
            project_id="adaos",
            expected_revision_id="draft-000116",
            expected_source_digest=SOURCE_DIGEST,
        )


def test_domain_lifecycle_is_root_owned_and_rejects_platform_hosts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = FakeDelivery()
    service = PublicSitePublicationService(
        state_dir=tmp_path / "state",
        adaos_root=tmp_path,
        delivery=delivery,
        trial_resolver=lambda *args: None,
    )
    monkeypatch.setattr(
        service,
        "_active_authoring",
        lambda **kwargs: (_active(), tmp_path / "db", tmp_path / "files"),
    )
    request = {
        "scenario_id": "studio",
        "skill_id": "studio_skill",
        "webspace_id": "desktop",
        "project_id": "adaos",
    }

    pending = service.request_domain(**request, hostname="Site.Customer.Example.")

    assert pending["hostname"] == "site.customer.example"
    assert pending["status"] == "pending"
    assert pending["project_id"] == "adaos"
    assert pending["release"]["source_digest"] == SOURCE_DIGEST
    assert pending["health"] == {
        "status": "pending",
        "reason_code": "dns_verification_pending",
        "administrative_action": "update_dns_records",
        "checked_at": pending["health"]["checked_at"],
    }
    assert pending["delivery_zone_policy"]["fallback"] == ["regru"]
    assert pending["ownership_verification"]["value"] == "ownership-proof"
    assert service.domains(**request)["items"] == [pending]
    with pytest.raises(ValueError, match="reserved"):
        service.request_domain(**request, hostname="demo.adaos.io")

    delivery.request_domain = lambda hostname: {
        "status": "active",
        "ssl_status": "active",
    }
    active = service.activate_domain(
        **request, hostname="site.customer.example", confirmed=True
    )

    assert active["status"] == "active"
    assert active["public_url"] == "https://site.customer.example/"
    assert active["primary_canonical"] is True
    assert active["health"]["status"] == "healthy"
    assert active["certificate"]["renewal_status"] == "managed"
    selected = service.set_primary_domain(
        **request, hostname="site.customer.example", confirmed=True
    )
    assert selected["primary_canonical"] is True
    with pytest.raises(ValueError, match="active verified"):
        service.set_primary_domain(
            **request, hostname="pending.customer.example", confirmed=True
        )


def test_managed_domain_reconcile_requires_private_apply_receipt(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    service = PublicSitePublicationService(
        state_dir=state_dir,
        adaos_root=tmp_path,
        delivery=FakeDelivery(),
        trial_resolver=lambda *args: None,
    )
    service.public_root.mkdir(parents=True)
    service.registry_path.write_text(
        json.dumps({
            "schema": "adaos.public_site_publication_registry.v1",
            "sites": {"adaos": {"site_id": "adaos"}},
        }),
        encoding="utf-8",
    )
    receipt = (
        state_dir / "private_recovery/public-sites/managed-domains/demo/receipt.json"
    )
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps({
            "schema": "inimatic.public_site_managed_domain_apply.v1",
            "status": "active",
            "hostname": "demo.adaos.io",
            "site_id": "adaos",
            "public_url": "https://demo.adaos.io/",
            "plan_sha256": "a" * 64,
        }),
        encoding="utf-8",
    )

    record = service.reconcile_managed_domain(
        site_id="adaos", hostname="Demo.AdaOS.io.", receipt_path=receipt
    )

    assert record["managed_by"] == "platform"
    assert record["ssl_status"] == "active"
    registry = json.loads(service.registry_path.read_text(encoding="utf-8"))
    assert registry["domains"]["demo.adaos.io"] == record
    foreign = tmp_path / "foreign-receipt.json"
    foreign.write_text(receipt.read_text(encoding="utf-8"), encoding="utf-8")
    with pytest.raises(ValueError, match="escaped"):
        service.reconcile_managed_domain(
            site_id="adaos",
            hostname="demo.adaos.io",
            receipt_path=foreign,
        )


def test_zoned_delivery_mirrors_before_cloudflare_activation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = CloudflarePublicationDelivery(tmp_path)
    calls = []

    def fake_cloudflare(command, **kwargs):
        calls.append(("cloudflare", command))
        if command == "plan":
            kwargs["workspace"].mkdir(parents=True)
            (kwargs["workspace"] / "publication-plan.json").write_text(
                json.dumps(
                    {
                        "environment": kwargs.get("environment", "prod"),
                        "platform_path": PLATFORM_PATH,
                        "public_url": PUBLIC_URL,
                        "pointer": {
                            "site_id": "adaos",
                            "deployment_digest": DEPLOYMENT_DIGEST,
                            "pointer_digest": POINTER_DIGEST,
                        },
                        "routing_descriptor": {
                            "descriptor_digest": "sha256:" + "6" * 64
                        },
                    }
                ),
                encoding="utf-8",
            )
            return {
                "ok": True,
                "plan_sha256": "a" * 64,
                "deployment_digest": DEPLOYMENT_DIGEST,
                "pointer_digest": POINTER_DIGEST,
            }
        return {
            "ok": True,
            "deployment_digest": DEPLOYMENT_DIGEST,
            "pointer_digest": POINTER_DIGEST,
            "url": PUBLIC_URL,
            "receipt": "cloudflare.json",
        }

    def fake_regru(command, **kwargs):
        calls.append(("regru", command))
        return {
            "ok": True,
            "plan_sha256": "sha256:" + "b" * 64,
            "deployment_digest": DEPLOYMENT_DIGEST,
            "receipt": "regru.json",
        }

    def fake_firebase(command, **kwargs):
        calls.append(("firebase", command))
        return {
            "ok": True,
            "plan_sha256": "sha256:" + "c" * 64,
            "descriptor_digest": "sha256:" + "6" * 64,
            "url": "https://adaos-public-bootstrap.web.app/" + PLATFORM_PATH + "/",
        }

    def fake_gc(**kwargs):
        calls.append(("gc", "apply"))
        return {"ok": True, "receipt": "gc.json"}

    monkeypatch.setattr(delivery, "_run_cloudflare", fake_cloudflare)
    monkeypatch.setattr(delivery, "_run_regru", fake_regru)
    monkeypatch.setattr(delivery, "_run_firebase", fake_firebase)
    monkeypatch.setattr(delivery, "_run_gc", fake_gc)

    prepared = delivery.prepare(
        site_dir=tmp_path / "site",
        workspace=tmp_path / "work",
        platform_path=PLATFORM_PATH,
    )
    result = delivery.publish(
        site_dir=tmp_path / "site",
        workspace=tmp_path / "work",
        platform_path=PLATFORM_PATH,
        plan_digest=prepared["plan_sha256"],
    )

    assert prepared["plan_sha256"].startswith("sha256:")
    assert calls == [
        ("cloudflare", "plan"),
        ("regru", "mirror-plan"),
        ("firebase", "routing-plan"),
        ("regru", "mirror-apply"),
        ("cloudflare", "apply"),
        ("firebase", "routing-apply"),
        ("gc", "apply"),
    ]
    assert result["gc_status"] == "confirmed"


def test_development_delivery_uses_only_dev_cloudflare_plane(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    delivery = CloudflarePublicationDelivery(tmp_path, state_dir=tmp_path / "state")
    calls = []

    def fake_cloudflare(command, **kwargs):
        calls.append((command, kwargs["environment"]))
        if command == "plan":
            kwargs["workspace"].mkdir(parents=True)
            public_url = f"https://dev.p.adaos.io/{PLATFORM_PATH}/"
            (kwargs["workspace"] / "publication-plan.json").write_text(
                json.dumps(
                    {
                        "environment": "dev",
                        "platform_path": PLATFORM_PATH,
                        "public_url": public_url,
                        "pointer": {
                            "site_id": "adaos",
                            "deployment_digest": DEPLOYMENT_DIGEST,
                            "pointer_digest": POINTER_DIGEST,
                        },
                        "routing_descriptor": {
                            "descriptor_digest": "sha256:" + "6" * 64
                        },
                    }
                ),
                encoding="utf-8",
            )
            return {
                "ok": True,
                "plan_sha256": "a" * 64,
                "deployment_digest": DEPLOYMENT_DIGEST,
                "pointer_digest": POINTER_DIGEST,
            }
        return {
            "ok": True,
            "deployment_digest": DEPLOYMENT_DIGEST,
            "pointer_digest": POINTER_DIGEST,
            "url": f"https://dev.p.adaos.io/{PLATFORM_PATH}/",
        }

    monkeypatch.setattr(delivery, "_run_cloudflare", fake_cloudflare)
    monkeypatch.setattr(
        delivery,
        "_run_regru",
        lambda *args, **kwargs: pytest.fail("dev must not use REG.RU production mirror"),
    )
    monkeypatch.setattr(
        delivery,
        "_run_firebase",
        lambda *args, **kwargs: pytest.fail("dev must not move Firebase production"),
    )
    prepared = delivery.prepare(
        site_dir=tmp_path / "site",
        workspace=tmp_path / "work",
        platform_path=PLATFORM_PATH,
        environment="dev",
    )
    result = delivery.publish(
        site_dir=tmp_path / "site",
        workspace=tmp_path / "work",
        platform_path=PLATFORM_PATH,
        plan_digest=prepared["plan_sha256"],
        environment="dev",
    )

    assert calls == [("plan", "dev"), ("apply", "dev")]
    assert result["url"].startswith("https://dev.p.adaos.io/")
    assert result["gc_status"] == "not_applicable"


def test_root_plane_binds_publication_to_calling_skill(monkeypatch) -> None:
    class Service:
        def status(self, **request):
            return {"status": "changes_pending", "skill_id": request["skill_id"]}

    monkeypatch.setattr(public_sites_plane, "public_site_publication_service", Service)

    class Entitlements:
        def status(self, **request):
            assert request["application_id"] == "studio"
            return {
                "status": "available",
                "features": {
                    "authoring": {"status": "available"},
                    "publication": {"status": "available"},
                    "custom_domains": {"status": "available"},
                },
            }

    monkeypatch.setattr(public_sites_plane, "site_studio_entitlement_service", Entitlements)
    arguments = {
        "scenario_id": "studio",
        "skill_id": "studio_skill",
        "webspace_id": "desktop",
        "project_id": "adaos",
        "_mcp_context": {
            "actor": "skill:studio_skill",
            "scope": {"subnet_id": "owner-subnet"},
        },
    }

    result = public_sites_plane.handlers()["public_sites.status"](
        arguments, dry_run=False
    )

    assert result["publication"]["status"] == "changes_pending"
    arguments["_mcp_context"] = {"actor": "skill:another_skill"}
    with pytest.raises(ValueError, match="owning skill"):
        public_sites_plane.handlers()["public_sites.status"](
            arguments, dry_run=False
        )


@pytest.mark.parametrize("mode", ["explicit_review", "compatible_auto"])
def test_policy_for_authorized_site_without_component_deployment(tmp_path, monkeypatch, mode):
    service = PublicSitePublicationService(state_dir=tmp_path / "state", adaos_root=tmp_path)
    scopes = []

    def authorized(**scope):
        scopes.append(scope)
        return _active(), tmp_path / "authoring.sqlite3", tmp_path / "files"

    monkeypatch.setattr(service, "_active_authoring", authorized)
    scope = dict(scenario_id="studio", skill_id="studio_skill", webspace_id="dev2", project_id="adaos")
    policy = service.set_component_upgrade_policy(**scope, mode=mode)
    assert scopes == [scope]
    assert policy["site_id"] == "adaos"
    assert policy["mode"] == mode
    assert policy["actor"] == "skill:studio_skill"
    assert service.component_registry.site_status("adaos")["current_deployment"] is None
    assert not service.registry_path.exists()



def test_policy_rejects_unselected_project_before_writing(tmp_path, monkeypatch):
    service = PublicSitePublicationService(state_dir=tmp_path / "state", adaos_root=tmp_path)

    def unauthorized(**scope):
        raise ValueError("Site Studio project does not match the selected project")

    monkeypatch.setattr(service, "_active_authoring", unauthorized)
    with pytest.raises(ValueError, match="selected project"):
        service.set_component_upgrade_policy(
            scenario_id="studio", skill_id="studio_skill", webspace_id="dev2",
            project_id="foreign", mode="explicit_review",
        )
    assert not service.component_registry.path.exists()
