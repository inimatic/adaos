from __future__ import annotations

import asyncio
import hashlib
import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from adaos.services.builder.repair import BuilderRepairService
from adaos.services.builder.workspace import BuilderWorkspaceService
from adaos.services import development_tickets as development_tickets_module
from adaos.services.development_tickets import (
    COMPATIBILITY_PENDING_ACTION_KIND,
    DevelopmentTicketService,
    PUBLICATION_PERMISSION_PENDING_ACTION_KIND,
    development_source_options,
)
from adaos.services.skill.activation import stream_receiver_event_admission


def test_prototype_ready_is_bound_to_current_package_link() -> None:
    assert (
        development_tickets_module._prototype_ready_for_package(
            "automation_ready", None
        )
        is False
    )
    assert (
        development_tickets_module._prototype_ready_for_package(
            "automation_ready", {"session_id": "builder.session.current"}
        )
        is True
    )


def test_failed_automation_resume_requires_same_accepted_prototype() -> None:
    old_session = {
        "prototype_acceptance": {"revision": "007"},
        "source_prototype_version": "UI 006",
    }
    current_session = {
        "prototype_acceptance": {"revision": "007"},
        "source_prototype_version": "UI 007",
    }

    assert (
        development_tickets_module._automation_session_matches_prototype(
            old_session,
            "007",
        )
        is False
    )
    assert (
        development_tickets_module._automation_session_matches_prototype(
            current_session,
            "007",
        )
        is True
    )
    assert (
        development_tickets_module._prototype_ready_for_package(
            "prototype_editing", {"session_id": "builder.session.current"}
        )
        is False
    )
    assert (
        development_tickets_module._prototype_ready_for_package(
            "automation_ready",
            {"session_id": "builder.session.current", "revision": "006"},
            accepted_revision="007",
        )
        is False
    )
    assert (
        development_tickets_module._prototype_ready_for_package(
            "automation_ready",
            {"session_id": "builder.session.current", "revision": "007"},
            accepted_revision="007",
        )
        is True
    )


def test_source_preconditions_resolve_exact_companion_artifact_paths(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "dev" / "node"
    scenario_root = workspace / "scenarios" / "manager"
    skill_file = workspace / "skills" / "manager_skill" / "handlers" / "main.py"
    scenario_root.mkdir(parents=True)
    skill_file.parent.mkdir(parents=True)
    webui = scenario_root / "webui.json"
    webui.write_text("{}\n", encoding="utf-8")
    skill_file.write_text("def query():\n    return []\n", encoding="utf-8")

    def condition(path: str, absolute: Path) -> dict[str, object]:
        payload = absolute.read_bytes()
        return {
            "path": path,
            "sha256": "sha256:" + hashlib.sha256(payload).hexdigest(),
            "size": len(payload),
        }

    result = development_tickets_module._validate_repair_source_preconditions(
        {
            "source_preconditions": [
                condition("scenarios/manager/webui.json", webui),
                condition(
                    "skills/manager_skill/handlers/main.py",
                    skill_file,
                ),
            ]
        },
        development_source={"dev_source_path": str(scenario_root)},
        target={"object_type": "scenario", "object_id": "manager"},
    )

    assert result["ok"] is True
    assert [item["status"] for item in result["checks"]] == [
        "matched",
        "matched",
    ]


def test_state_read_cache_is_copy_safe_and_invalidated_on_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    service._write(
        {
            "schema": development_tickets_module.STATE_SCHEMA,
            "signals": {},
            "tickets": {},
            "command_receipts": {},
        }
    )
    original_read_text = Path.read_text
    state_reads = 0

    def counted_read_text(path: Path, *args, **kwargs):
        nonlocal state_reads
        if path == service.state_path:
            state_reads += 1
        return original_read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", counted_read_text)
    first = service._read()
    first["tickets"]["caller-mutation"] = {"status": "open"}
    second = service._read()

    assert state_reads == 1
    assert "caller-mutation" not in second["tickets"]

    service._write(second)
    service._read()
    # The write performs one explicit optimistic-revision read; the following
    # read must perform another because the cache was invalidated.
    assert state_reads == 3


def test_public_ticket_reads_detach_records_from_cached_snapshot(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    state = {
        "schema": development_tickets_module.STATE_SCHEMA,
        "signals": {},
        "tickets": {
            "dticket.cached": {
                "schema": development_tickets_module.DEV_TICKET_SCHEMA,
                "ticket_id": "dticket.cached",
                "revision": 1,
                "kind": "development_request",
                "status": "proposed",
                "summary": "Cached ticket",
                "target_scope": {"type": "scenario", "id": "applications"},
                "metadata": {"nested": {"value": "original"}},
            }
        },
        "command_receipts": {},
    }
    service._write(state)

    listed = service.list_tickets()
    listed[0]["metadata"]["nested"]["value"] = "mutated"
    summarized = service.list_tickets(projection="summary")
    summarized[0]["target_scope"]["id"] = "mutated"
    fetched = service.get_ticket("dticket.cached")

    assert fetched is not None
    assert fetched["metadata"]["nested"]["value"] == "original"
    assert fetched["target_scope"]["id"] == "applications"
    assert "metadata" not in summarized[0]


def test_default_builder_prototype_submitter_uses_shared_conversation_packet(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, object]] = []

    class _Manager:
        def run_tool(self, skill_id, tool_name, params, *, timeout):
            calls.append(
                {
                    "skill_id": skill_id,
                    "tool_name": tool_name,
                    "params": params,
                    "timeout": timeout,
                }
            )
            return {"ok": True, "status": "prototype_review"}

    monkeypatch.setattr(
        development_tickets_module,
        "_builder_skill_manager",
        lambda: _Manager(),
    )

    result = development_tickets_module._default_builder_prototype_submitter(
        instruction="Rename Board to Dashboard.",
        object_id="flowboard",
        project_id="flowboard",
        package_id="bpackage.test",
        repair_id="repair.test",
        ticket_ids=["dticket.test"],
        webspace_id="desktop-dev",
        conversation_id="conversation.test",
    )

    assert result["ok"] is True
    params = calls[0]["params"]
    assert isinstance(params, dict)
    packet = params["conversation_context"]
    assert packet["schema"] == "adaos.context.packet.v1"
    assert packet["conversation_id"] == "conversation.test"
    assert packet["messages"] == []
    assert packet["diagnostics"]["fallbacks"] == [
        "development_ticket:dticket.test",
        "builder_package:bpackage.test",
        "builder_repair:repair.test",
    ]
    assert params["instruction"] == "Rename Board to Dashboard."
    assert params["_meta"]["builder_llm_async"] is False
    assert calls[0]["timeout"] == 900


def test_default_prototype_submitter_reconciles_failed_automation_before_recording_revision(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from adaos.sdk.builder import workflow as builder_workflow

    revision_path = tmp_path / "007.json"
    revision_path.write_text(
        json.dumps(
            {
                "change_id": "builder_change.test",
                "patch": {
                    "prototype_resource": {"webui_digest": "sha256:" + "a" * 64}
                },
            }
        ),
        encoding="utf-8",
    )
    state = {
        "generation": 10,
        "active_phase": "automation",
        "automation": {"status": "failed"},
        "prototype": {"head_revision": "006"},
        "governed": {"state": "prototype_editing"},
    }
    transitions: list[tuple[str, dict[str, object]]] = []

    def transition(_kind, _object_id, action, **kwargs):
        transitions.append((action, kwargs))
        state["generation"] += 1
        if action == "prototype_acceptance_invalidated":
            state["active_phase"] = "prototype"
            state["automation"] = {"status": "not_started"}
        elif action == "prototype_revision_recorded":
            state["prototype"] = {"head_revision": kwargs["metadata"]["revision"]}
        return {"workflow": dict(state)}

    class _Manager:
        def run_tool(self, *_args, **_kwargs):
            return {
                "ok": True,
                "ui_revision": {"revision": "007", "path": str(revision_path)},
            }

    monkeypatch.setattr(builder_workflow, "get_state", lambda *_args: dict(state))
    monkeypatch.setattr(builder_workflow, "transition", transition)
    monkeypatch.setattr(
        development_tickets_module, "_builder_skill_manager", lambda: _Manager()
    )

    result = development_tickets_module._default_builder_prototype_submitter(
        instruction="Add typed details.",
        object_id="automation_manager",
        project_id="automation_manager",
        package_id="bpackage.test",
        repair_id="repair.test",
        ticket_ids=["dticket.test"],
        webspace_id="desktop",
        conversation_id="conversation.test",
    )

    assert result["ok"] is True
    assert [action for action, _ in transitions] == [
        "prototype_acceptance_invalidated",
        "prototype_revision_recorded",
    ]
    assert state["active_phase"] == "prototype"
    assert state["prototype"]["head_revision"] == "007"
    assert transitions[1][1]["metadata"]["prototype_acceptance_required"] is True


def test_autonomous_repair_brief_excludes_historical_builder_noise() -> None:
    ticket = {
        "ticket_id": "dticket.compact",
        "revision": 9,
        "kind": "development_request",
        "summary": "Rename one visible heading.",
        "target_scope": {"type": "skill", "id": "demo_metrics_skill"},
        "owner_area": "skill",
        "component_ref": "skill:demo_metrics_skill.heading",
        "policy": {"publication_required": True},
        "metadata": {
            "builder_repair": {
                "profile": "surgical_ui",
                "target_files": ["skills/demo_metrics_skill/webui.json"],
                "target_refs": ["view:demo.heading"],
                "acceptance_checks": ["The heading is concise."],
                "max_changed_files": 1,
            },
            "historical_builder_payload": "x" * 100_000,
        },
        "evidence_refs": [
            {
                "type": "builder_automation",
                "id": f"automation.{index}",
                "status": "completed",
                "payload": "x" * 10_000,
            }
            for index in range(20)
        ]
        + [
            {
                "type": "screenshot",
                "id": "artifact.screenshot",
                "digest": "sha256:screenshot",
            }
        ],
    }

    brief_text = development_tickets_module._autonomous_repair_brief(
        ticket,
        {"repair_id": "repair.compact"},
        target={"object_type": "skill", "object_id": "demo_metrics_skill"},
    )
    brief = json.loads(brief_text)

    assert len(brief_text.encode("utf-8")) < 6_000
    assert "metadata" not in brief
    assert "evidence_refs" not in brief
    assert brief["input_evidence_refs"] == [
        {
            "type": "screenshot",
            "id": "artifact.screenshot",
            "digest": "sha256:screenshot",
        }
    ]


def test_publication_gate_lineage_requires_authoritative_failed_task_evidence() -> None:
    ticket = {
        "source": "builder_publication_gate",
        "component_ref": "skill:demo_metrics_skill",
        "metadata": {
            "producer": "builder_publication_gate",
            "task_id": "task.failed-validation",
            "related_ticket_ids": ["dticket.parent"],
        },
        "evidence_refs": [
            {
                "type": "test",
                "task_id": "task.failed-validation",
                "status": "failed",
                "gate": "validation",
            }
        ],
    }

    lineage = development_tickets_module._trusted_publication_gate_lineage(
        ticket,
        target={"object_type": "skill", "object_id": "demo_metrics_skill"},
    )

    assert lineage == {
        "development_ticket_source": "builder_publication_gate",
        "development_ticket_gate_parent_task_id": "task.failed-validation",
        "development_ticket_gate_parent_ticket_ids": ["dticket.parent"],
        "development_ticket_gate": "validation",
    }
    assert (
        development_tickets_module._trusted_publication_gate_lineage(
            {**ticket, "source": "client_feedback"},
            target={"object_type": "skill", "object_id": "demo_metrics_skill"},
        )
        == {}
    )


class _FakeBuilderAutomation:
    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.counter = 0
        self.latest_links: dict = {}
        self.latest_budget: dict = {"max_tokens": 200000}

    def start_from_execute(self, **kwargs):
        self.counter += 1
        self.calls.append(dict(kwargs))
        self.latest_links = dict(kwargs.get("links") or {})
        self.latest_budget = dict(kwargs.get("execution_budget") or self.latest_budget)
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
                    "declared": dict(self.latest_budget),
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
        self.latest_budget = dict(kwargs.get("execution_budget") or self.latest_budget)
        return self._payload(
            status="running",
            suffix=f"resumed-{self.counter}",
            links=self.latest_links,
        )


class _FakeCoreUnblockedBuilderAutomation(_FakeBuilderAutomation):
    def __init__(self) -> None:
        super().__init__()
        self.resume_calls: list[dict] = []
        self.ticket_id = ""

    def status(self, *, object_type: str, object_id: str):
        return {
            "ok": True,
            "session": {
                "session_id": "automation.session.waiting",
                "status": "waiting_for_core",
                "links": {"development_ticket_id": self.ticket_id},
            },
            "automation": {
                "session_id": "automation.session.waiting",
                "status": "waiting_for_core",
                "terminal": False,
                "project": {"object_type": object_type, "object_id": object_id},
            },
        }

    def resume_waiting_for_core_dev_ticket_repair(self, **kwargs):
        self.resume_calls.append(dict(kwargs))
        self.counter += 1
        self.latest_links = dict(kwargs.get("links") or {})
        self.latest_budget = dict(kwargs.get("execution_budget") or self.latest_budget)
        return self._payload(
            status="running",
            suffix=f"core-resumed-{self.counter}",
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
        self.latest_budget = dict(kwargs.get("execution_budget") or self.latest_budget)
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


class _FakePrototypeFirstBuilderAutomation(_FakeBuilderAutomation):
    def __init__(self) -> None:
        super().__init__()
        self.workflow_state = "published"
        self.workflow_source_message_ids: list[str] = []

    def current_workflow_head(self, *, object_type: str, object_id: str):
        return {
            "schema": "adaos.builder.workflow_head.v1",
            "object_type": object_type,
            "object_id": object_id,
            "state": self.workflow_state,
            "change_set_id": "CH-prototype-first",
            "source_message_ids": list(self.workflow_source_message_ids),
        }


def test_declared_dev_source_uses_authoritative_workspace_status(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_path = tmp_path / "skills" / "subscription_status_skill"
    source_path.mkdir(parents=True)

    class _Workspace:
        def development_source_status(self, *, kind: str, artifact_id: str, project_id: str | None):
            assert (kind, artifact_id, project_id) == (
                "skill",
                "subscription_status_skill",
                "subscription_status",
            )
            return {
                "status": "source_available",
                "source": "dev",
                "target_type": kind,
                "target_id": artifact_id,
                "project_id": project_id,
                "dev_source_path": str(source_path),
                "options": ["use_existing_dev_source"],
                "default_option": "use_existing_dev_source",
            }

    monkeypatch.setattr(
        BuilderWorkspaceService,
        "from_context",
        classmethod(lambda cls: _Workspace()),
    )

    result = development_source_options(
        {
            "type": "skill",
            "id": "subscription_status_skill",
            "project_id": "subscription_status",
            "source": "dev",
        }
    )

    assert result["dev_source_path"] == str(source_path)
    assert result["declared_source"] == "dev"


def test_validation_only_qualification_requires_exact_source_preconditions(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="development_request",
        summary="Validate the prepared subscription usage projection.",
        target_scope={"type": "skill", "id": "subscription_status_skill", "source": "dev"},
        source="codex:test",
        owner_area="skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_data",
                "target_files": ["handlers/main.py", "webui.json"],
                "target_refs": ["modal:subscription_status_modal"],
                "acceptance_checks": ["The subscription projection tests pass."],
                "source_preconditions": [
                    {"path": "handlers/main.py", "sha256": "sha256:" + "a" * 64, "size": 10},
                    {"path": "webui.json", "sha256": "sha256:" + "b" * 64, "size": 20},
                ],
                "validation_only": True,
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

    qualification = service.autonomous_repair_qualification(ticket["ticket_id"])

    assert qualification["ready"] is True
    assert qualification["execution_route"] == "validation_only"
    assert qualification["model_call_expected"] is False
    assert qualification["expected_model_tokens"] == 0

    incomplete = dict(ticket["metadata"]["builder_repair"])
    incomplete["source_preconditions"] = incomplete["source_preconditions"][:1]
    updated = service.requalify_builder_repair(
        ticket["ticket_id"],
        builder_repair=incomplete,
        actor="builder:test",
        reason="exercise incomplete guard",
        expected_revision=ticket["revision"],
    )
    blocked = service.autonomous_repair_qualification(updated["ticket_id"])
    assert blocked["ready"] is False
    assert "validation_source_preconditions" in blocked["missing_fields"]


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


def _qualified_receiver_snapshot(*, policy_state: str = "absent") -> dict:
    return {
        "desired_release": {
            "admitted": True,
            "version": "1.0.0",
            "package_digest": "sha256:current",
        },
        "installed_release": {
            "version": "1.0.0",
            "package_digest": "sha256:current",
        },
        "loaded_runtime": {
            "module_available": True,
            "generation": "generation-1",
            "package_digest": "sha256:current",
            "source_drift": False,
            "selection_drift": False,
        },
        "receiver_policy": {"state": policy_state, "patterns": []},
        "observation": {
            "receiver": "owned.panel",
            "own_stream": True,
            "receiver_admitted": False,
        },
        "core_contract": {"supported": True},
        "eligible_update": {},
        "builder_work": [],
    }


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
    assert result["ticket"]["status"] == "captured"
    assert result["pending_action_published"] is False
    assert result["pending_action_suppressed"] is True
    assert result["qualification"]["evidence_complete"] is False
    assert result["qualification"]["human_decision_required"] is False
    assert published == []
    publication = service.publish_compatibility_pending_action(
        result["ticket"]["ticket_id"]
    )
    assert publication["published"] is False
    assert publication["reason"] == (
        "compatibility_qualification_does_not_require_user_decision"
    )

    preview = service.handle_compatibility_response(
        ticket_id=result["ticket"]["ticket_id"],
        response_action_id="preview_evidence",
        responder={"id": "user:owner"},
    )
    assert preview["preview"]["schema"] == (
        "adaos.runtime_compatibility.evidence_preview.v1"
    )
    assert preview["preview"]["qualification"]["evidence_complete"] is False

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
    assert published == []

    Draft202012Validator(_schema("development_signal.v1.schema.json")).validate(duplicate["signal"])
    Draft202012Validator(_schema("dev_ticket.v1.schema.json")).validate(duplicate["ticket"])

    # Broadcasts for another application's receiver expose the same missing
    # policy; they do not establish that this skill owns that receiver.
    foreign = service.report_stream_receiver_compatibility_finding(
        skill_id="legacy_skill",
        admission={**admission, "receiver": "foreign.messages"},
        topic="webio.stream.snapshot.requested",
        publish_pending_action=True,
    )
    assert foreign["ticket"]["ticket_id"] == result["ticket"]["ticket_id"]
    assert published == []
    assert "foreign.messages" not in foreign["ticket"]["summary"]
    assert "Declare only the streams owned" in foreign["ticket"]["summary"]


def test_declared_receiver_denials_remain_separate_compatibility_findings(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    findings = [service.report_stream_receiver_compatibility_finding(
        skill_id="governed_skill",
        admission={"reason": "stream_receiver_not_declared", "receiver": receiver, "receiver_patterns": ["owned.panel"]},
    ) for receiver in ("other.a", "other.b")]
    assert findings[0]["ticket"]["ticket_id"] != findings[1]["ticket"]["ticket_id"]
    assert all(item["ticket"]["policy"]["blocking"] for item in findings)


def test_runtime_requalification_cancels_obsolete_compatibility_card(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_compatibility_finding(
        code="compat.stream_receiver_policy_missing",
        summary="Legacy compatibility finding",
        target_scope={"type": "skill", "id": "legacy_skill"},
        context={"receiver": "legacy.panel"},
        blocking=True,
    )
    ticket = service._update_ticket(
        report["ticket"]["ticket_id"],
        status="waiting_for_user",
        pending_action_refs=[
            {
                "id": "pa.legacy.compatibility",
                "kind": COMPATIBILITY_PENDING_ACTION_KIND,
                "status": "pending",
            }
        ],
    )
    cancelled: list[dict] = []

    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(
        pending_actions,
        "cancel_pending_action",
        lambda action_id, **kwargs: cancelled.append({"id": action_id, **kwargs})
        or {"duplicate": False, "action": {"id": action_id, "status": "cancelled"}},
    )
    qualification = {
        "schema": "adaos.runtime_compatibility.classification.v1",
        "code": "stale_runtime_memory",
        "recommended_action": "reactivate_exact_admitted_package",
    }

    result = service.reconcile_compatibility_pending_actions(
        ticket["ticket_id"],
        qualification=qualification,
    )

    assert result["cancelled"] == ["pa.legacy.compatibility"]
    assert result["failures"] == []
    assert result["ticket"]["status"] == "accepted"
    assert result["ticket"]["pending_action_refs"][0]["status"] == "cancelled"
    assert cancelled[0]["reason"] == "compatibility_requalified:stale_runtime_memory"
    assert result["ticket"]["history"][-1]["kind"] == (
        "compatibility_pending_actions_reconciled"
    )


def test_runtime_requalification_does_not_treat_missing_action_as_cancelled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_compatibility_finding(
        code="compat.stream_receiver_policy_missing",
        summary="Legacy compatibility finding",
        target_scope={"type": "skill", "id": "legacy_skill"},
        context={"receiver": "legacy.panel"},
    )
    ticket = service._update_ticket(
        report["ticket"]["ticket_id"],
        status="waiting_for_user",
        pending_action_refs=[
            {
                "id": "pa.legacy.missing",
                "kind": COMPATIBILITY_PENDING_ACTION_KIND,
                "status": "pending",
            }
        ],
    )
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(
        pending_actions,
        "cancel_pending_action",
        lambda action_id, **_kwargs: {
            "duplicate": True,
            "action": {"id": action_id, "status": "cancelled", "stale": True},
        },
    )

    result = service.reconcile_compatibility_pending_actions(
        ticket["ticket_id"],
        qualification={"code": "application_declaration_defect"},
    )

    assert result["cancelled"] == []
    assert result["failures"] == [
        {
            "pending_action_id": "pa.legacy.missing",
            "error": "pending_action_not_found",
        }
    ]
    current = service.get_ticket(ticket["ticket_id"])
    assert current["status"] == "waiting_for_user"
    assert current["pending_action_refs"][0]["status"] == "pending"


def test_legacy_compatibility_cohort_is_grouped_requalified_and_repaired_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    receivers = (
        "adaos_drive.preview",
        "voice_chat.messages",
        "notebook_skill.latest",
        "slideshow_skill.session",
        "notebook_skill.latest",
    )
    tickets: list[dict] = []
    for index, receiver in enumerate(receivers):
        dedup_receiver = "policy" if index == len(receivers) - 1 else receiver
        report = service.report_compatibility_finding(
            code="compat.stream_receiver_policy_missing",
            summary=(
                "Skill web_desktop_runtime_skill has no stream receiver policy."
            ),
            target_scope={
                "type": "skill",
                "id": "web_desktop_runtime_skill",
                "source": "installed",
            },
            context={
                "reason": "stream_receiver_policy_missing",
                "receiver": receiver,
                "receiver_patterns": [],
            },
            blocking=False,
            run_policy="degrade",
            dedup_key=development_tickets_module._fingerprint(
                "compat.receiver",
                "web_desktop_runtime_skill",
                "stream_receiver_policy_missing",
                dedup_receiver,
            ),
        )
        ticket = service._update_ticket(
            report["ticket"]["ticket_id"],
            status="waiting_for_user",
            pending_action_refs=[
                {
                    "id": f"pa.legacy.{index}",
                    "kind": COMPATIBILITY_PENDING_ACTION_KIND,
                    "status": "pending",
                }
            ],
        )
        tickets.append(ticket)

    cancelled: list[dict] = []
    cancellation_loop_states: list[bool] = []
    import adaos.services.pending_actions as pending_actions

    def _cancel_from_worker(action_id, **kwargs):
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            cancellation_loop_states.append(False)
        else:
            cancellation_loop_states.append(True)
        cancelled.append({"id": action_id, **kwargs})
        return {"duplicate": False, "action": {"id": action_id, "status": "cancelled"}}

    monkeypatch.setattr(
        pending_actions,
        "cancel_pending_action",
        _cancel_from_worker,
    )
    snapshots: list[dict] = []

    def _collect(skill_id, **kwargs):
        snapshots.append({"skill_id": skill_id, **kwargs})
        return {
            "schema": "adaos.runtime_compatibility.snapshot.v1",
            "skill_id": skill_id,
            "desired_release": {
                "admitted": True,
                "version": "1.0.0",
                "package_digest": "sha256:package",
                "manifest_digest": "sha256:manifest",
            },
            "installed_release": {
                "version": "1.0.0",
                "slot": "A",
                "package_digest": "sha256:package",
                "manifest_digest": "sha256:manifest",
            },
            "loaded_runtime": {
                "module_available": True,
                "generation": "generation-1",
                "package_digest": "sha256:package",
                "manifest_digest": "sha256:manifest",
                "source_drift": False,
                "selection_drift": False,
            },
            "receiver_policy": {"state": "absent", "patterns": []},
            "observation": {
                "receiver": kwargs["admission"]["receiver"],
                "own_stream": None,
                "receiver_admitted": False,
            },
            "core_contract": {"supported": True},
            "eligible_update": {},
            "builder_work": [],
        }

    audit = asyncio.run(
        service.reconcile_legacy_compatibility_pending_action_cohort(
            limit=20,
            apply=False,
            snapshot_collector=_collect,
        )
    )

    canonical_id = tickets[-1]["ticket_id"]
    assert audit["candidate_count"] == 5
    assert audit["group_count"] == 1
    assert audit["groups"][0]["canonical_ticket_id"] == canonical_id
    assert audit["groups"][0]["qualification"]["code"] == (
        "application_declaration_defect"
    )
    assert cancelled == []
    assert all(service.get_ticket(item["ticket_id"])["status"] == "waiting_for_user" for item in tickets)

    repair_service = BuilderRepairService(state_dir=tmp_path)
    applied = asyncio.run(
        service.reconcile_legacy_compatibility_pending_action_cohort(
            limit=20,
            apply=True,
            snapshot_collector=_collect,
            repair_service=repair_service,
        )
    )

    group = applied["groups"][0]
    assert applied["complete"] is True
    assert applied["errors"] == []
    assert group["applied"] is True
    assert group["outcome"] == "scoped_builder_repair_created"
    assert group["cancelled_pending_action_ids"] == [
        f"pa.legacy.{index}" for index in range(5)
    ]
    assert sorted(group["superseded_ticket_ids"]) == sorted(
        item["ticket_id"] for item in tickets[:-1]
    )
    assert len(cancelled) == 5
    assert cancellation_loop_states == [False] * 5
    assert all(
        item["reason"]
        == "compatibility_requalified:application_declaration_defect"
        for item in cancelled
    )
    assert len(snapshots) == 2
    assert all(
        service.get_ticket(item["ticket_id"])["status"] == "superseded"
        for item in tickets[:-1]
    )
    canonical = service.get_ticket(canonical_id)
    assert canonical["status"] == "in_builder"
    assert canonical["metadata"]["context"]["qualification"]["code"] == (
        "application_declaration_defect"
    )
    assert canonical["pending_action_refs"][0]["status"] == "cancelled"
    assert len(repair_service.list(project_id="web_desktop_runtime_skill")) == 1

    duplicate = asyncio.run(
        service.reconcile_legacy_compatibility_pending_action_cohort(
            limit=20,
            apply=True,
            snapshot_collector=_collect,
            repair_service=repair_service,
        )
    )
    assert duplicate["candidate_count"] == 0
    assert duplicate["groups"] == []
    assert len(cancelled) == 5
    assert len(repair_service.list(project_id="web_desktop_runtime_skill")) == 1


def test_legacy_compatibility_cohort_routes_missing_identity_evidence_to_core(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_compatibility_finding(
        code="compat.stream_receiver_policy_missing",
        summary="Skill incomplete_skill has no stream receiver policy.",
        target_scope={"type": "skill", "id": "incomplete_skill"},
        context={
            "reason": "stream_receiver_policy_missing",
            "receiver": "incomplete_skill.owned",
            "receiver_patterns": [],
        },
        dedup_key=development_tickets_module._fingerprint(
            "compat.receiver",
            "incomplete_skill",
            "stream_receiver_policy_missing",
            "policy",
        ),
    )
    ticket = service._update_ticket(
        report["ticket"]["ticket_id"],
        status="waiting_for_user",
        pending_action_refs=[
            {
                "id": "pa.legacy.incomplete",
                "kind": COMPATIBILITY_PENDING_ACTION_KIND,
                "status": "cancelled",
            }
        ],
    )
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(
        pending_actions,
        "cancel_pending_action",
        lambda action_id, **_kwargs: {
            "duplicate": False,
            "action": {"id": action_id, "status": "cancelled"},
        },
    )
    async def _list_pending_actions(**_kwargs):
        return {"active": ["pa.legacy.incomplete"]}

    monkeypatch.setattr(
        pending_actions,
        "list_pending_actions_async",
        _list_pending_actions,
    )
    qualification = {
        "schema": "adaos.runtime_compatibility.classification.v1",
        "code": "application_declaration_defect",
        "owner": "application",
        "evidence_complete": False,
        "missing_evidence": ["installed_release.package_digest"],
        "recommended_action": "open_scoped_builder_repair",
        "automatic_recovery_eligible": False,
        "human_decision_required": False,
    }

    applied = asyncio.run(
        service.reconcile_legacy_compatibility_pending_action_cohort(
            limit=20,
            apply=True,
            webspace_id="desktop",
            snapshot_collector=lambda *_args, **_kwargs: {
                "schema": "adaos.runtime_compatibility.snapshot.v1",
                "skill_id": "incomplete_skill",
            },
            classifier=lambda _snapshot: qualification,
        )
    )

    group = applied["groups"][0]
    assert group["outcome"] == "runtime_identity_evidence_required"
    assert group["missing_evidence"] == ["installed_release.package_digest"]
    current = service.get_ticket(ticket["ticket_id"])
    assert current["status"] == "waiting_for_core"
    assert current["pending_action_refs"][0]["status"] == "cancelled"
    assert current["history"][-1]["kind"] == (
        "compatibility_reconciliation_evidence_required"
    )

    repair_service = BuilderRepairService(state_dir=tmp_path)
    complete_qualification = {
        **qualification,
        "evidence_complete": True,
        "missing_evidence": [],
    }
    refreshed = asyncio.run(
        service.reconcile_legacy_compatibility_pending_action_cohort(
            limit=20,
            apply=True,
            snapshot_collector=lambda *_args, **_kwargs: {
                "schema": "adaos.runtime_compatibility.snapshot.v1",
                "skill_id": "incomplete_skill",
                "installed_release": {
                    "package_digest": "sha256:package",
                    "manifest_digest": "sha256:manifest",
                },
            },
            classifier=lambda _snapshot: complete_qualification,
            repair_service=repair_service,
        )
    )

    assert refreshed["candidate_count"] == 1
    assert refreshed["groups"][0]["outcome"] == "scoped_builder_repair_created"
    current = service.get_ticket(ticket["ticket_id"])
    assert current["status"] == "in_builder"
    assert len(repair_service.list(project_id="incomplete_skill")) == 1


def test_legacy_compatibility_cohort_replaces_generic_card_with_exact_update(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_compatibility_finding(
        code="compat.stream_receiver_policy_missing",
        summary="Skill update_skill has no stream receiver policy.",
        target_scope={"type": "skill", "id": "update_skill"},
        context={
            "reason": "stream_receiver_policy_missing",
            "receiver": "update_skill.owned",
            "receiver_patterns": [],
        },
        dedup_key=development_tickets_module._fingerprint(
            "compat.receiver",
            "update_skill",
            "stream_receiver_policy_missing",
            "policy",
        ),
    )
    service._update_ticket(
        report["ticket"]["ticket_id"],
        status="waiting_for_user",
        pending_action_refs=[
            {
                "id": "pa.legacy.update",
                "kind": COMPATIBILITY_PENDING_ACTION_KIND,
                "status": "pending",
            }
        ],
    )
    import adaos.services.pending_actions as pending_actions

    monkeypatch.setattr(
        pending_actions,
        "cancel_pending_action",
        lambda action_id, **_kwargs: {
            "duplicate": False,
            "action": {"id": action_id, "status": "cancelled"},
        },
    )
    qualification = {
        "schema": "adaos.runtime_compatibility.classification.v1",
        "code": "eligible_exact_update",
        "owner": "artifact_authority",
        "evidence_complete": True,
        "recommended_action": "offer_exact_update",
        "automatic_recovery_eligible": False,
        "human_decision_required": True,
        "desired_package_digest": "sha256:new",
        "installed_package_digest": "sha256:old",
        "eligible_update_package_digest": "sha256:new",
        "eligible_update_from_package_digest": "sha256:old",
        "eligible_update_version": "2.0.0",
    }
    publisher_calls: list[dict] = []

    async def _publish(**kwargs):
        publisher_calls.append(dict(kwargs))
        return {
            "interaction": {
                "interaction_id": "interaction.runtime-compatibility.update",
                "created_at": "2026-10-08T00:00:00+00:00",
            }
        }

    result = asyncio.run(
        service.reconcile_legacy_compatibility_pending_action_cohort(
            ctx=object(),
            webspace_id="desktop",
            limit=10,
            apply=True,
            execute_recovery=True,
            snapshot_collector=lambda *_args, **_kwargs: {"snapshot": "exact"},
            classifier=lambda _snapshot: qualification,
            exact_update_publisher=_publish,
        )
    )

    group = result["groups"][0]
    assert group["outcome"] == "exact_update_decision_published"
    assert group["interaction_id"] == "interaction.runtime-compatibility.update"
    assert len(publisher_calls) == 1
    assert publisher_calls[0]["skill_id"] == "update_skill"
    assert publisher_calls[0]["webspace_id"] == "desktop"
    ticket = service.get_ticket(report["ticket"]["ticket_id"])
    assert ticket["status"] == "waiting_for_user"
    assert [ref["status"] for ref in ticket["pending_action_refs"]] == [
        "cancelled",
        "pending",
    ]
    assert ticket["pending_action_refs"][1]["kind"] == (
        "runtime_compatibility_exact_update"
    )


def test_qualified_runtime_reactivation_closes_ticket_with_safe_receipt(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services import runtime_reactivation

    snapshot = {
        "schema": "adaos.runtime_compatibility.snapshot.v1",
        "skill_id": "legacy_skill",
        "desired_release": {
            "admitted": True,
            "version": "2.0.0",
            "package_digest": "sha256:admitted",
            "manifest_digest": "sha256:manifest",
        },
        "installed_release": {
            "version": "2.0.0",
            "slot": "B",
            "package_digest": "sha256:admitted",
            "manifest_digest": "sha256:manifest",
            "source_manifest_digest": "sha256:source",
        },
        "loaded_runtime": {
            "module_available": True,
            "generation": "10",
            "package_digest": "sha256:previous",
            "manifest_digest": "sha256:previous-manifest",
            "source_drift": False,
            "selection_drift": True,
        },
        "receiver_policy": {"state": "valid", "patterns": ["legacy.owned"]},
        "observation": {
            "own_stream": True,
            "receiver_admitted": False,
        },
        "core_contract": {"supported": True},
        "eligible_update": {},
        "builder_work": [],
    }
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_stream_receiver_compatibility_finding(
        skill_id="legacy_skill",
        admission={
            "reason": "stream_receiver_not_declared",
            "receiver": "legacy.owned",
            "receiver_patterns": ["legacy.owned"],
        },
        compatibility_snapshot=snapshot,
    )
    calls: list[dict] = []

    async def _reactivate(ctx, **kwargs):
        calls.append({"ctx": ctx, **kwargs})
        return {
            "schema": "adaos.runtime_reactivation.receipt.v1",
            "ok": True,
            "admitted": True,
            "reason": "reactivation_succeeded",
            "operation_id": "reactivation.test",
            "skill_id": "legacy_skill",
            "expected_version": "2.0.0",
            "expected_slot": "B",
            "package_digest": "sha256:admitted",
            "attempt_count": 1,
            "attempt_number": 1,
            "reload": {"private_path": "must-not-enter-ticket"},
        }

    monkeypatch.setattr(
        runtime_reactivation,
        "reactivate_exact_admitted_package",
        _reactivate,
    )
    context = object()
    result = asyncio.run(
        service.execute_qualified_runtime_recovery(
            report["ticket"]["ticket_id"],
            skill_id="legacy_skill",
            qualification=report["qualification"],
            compatibility_snapshot=snapshot,
            ctx=context,
        )
    )

    assert report["qualification"]["automatic_recovery_eligible"] is True
    assert result["ok"] is True
    assert result["executed"] is True
    assert result["ticket"]["status"] == "closed"
    assert result["ticket"]["closure"]["reason"] == "verified"
    assert result["receipt"]["operation_id"] == "reactivation.test"
    assert "reload" not in result["receipt"]
    assert calls[0]["expected_version"] == "2.0.0"
    assert calls[0]["expected_slot"] == "B"
    assert calls[0]["expected_package_digest"] == "sha256:admitted"
    assert calls[0]["expected_package_manifest_digest"] == "sha256:manifest"
    assert calls[0]["expected_source_manifest_digest"] == "sha256:source"
    assert calls[0]["ctx"] is context


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
        compatibility_snapshot=_qualified_receiver_snapshot(),
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
        "receipt": {
            "status": "failed",
            "failures": [
                {"package": "scenario:builder", "error": "first"},
                {"package": "skill:builder_skill", "error": "second"},
            ],
        },
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
    assert result["ticket"]["metadata"]["affected_component_refs"] == [
        "scenario:builder",
        "skill:builder_skill",
    ]
    assert result["ticket"]["metadata"]["failed_component_count"] == 2
    assert result["ticket"]["evidence_refs"][0]["affected_component_ref"] == "scenario:builder"
    assert result["ticket"]["evidence_refs"][0]["affected_component_refs"] == [
        "scenario:builder",
        "skill:builder_skill",
    ]
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
    resolved = service.resolve_publication_gate_failures(
        component_type="skill",
        component_id="demo_metrics_skill",
        actor="builder.automation",
        evidence_refs=[
            {
                "type": "builder_trial",
                "id": "candidate.second",
                "status": "trial",
            },
            {"type": "test", "id": "task.second", "status": "passed"},
        ],
        resolved_by_version="0.2.0",
        resolved_by_overlay="candidate.second",
    )
    assert [item["status"] for item in resolved] == ["resolved"]
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


def test_publication_permission_gate_waits_for_user_and_records_exact_decision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    published: list[dict] = []

    def publish_pending_action(**kwargs):
        published.append(kwargs)
        return {
            "id": "pa.permission.1",
            "kind": kwargs["kind"],
            "status": "pending",
            "created_at": 1.0,
        }

    monkeypatch.setattr(
        "adaos.services.pending_actions.publish_pending_action",
        publish_pending_action,
    )
    failure = service.report_publication_gate_failure(
        component_type="scenario",
        component_id="automation_manager",
        gate="trial_projection",
        error="activation introduces permissions but has no explicit permission decision",
        task_id="task.1",
        autonomous_repair_eligible=False,
        design_time_fixable=False,
        run_policy="await_user_permission_decision",
        metadata={"requires_user_decision": True},
    )
    pending = service.publish_publication_permission_pending_action(
        failure["ticket"]["ticket_id"],
        object_type="scenario",
        object_id="automation_manager",
        task_id="task.1",
        package_digest="sha256:" + "4" * 64,
        permissions=["workspace.write", "workspace.read"],
    )

    assert pending["ticket"]["status"] == "waiting_for_user"
    assert published[0]["kind"] == PUBLICATION_PERMISSION_PENDING_ACTION_KIND
    assert "workspace.read, workspace.write" in published[0]["request_text"]
    assert pending["ticket"]["policy"]["design_time_fixable"] is False
    assert pending["ticket"]["policy"]["autonomous_repair_eligible"] is False

    answered = service.handle_publication_permission_response(
        ticket_id=failure["ticket"]["ticket_id"],
        response_action_id="approve",
        object_type="scenario",
        object_id="automation_manager",
        task_id="task.1",
        package_digest="sha256:" + "4" * 64,
        pending_action_id="pa.permission.1",
        responder={"id": "user:owner"},
        resume=False,
    )

    assert answered["ok"] is True
    assert answered["decision"]["approved"] is True
    assert answered["decision"]["task_id"] == "task.1"
    assert answered["ticket"]["status"] == "accepted"
    ref = next(
        item
        for item in answered["ticket"]["pending_action_refs"]
        if item["id"] == "pa.permission.1"
    )
    assert ref["status"] == "responded"


def test_runtime_activation_observation_respects_policy_and_closes_on_retry(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)

    diagnostic = service.report_runtime_activation_observation(
        {
            "status": "failed",
            "component_type": "skill",
            "skill_name": "demo_skill",
            "failed_stage": "prepare",
            "error": "dependency unavailable",
            "report_policy": "diagnostic_only",
        }
    )
    assert diagnostic["reported"] is False
    assert service.list_tickets() == []

    failure = service.report_runtime_activation_observation(
        {
            "status": "failed",
            "component_type": "skill",
            "skill_name": "demo_skill",
            "failed_stage": "tests",
            "error": "skill tests failed: test_demo",
            "report_policy": "project_inbox",
            "source": "cli.skill.install",
            "space": "default",
            "webspace_id": "desktop",
            "attempted_version": "1.2.0",
            "slot": "B",
        }
    )
    ticket = failure["ticket"]
    assert ticket["source"] == "runtime_activation"
    assert ticket["component_ref"] == "skill:demo_skill"
    assert ticket["policy"]["publication_required"] is False
    assert ticket["metadata"]["activation_source"] == "cli.skill.install"

    passed = service.report_runtime_activation_observation(
        {
            "status": "passed",
            "component_type": "skill",
            "skill_name": "demo_skill",
            "stage": "tests",
            "space": "default",
            "webspace_id": "desktop",
            "version": "1.2.0",
            "slot": "B",
        }
    )
    assert passed["reported"] is True
    assert passed["closed_tickets"][0]["ticket_id"] == ticket["ticket_id"]
    assert passed["closed_tickets"][0]["status"] == "closed"


def test_runtime_activation_success_closes_only_the_matching_gate(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    validation = service.report_runtime_activation_observation(
        {
            "status": "failed",
            "component_type": "scenario",
            "scenario_id": "demo_metrics",
            "failed_stage": "validation",
            "error": "scenario schema rejected",
            "report_policy": "project_inbox",
            "space": "dev",
        }
    )["ticket"]
    assert validation["evidence_refs"][0]["type"] == "test"
    tests = service.report_runtime_activation_observation(
        {
            "status": "failed",
            "component_type": "scenario",
            "scenario_id": "demo_metrics",
            "failed_stage": "tests",
            "error": "test_workbench failed",
            "report_policy": "project_inbox",
            "space": "dev",
        }
    )["ticket"]
    assert tests["evidence_refs"][0]["type"] == "test"

    passed_validation = service.report_runtime_activation_observation(
        {
            "status": "passed",
            "component_type": "scenario",
            "scenario_id": "demo_metrics",
            "stage": "validation",
            "space": "dev",
        }
    )

    assert [item["ticket_id"] for item in passed_validation["closed_tickets"]] == [
        validation["ticket_id"]
    ]
    assert service.get_ticket(validation["ticket_id"])["status"] == "closed"
    assert service.get_ticket(tests["ticket_id"])["status"] == "accepted"

    passed_tests = service.report_runtime_activation_observation(
        {
            "status": "passed",
            "component_type": "scenario",
            "scenario_id": "demo_metrics",
            "stage": "tests",
            "space": "dev",
        }
    )
    assert passed_tests["closed_tickets"][0]["ticket_id"] == tests["ticket_id"]
    assert service.get_ticket(tests["ticket_id"])["status"] == "closed"


def test_ticket_resolution_requires_evidence_and_closes_linked_repair(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    report = service.report_compatibility_finding(
        code="compat.stream_receiver_not_declared",
        summary="Skill legacy_skill lacks receiver declaration for legacy.panel",
        target_scope={"type": "skill", "id": "legacy_skill", "version": "1.0.0"},
        context={
            "receiver": "legacy.panel",
            "qualification": {
                "schema": "adaos.runtime_compatibility.classification.v1",
                "code": "application_declaration_defect",
                "evidence_complete": True,
                "recommended_action": "open_scoped_builder_repair",
            },
        },
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
    preflight = service.autonomous_repair_qualification(ticket["ticket_id"])
    assert preflight["execution_route"] == "structured_edits"
    assert preflight["model_call_expected"] is False
    assert preflight["expected_model_tokens"] == 0
    assert preflight["estimated_budget"]["max_tokens"] == 8000
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


def test_planned_builder_handoff_can_be_qualified_before_execution(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="development_request",
        summary="Declare the exact owned stream receivers.",
        target_scope={"type": "skill", "id": "demo_skill", "source": "dev"},
        source="runtime_guard",
        owner_area="skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="runtime_compatibility_debt",
        status="ready_for_builder",
    )["ticket"]
    planned = service._update_ticket(
        ticket["ticket_id"],
        status="in_builder",
        builder_refs=[
            {
                "type": "builder_repair_task",
                "repair_id": "repair.previous",
                "mode": "autonomous",
                "status": "resolved",
                "automation_session_id": "session.previous",
                "automation_task_id": "task.previous",
            },
            {
                "type": "builder_repair_task",
                "repair_id": "repair.planned",
                "mode": "interactive",
                "status": "open",
            }
        ],
    )
    repair = {
        "profile": "surgical_data",
        "change_summary": "Validate the exact owned stream receiver policy.",
        "target_object_type": "skill",
        "target_object_id": "demo_skill",
        "target_files": ["skills/demo_skill/skill.yaml"],
        "target_refs": ["sdk:skill.data_routes"],
        "acceptance_checks": ["Only receivers owned by demo_skill are declared."],
        "max_changed_files": 1,
        "requires_root_mcp": False,
    }

    qualified = service.requalify_builder_repair(
        ticket["ticket_id"],
        builder_repair=repair,
        actor="builder:qualifier",
        reason="deterministic pre-execution qualification",
        expected_revision=planned["revision"],
    )

    assert qualified["status"] == "in_builder"
    assert qualified["metadata"]["builder_repair"]["target_files"] == [
        "skills/demo_skill/skill.yaml"
    ]

    running = service._update_ticket(
        ticket["ticket_id"],
        builder_refs=[
            {
                "type": "builder_repair_task",
                "repair_id": "repair.planned",
                "mode": "autonomous",
                "status": "in_progress",
                "automation_session_id": "session.running",
                "automation_task_id": "task.running",
            }
        ],
    )
    with pytest.raises(ValueError, match="while Builder is running"):
        service.requalify_builder_repair(
            ticket["ticket_id"],
            builder_repair={**repair, "change_summary": "A racing change."},
            actor="builder:qualifier",
            reason="must remain fenced",
            expected_revision=running["revision"],
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
                "target_refs": ["modal:subscription_status_modal"],
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


def test_single_autonomous_repair_requires_qualification_before_spending_tokens(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeBuilderAutomation()
    signal = service.capture_signal(
        kind="development_request",
        summary="Rename a Demo Metrics heading",
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

    assert result["started"] is False
    assert result["status"] == "qualification_required"
    assert result["reason"] == "builder_qualification_required"
    assert result["qualification"]["execution_route"] == "qualification"
    assert result["qualification"]["missing_fields"] == [
        "profile",
        "target_files",
        "target_refs",
        "acceptance_checks",
    ]
    assert automation.calls == []
    assert repair_service.list() == []
    assert service.get_ticket(ticket["ticket_id"])["builder_refs"] == []


def test_autonomous_repair_applies_zero_model_source_qualification_before_builder(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "demo_metrics_skill"
    (source / "handlers").mkdir(parents=True)
    (source / "tests").mkdir()
    (source / "webui.json").write_text(
        json.dumps({"semantic": {"views": [{"id": "metrics", "title": "Metrics"}]}}),
        encoding="utf-8",
    )
    (source / "handlers" / "main.py").write_text(
        "def refresh_metrics():\n    return []\n",
        encoding="utf-8",
    )
    (source / "tests" / "test_demo_metrics.py").write_text(
        "def test_refresh_metrics():\n    assert True\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "demo_metrics_skill",
            "dev_source_path": str(source),
        },
    )
    service = DevelopmentTicketService(state_dir=tmp_path / "state")
    repair_service = BuilderRepairService(state_dir=tmp_path / "state")
    automation = _FakeBuilderAutomation()
    signal = service.capture_signal(
        kind="feedback_note",
        summary="Refresh должен сразу обновлять таблицу Metrics.",
        target_scope={
            "type": "skill",
            "id": "demo_metrics_skill",
            "source": "workspace",
            "surface": "modal",
        },
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="feedback",
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
    assert automation.calls[0]["execution_budget"]["max_tokens"] == 24000
    source_validation = automation.calls[0]["links"]["source_precondition_validation"]
    assert source_validation["status"] == "passed"
    assert all(item["status"] == "matched" for item in source_validation["checks"])
    brief = json.loads(automation.calls[0]["implementation_brief"])
    assert brief["repair_hints"]["source_preconditions"]
    assert any(
        item["kind"] == "builder_repair_requalified"
        for item in result["ticket"]["history"]
    )


def test_autonomous_repair_stops_when_qualified_source_digest_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "demo_metrics_skill"
    source.mkdir()
    webui = source / "webui.json"
    webui.write_text(
        json.dumps({"semantic": {"views": [{"id": "metrics", "title": "Metrics"}]}}),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "skill",
            "target_id": "demo_metrics_skill",
            "dev_source_path": str(source),
        },
    )
    service = DevelopmentTicketService(state_dir=tmp_path / "state")
    repair_service = BuilderRepairService(state_dir=tmp_path / "state")
    automation = _FakeBuilderAutomation()
    signal = service.capture_signal(
        kind="feedback_note",
        summary="Переименовать заголовок таблицы Metrics.",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "workspace"},
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="feedback",
        status="ready_for_builder",
        owner_area="skill",
    )["ticket"]
    qualified = service.prepare_builder_repair_qualification(
        ticket["ticket_id"],
        apply=True,
        expected_revision=ticket["revision"],
    )
    assert qualified["autonomous_repair_qualification"]["ready"] is True
    webui.write_text(
        json.dumps({"semantic": {"views": [{"id": "metrics", "title": "Changed elsewhere"}]}}),
        encoding="utf-8",
    )

    result = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
    )

    assert result["started"] is False
    assert result["status"] == "source_changed"
    assert result["reason"] == "builder_requalification_required"
    assert automation.calls == []
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
    assert planned["execution_budget"]["max_tokens"] == 30000
    assert planned["execution_budget"]["max_billable_tokens"] == 480000
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
    assert automation.calls[0]["execution_budget"]["max_tokens"] == 30000
    assert automation.calls[0]["links"]["development_ticket_ids"] == ticket_ids
    assert automation.calls[0]["links"]["development_ticket_project_ref"] == "project:demo_metrics"
    assert automation.calls[0]["links"]["development_ticket_project_id"] == "demo_metrics"
    brief = json.loads(automation.calls[0]["implementation_brief"])
    assert brief["ticket_ids"] == ticket_ids
    assert brief["policy"]["one_release_for_package"] is True
    assert [item["ticket_id"] for item in brief["issues"]] == ticket_ids
    assert all("evidence_refs" not in item for item in brief["issues"])
    assert [item["detail_source"] for item in brief["issues"]] == [
        {
            "server": "adaos_task_root",
            "tool": "get_dev_ticket",
            "arguments": {"ticket_id": ticket_id},
        }
        for ticket_id in ticket_ids
    ]
    assert all(
        service.get_ticket(ticket_id)["builder_refs"][0]["automation_task_id"]
        == "factory.task.1"
        for ticket_id in ticket_ids
    )
    assert started["rollup"]["total_tokens"] == 150


def test_scenario_webui_package_requires_prototype_acceptance_before_automation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    scenario_id = "flowboard"
    source = tmp_path / "dev" / "scenarios" / scenario_id
    source.mkdir(parents=True)
    webui = source / "webui.json"
    original = b'{"schema":"adaos.webui.v1","title":"Board"}'
    webui.write_bytes(original)
    semantic = source / "semantic.webui.json"
    semantic_original = b'{"schema":"adaos.webui.semantic.v2","views":[]}'
    semantic.write_bytes(semantic_original)
    focused_test = source / "tests" / "test_board.py"
    focused_test.parent.mkdir()
    test_original = b"def test_board():\n    assert True\n"
    focused_test.write_bytes(test_original)
    monkeypatch.setattr(
        "adaos.services.development_tickets.development_source_options",
        lambda _scope: {
            "status": "source_available",
            "source": "dev",
            "target_type": "scenario",
            "target_id": scenario_id,
            "project_id": "flowboard_project",
            "dev_source_path": str(source),
        },
    )
    service = DevelopmentTicketService(state_dir=tmp_path / "state")
    repair_service = BuilderRepairService(state_dir=tmp_path / "state")
    automation = _FakePrototypeFirstBuilderAutomation()
    summary = "Rename the Board heading to Board view. Change nothing else."
    target_file = f"scenarios/{scenario_id}/webui.json"
    semantic_file = f"scenarios/{scenario_id}/semantic.webui.json"
    test_file = f"scenarios/{scenario_id}/tests/test_board.py"
    signal = service.capture_signal(
        kind="development_request",
        summary=summary,
        target_scope={
            "type": "scenario",
            "id": scenario_id,
            "source": "dev",
            "project_ref": "project:flowboard_project",
            "project_id": "flowboard_project",
        },
        source="client_feedback",
        owner_area="scenario",
        component_ref=f"scenario:{scenario_id}",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "concepts": ["ui"],
                "target_files": [target_file, semantic_file, test_file],
                "target_refs": ["page:board"],
                "acceptance_checks": ["The heading is Board view."],
                "max_changed_files": 3,
                "requires_root_mcp": False,
                "source_preconditions": [
                    {
                        "path": target_file,
                        "sha256": "sha256:" + hashlib.sha256(original).hexdigest(),
                        "size": len(original),
                    },
                    {
                        "path": semantic_file,
                        "sha256": "sha256:"
                        + hashlib.sha256(semantic_original).hexdigest(),
                        "size": len(semantic_original),
                    },
                    {
                        "path": test_file,
                        "sha256": "sha256:"
                        + hashlib.sha256(test_original).hexdigest(),
                        "size": len(test_original),
                    },
                ],
            }
        },
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="scenario",
        component_ref=f"scenario:{scenario_id}",
    )["ticket"]
    planned = service.plan_builder_package(
        [ticket["ticket_id"]],
        actor="builder:qualifier",
        repair_service=repair_service,
    )
    prototype_calls: list[dict] = []

    def submit_prototype(**kwargs):
        prototype_calls.append(dict(kwargs))
        automation.workflow_state = "prototype_editing"
        webui.write_bytes(b'{"schema":"adaos.webui.v1","title":"Board view"}')
        return {
            "ok": True,
            "session_id": "builder.session.flowboard",
            "patch": {"change_id": "builder_change.flowboard"},
            "ui_revision": {"revision": "010"},
        }

    def read_prototype(**kwargs):
        assert kwargs["session_id"] == "builder.session.flowboard"
        return {
            "ok": True,
            "session": {
                "id": "builder.session.flowboard",
                "active_change_id": "builder_change.flowboard",
                "ui_revision": "010",
                "pending_llm_jobs": {},
            },
        }

    assert planned["repair_hints"]["execution_route"] == "prototype_first"
    assert planned["repair_hints"]["prototype_model_policy"] == "deterministic_first"

    automation.workflow_state = "prototype_editing"
    busy = service.start_autonomous_package(
        planned["package_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        prototype_submitter=submit_prototype,
        prototype_status_reader=read_prototype,
    )
    assert busy["status"] == "builder_workflow_busy"
    assert prototype_calls == []

    automation.workflow_source_message_ids = [
        f"m.{planned['package_id']}.prototype.request"
    ]
    repair_service.transition_work_item(
        planned["repair"]["repair_id"],
        status="failed",
        actor="builder:prototype",
        reason="prototype_start:RuntimeError",
    )

    prototype_started = service.start_autonomous_package(
        planned["package_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        prototype_submitter=submit_prototype,
        prototype_status_reader=read_prototype,
    )

    assert prototype_started["started"] is True
    assert prototype_started["stage"] == "prototype"
    assert prototype_started["status"] == "prototype_acceptance_required"
    assert prototype_calls[0]["instruction"] == summary
    assert prototype_calls[0]["ticket_ids"] == [ticket["ticket_id"]]
    assert automation.calls == []
    assert prototype_started["repair"]["work_status"] == "in_progress"
    assert prototype_started["repair"]["context"]["prototype"]["revision"] == "010"
    assert prototype_started["tickets"][0]["status"] == "in_builder"
    prototype_ref = prototype_started["tickets"][0]["builder_refs"][0]
    assert prototype_ref["prototype_status"] == "prototype_review"
    assert prototype_ref["prototype"]["revision"] == "010"

    waiting = service.start_autonomous_package(
        planned["package_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        prototype_submitter=submit_prototype,
        prototype_status_reader=read_prototype,
    )

    assert waiting["started"] is False
    assert waiting["status"] == "prototype_acceptance_required"
    assert len(prototype_calls) == 1
    assert automation.calls == []
    assert waiting["tickets"][0]["status"] == "in_builder"

    automation.workflow_state = "automation_ready"
    automated = service.start_autonomous_package(
        planned["package_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        prototype_submitter=submit_prototype,
        prototype_status_reader=read_prototype,
    )

    assert automated["started"] is True
    assert len(prototype_calls) == 1
    assert len(automation.calls) == 1
    assert automation.calls[0]["links"]["source_precondition_validation"]["status"] == (
        "superseded_by_accepted_prototype"
    )


def test_builder_package_preserves_all_structured_edits_for_zero_model_route(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    target_file = "skills/demo_metrics_skill/handlers/main.py"
    signal = service.capture_signal(
        kind="development_request",
        summary="Rename one generated Demo Metrics row",
        target_scope={
            "type": "skill",
            "id": "demo_metrics_skill",
            "source": "dev",
            "project_ref": "project:demo_metrics",
            "project_id": "demo_metrics",
        },
        source="client_feedback",
        owner_area="skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_files": [target_file],
                "target_refs": ["demo.metric[id=open-dev-tickets].title"],
                "acceptance_checks": ["The generated row uses its clearer title."],
                "max_changed_files": 1,
                "requires_root_mcp": False,
                "structured_edits": {
                    "schema": "adaos.builder.structured_edit_set.v1",
                    "operations": [
                        {
                            "id": "rename-generated-title",
                            "op": "replace_text",
                            "path": target_file,
                            "old": '"title": "Open Dev Tickets"',
                            "new": '"title": "Open change requests"',
                            "expected_count": 1,
                        }
                    ],
                },
            }
        },
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="skill",
    )["ticket"]

    planned = service.plan_builder_package(
        [ticket["ticket_id"]],
        actor="builder:qualifier",
        repair_service=repair_service,
        execution_budget={"max_model_tokens": 4_000},
    )

    assert planned["repair_hints"]["structured_edits"]["operations"] == [
        {
            "id": "rename-generated-title",
            "op": "replace_text",
            "path": target_file,
            "old": '"title": "Open Dev Tickets"',
            "new": '"title": "Open change requests"',
            "expected_count": 1,
        }
    ]
    repair = repair_service.list(package_id=planned["package_id"])[0]
    package = repair["context"]["package"]
    assert package["repair_hints"]["structured_edits"] == planned["repair_hints"][
        "structured_edits"
    ]


def test_builder_package_starts_successor_after_published_workflow(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakePublishedBuilderAutomation()
    ticket = _bounded_demo_ticket(
        service,
        summary="Rename the next Demo Metrics row",
        target_files=["skills/demo_metrics_skill/handlers/main.py"],
        acceptance="The next metric row uses its clearer title.",
    )
    planned = service.plan_builder_package(
        [ticket["ticket_id"]],
        actor="builder:qualifier",
        repair_service=repair_service,
    )

    started = service.start_autonomous_package(
        planned["package_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
    )

    assert started["started"] is True
    assert len(automation.calls) == 1
    assert automation.followup_calls == []
    assert automation.calls[0]["links"]["development_ticket_id"] == ticket["ticket_id"]


def test_builder_package_preserves_public_tool_contract_closure(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    paths = [
        "skills/demo_metrics_skill/skill.yaml",
        "skills/demo_metrics_skill/handlers/main.py",
        "skills/demo_metrics_skill/webui.json",
    ]
    ticket = _bounded_demo_ticket(
        service,
        summary="Add a public refresh action",
        target_files=paths,
        acceptance="The declared Refresh action invokes the exported handler.",
    )
    service.requalify_builder_repair(
        ticket["ticket_id"],
        builder_repair={
            "profile": "project_batch",
            "change_summary": "Add a public refresh action",
            "target_files": paths,
            "target_refs": ["contract:skill_public_tool_graph"],
            "acceptance_checks": [
                "The declared Refresh action invokes the exported handler."
            ],
            "max_changed_files": len(paths),
            "requires_root_mcp": False,
            "source_preconditions": [
                {"path": path, "sha256": "sha256:" + str(index + 1) * 64, "size": 10}
                for index, path in enumerate(paths)
            ],
            "contract_closure": {
                "kind": "skill_public_tool_graph",
                "required_paths": paths,
                "reason": "the repair adds a public WebUI action and handler",
            },
        },
        actor="builder:qualifier",
        reason="test exact public contract closure",
    )

    qualification = service.autonomous_repair_qualification(ticket["ticket_id"])
    assert qualification["ready"] is True
    assert qualification["contract_closure"]["required_paths"] == paths

    planned = service.plan_builder_package(
        [ticket["ticket_id"]],
        actor="builder:qualifier",
        repair_service=repair_service,
    )

    assert planned["repair_hints"]["contract_closure"]["required_paths"] == paths


def test_builder_package_launch_failure_releases_every_ticket(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakePublishedBuilderAutomation()
    tickets = [
        _bounded_demo_ticket(
            service,
            summary="Rename the first Demo Metrics row",
            target_files=["skills/demo_metrics_skill/handlers/main.py"],
            acceptance="The first metric row uses its clearer title.",
        ),
        _bounded_demo_ticket(
            service,
            summary="Rename the second Demo Metrics row",
            target_files=["skills/demo_metrics_skill/tests/test_resource_workbench.py"],
            acceptance="The second metric row has a focused assertion.",
        ),
    ]
    planned = service.plan_builder_package(
        [ticket["ticket_id"] for ticket in tickets],
        actor="builder:qualifier",
        repair_service=repair_service,
    )
    automation.start_from_execute = lambda **kwargs: (_ for _ in ()).throw(  # type: ignore[method-assign]
        RuntimeError("successor launch failed")
    )

    with pytest.raises(RuntimeError, match="successor launch failed"):
        service.start_autonomous_package(
            planned["package_id"],
            actor="builder:automation",
            repair_service=repair_service,
            automation_service=automation,
        )

    assert {
        service.get_ticket(ticket["ticket_id"])["status"]
        for ticket in tickets
    } == {"ready_for_builder"}
    work_item = repair_service.list(package_id=planned["package_id"])[0]
    assert work_item["work_status"] == "failed"


def test_builder_package_adopts_standalone_dev_skill_into_project(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    dev_root = tmp_path / "dev" / "test-subnet"
    dev_skill = dev_root / "skills" / "subscription_status_skill"
    dev_skill.mkdir(parents=True)
    (dev_skill / "skill.yaml").write_text(
        "name: subscription_status_skill\nversion: 0.1.0\ntools: []\n",
        encoding="utf-8",
    )
    workspace_service = BuilderWorkspaceService(
        state_dir=state_dir,
        workspace_root=tmp_path / "workspace",
        skills_root=tmp_path / "workspace" / "skills",
        scenarios_root=tmp_path / "workspace" / "scenarios",
        dev_skills_root=dev_root / "skills",
        dev_scenarios_root=dev_root / "scenarios",
    )
    service = DevelopmentTicketService(state_dir=state_dir)
    repair_service = BuilderRepairService(state_dir=state_dir)
    tickets = []
    for summary in (
        "Rename the Subscription resource heading",
        "Move the Subscription refresh action",
    ):
        signal = service.capture_signal(
            kind="development_request",
            summary=summary,
            target_scope={
                "type": "skill",
                "id": "subscription_status_skill",
                "source": "dev",
                "component_ref": "skill:subscription_status_skill",
            },
            source="client_feedback",
            owner_area="skill",
            component_ref="skill:subscription_status_skill",
            metadata={
                "builder_repair": {
                    "profile": "surgical_ui",
                    "change_summary": summary,
                    "target_files": ["skills/subscription_status_skill/webui.json"],
                    "target_refs": ["modal:subscription_status_modal"],
                    "acceptance_checks": [summary],
                    "max_changed_files": 1,
                    "requires_root_mcp": False,
                }
            },
        )["signal"]
        tickets.append(
            service.ensure_ticket_for_signal(
                signal,
                kind="development_request",
                status="ready_for_builder",
                owner_area="skill",
                component_ref="skill:subscription_status_skill",
            )["ticket"]
        )

    planned = service.plan_builder_package(
        [ticket["ticket_id"] for ticket in tickets],
        actor="builder:qualifier",
        repair_service=repair_service,
        workspace_service=workspace_service,
    )

    assert planned["ready"] is True
    assert planned["project_resolution"]["status"] == "created"
    assert planned["project_id"] == "subscription_status"
    assert planned["project_ref"] == "project:subscription_status"
    assert planned["repair"]["project_id"] == "subscription_status"
    assert planned["execution_budget"]["max_tokens"] == 30000
    assert (
        dev_root / "projects" / "subscription_status" / "project.yaml"
    ).is_file()
    for ticket in tickets:
        updated = service.get_ticket(ticket["ticket_id"])
        assert updated["target_scope"]["project_id"] == "subscription_status"
        assert updated["metadata"]["project_ref"] == "project:subscription_status"
        assert any(
            item["kind"] == "project_scope_bound" for item in updated["history"]
        )


def test_builder_package_can_explicitly_fork_unlocked_workspace_source(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    workspace_root = tmp_path / "workspace"
    workspace_skill = workspace_root / "skills" / "local_probe_skill"
    workspace_skill.mkdir(parents=True)
    (workspace_skill / "skill.yaml").write_text(
        "name: local_probe_skill\nversion: 0.1.0\ntools: []\n",
        encoding="utf-8",
    )
    dev_root = tmp_path / "dev" / "test-subnet"
    workspace_service = BuilderWorkspaceService(
        state_dir=state_dir,
        workspace_root=workspace_root,
        skills_root=workspace_root / "skills",
        scenarios_root=workspace_root / "scenarios",
        dev_skills_root=dev_root / "skills",
        dev_scenarios_root=dev_root / "scenarios",
    )
    service = DevelopmentTicketService(state_dir=state_dir)
    repair_service = BuilderRepairService(state_dir=state_dir)
    signal = service.capture_signal(
        kind="development_request",
        summary="Clarify the local probe description",
        target_scope={"type": "skill", "id": "local_probe_skill", "source": "workspace"},
        source="client_feedback",
        owner_area="skill",
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_object_type": "skill",
                "target_object_id": "local_probe_skill",
                "target_files": ["skills/local_probe_skill/skill.yaml"],
                "target_refs": ["skill:local_probe_skill.description"],
                "acceptance_checks": ["The description is clear."],
                "max_changed_files": 1,
            }
        },
    )["signal"]
    ticket = service.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="ready_for_builder",
        owner_area="skill",
    )["ticket"]

    planned = service.plan_builder_package(
        [ticket["ticket_id"]],
        actor="builder:qualifier",
        repair_service=repair_service,
        workspace_service=workspace_service,
        source_strategy="create_local_fork",
    )

    assert planned["ready"] is True
    assert planned["project_id"] == "local_probe"
    assert planned["project_resolution"]["status"] == "source_available"
    assert planned["source_materialization"]["strategy"] == "create_local_fork"
    assert (dev_root / "skills" / "local_probe_skill" / "skill.yaml").is_file()
    assert (dev_root / "projects" / "local_probe" / "project.yaml").is_file()
    updated = service.get_ticket(ticket["ticket_id"])
    assert updated is not None
    assert updated["metadata"]["project_ref"] == "project:local_probe"


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
    assert repair["context"]["cost_estimate"]["max_tokens"] == 24000
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


def test_autonomous_repair_resumes_same_session_after_core_is_verified(tmp_path: Path) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    automation = _FakeCoreUnblockedBuilderAutomation()
    ticket = _bounded_demo_ticket(
        service,
        summary="Show Codex usage through the newly released public SDK",
        target_files=["skills/demo_metrics_skill/handlers/main.py"],
        acceptance="The skill uses the public subscription SDK.",
    )
    automation.ticket_id = ticket["ticket_id"]

    result = service.start_autonomous_repair(
        ticket["ticket_id"],
        actor="builder:automation",
        repair_service=repair_service,
        automation_service=automation,
        webspace_id="desktop",
    )

    assert result["started"] is True
    assert automation.calls == []
    assert len(automation.resume_calls) == 1
    assert automation.resume_calls[0]["links"]["development_ticket_id"] == ticket["ticket_id"]


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
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_files": ["skills/demo_metrics_skill/webui.json"],
                "target_refs": ["widget:metric-trend.title"],
                "acceptance_checks": ["The next selected metric heading is renamed."],
                "max_changed_files": 1,
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
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_files": ["skills/demo_metrics_skill/webui.json"],
                "target_refs": ["widget:resource-workbench.title"],
                "acceptance_checks": ["The requested marker is visible after validation."],
                "max_changed_files": 1,
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


def test_builder_sync_turns_validated_escalation_into_linked_core_ticket(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    repair_service = BuilderRepairService(state_dir=tmp_path)
    ticket = _bounded_demo_ticket(
        service,
        summary="Show subscription token usage through the public SDK",
        target_files=["skills/demo_metrics_skill/handlers/main.py"],
        acceptance="The UI shows the current plan allowance.",
    )
    handoff = service.handoff_ticket(
        ticket["ticket_id"],
        mode="autonomous",
        repair_service=repair_service,
        actor="user:owner",
    )
    repair_id = handoff["repair"]["repair_id"]
    links = {
        "development_ticket_id": ticket["ticket_id"],
        "development_ticket_ids": [ticket["ticket_id"]],
        "builder_repair_id": repair_id,
    }
    automation_result = _FakeBuilderAutomation()._payload(
        status="completed",
        suffix="core-gap",
        links=links,
    )
    automation_result["session"]["task"]["result"]["development_escalations"] = [
        {
            "schema": "adaos.development_escalation.v1",
            "kind": "core_capability_request",
            "summary": "Expose subscription token usage",
            "component_ref": "core:sdk.subscription",
            "desired_contract": "Read current plan token use and remaining allowance.",
            "impact": "blocker",
            "motivation": "Project code must use a public SDK contract.",
            "observed_limitation": "The current quota API exposes transport quotas only.",
            "rejected_workarounds": [
                {
                    "approach": "Read Root state directly",
                    "reason": "Project code cannot access private core state.",
                }
            ],
        }
    ]

    result = service.sync_builder_repair(
        ticket["ticket_id"],
        repair_id=repair_id,
        actor="builder.automation",
        repair_service=repair_service,
        automation_result=automation_result,
    )

    assert result["escalated"] is True
    assert result["resolved"] is False
    assert result["ticket"]["status"] == "waiting_for_core"
    assert result["repair"]["work_status"] == "blocked"
    core_ticket = result["core_requests"][0]["ticket"]
    assert core_ticket["owner_area"] == "core"
    assert core_ticket["component_ref"] == "core:sdk.subscription"
    assert core_ticket["metadata"]["source_task_id"] == "factory.task.core-gap"
    assert result["ticket"]["relation_refs"][0]["ticket_id"] == core_ticket["ticket_id"]


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
        metadata={
            "builder_repair": {
                "profile": "surgical_ui",
                "target_files": ["skills/demo_metrics_skill/webui.json"],
                "target_refs": ["widget:resource-workbench.title"],
                "acceptance_checks": ["The bounded repair is validated."],
                "max_changed_files": 1,
            }
        },
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


def test_platform_defect_escalation_routes_shared_client_bug_and_blocks_source(
    tmp_path: Path,
) -> None:
    service = DevelopmentTicketService(state_dir=tmp_path)
    signal = service.capture_signal(
        kind="review_comment",
        summary="Status badges stretch into ellipses in cards without previews.",
        target_scope={"type": "scenario", "id": "automation_manager_prototype"},
        source="client_feedback",
        owner_area="scenario",
        component_ref="scenario:automation_manager_prototype",
    )["signal"]
    source = service.ensure_ticket_for_signal(
        signal,
            kind="feedback",
        status="captured",
        owner_area="scenario",
        component_ref="scenario:automation_manager_prototype",
    )["ticket"]

    escalated = service.escalate_platform_defect(
        source["ticket_id"],
        component_ref="core:client.renderer.list.cards.meta-badge",
        expected_behavior="Card metadata badges keep intrinsic height when preview content is absent.",
        observed_behavior="The badge stretches across the card grid row and appears as a tall ellipse.",
        failure_class="layout.cross_axis_stretch",
        actor="builder:platform-router",
        confidence=0.98,
        evidence_refs=[{"type": "test", "id": "list-card-meta-badge-layout"}],
    )

    assert escalated["platform_defect"] is True
    assert escalated["ticket"]["owner_area"] == "core"
    assert escalated["ticket"]["component_ref"] == (
        "core:client.renderer.list.cards.meta-badge"
    )
    assert escalated["ticket"]["metadata"]["development_escalation_kind"] == (
        "platform_defect"
    )
    updated_source = service.get_ticket(source["ticket_id"])
    assert updated_source is not None
    assert updated_source["status"] == "waiting_for_core"


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
