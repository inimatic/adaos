from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import component_updates as updates_api
from adaos.services.component_updates import ComponentUpdateService


def _aprobation(*, status: str = "trial", decision: str = "") -> dict:
    trial = {
        "candidate_id": "candidate.demo.1",
        "candidate_digest": "sha256:candidate",
        "version": "0.2.0",
        "status": status,
        "started_at": "2026-09-01T10:00:00Z",
    }
    if decision:
        trial["decision"] = decision
    return {
        "ok": True,
        "audience": "alpha",
        "source_kind": "devspace",
        "trial": trial,
        "changelog": {
            "title": "Demo Metrics update",
            "summary": "Metrics table now uses the Resource Workbench.",
            "changes": ["Use typed queries", "Add edit and delete actions"],
            "ticket_ids": ["dticket.demo"],
        },
    }


def test_component_update_tracks_release_and_viewer_lifecycle(tmp_path: Path) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)

    alpha = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(),
        webspace_id="desktop",
    )

    assert alpha is not None
    assert alpha["stage"] == "alpha"
    assert alpha["version"] == "0.2.0"
    assert service.list_notices(actor="user:owner", webspace_id="desktop")[0]["auto_prompt"] is True

    presented = service.respond(
        alpha["notice_id"],
        action="dismiss_auto",
        actor="user:owner",
        webspace_id="desktop",
    )
    assert presented["unread"] is True
    assert presented["auto_prompt"] is False

    reviewed = service.respond(
        alpha["notice_id"],
        action="review_started",
        actor="user:owner",
        webspace_id="desktop",
    )
    assert reviewed["unread"] is False
    assert reviewed["viewer_state"]["review_started_at"]

    published = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(status="published", decision="accept"),
        webspace_id="desktop",
    )
    assert published is not None
    assert published["notice_id"] == alpha["notice_id"]
    assert published["stage"] == "beta"
    assert service.active_component_metadata("skill", "demo_metrics_skill")["stage"] == "beta"


def test_component_update_reconciles_builder_session_and_api(tmp_path: Path) -> None:
    automation_root = tmp_path / "builder" / "automation"
    automation_root.mkdir(parents=True)
    (automation_root / "skill.demo_metrics_skill.json").write_text(
        json.dumps(
            {
                "object_type": "skill",
                "object_id": "demo_metrics_skill",
                "webspace_id": "desktop",
                "links": {"development_ticket_id": "dticket.demo"},
                "completion_readiness": {"aprobation": _aprobation()},
            }
        ),
        encoding="utf-8",
    )
    service = ComponentUpdateService(state_dir=tmp_path)
    app = FastAPI()
    app.include_router(updates_api.router, prefix="/api/component-updates")
    app.dependency_overrides[updates_api._get_service] = lambda: service
    client = TestClient(app)
    headers = {"X-AdaOS-Token": "dev-local-token"}

    response = client.get(
        "/api/component-updates",
        params={"component_type": "skill", "component_id": "demo_metrics_skill"},
        headers=headers,
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["ticket_ids"] == ["dticket.demo"]

    notice_id = payload["items"][0]["notice_id"]
    responded = client.post(
        f"/api/component-updates/{notice_id}/respond",
        json={"action": "review_started", "actor": "user:owner", "webspace_id": "desktop"},
        headers=headers,
    )
    assert responded.status_code == 200
    assert responded.json()["notice"]["unread"] is False
