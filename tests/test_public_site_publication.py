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
