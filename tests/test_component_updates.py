from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import component_updates as updates_api
from adaos.services.component_updates import ComponentUpdateService


def _aprobation(
    *,
    status: str = "trial",
    decision: str = "",
    candidate_id: str = "candidate.demo.1",
    version: str = "0.2.0",
) -> dict:
    trial = {
        "candidate_id": candidate_id,
        "candidate_digest": f"sha256:{candidate_id}",
        "release_digest": f"sha256:release-{candidate_id}",
        "version": version,
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
    assert alpha["review_state"] == "pending"
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

    publishing = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(status="accepted", decision="accept"),
        webspace_id="desktop",
    )
    assert publishing is not None
    assert publishing["stage"] == "beta"
    assert publishing["status"] == "active"
    assert publishing["review_state"] == "publishing"

    published = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(status="published", decision="accept"),
        webspace_id="desktop",
    )
    assert published is not None
    assert published["notice_id"] == alpha["notice_id"]
    assert published["stage"] == "stable"
    assert published["status"] == "accepted"
    assert published["review_state"] == "accepted"
    assert published["title"] == "Demo Metrics update"
    assert published["transition"] == {
        "state": "accepted",
        "requires_user_decision": False,
        "workspace_committed": True,
        "workspace_version": "0.2.0",
        "release_digest": "sha256:release-candidate.demo.1",
    }
    assert service.active_component_metadata("skill", "demo_metrics_skill") is None
    assert service.list_notices(status="accepted")[0]["notice_id"] == alpha["notice_id"]


def test_component_update_current_view_returns_latest_changeset_per_component(
    tmp_path: Path,
) -> None:
    service = ComponentUpdateService(state_dir=tmp_path)
    published = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(status="published", decision="accept"),
    )
    pending = service.record_aprobation(
        component_type="skill",
        component_id="demo_metrics_skill",
        aprobation=_aprobation(
            candidate_id="candidate.demo.2",
            version="0.3.0",
        ),
    )
    other = service.record_aprobation(
        component_type="skill",
        component_id="subscription_status_skill",
        aprobation=_aprobation(
            candidate_id="candidate.subscription.1",
            version="0.1.0",
        ),
    )

    current = service.list_notices(status="current")

    assert published is not None
    assert pending is not None
    assert other is not None
    assert {item["notice_id"] for item in current} == {
        pending["notice_id"],
        other["notice_id"],
    }
    demo = next(item for item in current if item["component"]["id"] == "demo_metrics_skill")
    assert demo["stage"] == "alpha"
    assert demo["transition"]["requires_user_decision"] is True
    assert demo["transition"]["workspace_committed"] is False


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
    assert payload["awaiting_decision"] == 1
    assert payload["publishing"] == 0
    assert payload["workspace_committed"] == 0

    notice_id = payload["items"][0]["notice_id"]
    responded = client.post(
        f"/api/component-updates/{notice_id}/respond",
        json={"action": "review_started", "actor": "user:owner", "webspace_id": "desktop"},
        headers=headers,
    )
    assert responded.status_code == 200
    assert responded.json()["notice"]["unread"] is False
