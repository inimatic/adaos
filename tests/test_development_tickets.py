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
        self.latest_links: dict = {}

    def start_from_execute(self, **kwargs):
        self.counter += 1
        self.calls.append(dict(kwargs))
        self.latest_links = dict(kwargs.get("links") or {})
        return self._payload(status="running", suffix=str(self.counter), links=self.latest_links)

    def status(self, *, object_type: str, object_id: str):
        suffix = str(self.counter or 1)
        return self._payload(
            status="completed",
            suffix=suffix,
            links=self.latest_links,
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
                "task": {
                    "task_id": task_id,
                    "status": "completed",
                    "result": result,
                    "realize_request": {"links": dict(links)},
                },
                "links": dict(links),
                "completion_readiness": {"ok": True, "checks": [{"id": "tests", "status": "passed"}]},
                "codex_usage_history": [
                    {
                        "task_id": f"factory.task.previous.{suffix}",
                        "status": "reported",
                        "accuracy": "provider_reported",
                        "root_event_id": f"codex.usage.previous.{suffix}",
                        "total_tokens": 25,
                    }
                ],
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


class _FakeResumableBuilderAutomation(_FakeBuilderAutomation):
    def __init__(self) -> None:
        super().__init__()
        self.resume_calls: list[dict] = []
        self.ticket_id = ""

    def status(self, *, object_type: str, object_id: str):
        return {
            "ok": True,
            "session": {
                "session_id": "automation.session.failed",
                "status": "failed",
                "links": {"development_ticket_id": self.ticket_id},
            },
            "automation": {
                "session_id": "automation.session.failed",
                "status": "failed",
                "terminal": True,
                "project": {"object_type": object_type, "object_id": object_id},
            },
        }

    def resume_failed_dev_ticket_repair(self, **kwargs):
        self.resume_calls.append(dict(kwargs))
        self.counter += 1
        self.latest_links = dict(kwargs.get("links") or {})
        return self._payload(
            status="running",
            suffix=f"resumed-{self.counter}",
            links=self.latest_links,
        )


class _FakeFollowupBuilderAutomation(_FakeBuilderAutomation):
    def __init__(self) -> None:
        super().__init__()
        self.followup_calls: list[dict] = []

    def status(self, *, object_type: str, object_id: str):
        result = self._payload(
            status="completed",
            suffix=str(self.counter or 1),
            links={"object_type": object_type, "object_id": object_id},
        )
        result["session"]["completion_readiness"]["aprobation"] = {"ok": True}
        return result

    def start_followup_dev_ticket_repair(self, **kwargs):
        self.followup_calls.append(dict(kwargs))
        self.counter += 1
        self.latest_links = dict(kwargs.get("links") or {})
        return self._payload(
            status="running",
            suffix=f"followup-{self.counter}",
            links=self.latest_links,
        )


class _FakePublishedBuilderAutomation(_FakeFollowupBuilderAutomation):
    def current_workflow_head(self, *, object_type: str, object_id: str):
        return {
            "schema": "adaos.builder.workflow_head.v1",
            "object_type": object_type,
            "object_id": object_id,
            "state": "published",
            "change_set_id": "CH-published",
            "change_set_status": "published",
        }


class _FakeFailingBuilderAutomation(_FakeBuilderAutomation):
    def status(self, *, object_type: str, object_id: str):
        suffix = str(self.counter or 1)
        return self._failed_payload(
            suffix=suffix,
            links=self.latest_links,
        )

    def _failed_payload(self, *, suffix: str, links: dict) -> dict:
        task_id = f"factory.task.{suffix}"
        session_id = f"automation.session.{suffix}"
        return {
            "ok": True,
            "automation": {
                "schema": "adaos.builder.automation_session_projection.v1",
                "session_id": session_id,
                "task_id": task_id,
                "status": "failed",
                "phase": "failed",
                "terminal": True,
                "busy": False,
                "change_id": f"change.{suffix}",
                "result_branch": f"builder/dev-ticket-{suffix}",
                "webspace_id": "desktop",
                "links": dict(links),
                "budget_usage": {"declared": {"max_tokens": 200000}, "observed": {}},
                "error": "codex timeout",
            },
            "session": {
                "session_id": session_id,
                "status": "failed",
                "current_task_id": task_id,
                "task": {
                    "task_id": task_id,
                    "status": "failed",
                    "failure": {"message": "codex timeout"},
                    "realize_request": {"links": dict(links)},
                },
                "links": dict(links),
                "completion_readiness": {"ok": False, "checks": [{"id": "codex", "status": "failed"}]},
                "codex_usage_accounting": {
                    "status": "unavailable",
                    "reason": "No provider usage was found in the terminal Codex journal.",
                    "total_tokens": None,
                },
            },
        }


class _FakeLaunchErrorBuilderAutomation(_FakeBuilderAutomation):
    def start_from_execute(self, **kwargs):
        self.counter += 1
        self.calls.append(dict(kwargs))
        raise ValueError("Builder Context Plan is insufficient for Automation")


def _schema(name: str) -> dict:
    return json.loads((Path(__file__).parents[1] / "src" / "adaos" / "abi" / name).read_text(encoding="utf-8"))


def _bounded_demo_ticket(
    service: DevelopmentTicketService,
    *,
    summary: str,
    target_files: list[str],
    acceptance: str,
    project_id: str = "demo_metrics",
) -> dict:
    signal = service.capture_signal(
        kind="development_request",
        summary=summary,
        target_scope={
            "type": "skill",
            "id": "demo_metrics_skill",
            "source": "dev",
            "component_ref": "skill:demo_metrics_skill",
            "project_ref": f"project:{project_id}",
            "project_id": project_id,
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:demo_metrics_skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "change_summary": summary,
                "target_files": target_files,
                "target_refs": [f"widget:{Path(target_files[0]).stem}"],
                "acceptance_checks": [acceptance],
                "max_changed_files": len(target_files),
                "requires_root_mcp": False,
            }
        },
    )["signal"]
    return service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="skill",
        component_ref="skill:demo_metrics_skill",
    )["ticket"]


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


def test_failed_artifact_activation_observation_creates_deduplicated_core_ticket(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    observation = {
        "observation_id": "observation-1",
        "status": "failed",
        "expected_lock_digest": "sha256:" + "a" * 64,
        "observed_lock_digest": "sha256:" + "b" * 64,
        "error": (
            "ActivationError: materialized package file size changed: "
            "scenario:builder:scenario.json"
        ),
    }

    result = service.report_artifact_activation_observation(observation)
    duplicate = service.report_artifact_activation_observation(
        {**observation, "observation_id": "observation-2"}
    )

    assert result["reported"] is True
    assert result["ticket"]["owner_area"] == "core"
    assert result["ticket"]["component_ref"] == "core:artifact-pipeline.workspace-lock"
    assert result["ticket"]["source"] == "artifact_activation_guard"
    assert result["ticket"]["metadata"]["affected_component_ref"] == "scenario:builder"
    assert result["ticket"]["evidence_refs"][0]["affected_component_ref"] == "scenario:builder"
    assert duplicate["ticket_duplicate"] is True
    assert duplicate["ticket"]["ticket_id"] == result["ticket"]["ticket_id"]
    assert duplicate["ticket"]["occurrence_count"] == 2


def test_passed_artifact_activation_observation_does_not_create_ticket(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)

    result = service.report_artifact_activation_observation(
        {"observation_id": "observation-ok", "status": "passed"}
    )

    assert result == {"ok": True, "reported": False, "reason": "passed"}
    assert service.list_tickets() == []


def test_publication_gate_failure_creates_linked_deduplicated_project_ticket(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="development_request",
        summary="Improve Demo Metrics",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
    )["signal"]
    original = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="resolved",
    )["ticket"]

    first = service.report_publication_gate_failure(
        component_type="skill",
        component_id="demo_metrics_skill",
        gate="tests",
        error="Candidate candidate.first failed test_resource_workbench",
        candidate_id="candidate.first",
        related_ticket_ids=[original["ticket_id"]],
    )
    duplicate = service.report_publication_gate_failure(
        component_type="skill",
        component_id="demo_metrics_skill",
        gate="tests",
        error="Candidate candidate.second failed test_resource_workbench",
        candidate_id="candidate.second",
        related_ticket_ids=[original["ticket_id"]],
    )

    assert first["ticket"]["ticket_id"] == duplicate["ticket"]["ticket_id"]
    assert duplicate["ticket_duplicate"] is True
    assert duplicate["ticket"]["occurrence_count"] == 2
    assert duplicate["ticket"]["kind"] == "runtime_failure"
    assert duplicate["ticket"]["owner_area"] == "skill"
    assert duplicate["ticket"]["blocking"] is True
    assert service.get_ticket(original["ticket_id"])["status"] == "resolved"
    assert any(
        ref.get("ticket_id") == duplicate["ticket"]["ticket_id"]
        for ref in service.get_ticket(original["ticket_id"])["relation_refs"]
    )
    closed = service.close_publication_gate_failures(
        component_type="skill",
        component_id="demo_metrics_skill",
        actor="builder.automation",
        evidence_refs=[
            {
                "type": "builder_trial",
                "id": "candidate.second",
                "status": "accepted",
                "decision": "accept",
            },
            {
                "type": "project_release",
                "id": "demo_metrics_skill@0.2.0",
                "status": "published",
            },
        ],
        resolved_by_version="0.2.0",
        resolved_by_overlay="candidate.second",
    )
    assert [item["status"] for item in closed] == ["closed"]
    assert service.get_ticket(duplicate["ticket"]["ticket_id"])["status"] == "closed"


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
    assert "verification" not in reopened
    assert "closure" not in reopened
    assert reopened["history"][-1]["previous_verification"]["kind"] == "verified"


def test_publication_required_ticket_verification_requires_accepted_release(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="development_request",
        summary="Publish the reviewed Demo Metrics changeset",
        target_scope={"type": "skill", "id": "demo_metrics_skill"},
        policy={"publication_required": True},
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="in_builder",
    )["ticket"]
    service.record_resolution(
        ticket["ticket_id"],
        evidence_refs=[{"type": "test", "id": "demo-metrics-tests", "status": "passed"}],
        actor="builder:test",
    )

    with pytest.raises(ValueError, match="accept the current changeset"):
        service.verify_ticket(
            ticket["ticket_id"],
            evidence_refs=[{"type": "test", "id": "human-review", "status": "passed"}],
            actor="browser",
        )

    verified = service.verify_ticket(
        ticket["ticket_id"],
        evidence_refs=[
            {
                "type": "builder_trial",
                "id": "candidate.demo.1",
                "status": "accepted",
                "decision": "accept",
            },
            {
                "type": "project_release",
                "id": "demo_metrics@0.2.0",
                "status": "published",
            },
        ],
        actor="builder:publication",
    )

    assert verified["ticket"]["status"] == "verified"


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


def test_builder_repair_requalification_is_bounded_and_audited(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="development_request",
        summary="Rename a Demo Metrics action",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_files": ["skills/demo_metrics_skill/missing_test.py"],
            }
        },
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
    )["ticket"]

    updated = service.requalify_builder_repair(
        ticket["ticket_id"],
        actor="builder:qualifier",
        reason="The first pass discovered the focused test in a different file.",
        expected_updated_at=ticket["updated_at"],
        builder_repair={
            "profile": "surgical_ui",
            "change_summary": "Rename only the selected action label.",
            "target_object_type": "skill",
            "target_object_id": "demo_metrics_skill",
            "target_files": [
                "skills/demo_metrics_skill/webui.json",
                "skills/demo_metrics_skill/tests/test_resource_workbench.py",
            ],
            "target_refs": [
                "ydoc_defaults.data/demo_metrics/summary.buttons[id=open-operations]"
            ],
            "acceptance_checks": ["The action id and order are unchanged."],
            "max_changed_files": 2,
            "requires_root_mcp": False,
            "structured_edits": {
                "schema": "adaos.builder.structured_edit_set.v1",
                "operations": [
                    {
                        "id": "rename-action",
                        "op": "json_replace",
                        "path": "skills/demo_metrics_skill/webui.json",
                        "pointer": "/widgets/0/title",
                        "expected": "Metrics",
                        "value": "Live metrics",
                    }
                ],
            },
        },
    )

    assert updated["metadata"]["builder_repair"]["target_files"][-1].endswith(
        "test_resource_workbench.py"
    )
    assert updated["metadata"]["builder_repair"]["target_object_type"] == "skill"
    assert updated["metadata"]["builder_repair"]["target_object_id"] == "demo_metrics_skill"
    assert updated["metadata"]["builder_repair"]["structured_edits"]["operations"][0]["id"] == (
        "rename-action"
    )
    history = updated["history"][-1]
    assert history["kind"] == "builder_repair_requalified"
    assert history["previous_builder_repair"]["target_files"] == [
        "skills/demo_metrics_skill/missing_test.py"
    ]
    assert history["builder_repair"] == updated["metadata"]["builder_repair"]

    with pytest.raises(ValueError, match="unsafe paths"):
        service.requalify_builder_repair(
            ticket["ticket_id"],
            actor="builder:qualifier",
            reason="invalid envelope",
            builder_repair={
                "profile": "surgical_ui",
                "target_files": ["../outside.py"],
                "max_changed_files": 1,
            },
        )

    with pytest.raises(ValueError, match="outside target_files"):
        service.requalify_builder_repair(
            ticket["ticket_id"],
            actor="builder:qualifier",
            reason="invalid structured edit",
            builder_repair={
                "profile": "surgical_ui",
                "target_files": ["skills/demo_metrics_skill/webui.json"],
                "max_changed_files": 1,
                "structured_edits": {
                    "schema": "adaos.builder.structured_edit_set.v1",
                    "operations": [
                        {
                            "op": "replace_text",
                            "path": "skills/other/handlers/main.py",
                            "old": "before",
                            "new": "after",
                        }
                    ],
                },
            },
        )


def test_qualified_modal_ticket_targets_its_owner_skill(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeBuilderAutomation()
    signal = service.capture_signal(
        kind="development_request",
        summary="Open Subscription details only once",
        target_scope={
            "type": "modal",
            "id": "subscription_status_modal",
            "source": "dev",
            "component_ref": "modal:subscription_status_modal",
        },
        source="client_feedback",
        owner_area="project",
        component_ref="modal:subscription_status_modal",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_object_type": "skill",
                "target_object_id": "subscription_status_skill",
                "target_files": ["skills/subscription_status_skill/webui.json"],
                "acceptance_checks": ["One Details click opens one modal."],
                "max_changed_files": 1,
            }
        },
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="project",
        component_ref="modal:subscription_status_modal",
    )["ticket"]

    result = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        webspace_id="desktop",
    )

    assert result["started"] is True
    assert automation.calls[0]["object_type"] == "skill"
    assert automation.calls[0]["object_id"] == "subscription_status_skill"
    assert result["repair"]["project_id"] == "subscription_status_skill"


def test_builder_package_requires_qualification_before_spending_tokens(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="development_request",
        summary="Improve a Demo Metrics control",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="skill",
    )["ticket"]

    result = service.plan_builder_package(
        [ticket["ticket_id"]],
        actor="builder:qualifier",
        repair_service=repair_service,
    )

    assert result["ready"] is False
    assert result["status"] == "qualification_required"
    assert result["unqualified_ticket_ids"] == [ticket["ticket_id"]]
    assert result["repair"] is None
    assert repair_service.list() == []
    assert service.get_ticket(ticket["ticket_id"])["builder_refs"] == []


def test_builder_package_uses_one_work_item_budget_and_automation(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeBuilderAutomation()
    tickets = [
        _bounded_demo_ticket(
            service,
            summary="Rename the Demo Metrics table heading",
            target_files=["skills/demo_metrics_skill/webui.json"],
            acceptance="The table heading is Live metrics.",
        ),
        _bounded_demo_ticket(
            service,
            summary="Move the Demo Metrics refresh action",
            target_files=[
                "skills/demo_metrics_skill/webui.json",
                "skills/demo_metrics_skill/tests/test_resource_workbench.py",
            ],
            acceptance="Refresh appears before Create note.",
        ),
    ]
    ticket_ids = [ticket["ticket_id"] for ticket in tickets]

    planned = service.plan_builder_package(
        ticket_ids,
        actor="builder:qualifier",
        repair_service=repair_service,
    )

    assert planned["ready"] is True
    assert planned["project_ref"] == "project:demo_metrics"
    assert planned["project_id"] == "demo_metrics"
    assert planned["execution_budget"]["max_tokens"] == 60000
    assert planned["repair_hints"]["profile"] == "project_batch"
    assert planned["repair_hints"]["target_files"] == [
        "skills/demo_metrics_skill/webui.json",
        "skills/demo_metrics_skill/tests/test_resource_workbench.py",
    ]
    assert len(repair_service.list(package_id=planned["package_id"])) == 1
    assert planned["rollup"]["ticket_ids"] == sorted(ticket_ids)
    assert {
        service.get_ticket(ticket_id)["builder_refs"][0]["repair_id"]
        for ticket_id in ticket_ids
    } == {planned["repair"]["repair_id"]}

    started = service.start_autonomous_package(
        planned["package_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
    )

    assert started["started"] is True
    assert len(automation.calls) == 1
    assert automation.calls[0]["execution_budget"]["max_tokens"] == 60000
    assert automation.calls[0]["links"]["development_ticket_ids"] == ticket_ids
    assert automation.calls[0]["links"]["development_ticket_project_ref"] == "project:demo_metrics"
    assert automation.calls[0]["links"]["development_ticket_project_id"] == "demo_metrics"
    brief = json.loads(automation.calls[0]["implementation_brief"])
    assert brief["ticket_ids"] == ticket_ids
    assert brief["policy"]["one_release_for_package"] is True
    assert [item["ticket_id"] for item in brief["issues"]] == ticket_ids
    assert all(
        service.get_ticket(ticket_id)["builder_refs"][0]["automation_task_id"]
        == "factory.task.1"
        for ticket_id in ticket_ids
    )
    assert started["rollup"]["total_tokens"] == 150


def test_builder_package_rejects_same_skill_from_different_projects(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    tickets = [
        _bounded_demo_ticket(
            service,
            summary="Rename the shared skill for project one",
            target_files=["skills/demo_metrics_skill/webui.json"],
            acceptance="Project one sees the new heading.",
            project_id="project_one",
        ),
        _bounded_demo_ticket(
            service,
            summary="Move the shared skill control for project two",
            target_files=["skills/demo_metrics_skill/webui.json"],
            acceptance="Project two sees the moved control.",
            project_id="project_two",
        ),
    ]

    with pytest.raises(ValueError, match="must belong to one project"):
        service.plan_builder_package(
            [ticket["ticket_id"] for ticket in tickets],
            actor="builder:qualifier",
            repair_service=repair_service,
        )

    assert repair_service.list() == []


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
            "source": "dev",
            "component_ref": "skill:demo_metrics_skill",
            "project_ref": "project:demo_metrics",
            "project_id": "demo_metrics",
        },
        source="client_feedback",
        owner_area="skill",
        component_ref="skill:demo_metrics_skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "change_summary": "Rename the visible Metrics table heading.",
                "target_files": [
                    "skills/demo_metrics_skill/webui.json",
                    "skills/demo_metrics_skill/tests/test_resource_workbench.py",
                    "../outside.py",
                ],
                "target_refs": ["widget:metrics-table.title"],
                "acceptance_checks": ["The heading is Live metrics."],
                "max_changed_files": 2,
                "requires_root_mcp": False,
            }
        },
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
    assert automation.calls[0]["links"]["development_ticket_project_ref"] == "project:demo_metrics"
    assert automation.calls[0]["links"]["development_ticket_project_id"] == "demo_metrics"
    brief = json.loads(automation.calls[0]["implementation_brief"])
    assert brief["policy"]["publication_required"] is True
    assert brief["repair_hints"]["profile"] == "surgical_ui"
    assert brief["repair_hints"]["target_files"] == [
        "skills/demo_metrics_skill/webui.json",
        "skills/demo_metrics_skill/tests/test_resource_workbench.py",
    ]
    assert brief["repair_hints"]["requires_root_mcp"] is False
    first_ref = result["ticket"]["builder_refs"][0]
    assert first_ref["automation_session_id"] == "automation.session.1"
    assert first_ref["automation_task_id"] == "factory.task.1"
    assert first_ref["token_usage"]["total_tokens"] == 150
    evidence_types = {ref["type"] for ref in result["ticket"]["closure"]["evidence_refs"]}
    assert {"builder_automation", "skill_factory_task", "builder_change", "test", "validation", "codex_usage"} <= evidence_types
    usage_refs = [
        ref
        for ref in result["ticket"]["closure"]["evidence_refs"]
        if ref["type"] == "codex_usage"
    ]
    assert {ref["id"] for ref in usage_refs} == {"codex.usage.1"}

    repair = next(item for item in repair_service.list(status="resolved") if item["repair_id"] == result["repair"]["repair_id"])
    assert repair["context"]["automation"]["session_id"] == "automation.session.1"
    assert repair["context"]["usage"]["billable_tokens"] == 130
    assert repair["context"]["cost_estimate"]["max_tokens"] == 200000
    assert repair["acceptance"]["evidence_refs"]

    unrelated_usage = {
        "type": "codex_usage",
        "id": "codex.usage.unrelated",
        "task_id": "factory.task.unrelated",
        "repair_id": result["repair"]["repair_id"],
        "total_tokens": 999,
    }
    polluted_closure = dict(result["ticket"]["closure"])
    polluted_closure["evidence_refs"] = [
        *polluted_closure["evidence_refs"],
        unrelated_usage,
    ]
    service._update_ticket(
        ticket["ticket_id"],
        evidence_refs=[*result["ticket"]["evidence_refs"], unrelated_usage],
        closure=polluted_closure,
    )

    polled = service.sync_builder_repair(
        ticket["ticket_id"],
        actor="builder:poller",
        repair_id=result["repair"]["repair_id"],
        repair_service=repair_service,
        automation_result=automation.status(
            object_type="skill",
            object_id="demo_metrics_skill",
        ),
    )

    assert polled["ticket"]["status"] == "resolved"
    assert polled["repair"]["work_status"] == "completed"
    assert "codex.usage.unrelated" not in {
        ref["id"]
        for ref in polled["ticket"]["closure"]["evidence_refs"]
        if ref["type"] == "codex_usage"
    }
    assert sum(
        item["kind"] == "builder_evidence_reconciled"
        for item in polled["ticket"]["history"]
    ) == 1

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


def test_autonomous_repair_joins_completed_builder_trial_as_followup(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeFollowupBuilderAutomation()
    signal = service.capture_signal(
        kind="development_request",
        summary="Rename the selected metric trend heading",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_files": ["skills/demo_metrics_skill/webui.json"],
                "target_refs": ["widget:metric-trend.title"],
                "acceptance_checks": ["The heading is Selected metric trend."],
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

    result = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        webspace_id="desktop",
    )

    assert result["started"] is True
    assert automation.calls == []
    assert len(automation.followup_calls) == 1
    assert automation.followup_calls[0]["links"]["development_ticket_id"] == ticket["ticket_id"]


def test_autonomous_repair_starts_successor_after_published_workflow(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakePublishedBuilderAutomation()
    signal = service.capture_signal(
        kind="development_request",
        summary="Rename the next selected metric trend heading",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="skill",
    )["ticket"]

    result = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        webspace_id="desktop",
    )

    assert result["started"] is True
    assert len(automation.calls) == 1
    assert automation.followup_calls == []
    assert automation.calls[0]["links"]["development_ticket_id"] == ticket["ticket_id"]


def test_failed_autonomous_repair_returns_ticket_to_builder_queue_with_evidence(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeFailingBuilderAutomation()
    signal = service.capture_signal(
        kind="development_request",
        summary="Tune Demo Metrics Resource Workbench marker",
        target_scope={
            "type": "skill",
            "id": "demo_metrics_skill",
            "source": "dev",
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

    assert result["sync"]["resolved"] is False
    assert result["ticket"]["status"] == "ready_for_builder"
    builder_ref = result["ticket"]["builder_refs"][0]
    assert builder_ref["status"] == "failed"
    assert builder_ref["automation_status"] == "failed"
    evidence_types = {ref["type"] for ref in result["ticket"]["evidence_refs"]}
    assert {"builder_automation", "skill_factory_task", "builder_change", "validation"} <= evidence_types
    assert any(item["kind"] == "builder_automation_failed" for item in result["ticket"]["history"])
    repair = repair_service.list(project_id="demo_metrics_skill")[0]
    assert repair["status"] == "in_progress"
    assert repair["context"]["automation"]["status"] == "failed"
    assert repair["context"]["usage"]["receipt_status"] == "unavailable"


def test_autonomous_launch_error_releases_ticket_from_in_builder(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeLaunchErrorBuilderAutomation()
    ticket = _bounded_demo_ticket(
        service,
        summary="Rename the Demo Metrics heading",
        target_files=["skills/demo_metrics_skill/webui.json"],
        acceptance="The heading is renamed.",
    )

    with pytest.raises(ValueError, match="Context Plan is insufficient"):
        service.start_autonomous_repair(
            ticket["ticket_id"],
            actor="builder:automation",
            repair_service=repair_service,
            automation_service=automation,
            webspace_id="desktop",
        )

    updated = service.get_ticket(ticket["ticket_id"])
    assert updated is not None
    assert updated["status"] == "ready_for_builder"
    builder_ref = updated["builder_refs"][0]
    assert builder_ref["status"] == "failed"
    assert builder_ref["work_status"] == "failed"
    assert builder_ref["automation_status"] == "start_failed"
    assert any(
        item["kind"] == "builder_automation_start_failed"
        for item in updated["history"]
    )
    repair = repair_service.list(project_id="demo_metrics_skill")[0]
    assert repair["work_status"] == "failed"


def test_builder_sync_rejects_completed_result_from_another_ticket_repair(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    ticket = _bounded_demo_ticket(
        service,
        summary="Rename the current Demo Metrics heading",
        target_files=["skills/demo_metrics_skill/webui.json"],
        acceptance="The current heading is renamed.",
    )
    handoff = service.handoff_ticket(
        ticket["ticket_id"],
        mode="autonomous",
        repair_service=repair_service,
        actor="user:owner",
    )
    stale = _FakeBuilderAutomation()._payload(
        status="completed",
        suffix="stale",
        links={
            "development_ticket_id": "dticket.previous",
            "builder_repair_id": "repair.previous",
        },
    )

    result = service.sync_builder_repair(
        ticket["ticket_id"],
        repair_id=handoff["repair"]["repair_id"],
        actor="builder.automation",
        repair_service=repair_service,
        automation_result=stale,
    )

    assert result["synchronized"] is False
    assert result["resolved"] is False
    assert result["reason"] == "automation_correlation_mismatch"
    assert result["correlation"]["observed_ticket_ids"] == ["dticket.previous"]
    current = service.get_ticket(ticket["ticket_id"])
    assert current["status"] == "in_builder"
    assert current.get("closure") is None
    assert len(current["builder_refs"]) == 1


def test_failed_autonomous_repair_resumes_same_ticket_session(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeResumableBuilderAutomation()
    signal = service.capture_signal(
        kind="development_request",
        summary="Resume a bounded Demo Metrics repair",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="skill",
    )["ticket"]
    automation.ticket_id = ticket["ticket_id"]

    result = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        webspace_id="desktop",
    )

    assert result["started"] is True
    assert len(automation.resume_calls) == 1
    assert automation.calls == []
    assert automation.resume_calls[0]["links"]["development_ticket_id"] == ticket["ticket_id"]


def test_builder_refs_preserve_multiple_automation_tasks_for_one_repair(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="development_request",
        summary="Repair the same component through bounded Builder iterations",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
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
    automation = _FakeBuilderAutomation()

    for suffix, status in (("2", "completed"), ("1", "failed")):
        automation_result = automation._payload(status=status, suffix=suffix, links={})
        automation_result["session"]["task_history"] = ["factory.task.1", "factory.task.2"]
        service._link_builder_automation(
            ticket["ticket_id"],
            repair_id="repair.shared",
            automation=automation_result,
            actor="builder.automation",
        )

    linked = service.get_ticket(ticket["ticket_id"])
    assert [ref["automation_task_id"] for ref in linked["builder_refs"]] == [
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


def test_core_release_fanout_unblocks_project_only_after_verification(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    project_signal = service.capture_signal(
        kind="development_request",
        summary="Use a missing public SDK operation",
        target_scope={"type": "skill", "id": "demo_metrics_skill"},
        source="client_feedback",
    )["signal"]
    project_ticket = service.ensure_ticket_for_signal(
        project_signal,
        kind="development_request",
        status="accepted",
    )["ticket"]
    created = service.create_core_capability_request(
        summary="Expose the missing public SDK operation",
        component_ref="core:sdk",
        desired_contract="sdk.demo_metrics.replace_source",
        actor="builder:test",
        impact="blocker",
        blocked_ticket_ids=[project_ticket["ticket_id"]],
        evidence_refs=[{"type": "trace", "id": "sdk-miss"}],
    )
    core_ticket_id = created["ticket"]["ticket_id"]
    created_event = next(
        event
        for event in service.list_lifecycle_events(owner_area="core")
        if event["semantic_type"] == "core_ticket.created"
    )

    with pytest.raises(ValueError, match="released lifecycle transition"):
        service.record_resolution(
            core_ticket_id,
            evidence_refs=[{"type": "test", "id": "core-test"}],
            actor="core:maintainer",
        )

    with pytest.raises(ValueError, match="blocked by unresolved Core Dev Tickets"):
        service.record_resolution(
            project_ticket["ticket_id"],
            evidence_refs=[{"type": "test", "id": "project-test"}],
            actor="builder:test",
        )

    released = service.transition_core_ticket(
        core_ticket_id,
        transition="released",
        actor="core:maintainer",
        evidence_refs=[{"type": "release", "id": "adaos@1.2.3"}],
        release_ref={"project_id": "adaos", "version": "1.2.3", "digest": "sha256:release"},
        publish_pending_actions=False,
    )
    assert released["ticket"]["status"] == "resolved"
    assert service.get_ticket(project_ticket["ticket_id"])["status"] == "waiting_for_core"

    verified = service.transition_core_ticket(
        core_ticket_id,
        transition="verified",
        actor="core:evaluator",
        evidence_refs=[{"type": "test", "id": "sdk-contract-test"}],
        notes="Public contract verified on the target subnet.",
        publish_pending_actions=False,
    )
    unblocked = service.get_ticket(project_ticket["ticket_id"])
    resolved = service.record_resolution(
        project_ticket["ticket_id"],
        evidence_refs=[{"type": "test", "id": "project-test"}],
        actor="builder:test",
        resolved_by_version="demo_metrics@0.2.0",
    )
    events = service.list_lifecycle_events(owner_area="core")

    assert verified["ticket"]["status"] == "verified"
    assert unblocked["status"] == "ready_for_builder"
    assert resolved["ticket"]["status"] == "resolved"
    assert [event["semantic_type"] for event in events][-3:] == [
        "core_ticket.created",
        "core_ticket.released",
        "core_ticket.verified",
    ]
    assert events[-3]["integrity"]["digest"] == created_event["integrity"]["digest"]
    assert events[-3]["status"] == "accepted"
    Draft202012Validator(_schema("dev_ticket.lifecycle_event.v1.schema.json")).validate(events[-1])


def test_generic_core_verify_and_reopen_use_core_lifecycle(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    created = service.create_core_capability_request(
        summary="Expose stable demo source selection",
        component_ref="core:sdk",
        desired_contract="sdk.demo.select_source",
        actor="builder:test",
        impact="generalization",
        evidence_refs=[{"type": "trace", "id": "sdk-gap"}],
    )
    ticket_id = created["ticket"]["ticket_id"]
    service.transition_core_ticket(
        ticket_id,
        transition="accepted",
        actor="core:maintainer",
    )
    service.transition_core_ticket(
        ticket_id,
        transition="released",
        actor="core:maintainer",
        evidence_refs=[{"type": "release", "id": "adaos@1.2.4"}],
        release_ref={"project_id": "adaos", "version": "1.2.4"},
        publish_pending_actions=False,
    )

    verified = service.verify_ticket(
        ticket_id,
        evidence_refs=[{"type": "test", "id": "sdk-contract"}],
        actor="core:evaluator",
    )
    reopened = service.reopen_ticket(
        ticket_id,
        actor="core:evaluator",
        reason="Regression found on another node",
    )

    assert verified["event"]["semantic_type"] == "core_ticket.verified"
    assert verified["ticket"]["status"] == "verified"
    assert reopened["status"] == "accepted"
    assert reopened["metadata"]["core_lifecycle"]["stage"] == "reopened"


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
