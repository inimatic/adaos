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
    assert response["repair"]["source_refs"][0] == {"type": "dev_ticket", "id": report["ticket"]["ticket_id"]}
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
    assert service.get_signal(report["signal"]["signal_id"])["status"] == "resolved_by_version"

    repair = next(item for item in repair_service.list() if item["repair_id"] == handoff["repair"]["repair_id"])
    assert repair["status"] == "resolved"
    assert repair["acceptance"]["capability_works"] is True
    assert repair["acceptance"]["regression_free"] is True


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
