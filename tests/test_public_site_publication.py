from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services import public_site_publication as publication_module
from adaos.services.public_site_publication import (
    CloudflarePublicationDelivery,
    PublicSitePublicationService,
)
from adaos.services.root_mcp import public_sites_plane


SOURCE_DIGEST = "sha256:" + "1" * 64
DEPLOYMENT_DIGEST = "sha256:" + "2" * 64
POINTER_DIGEST = "sha256:" + "3" * 64
PLAN_DIGEST = "sha256:" + "4" * 64
PLATFORM_PATH = "st_0123456789ab"
PUBLIC_URL = f"https://p.adaos.io/{PLATFORM_PATH}/"


class FakeDelivery:
    def __init__(self) -> None:
        self.prepared = 0
        self.published = 0

    def prepare(self, *, site_dir: Path, workspace: Path, platform_path: str) -> dict:
        self.prepared += 1
        assert platform_path.startswith("st_")
        workspace.mkdir(parents=True)
        (workspace / "publication-plan.json").write_text(
            json.dumps(
                {
                    "schema": "inimatic.public_site_publication_plan.v1",
                    "environment": "prod",
                    "host": "p.adaos.io",
                    "platform_path": platform_path,
                    "public_url": f"https://p.adaos.io/{platform_path}/",
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
        self, *, site_dir: Path, workspace: Path, platform_path: str, plan_digest: str
    ) -> dict:
        assert plan_digest == PLAN_DIGEST
        self.published += 1
        return {
            "ok": True,
            "deployment_digest": DEPLOYMENT_DIGEST,
            "pointer_digest": POINTER_DIGEST,
            "url": f"https://p.adaos.io/{platform_path}/",
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


def test_prepare_and_publish_keep_provider_paths_private(
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
        lambda **kwargs: (_active(), tmp_path / "authoring.sqlite3", tmp_path / "files"),
    )

    def fake_export(database, files, output):
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


def test_root_plane_binds_publication_to_calling_skill(monkeypatch) -> None:
    class Service:
        def status(self, **request):
            return {"status": "changes_pending", "skill_id": request["skill_id"]}

    monkeypatch.setattr(public_sites_plane, "public_site_publication_service", Service)
    arguments = {
        "scenario_id": "studio",
        "skill_id": "studio_skill",
        "webspace_id": "desktop",
        "project_id": "adaos",
        "_mcp_context": {"actor": "skill:studio_skill"},
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
