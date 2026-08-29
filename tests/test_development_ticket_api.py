from __future__ import annotations

import base64
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from adaos.apps.api import development_tickets as tickets_api
from adaos.services.development_tickets import DevelopmentTicketService


def _client(service: DevelopmentTicketService) -> TestClient:
    app = FastAPI()
    app.include_router(tickets_api.router, prefix="/api/development-tickets")
    app.dependency_overrides[tickets_api._get_service] = lambda: service
    return TestClient(app)


def _headers() -> dict[str, str]:
    return {"X-AdaOS-Token": "dev-local-token"}


def test_development_ticket_api_create_list_show_evidence_and_defer(tmp_path: Path) -> None:
    client = _client(DevelopmentTicketService(state_dir=tmp_path))

    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "summary": "Improve feedback modal wording",
            "kind": "feedback",
            "target_scope": {"type": "scenario", "id": "daily_dashboard", "source": "workspace"},
            "evidence_refs": [{"type": "ui", "id": "header.feedback"}],
        },
    )
    assert created.status_code == 201, created.text
    created_payload = created.json()
    ticket_id = created_payload["ticket"]["ticket_id"]
    assert created_payload["detail"]["development_source"]["status"] == "source_available"

    listed = client.get(
        "/api/development-tickets?target_id=daily_dashboard",
        headers=_headers(),
    )
    assert listed.status_code == 200
    listed_payload = listed.json()
    assert [item["ticket_id"] for item in listed_payload["tickets"]] == [ticket_id]
    assert [item["ticket_id"] for item in listed_payload["items"]] == [ticket_id]
    assert listed_payload["count"] == 1

    shown = client.get(f"/api/development-tickets/{ticket_id}", headers=_headers())
    assert shown.status_code == 200
    assert shown.json()["ticket"]["summary"] == "Improve feedback modal wording"

    evidence = client.get(f"/api/development-tickets/{ticket_id}/evidence", headers=_headers())
    assert evidence.status_code == 200
    evidence_payload = evidence.json()["evidence"]
    assert evidence_payload["ticket_evidence_refs"] == [{"type": "ui", "id": "header.feedback"}]
    assert evidence_payload["evidence_refs"] == [{"type": "ui", "id": "header.feedback"}]

    deferred = client.post(
        f"/api/development-tickets/{ticket_id}/respond",
        headers=_headers(),
        json={"response_action_id": "postpone", "responder": {"id": "user:owner"}},
    )
    assert deferred.status_code == 200
    assert deferred.json()["ticket"]["status"] == "deferred"


def test_development_ticket_api_create_accepts_signal_kind_with_ticket_kind(tmp_path: Path) -> None:
    client = _client(DevelopmentTicketService(state_dir=tmp_path))

    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "kind": "feedback_note",
            "ticket_kind": "feedback",
            "status": "captured",
            "summary": "Screenshot feedback from header panel",
            "source": "client_feedback",
            "target_scope": {"type": "scenario", "id": "builder", "source": "workspace"},
            "artifact_refs": [{"type": "screenshot", "uri": "artifact://shot.png"}],
            "evidence_refs": [{"type": "trace", "source": "client_feedback_ui"}],
        },
    )

    assert created.status_code == 201, created.text
    payload = created.json()
    assert payload["signal"]["kind"] == "feedback_note"
    assert payload["ticket"]["kind"] == "feedback"
    assert payload["detail"]["evidence"]["artifact_refs"] == [
        {"type": "screenshot", "uri": "artifact://shot.png"},
    ]


def test_development_ticket_api_uploads_screenshot_artifact(tmp_path: Path) -> None:
    client = _client(DevelopmentTicketService(state_dir=tmp_path))
    content = b"\x89PNG\r\n\x1a\nfake screenshot"

    uploaded = client.post(
        "/api/development-tickets/artifacts",
        headers=_headers(),
        json={
            "kind": "screenshot",
            "content_type": "image/png",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "filename": "feedback.png",
            "origin_scope": {"type": "client", "surface": "dev_tickets_panel_screenshot"},
            "target_scope": {"type": "scenario", "id": "builder"},
        },
    )

    assert uploaded.status_code == 201, uploaded.text
    ref = uploaded.json()["artifact_ref"]
    assert ref["type"] == "screenshot"
    assert ref["content_api_path"].startswith("/api/development-tickets/artifacts/")
    assert ref["sha256"].startswith("sha256:")

    content_response = client.get(ref["content_api_path"], headers=_headers())
    assert content_response.status_code == 200
    assert content_response.content == content

    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "kind": "feedback_note",
            "summary": "Screenshot feedback",
            "source": "client_feedback",
            "target_scope": {"type": "scenario", "id": "builder", "source": "workspace"},
            "artifact_refs": [ref],
            "evidence_refs": [{"type": "trace", "source": "client_feedback_ui"}],
        },
    )

    assert created.status_code == 201, created.text
    assert created.json()["detail"]["evidence"]["artifact_refs"] == [ref]


def test_development_ticket_api_updates_summary_and_keeps_artifact_refs(tmp_path: Path) -> None:
    client = _client(DevelopmentTicketService(state_dir=tmp_path))

    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "kind": "feedback_note",
            "ticket_kind": "feedback",
            "status": "captured",
            "summary": "Screenshot for Capabilities",
            "source": "client_feedback",
            "target_scope": {"type": "scenario", "id": "web_desktop", "source": "workspace"},
            "artifact_refs": [{"type": "screenshot", "uri": "dev-ticket-artifact:shot"}],
            "evidence_refs": [{"type": "trace", "source": "client_feedback_ui"}],
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["ticket"]["ticket_id"]

    updated = client.patch(
        f"/api/development-tickets/{ticket_id}",
        headers=_headers(),
        json={
            "summary": "Unify Dev Tickets icon in scenario header and skill modal",
            "actor": "user:owner",
        },
    )

    assert updated.status_code == 200, updated.text
    payload = updated.json()
    assert payload["ticket"]["summary"] == "Unify Dev Tickets icon in scenario header and skill modal"
    assert payload["evidence"]["artifact_refs"] == [{"type": "screenshot", "uri": "dev-ticket-artifact:shot"}]
    assert payload["ticket"]["history"][-1]["kind"] == "summary_updated"
    assert payload["ticket"]["history"][-1]["previous_summary"] == "Screenshot for Capabilities"

    listed = client.get("/api/development-tickets", headers=_headers())
    assert listed.status_code == 200
    assert listed.json()["tickets"][0]["summary"] == "Unify Dev Tickets icon in scenario header and skill modal"


def test_development_ticket_api_rejects_terminal_summary_update(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    client = _client(service)

    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "kind": "feedback",
            "summary": "Already closed",
            "target_scope": {"type": "scenario", "id": "web_desktop"},
        },
    )
    ticket_id = created.json()["ticket"]["ticket_id"]
    service.close_ticket(ticket_id, reason="stale", actor="test")

    updated = client.patch(
        f"/api/development-tickets/{ticket_id}",
        headers=_headers(),
        json={"summary": "Edited after close", "actor": "test"},
    )

    assert updated.status_code == 400
    assert "terminal Dev Ticket cannot be edited" in updated.text


def test_development_ticket_api_lists_project_and_component_scope(tmp_path: Path) -> None:
    client = _client(DevelopmentTicketService(state_dir=tmp_path))

    project = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "kind": "feedback",
            "summary": "Project level feedback",
            "target_scope": {
                "type": "project",
                "id": "recipes",
                "ref": "project:recipes",
                "component_refs": ["scenario:recipes", "skill:recipes_worker"],
            },
        },
    )
    skill = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "kind": "review_debt",
            "summary": "Skill component feedback",
            "target_scope": {
                "type": "skill",
                "id": "recipes_worker",
                "ref": "skill:recipes_worker",
                "project_ref": "project:recipes",
            },
        },
    )
    unrelated = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "kind": "feedback",
            "summary": "Unrelated feedback",
            "target_scope": {"type": "scenario", "id": "clock", "ref": "scenario:clock"},
        },
    )
    assert project.status_code == 201, project.text
    assert skill.status_code == 201, skill.text
    assert unrelated.status_code == 201, unrelated.text

    listed = client.get(
        "/api/development-tickets"
        "?target_ref=project:recipes"
        "&target_ref=skill:recipes_worker"
        "&kind=feedback"
        "&kind=review_debt",
        headers=_headers(),
    )

    assert listed.status_code == 200, listed.text
    summaries = {item["summary"] for item in listed.json()["tickets"]}
    assert summaries == {"Project level feedback", "Skill component feedback"}


def test_development_ticket_api_handoff_and_resolution_require_evidence(tmp_path: Path) -> None:
    client = _client(DevelopmentTicketService(state_dir=tmp_path))

    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "summary": "Skill legacy_skill lacks receiver/data-route declaration for legacy.panel",
            "kind": "runtime_compatibility_debt",
            "target_scope": {"type": "skill", "id": "legacy_skill", "version": "1.0.0", "source": "installed"},
            "blocking": True,
            "source": "runtime_guard",
            "metadata": {"code": "compat.stream_receiver_policy_missing"},
            "policy": {
                "blocking": True,
                "run_policy": "degrade",
                "design_time_fixable": True,
                "autonomous_repair_eligible": True,
            },
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["ticket"]["ticket_id"]
    assert created.json()["detail"]["development_source"]["status"] == "needs_materialization"

    handoff = client.post(
        f"/api/development-tickets/{ticket_id}/handoff",
        headers=_headers(),
        json={"mode": "autonomous", "actor": "user:owner"},
    )
    assert handoff.status_code == 200, handoff.text
    handoff_payload = handoff.json()
    assert handoff_payload["ticket"]["status"] == "in_builder"
    assert handoff_payload["repair"]["project_id"] == "legacy_skill"
    assert handoff_payload["repair"]["context"]["development_source"]["status"] == "needs_materialization"
    assert "materialize_dev_source" in handoff_payload["repair"]["context"]["development_source"]["options"]

    rejected = client.post(
        f"/api/development-tickets/{ticket_id}/resolve",
        headers=_headers(),
        json={"evidence_refs": [], "actor": "builder:test"},
    )
    assert rejected.status_code in {400, 422}

    resolved = client.post(
        f"/api/development-tickets/{ticket_id}/resolve",
        headers=_headers(),
        json={
            "evidence_refs": [
                {"type": "test", "id": "tests/test_skill_activation.py::receiver_contract", "status": "passed"},
                {"type": "activation", "id": "legacy_skill@1.0.1", "status": "passed"},
            ],
            "actor": "builder:test",
            "resolved_by_version": "legacy_skill@1.0.1",
        },
    )
    assert resolved.status_code == 200, resolved.text
    assert resolved.json()["ticket"]["status"] == "resolved"
    assert resolved.json()["closure"]["resolved_by_version"] == "legacy_skill@1.0.1"

    close_too_early = client.post(
        f"/api/development-tickets/{ticket_id}/close",
        headers=_headers(),
        json={"reason": "closed", "actor": "user:owner"},
    )
    assert close_too_early.status_code == 400
    assert "verified status" in close_too_early.text

    verified = client.post(
        f"/api/development-tickets/{ticket_id}/verify",
        headers=_headers(),
        json={
            "evidence_refs": [{"type": "runtime_guard", "id": "receiver_contract_after_fix", "status": "passed"}],
            "actor": "validation:test",
        },
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["ticket"]["status"] == "verified"

    closed = client.post(
        f"/api/development-tickets/{ticket_id}/close",
        headers=_headers(),
        json={"reason": "closed", "actor": "user:owner"},
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["ticket"]["status"] == "closed"

    reopened = client.post(
        f"/api/development-tickets/{ticket_id}/reopen",
        headers=_headers(),
        json={
            "reason": "smoke failed on runtime",
            "actor": "user:owner",
            "evidence_refs": [{"type": "trace", "id": "runtime.trace.after-close"}],
        },
    )
    assert reopened.status_code == 200, reopened.text
    assert reopened.json()["ticket"]["status"] == "in_progress"
