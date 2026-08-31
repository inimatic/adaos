from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.repair import BuilderRepairService
from adaos.services.development_tickets import (
    COMPATIBILITY_PENDING_ACTION_KIND,
    COMPATIBILITY_RESPONSE_TOPIC,
    DevelopmentTicketService,
)
from adaos.services.skill.activation import stream_receiver_event_admission


class _FakeBuilderAutomation:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.counter = 0

    def start_from_execute(self, **kwargs):
        self.counter += 1
        self.calls.append(dict(kwargs))
        return self._payload(status="running", suffix=str(self.counter), links=kwargs.get("links") or {})

    def status(self, *, object_type: str, object_id: str):
        suffix = str(self.counter or 1)
        return self._payload(
            status="completed",
            suffix=suffix,
            links={
                "object_type": object_type,
                "object_id": object_id,
            },
        )

    def _payload(self, *, status: str, suffix: str, links: dict) -> dict:
        task_id = f"factory.task.{suffix}"
        session_id = f"automation.session.{suffix}"
        result = {
            "commit_hash": f"commit-{suffix}",
            "tests": {"status": "passed", "report": f"reports/tests-{suffix}.json"},
        }
        return {
            "ok": True,
            "automation": {
                "schema": "adaos.builder.automation_session_projection.v1",
                "session_id": session_id,
                "task_id": task_id,
                "status": status,
                "phase": "completed" if status == "completed" else "running",
                "terminal": status == "completed",
                "busy": status != "completed",
                "change_id": f"change.{suffix}",
                "result_branch": f"builder/dev-ticket-{suffix}",
                "webspace_id": "desktop",
                "links": dict(links),
                "budget_usage": {
                    "declared": {"max_tokens": 200000},
                    "observed": {
                        "input_tokens": 100,
                        "cached_input_tokens": 20,
                        "output_tokens": 50,
                        "reasoning_tokens": 10,
                        "total_tokens": 150,
                    },
                },
            },
            "session": {
                "session_id": session_id,
                "status": status,
                "current_task_id": task_id,
                "task": {"task_id": task_id, "status": "completed", "result": result},
                "completion_readiness": {"ok": True, "checks": [{"id": "tests", "status": "passed"}]},
                "codex_usage_accounting": {
                    "status": "recorded",
                    "root_event_id": f"codex.usage.{suffix}",
                    "model_tokens": 140,
                    "input_tokens": 100,
                    "cached_input_tokens": 20,
                    "output_tokens": 50,
                    "reasoning_tokens": 10,
                    "total_tokens": 150,
                    "billable_tokens": 130,
                },
                "last_result": result,
            },
        }


def _schema(name: str) -> dict:
    return json.loads((Path(__file__).parents[1] / "src" / "adaos" / "abi" / name).read_text(encoding="utf-8"))


def test_receiver_compatibility_finding_creates_signal_ticket_pending_action_and_dedups(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[dict] = []

    import adaos.services.pending_actions as pending_actions

    def _publish_pending_action(**kwargs):
        published.append(dict(kwargs))
        return {
            "id": "pa.compat.receiver",
            "kind": kwargs["kind"],
            "status": "pending",
            "created_at": 123.0,
            "domain_ref": kwargs["domain_ref"],
            "metadata": kwargs["metadata"],
        }

    monkeypatch.setattr(pending_actions, "publish_pending_action", _publish_pending_action)

    admission = stream_receiver_event_admission(
        (),
        {"type": "webio.stream.subscription.changed", "receiver": "legacy.panel"},
        "webio.stream.subscription.changed",
    )
    service = DevelopmentTicketService(state_dir=tmp_path)
    result = service.report_stream_receiver_compatibility_finding(
        skill_id="legacy_skill",
        admission=admission,
        topic="webio.stream.subscription.changed",
        publish_pending_action=True,
    )

    assert result["reported"] is True
    assert result["signal"]["schema"] == "adaos.development_signal.v1"
    assert result["signal"]["kind"] == "compatibility_finding"
    assert result["signal"]["metadata"]["code"] == "compat.stream_receiver_policy_missing"
    assert result["ticket"]["schema"] == "adaos.dev_ticket.v1"
    assert result["ticket"]["kind"] == "runtime_compatibility_debt"
    assert result["ticket"]["status"] == "waiting_for_user"
    assert result["pending_action_published"] is True
    assert published[0]["kind"] == COMPATIBILITY_PENDING_ACTION_KIND
    assert published[0]["response_topic"] == COMPATIBILITY_RESPONSE_TOPIC
    assert published[0]["domain_ref"]["ticket_id"] == result["ticket"]["ticket_id"]
    assert {item["id"] for item in published[0]["allowed_actions"]} == {
        "preview_evidence",
        "postpone",
        "open_builder",
        "start_autonomous_repair",
        "refuse",
    }

    duplicate = service.report_stream_receiver_compatibility_finding(
        skill_id="legacy_skill",
        admission=admission,
        topic="webio.stream.subscription.changed",
        publish_pending_action=True,
    )

    assert duplicate["signal_duplicate"] is True
    assert duplicate["ticket_duplicate"] is True
    assert duplicate["ticket"]["ticket_id"] == result["ticket"]["ticket_id"]
    assert duplicate["ticket"]["occurrence_count"] == 2
    assert len(published) == 1

    Draft202012Validator(_schema("development_signal.v1.schema.json")).validate(duplicate["signal"])
    Draft202012Validator(_schema("dev_ticket.v1.schema.json")).validate(duplicate["ticket"])


def test_compatibility_pending_action_response_creates_builder_repair(tmp_path: Path) -> None:
    admission = stream_receiver_event_admission(
        ("declared.other",),
        {"type": "webio.stream.subscription.changed", "receiver": "legacy.panel"},
        "webio.stream.subscription.changed",
    )
    tickets = DevelopmentTicketService(state_dir=tmp_path)
    report = tickets.report_stream_receiver_compatibility_finding(
        skill_id="legacy_skill",
        admission=admission,
        topic="webio.stream.subscription.changed",
    )
    repair_service = BuilderRepairService(state_dir=tmp_path)

    response = tickets.handle_compatibility_response(
        ticket_id=report["ticket"]["ticket_id"],
        response_action_id="start_autonomous_repair",
        pending_action_id="pa.compat.receiver",
        responder={"id": "user:owner"},
        repair_service=repair_service,
    )

    assert response["ticket"]["status"] == "in_builder"
    assert response["repair"]["signal_type"] == "guard"
    assert response["repair"]["project_id"] == "legacy_skill"
    assert response["repair"]["context"]["development_ticket"]["ticket_id"] == report["ticket"]["ticket_id"]
    assert response["repair"]["context"]["development_ticket"]["handoff_mode"] == "autonomous"
    assert response["repair"]["context"]["economic"]["subscription_resource"] == "codex.api.tokens"
    assert response["repair"]["context"]["economic"]["required_for_statuses"] == [
        "succeeded",
        "failed",
        "errored",
        "cancelled",
    ]
    assert response["repair"]["source_refs"][0] == {"type": "dev_ticket", "id": report["ticket"]["ticket_id"]}
    assert response["ticket"]["builder_refs"][0]["token_accounting"]["source_of_truth"] == (
        "adaos.root_mgmnt.codex_usage_event.v1"
    )
    assert tickets.get_signal(report["signal"]["signal_id"])["status"] == "repair_created"

    context = repair_service.task_context("legacy_skill")
    assert context["active_count"] == 1
    assert context["tasks"][0]["repair_id"] == response["repair"]["repair_id"]


def test_ticket_resolution_requires_evidence_and_closes_linked_repair(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_compatibility_finding(
        code="compat.stream_receiver_not_declared",
        summary="Skill legacy_skill lacks receiver declaration for legacy.panel",
        target_scope={"type": "skill", "id": "legacy_skill", "version": "1.0.0"},
        context={"receiver": "legacy.panel"},
        blocking=True,
    )
    repair_service = BuilderRepairService(state_dir=tmp_path)
    handoff = service.handle_compatibility_response(
        ticket_id=report["ticket"]["ticket_id"],
        response_action_id="open_builder",
        responder={"id": "user:owner"},
        repair_service=repair_service,
    )

    with pytest.raises(ValueError, match="evidence_refs"):
        service.record_resolution(
            report["ticket"]["ticket_id"],
            evidence_refs=[],
            actor="builder:test",
            repair_service=repair_service,
        )

    resolved = service.record_resolution(
        report["ticket"]["ticket_id"],
        evidence_refs=[
            {"type": "test", "id": "tests/test_skill_activation.py::receiver_contract", "status": "passed"},
            {"type": "activation", "id": "legacy_skill@1.0.1", "status": "passed"},
        ],
        actor="builder:test",
        resolved_by_version="legacy_skill@1.0.1",
        repair_service=repair_service,
    )

    assert resolved["ticket"]["status"] == "resolved"
    assert resolved["ticket"]["closure"]["resolved_by_version"] == "legacy_skill@1.0.1"
    assert resolved["ticket"]["status_group"] == "review"
    assert service.get_signal(report["signal"]["signal_id"])["status"] == "resolved_by_version"

    repair = next(item for item in repair_service.list() if item["repair_id"] == handoff["repair"]["repair_id"])
    assert repair["status"] == "resolved"
    assert repair["acceptance"]["capability_works"] is True
    assert repair["acceptance"]["regression_free"] is True

    with pytest.raises(ValueError, match="verified status"):
        service.close_ticket(
            report["ticket"]["ticket_id"],
            reason="closed",
            actor="validation:test",
        )

    verified = service.verify_ticket(
        report["ticket"]["ticket_id"],
        evidence_refs=[{"type": "runtime_guard", "id": "receiver_contract_after_fix", "status": "passed"}],
        actor="validation:test",
    )
    assert verified["ticket"]["status"] == "verified"
    assert verified["ticket"]["verification"]["evidence_refs"][0]["status"] == "passed"

    closed = service.close_ticket(
        report["ticket"]["ticket_id"],
        reason="closed",
        actor="validation:test",
    )
    assert closed["status"] == "closed"

    reopened = service.reopen_ticket(
        report["ticket"]["ticket_id"],
        actor="user:test",
        reason="regression reproduced",
        evidence_refs=[{"type": "trace", "id": "runtime.trace.2"}],
    )
    assert reopened["status"] == "in_progress"
    assert reopened["status_group"] == "work"
    assert reopened["history"][-1]["kind"] == "reopened"


def test_postponed_ticket_does_not_create_builder_repair(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_compatibility_finding(
        code="compat.stream_receiver_policy_missing",
        summary="Skill legacy_skill lacks receiver policy",
        target_scope={"type": "skill", "id": "legacy_skill"},
        context={"receiver": "legacy.panel"},
        blocking=False,
        run_policy="degrade",
    )
    repair_service = BuilderRepairService(state_dir=tmp_path)

    response = service.handle_compatibility_response(
        ticket_id=report["ticket"]["ticket_id"],
        response_action_id="postpone",
        responder={"id": "user:owner"},
        repair_service=repair_service,
    )

    assert response["ticket"]["status"] == "deferred"
    assert response["repair"] is None
    assert repair_service.list(project_id="legacy_skill") == []


def test_autonomous_repair_links_builder_automation_and_resolves_with_evidence(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeBuilderAutomation()
    signal = service.capture_signal(
        kind="development_request",
        summary="Tune Demo Metrics Resource Workbench CRUD controls",
        target_scope={
            "type": "skill",
            "id": "demo_metrics_skill",
            "source": "workspace",
            "component_ref": "skill:demo_metrics_skill",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:demo_metrics_skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="accepted",
        owner_area="skill",
        component_ref="skill:demo_metrics_skill",
    )["ticket"]

    result = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="user:owner",
        repair_service=repair_service,
        automation_service=automation,
        webspace_id="desktop",
    )

    assert result["started"] is True
    assert result["sync"]["resolved"] is True
    assert result["ticket"]["status"] == "resolved"
    assert automation.calls[0]["object_type"] == "skill"
    assert automation.calls[0]["object_id"] == "demo_metrics_skill"
    assert automation.calls[0]["links"]["development_ticket_id"] == ticket["ticket_id"]
    first_ref = result["ticket"]["builder_refs"][0]
    assert first_ref["automation_session_id"] == "automation.session.1"
    assert first_ref["automation_task_id"] == "factory.task.1"
    assert first_ref["token_usage"]["total_tokens"] == 150
    evidence_types = {ref["type"] for ref in result["ticket"]["closure"]["evidence_refs"]}
    assert {"builder_automation", "skill_factory_task", "builder_change", "test", "validation", "codex_usage"} <= evidence_types

    repair = next(item for item in repair_service.list(status="resolved") if item["repair_id"] == result["repair"]["repair_id"])
    assert repair["context"]["automation"]["session_id"] == "automation.session.1"
    assert repair["context"]["usage"]["billable_tokens"] == 130
    assert repair["context"]["cost_estimate"]["max_tokens"] == 200000
    assert repair["acceptance"]["evidence_refs"]

    service.reopen_ticket(
        ticket["ticket_id"],
        actor="user:owner",
        reason="follow-up request after review",
        evidence_refs=[{"type": "trace", "id": "review.followup"}],
    )
    follow_up = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="user:owner",
        repair_service=repair_service,
        automation_service=automation,
        webspace_id="desktop",
    )

    assert follow_up["ticket"]["status"] == "resolved"
    assert len(follow_up["ticket"]["builder_refs"]) == 2
    assert [ref["automation_task_id"] for ref in follow_up["ticket"]["builder_refs"]] == [
        "factory.task.1",
        "factory.task.2",
    ]


def test_close_ticket_maps_terminal_reason_to_ticket_and_signal_status(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    stale_report = service.report_compatibility_finding(
        code="compat.stream_receiver_policy_missing",
        summary="Skill legacy_skill lacks receiver policy",
        target_scope={"type": "skill", "id": "legacy_skill"},
        context={"receiver": "legacy.panel"},
        blocking=False,
        run_policy="degrade",
    )

    stale = service.close_ticket(
        stale_report["ticket"]["ticket_id"],
        reason="stale",
        actor="validation:test",
        evidence_refs=[{"type": "revalidation", "id": "receiver-contract", "status": "not-reproduced"}],
    )
    assert stale["status"] == "stale"
    assert service.get_signal(stale_report["signal"]["signal_id"])["status"] == "stale"

    duplicate_report = service.report_compatibility_finding(
        code="compat.stream_receiver_not_declared",
        summary="Skill legacy_skill lacks receiver declaration for legacy.panel",
        target_scope={"type": "skill", "id": "legacy_skill"},
        context={"receiver": "legacy.panel", "route": "stream"},
        blocking=True,
    )
    duplicate = service.close_ticket(
        duplicate_report["ticket"]["ticket_id"],
        reason="duplicate",
        actor="triage:test",
    )
    assert duplicate["status"] == "superseded"
    assert service.get_signal(duplicate_report["signal"]["signal_id"])["status"] == "superseded"


def test_core_capability_request_blocks_project_ticket_and_filters_by_owner_area(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    project_signal = service.capture_signal(
        kind="development_request",
        summary="Builder cannot implement modal repair with the current SDK",
        target_scope={
            "type": "modal",
            "id": "nlu_teacher_modal",
            "project_ref": "project:homepoint",
            "scenario_ref": "scenario:web_desktop",
            "component_ref": "modal:nlu_teacher_modal",
        },
        source="client_feedback",
        owner_area="project",
        component_ref="modal:nlu_teacher_modal",
    )["signal"]
    project_ticket = service.ensure_ticket_for_signal(
        project_signal,
        kind="development_request",
        status="accepted",
        owner_area="project",
        component_ref="modal:nlu_teacher_modal",
    )["ticket"]

    result = service.create_core_capability_request(
        summary="Builder needs a stable modal focus override API",
        component_ref="core:client",
        desired_contract="Expose a scoped modal focus handoff API for Dev Tickets overlays.",
        actor="builder:test",
        impact="blocker",
        motivation="Project repair cannot edit Dev Tickets when opened from a modal.",
        observed_limitation="Current client focus trap keeps focus inside the original modal.",
        rejected_workarounds=[{"summary": "Patch individual modals", "reason": "does not generalize"}],
        blocked_ticket_ids=[project_ticket["ticket_id"]],
        evidence_refs=[{"type": "trace", "id": "modal.focus.trap"}],
    )

    core_ticket = result["ticket"]
    blocked = result["blocked_tickets"][0]

    assert core_ticket["kind"] == "core_capability_request"
    assert core_ticket["owner_area"] == "core"
    assert core_ticket["component_ref"] == "core:client"
    assert core_ticket["status"] == "accepted"
    assert core_ticket["metadata"]["impact"] == "blocker"
    assert blocked["status"] == "waiting_for_core"
    assert blocked["status_group"] == "waiting"
    assert blocked["relation_refs"][0]["type"] == "blocked_by"
    assert blocked["relation_refs"][0]["ticket_id"] == core_ticket["ticket_id"]

    assert [item["ticket_id"] for item in service.list_tickets(owner_area="core")] == [core_ticket["ticket_id"]]
    assert [item["ticket_id"] for item in service.list_tickets(component_ref="modal:nlu_teacher_modal")] == [
        project_ticket["ticket_id"]
    ]


def test_sdk_understanding_signal_links_to_project_ticket(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    project_signal = service.capture_signal(
        kind="review_comment",
        summary="Builder result was rejected by user",
        target_scope={"type": "skill", "id": "media_center", "component_ref": "skill:media_center"},
        source="codex_review",
    )["signal"]
    project_ticket = service.ensure_ticket_for_signal(project_signal, kind="review_debt", status="accepted")["ticket"]

    result = service.record_sdk_understanding_signal(
        kind="sdk_application_failure",
        summary="Builder misunderstood the modal action contract",
        method_ref="ui.modal.actions",
        actor="builder:test",
        expected_behavior="Actions remain editable and separately grouped.",
        observed_behavior="Builder collapsed commands into the wrong action group.",
        diagnosis="sdk_doc_ambiguity",
        project_ticket_id=project_ticket["ticket_id"],
        evidence_refs=[{"type": "test", "id": "tests/test_media_center_modal.py"}],
    )

    ticket = result["ticket"]
    assert result["signal"]["kind"] == "sdk_application_failure"
    assert ticket["kind"] == "sdk_understanding"
    assert ticket["owner_area"] == "sdk"
    assert ticket["component_ref"] == "sdk:ui.modal.actions"
    assert ticket["relation_refs"][0]["ticket_id"] == project_ticket["ticket_id"]
    assert service.list_tickets(owner_area="sdk")[0]["ticket_id"] == ticket["ticket_id"]
