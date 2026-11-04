from __future__ import annotations

import json
from pathlib import Path

import pytest

from adaos.services import public_site_publication as publication_module
from adaos.services.public_site_publication import PublicSitePublicationService
from adaos.services.root_mcp import public_sites_plane


SOURCE_DIGEST = "sha256:" + "1" * 64
DEPLOYMENT_DIGEST = "sha256:" + "2" * 64
POINTER_DIGEST = "sha256:" + "3" * 64
PLAN_DIGEST = "sha256:" + "4" * 64


class FakeDelivery:
    def __init__(self) -> None:
        self.prepared = 0
        self.published = 0

    def prepare(self, *, site_dir: Path, workspace: Path) -> dict:
        self.prepared += 1
        workspace.mkdir(parents=True)
        (workspace / "publication-plan.json").write_text(
            json.dumps(
                {
                    "schema": "inimatic.public_site_publication_plan.v1",
                    "environment": "prod",
                    "host": "p.adaos.io",
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
        self, *, site_dir: Path, workspace: Path, plan_digest: str
    ) -> dict:
        assert plan_digest == PLAN_DIGEST
        self.published += 1
        return {
            "ok": True,
            "deployment_digest": DEPLOYMENT_DIGEST,
            "pointer_digest": POINTER_DIGEST,
            "url": "https://p.adaos.io/",
            "receipt": str(workspace / "receipt.json"),
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
