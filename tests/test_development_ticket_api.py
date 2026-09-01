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


class _FakeAutomationService:
    def __init__(self) -> None:
        self.started: list[dict] = []
        self.workspace_service = _FakeWorkspaceService()

    def start_from_execute(self, **kwargs):
        self.started.append(dict(kwargs))
        return self._payload(status="running")

    def status(self, *, object_type: str, object_id: str):
        return self._payload(status="completed", object_type=object_type, object_id=object_id)

    def decide_aprobation(self, **kwargs):
        return {
            "ok": True,
            "decision": kwargs["decision"],
            "candidate_id": "candidate.api",
            "target": {
                "object_type": kwargs["object_type"],
                "object_id": kwargs["object_id"],
            },
        }

    def _payload(self, *, status: str, object_type: str = "skill", object_id: str = "demo_metrics_skill") -> dict:
        result = {
            "commit_hash": "abc123",
            "tests": {"status": "passed", "report": "reports/autonomous-tests.json"},
        }
        return {
            "ok": True,
            "automation": {
                "session_id": "automation.session.api",
                "task_id": "factory.task.api",
                "status": status,
                "phase": "completed" if status == "completed" else "running",
                "terminal": status == "completed",
                "busy": status != "completed",
                "change_id": "builder.change.api",
                "result_branch": "builder/api-dev-ticket",
                "webspace_id": "desktop",
                "project": {"object_type": object_type, "object_id": object_id},
                "budget_usage": {
                    "declared": {"max_tokens": 200000},
                    "observed": {"total_tokens": 321, "input_tokens": 200, "output_tokens": 121},
                },
            },
            "session": {
                "session_id": "automation.session.api",
                "status": status,
                "current_task_id": "factory.task.api",
                "task": {"task_id": "factory.task.api", "status": "completed", "result": result},
                "completion_readiness": {"ok": True},
                "codex_usage_accounting": {
                    "status": "recorded",
                    "root_event_id": "codex.usage.api",
                    "total_tokens": 321,
                    "input_tokens": 200,
                    "output_tokens": 121,
                    "billable_tokens": 321,
                },
                "last_result": result,
            },
        }


class _FakeWorkspaceService:
    def __init__(self) -> None:
        self.materialized: list[dict] = []

    def materialize_dev_source(self, **kwargs):
        self.materialized.append(dict(kwargs))
        return {
            "ok": True,
            "status": "materialized",
            "development_source": {
                "status": "source_available",
                "source": "dev",
                "target_type": kwargs.get("kind"),
                "target_id": kwargs.get("artifact_id"),
                "project_id": kwargs.get("project_id"),
                "options": ["use_existing_dev_source"],
                "default_option": "use_existing_dev_source",
            },
            "components": [
                {
                    "kind": kwargs.get("kind"),
                    "name": kwargs.get("artifact_id"),
                    "status": "materialized",
                }
            ],
        }


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
    assert created_payload["detail"]["development_source"]["status"] == "needs_materialization"

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


def test_development_ticket_api_plans_and_starts_builder_package(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    automation = _FakeAutomationService()
    client = _client(service)
    signal = service.capture_signal(
        kind="development_request",
        summary="Rename the Demo Metrics table heading",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "change_summary": "Rename only the selected heading.",
                "target_files": ["skills/demo_metrics_skill/webui.json"],
                "target_refs": ["widget:metrics-table.title"],
                "acceptance_checks": ["The table heading is Live metrics."],
                "max_changed_files": 1,
                "requires_root_mcp": False,
            }
        },
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="skill",
    )["ticket"]

    planned = client.post(
        "/api/development-tickets/builder-packages/plan",
        headers=_headers(),
        json={"ticket_ids": [ticket["ticket_id"]], "actor": "builder:qualifier"},
    )

    assert planned.status_code == 201, planned.text
    package_id = planned.json()["package_id"]
    shown = client.get(
        f"/api/development-tickets/builder-packages/{package_id}",
        headers=_headers(),
    )
    assert shown.status_code == 200, shown.text
    assert shown.json()["rollup"]["ticket_ids"] == [ticket["ticket_id"]]

    monkeypatch.setattr(tickets_api, "_get_automation_service", lambda: automation)
    started = client.post(
        f"/api/development-tickets/builder-packages/{package_id}/start",
        headers=_headers(),
        json={"actor": "builder:automation", "webspace_id": "desktop"},
    )

    assert started.status_code == 200, started.text
    assert len(automation.started) == 1
    assert automation.started[0]["links"]["development_ticket_ids"] == [
        ticket["ticket_id"]
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

    shown_after_handoff = client.get(f"/api/development-tickets/{ticket_id}", headers=_headers())
    assert shown_after_handoff.status_code == 200, shown_after_handoff.text
    work_stream = shown_after_handoff.json()["work_stream"]
    assert work_stream["schema"] == "adaos.builder.ticket_work_stream.v1"
    assert work_stream["lifecycle_split"]["one_user_ticket_can_spawn_many_builder_items"] is True
    assert work_stream["builder_work_count"] == 1
    assert work_stream["builder_work_items"][0]["repair_id"] == handoff_payload["repair"]["repair_id"]
    assert work_stream["builder_work_items"][0]["status"] == "planned"
    assert work_stream["builder_work_items"][0]["compatibility_status"] == "open"
    assert work_stream["builder_work_items"][0]["authority"] == "adaos.builder.work_item"
    assert work_stream["builder_work_items"][0]["human_manageable"] is False
    assert work_stream["builder_work_items"][0]["token_accounting"]["subscription_resource"] == "codex.api.tokens"

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


def test_development_ticket_api_starts_autonomous_repair_and_exposes_builder_usage(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    automation = _FakeAutomationService()
    client = _client(service)
    client.app.dependency_overrides[tickets_api._get_automation_service] = lambda: automation

    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "summary": "Tune Demo Metrics Resource Workbench edit/delete controls",
            "kind": "development_request",
            "target_scope": {
                "type": "skill",
                "id": "demo_metrics_skill",
                "source": "workspace",
                "component_ref": "skill:demo_metrics_skill",
            },
            "owner_area": "skill",
            "component_ref": "skill:demo_metrics_skill",
        },
    )
    assert created.status_code == 201, created.text
    ticket_id = created.json()["ticket"]["ticket_id"]

    started = client.post(
        f"/api/development-tickets/{ticket_id}/autonomous-repair",
        headers=_headers(),
        json={
            "actor": "browser",
            "webspace_id": "desktop",
            "source_strategy": "materialize_dev_source",
            "mcp": {
                "root_mcp": {
                    "url": "https://ru.api.inimatic.com/v1/root/mcp",
                    "server_name": "adaos-root",
                    "bearer_token_env_var": "ADAOS_ROOT_MCP_AUTH",
                    "enabled_tools": ["get_status"],
                }
            },
        },
    )

    assert started.status_code == 200, started.text
    payload = started.json()
    assert payload["started"] is True
    assert payload["sync"]["resolved"] is True
    assert payload["ticket"]["status"] == "resolved"
    assert automation.started[0]["object_type"] == "skill"
    assert automation.started[0]["object_id"] == "demo_metrics_skill"
    assert automation.started[0]["links"]["development_ticket_id"] == ticket_id
    assert automation.started[0]["execution_budget"]["max_tokens"] == 200000
    assert automation.started[0]["mcp"]["root_mcp"]["server_name"] == "adaos-root"
    assert "bearer_token_env_var" in automation.started[0]["mcp"]["root_mcp"]
    assert automation.workspace_service.materialized[0]["kind"] == "skill"
    assert automation.workspace_service.materialized[0]["artifact_id"] == "demo_metrics_skill"
    work_item = payload["detail"]["work_stream"]["builder_work_items"][0]
    assert work_item["repair_id"] == payload["repair"]["repair_id"]
    assert work_item["automation_session_id"] == "automation.session.api"
    assert work_item["automation_task_id"] == "factory.task.api"
    assert work_item["token_accounting"]["reported_usage"]["total_tokens"] == 321
    assert work_item["token_accounting"]["estimate"]["max_tokens"] == 200000

    synced = client.post(
        f"/api/development-tickets/{ticket_id}/builder-sync",
        headers=_headers(),
        json={"actor": "browser", "repair_id": payload["repair"]["repair_id"]},
    )
    assert synced.status_code == 200, synced.text
    assert synced.json()["detail"]["work_stream"]["builder_work_count"] == 1
    assert synced.json()["detail"]["work_stream"]["builder_work_items"][0]["automation_status"] == "completed"


def test_development_ticket_api_delegates_trial_decision_to_scoped_builder_target(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    automation = _FakeAutomationService()
    client = _client(service)
    client.app.dependency_overrides[tickets_api._get_automation_service] = lambda: automation
    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "summary": "Review Demo Metrics trial",
            "kind": "development_request",
            "target_scope": {"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        },
    )
    ticket_id = created.json()["ticket"]["ticket_id"]

    response = client.post(
        f"/api/development-tickets/{ticket_id}/trial/decision",
        headers=_headers(),
        json={"decision": "accept", "actor": "user:owner", "reason": "Checked in UI"},
    )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["decision"] == "accept"
    assert payload["target"] == {"object_type": "skill", "object_id": "demo_metrics_skill"}
    assert payload["detail"]["ticket"]["ticket_id"] == ticket_id


def test_development_ticket_api_requalifies_builder_envelope_with_revision_guard(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    client = _client(service)
    created = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "summary": "Rename a Demo Metrics action",
            "kind": "development_request",
            "status": "ready_for_builder",
            "target_scope": {"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
            "metadata": {
                "builder_repair": {
                    "profile": "surgical_ui",
                    "target_files": ["skills/demo_metrics_skill/missing.py"],
                }
            },
        },
    )
    ticket = created.json()["ticket"]
    payload = {
        "actor": "builder:qualifier",
        "reason": "Discovery found the focused test file.",
        "expected_updated_at": ticket["updated_at"],
        "builder_repair": {
            "profile": "surgical_ui",
            "target_files": ["skills/demo_metrics_skill/webui.json"],
            "target_refs": ["ydoc_defaults.data/demo_metrics/summary.buttons[id=open-operations]"],
            "max_changed_files": 1,
        },
    }

    response = client.post(
        f"/api/development-tickets/{ticket['ticket_id']}/builder-qualification",
        headers=_headers(),
        json=payload,
    )
    assert response.status_code == 200, response.text
    assert response.json()["ticket"]["history"][-1]["kind"] == "builder_repair_requalified"

    conflict = client.post(
        f"/api/development-tickets/{ticket['ticket_id']}/builder-qualification",
        headers=_headers(),
        json={**payload, "builder_repair": {**payload["builder_repair"], "max_changed_files": 2}},
    )
    assert conflict.status_code == 409, conflict.text


def test_development_ticket_api_creates_core_and_sdk_qualification_tickets(tmp_path: Path) -> None:
    client = _client(DevelopmentTicketService(state_dir=tmp_path))

    project = client.post(
        "/api/development-tickets",
        headers=_headers(),
        json={
            "summary": "Autonomous repair cannot proceed with current SDK docs",
            "kind": "development_request",
            "owner_area": "project",
            "component_ref": "modal:nlu_teacher_modal",
            "target_scope": {
                "type": "modal",
                "id": "nlu_teacher_modal",
                "project_ref": "project:homepoint",
                "component_ref": "modal:nlu_teacher_modal",
            },
        },
    )
    assert project.status_code == 201, project.text
    project_ticket_id = project.json()["ticket"]["ticket_id"]

    core = client.post(
        "/api/development-tickets/core-capability-requests",
        headers=_headers(),
        json={
            "summary": "Builder needs an artifact-open SDK helper",
            "component_ref": "core:sdk",
            "desired_contract": "Expose ticket artifact open/read helpers for agent workflows.",
            "impact": "blocker",
            "actor": "builder:test",
            "blocked_ticket_ids": [project_ticket_id],
            "evidence_refs": [{"type": "trace", "id": "builder.artifact.lookup"}],
        },
    )
    assert core.status_code == 201, core.text
    core_ticket = core.json()["ticket"]
    assert core_ticket["owner_area"] == "core"
    assert core_ticket["component_ref"] == "core:sdk"
    assert core.json()["blocked_tickets"][0]["status"] == "waiting_for_core"

    sdk = client.post(
        "/api/development-tickets/sdk-understanding",
        headers=_headers(),
        json={
            "kind": "sdk_unclear_definition",
            "summary": "Artifact refs are readable but not directly openable by Codex",
            "method_ref": "dev_ticket.artifact",
            "actor": "codex:test",
            "diagnosis": "sdk_example_gap",
            "project_ticket_id": project_ticket_id,
        },
    )
    assert sdk.status_code == 201, sdk.text
    assert sdk.json()["ticket"]["kind"] == "sdk_understanding"
    assert sdk.json()["ticket"]["relation_refs"][0]["ticket_id"] == project_ticket_id

    listed = client.get("/api/development-tickets?owner_area=core&component_ref=core:sdk", headers=_headers())
    assert listed.status_code == 200, listed.text
    assert [item["ticket_id"] for item in listed.json()["tickets"]] == [core_ticket["ticket_id"]]
