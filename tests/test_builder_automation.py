from __future__ import annotations

import hashlib
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator
import pytest
import yaml

from adaos.services.builder import automation as automation_module
from adaos.services.builder.automation import (
    BuilderAutomationService,
    _brief_has_structured_edits,
    _context_budget_window,
    _context_plan_failure_message,
    _context_projection_brief,
    _iteration_context_projection,
)
from adaos.services.builder.workspace import BuilderWorkspaceService
from adaos.services.context_control import ContextControlService
from adaos.services.root.service import _rewrite_skill_template_identity
from adaos.services.skill_factory_worker import CodexRunResult, LocalSkillFactoryWorker


def _service(tmp_path: Path) -> BuilderAutomationService:
    repo_root = Path(__file__).resolve().parents[1]
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    scenario = dev_scenarios / "recipes"
    scenario.mkdir(parents=True)
    dev_skills.mkdir(parents=True)
    (scenario / "scenario.yaml").write_text(
        yaml.safe_dump({"id": "recipes", "version": "0.1.0", "depends": []}, sort_keys=False),
        encoding="utf-8",
    )
    (scenario / "webui.json").write_text(json.dumps({"schema": "adaos.webui.v1"}), encoding="utf-8")

    class _DeveloperService:
        def create_skill(self, name: str, template: str | None = None):
            source = repo_root / "src" / "adaos" / "skills_templates" / str(template or "skill_default")
            target = dev_skills / name
            shutil.copytree(source, target)
            _rewrite_skill_template_identity(target, name)
            return SimpleNamespace(path=target, name=name)

        def create_scenario(self, name: str, template: str | None = None):
            source = repo_root / "src" / "adaos" / "scenario_templates" / str(template or "scenario_default")
            target = dev_scenarios / name
            shutil.copytree(source, target)
            return SimpleNamespace(path=target, name=name)

    workspace_service = BuilderWorkspaceService(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        workspace_root=tmp_path / "workspace",
        skills_root=tmp_path / "workspace" / "skills",
        scenarios_root=tmp_path / "workspace" / "scenarios",
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        developer_service=_DeveloperService(),
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        handler = workspace / "skills" / "recipes_skill" / "handlers" / "main.py"
        handler.write_text(handler.read_text(encoding="utf-8") + "\n# automation iteration\n", encoding="utf-8")
        return CodexRunResult(returncode=0, final_message="Automation iteration completed.")

    def worker_factory() -> LocalSkillFactoryWorker:
        return LocalSkillFactoryWorker(
            state_dir=tmp_path / "state",
            repo_root=repo_root,
            dev_skills_root=dev_skills,
            dev_scenarios_root=dev_scenarios,
            runs_root=tmp_path / "runs",
            executor=fake_codex,
        )

    return BuilderAutomationService(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        worker_factory=worker_factory,
        workspace_service=workspace_service,
        background=False,
        materialize_on_completion=False,
    )


def _realize_content_artifact(
    service: BuilderAutomationService,
    task: dict,
    field: str,
) -> dict:
    artifacts = task["realize_request"]["artifacts"]
    assert field not in artifacts
    ref = artifacts[f"{field}_ref"]
    value = service._contexts().get_artifact(ref)
    assert isinstance(value, dict)
    return value


def test_execute_starts_local_automation_and_persists_session(tmp_path: Path) -> None:
    service = _service(tmp_path)

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search and detail actions.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert started["ok"] is True
    status = service.status(object_type="scenario", object_id="recipes")
    assert status["session"]["status"] == "completed"
    assert status["session"]["source_prototype_version"] == "0.1.0"
    assert status["automation"]["source_prototype_version"] == "0.1.0"
    assert status["session"]["standard_prompt_version"] == "adaos-skill-realization/0.12.0"
    assert status["session"]["created_artifacts"][0]["kind"] == "skill"
    assert status["session"]["created_artifacts"][0]["name"] == "recipes_skill"
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == status["session"]["current_task_id"]
    )
    assert task["forge"]["base_revision"].startswith("sha256:")
    assert task["forge"]["base_revision"] == task["forge"]["source_snapshot"]["digest"]
    assert (service.dev_skills_root / "recipes_skill" / "skill.yaml").exists()
    assert "new_skill" not in (service.dev_skills_root / "recipes_skill" / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )
    assert status["session"]["local_run"]["events_path"].endswith("codex-live.jsonl")


def test_completed_builder_context_restores_from_cold_service(tmp_path: Path) -> None:
    first = _service(tmp_path)
    completed = first.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search and detail actions.",
        webspace_id="prompt-dev",
        links={
            "development_ticket_id": "dticket.cold-restore",
            "development_ticket_project_ref": "project:recipe_suite",
            "development_ticket_project_id": "recipe_suite",
        },
    )
    control = completed["session"]["context_control"]
    first_inspection = first._contexts().inspect(control["run_ref"])

    restored_service = BuilderAutomationService(
        state_dir=first.state_dir,
        repo_root=first.repo_root,
        dev_skills_root=first.dev_skills_root,
        dev_scenarios_root=first.dev_scenarios_root,
        runs_root=first.runs_root,
        worker_factory=first.worker_factory,
        workspace_service=first.workspace_service,
        background=False,
        materialize_on_completion=False,
    )
    restored = restored_service.status(
        object_type="scenario",
        object_id="recipes",
    )["session"]
    restored_control = restored["context_control"]
    restored_inspection = restored_service._contexts().inspect(control["run_ref"])

    assert restored["status"] == "completed"
    assert restored_control["project_ref"] == "project:recipe_suite"
    assert restored_control["plan_ref"] == control["plan_ref"]
    assert restored_control["compiled_context_ref"] == control["compiled_context_ref"]
    assert restored_service._contexts().get_plan(control["plan_id"])["plan_ref"] == (
        control["plan_ref"]
    )
    assert restored_inspection == first_inspection
    assert restored_inspection["receipt_count"] == 1

    continued = restored_service.submit_turn(
        text="Apply one more scoped refinement to the same Dev Ticket project.",
        object_type="scenario",
        object_id="recipes",
    )
    assert continued["status"] == "automation_queued"
    assert continued["session"]["status"] == "queued"
    continued_session = restored_service.status(
        object_type="scenario",
        object_id="recipes",
    )["session"]
    continued_control = continued_session["context_control"]
    assert continued_session["status"] == "completed"
    assert continued_session["iteration"] == 1
    assert continued_control["project_ref"] == "project:recipe_suite"
    first_project_capsule = next(
        ref
        for ref in control["capsule_refs"]
        if first._contexts().get_capsule(ref)["kind"] == "project"
    )
    continued_project_capsule = next(
        ref
        for ref in continued_control["capsule_refs"]
        if restored_service._contexts().get_capsule(ref)["kind"] == "project"
    )
    assert continued_project_capsule != first_project_capsule
    assert continued_control["project_context_digest"] != control[
        "project_context_digest"
    ]
    first_task_capsule = next(
        ref
        for ref in control["capsule_refs"]
        if first._contexts().get_capsule(ref)["kind"] == "task"
    )
    continued_task_capsule = next(
        ref
        for ref in continued_control["capsule_refs"]
        if restored_service._contexts().get_capsule(ref)["kind"] == "task"
    )
    assert continued_task_capsule != first_task_capsule
    assert continued_control["run_ref"] != control["run_ref"]
    assert restored_service._contexts().inspect(continued_control["run_ref"])[
        "receipt_count"
    ] == 1


def test_related_runs_reuse_project_capsule_and_isolate_task_overlay(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    session = {
        "session_id": "automation.skill.metrics",
        "iteration": 1,
        "change_set_id": "CH-metrics",
        "links": {"development_ticket_project_ref": "project:metrics"},
    }
    packet = {
        "schema": "adaos.builder.context_packet.v1",
        "digest": "sha256:" + "1" * 64,
        "project": {"ref": "project:metrics"},
        "change": {
            "change_id": "CH-metrics",
            "status": "active",
            "issues": [],
        },
        "base": {"version": "0.1.0"},
        "artifacts": {},
        "dependencies": [],
        "allowed_paths": ["skills/metrics/"],
        "facets": {},
    }
    snapshot = {
        "digest": "sha256:" + "2" * 64,
        "created_at": "2026-09-02T00:00:00Z",
    }

    first = service._compile_iteration_context(
        session=session,
        kind="skill",
        project_id="metrics",
        context_packet=packet,
        source_snapshot=snapshot,
        implementation_brief="Repair the first ticket.",
    )
    session["iteration"] = 2
    second = service._compile_iteration_context(
        session=session,
        kind="skill",
        project_id="metrics",
        context_packet=packet,
        source_snapshot=snapshot,
        implementation_brief="Repair another related ticket.",
    )

    first_capsules = [
        service._contexts().get_capsule(ref) for ref in first["capsule_refs"]
    ]
    second_capsules = [
        service._contexts().get_capsule(ref) for ref in second["capsule_refs"]
    ]
    first_project = next(item for item in first_capsules if item["kind"] == "project")
    second_project = next(item for item in second_capsules if item["kind"] == "project")
    first_task = next(item for item in first_capsules if item["kind"] == "task")
    second_task = next(item for item in second_capsules if item["kind"] == "task")

    assert first_project["capsule_id"] == second_project["capsule_id"]
    assert first["project_context_digest"] == second["project_context_digest"]
    assert first_task["capsule_id"] != second_task["capsule_id"]
    assert "builder_context_packet" not in first_project["source_digests"]
    binding = service._contexts().get_binding(
        subject_ref="project:metrics",
        purpose="builder.automation",
        audience="builder",
    )
    assert binding["revision"] == 1


def test_session_persists_workflow_and_request_once_by_content_address(tmp_path: Path) -> None:
    service = _service(tmp_path)
    request = {
        "schema": "adaos.skill_factory.realize_request.v1",
        "request_id": "realize.compact",
        "payload": "request-context-" * 4_000,
    }
    request_artifact = service._contexts().put_artifact(request)
    snapshot_context = {
        "schema": "adaos.skill_factory.task_context.v1",
        "provenance": [{"kind": "legacy", "ref": "snapshot-" * 4_000}],
    }
    provenance = {
        "schema": "adaos.skill_factory.task_provenance.v1",
        "runner_version": "cold-restore-runner",
        "snapshot_refs": ["provenance-" * 4_000],
    }
    result = {
        "schema": "adaos.skill_factory.dev_result.v1",
        "status": "completed",
        "notes": ["result-context-" * 4_000],
        "provenance": provenance,
    }
    workflow = {
        "schema": "adaos.builder.workflow.v1",
        "active_phase": "automation",
        "workflow_state": "checkpoint",
        "governed": {"generation": 7},
        "change_set": {"change_set_id": "CH-compact", "status": "implemented"},
        "delivery": {
            "status": "checkpoint",
            "package_digest": "sha256:" + "a" * 64,
            "source_revision": "revision-1",
        },
        "history": ["workflow-history-" * 5_000],
    }
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.compact",
        "object_type": "skill",
        "object_id": "compact",
        "status": "completed",
        "current_task_id": "task.compact",
        "task": {
            "task_id": "task.compact",
            "status": "completed",
            "realize_request_ref": request_artifact["ref"],
            "realize_request_digest": request_artifact["digest"],
            "realize_request": request,
            "snapshot_context": snapshot_context,
            "result": result,
            "provenance": provenance,
        },
        "last_result": result,
        "completion_readiness": {
            "ok": True,
            "task_id": "task.compact",
            "workflow_checkpoint": {"ok": True, "workflow": workflow},
        },
        "updated_at": "2026-09-02T00:00:00+00:00",
    }

    service._save_session(session)

    session_path = service._session_path("skill", "compact")
    persisted = json.loads(session_path.read_text(encoding="utf-8"))
    checkpoint = persisted["completion_readiness"]["workflow_checkpoint"]
    for field in (
        "realize_request",
        "snapshot_context",
        "result",
        "provenance",
    ):
        assert field not in persisted["task"]
    assert "last_result" not in persisted
    assert persisted["last_result_ref"].startswith("artifact://context/sha256/")
    assert "workflow" not in checkpoint
    assert checkpoint["workflow_ref"].startswith("artifact://context/sha256/")
    assert checkpoint["workflow_summary"]["delivery"]["status"] == "checkpoint"
    assert session_path.stat().st_size < 15_000

    restored = service.get_session("skill", "compact")
    assert restored is not None
    assert restored["task"]["realize_request"] == request
    assert restored["task"]["snapshot_context"] == snapshot_context
    assert restored["task"]["result"] == result
    assert restored["task"]["provenance"] == provenance
    assert restored["last_result"] == result
    assert restored["completion_readiness"]["workflow_checkpoint"]["workflow"] == workflow


def test_compact_status_omits_private_session_payload_and_stays_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search and detail actions.",
        webspace_id="prompt-dev",
    )
    session = service.get_session("scenario", "recipes")
    assert session is not None
    session["large_private_diagnostic"] = "x" * 2_000_000
    service._save_session(session)
    summary_path = service._compact_status_path("scenario", "recipes")
    assert summary_path.is_file()

    monkeypatch.setattr(
        BuilderAutomationService,
        "get_session",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("terminal compact status must not load the full session")
        ),
    )

    status = service.status(
        object_type="scenario",
        object_id="recipes",
        include_session=False,
    )

    assert status["detail_available"] is True
    assert status["session"]["schema"] == "adaos.builder.automation_session_summary.v1"
    assert status["session"]["task_history"]["count"] == 1
    assert "large_private_diagnostic" not in status["session"]
    assert "implementation_brief" not in status["session"]
    assert len(json.dumps(status, ensure_ascii=False).encode("utf-8")) < 32 * 1024


def test_dev_ticket_repair_projects_minimal_diff_constraints(tmp_path: Path) -> None:
    service = _service(tmp_path)

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=json.dumps(
            {
                "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                "ticket_id": "dticket.demo",
                "summary": "Repair only the scoped issue.",
                "repair_hints": {
                    "profile": "surgical_ui",
                    "target_files": [
                        "scenarios/recipes/webui.json",
                        "scenarios/recipes/tests/test_webui.py",
                    ],
                    "target_refs": ["widget:metrics-table.title"],
                    "acceptance_checks": ["The visible title is Live metrics."],
                    "max_changed_files": 2,
                    "requires_root_mcp": False,
                },
            }
        ),
        links={
            "development_ticket_id": "dticket.demo",
            "development_ticket_project_ref": "project:recipe_suite",
            "development_ticket_project_id": "recipe_suite",
        },
        execution_budget={"max_wall_seconds": 300, "max_tokens": 20000},
    )

    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )
    constraints = task["realize_request"]["constraints"]
    assert constraints["mode"] == "dev_ticket_repair"
    assert constraints["minimal_diff"] is True
    assert constraints["preserve_declarative_manifests"] is True
    assert constraints["must_update_manifest"] is False
    assert constraints["repair_profile"] == "surgical_ui"
    assert constraints["max_changed_files"] == 2
    assert task["realize_request"]["mcp"] == {"enabled": False, "requested_scope": []}
    context_control = started["session"]["context_control"]
    assert context_control["model_call_expected"] is True
    assert context_control["required_estimated_tokens"] < 4_000
    plan = service._contexts().get_plan(context_control["plan_id"])
    assert "project:recipe_suite" in plan["subject_refs"]
    assert "project:recipes" not in plan["subject_refs"]
    inspection = service._contexts().inspect(context_control["run_ref"])
    assert inspection["receipt_count"] == 1
    receipt = inspection["receipts"][0]
    assert receipt["subject_refs"] == [
        context_control["run_ref"],
        "project:recipe_suite",
    ]
    assert {item["layer"] for item in receipt["layer_usage"]} == {
        "stable_prefix",
        "task_context",
        "model_projection",
    }
    assert receipt["tool_boundary_count"] == 1
    project_capsules = service._contexts().list_capsules(subject_ref="project:recipe_suite")
    assert project_capsules[0]["subject_refs"] == ["project:recipe_suite", "scenario:recipes"]
    compact = service.compact_session(started["session"])
    assert compact["links"]["development_ticket_project_ref"] == "project:recipe_suite"
    prompt = (
        tmp_path / "runs" / started["session"]["current_task_id"] / "input" / "task.md"
    ).read_text(encoding="utf-8")
    assert "AdaOS bounded surgical UI repair" in prompt
    assert "widget:metrics-table.title" in prompt
    assert "Governed Development Session inputs" not in prompt


def test_dev_ticket_repair_with_root_mcp_uses_only_bound_target_validation(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=json.dumps(
            {
                "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                "ticket_id": "dticket.root-data",
                "summary": "Show the bound subnet node identity.",
                "repair_hints": {
                    "profile": "subnet_data_integration",
                    "target_files": ["scenarios/recipes/webui.json"],
                    "target_refs": ["node:current"],
                    "acceptance_checks": ["The bound node identity is visible."],
                    "max_changed_files": 1,
                    "requires_root_mcp": True,
                },
            }
        ),
        links={
            "development_ticket_id": "dticket.root-data",
            "development_ticket_project_ref": "project:recipe_suite",
            "development_ticket_project_id": "recipe_suite",
            "subnet_id": "sn_demo",
        },
        execution_budget={"max_wall_seconds": 300, "max_tokens": 20000},
    )

    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )

    assert task["realize_request"]["mcp"] == {
        "enabled": True,
        "requested_scope": ["staging_validation"],
        "subnet_id": "sn_demo",
        "bound_target_id": "hub:sn_demo",
    }


def test_builder_adapts_inferred_context_budget_for_required_capsules(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    original_plan = ContextControlService.plan
    requested_budgets: list[int] = []

    def plan_with_first_pass_budget_miss(
        context_service: ContextControlService,
        request: dict,
    ) -> dict:
        planned = original_plan(context_service, request)
        requested_budgets.append(int(request["token_budget"]))
        if len(requested_budgets) == 1:
            return {
                **planned,
                "status": "insufficient",
                "required_estimated_tokens": 9_000,
                "omitted_required_refs": ["capsule:required-task"],
            }
        return planned

    monkeypatch.setattr(ContextControlService, "plan", plan_with_first_pass_budget_miss)

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Apply one bounded label refinement.",
        execution_budget={
            "max_model_tokens": 12_000,
            "token_budget_metric": "fresh_plus_output",
        },
    )

    control = started["session"]["context_control"]
    assert requested_budgets == [8_000, 9_000]
    assert control["initial_token_budget"] == 8_000
    assert control["token_budget"] == 9_000
    assert control["token_budget_adapted"] is True


def test_explicit_context_budget_is_a_hard_limit_with_diagnostics() -> None:
    assert _context_budget_window(
        {"max_model_tokens": 12_000, "max_context_tokens": 8_000}
    ) == (8_000, 8_000, True)

    message = _context_plan_failure_message(
        {
            "token_budget": 8_000,
            "required_estimated_tokens": 9_000,
            "omitted_required_refs": ["capsule:required-task"],
            "denied": [],
            "unavailable": [],
        },
        budget_ceiling=8_000,
    )

    assert "required_tokens=9000" in message
    assert "budget_ceiling=8000" in message
    assert "omitted_required=capsule:required-task" in message


def test_deterministic_execution_context_budget_is_independent_of_model_budget() -> None:
    execution_budget = {
        "max_model_tokens": 4_000,
        "token_budget_metric": "fresh_plus_output",
    }

    assert _context_budget_window(execution_budget) == (2_976, 2_976, False)
    assert _context_budget_window(
        execution_budget,
        model_call_expected=False,
    ) == (16_000, 32_000, False)
    assert _context_budget_window(
        {**execution_budget, "max_context_tokens": 8_000},
        model_call_expected=False,
    ) == (8_000, 8_000, True)


def test_unqualified_dev_ticket_defaults_to_project_batch_repair(tmp_path: Path) -> None:
    service = _service(tmp_path)

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=json.dumps(
            {
                "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                "ticket_id": "dticket.unqualified",
                "summary": "Expose a small validation marker.",
            }
        ),
        links={"development_ticket_id": "dticket.unqualified"},
        execution_budget={"max_wall_seconds": 300, "max_tokens": 12000},
    )

    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )
    request = task["realize_request"]
    assert request["constraints"]["repair_profile"] == "project_batch"
    assert request["artifacts"]["repair_hints"]["profile"] == "project_batch"
    prompt = (
        tmp_path / "runs" / started["session"]["current_task_id"] / "input" / "task.md"
    ).read_text(encoding="utf-8")
    assert "AdaOS bounded Dev Ticket repair" in prompt
    assert len(prompt.encode("utf-8")) < 12_000


def test_dev_ticket_repair_canonicalizes_component_relative_structured_paths(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=json.dumps(
            {
                "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                "ticket_id": "dticket.relative-path",
                "repair_hints": {
                    "profile": "surgical_ui",
                    "target_files": ["webui.json"],
                    "structured_edits": {
                        "schema": "adaos.builder.structured_edit_set.v1",
                        "operations": [
                            {
                                "op": "replace_text",
                                "path": "webui.json",
                                "old": "Old title",
                                "new": "New title",
                                "expected_count": 1,
                            }
                        ],
                    },
                },
            }
        ),
        links={"development_ticket_id": "dticket.relative-path"},
        execution_budget={"max_wall_seconds": 300, "max_model_tokens": 4_000},
    )

    context_control = started["session"]["context_control"]
    assert context_control["model_call_expected"] is False
    assert context_control["context_budget_source"] == "deterministic_execution"
    assert context_control["initial_token_budget"] == 16_000
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )
    artifacts = task["realize_request"]["artifacts"]
    expected_path = "scenarios/recipes/webui.json"
    assert task["realize_request"]["constraints"]["exact_changed_paths"] == [
        expected_path
    ]
    assert artifacts["repair_hints"]["target_files"] == [expected_path]
    assert artifacts["repair_hints"]["structured_edits"]["operations"][0][
        "path"
    ] == expected_path


def test_large_dev_ticket_brief_uses_bounded_workflow_projection(tmp_path: Path) -> None:
    service = _service(tmp_path)
    brief_payload = {
        "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
        "ticket_id": "dticket.large",
        "summary": "Rename one visible section heading.",
        "target": {"object_type": "scenario", "object_id": "recipes"},
        "component_ref": "scenario:recipes.controls",
        "repair_hints": {
            "profile": "surgical_ui",
            "target_files": [f"scenarios/recipes/path-{index}.json" for index in range(30)],
            "target_refs": [f"view:recipes.section-{index}.title" for index in range(30)],
            "acceptance_checks": ["The exact heading is changed and behavior is preserved. " * 12] * 12,
        },
        "guardrails": ["Do not change unrelated behavior. " * 20] * 20,
    }
    brief = json.dumps(brief_payload)
    assert len(brief) > 4000

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=brief,
        links={"development_ticket_id": "dticket.large"},
    )

    assert started["ok"] is True
    session = started["session"]
    assert session["implementation_brief"] == brief
    workflow = service._workflow().describe("scenario", "recipes")
    request = workflow["change_set"]["request"]
    assert len(request) <= 3800
    projected = json.loads(request)
    assert projected["summary"] == "Rename one visible section heading."
    assert projected["ticket_id"] == "dticket.large"
    assert projected["brief_digest"].startswith("sha256:")


def test_terminal_skill_candidate_runtime_release_is_exact_and_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation_candidate",
            "object_type": "skill",
            "object_id": "candidate_skill",
            "development_session_id": "dev_candidate_01",
            "status": "completed",
            "updated_at": "2026-08-20T00:00:00+00:00",
        }
    )
    calls: list[str] = []

    def cleanup(skill_id: str) -> dict[str, object]:
        calls.append(skill_id)
        return {
            "runtime_existed": True,
            "runtime_removed": True,
            "purged_data": True,
        }

    monkeypatch.setattr(automation_module, "_cleanup_dev_skill_runtime", cleanup)

    released = service.release_candidate_runtime(
        object_type="skill",
        object_id="candidate_skill",
        development_session_id="dev_candidate_01",
    )
    repeated = service.release_candidate_runtime(
        object_type="skill",
        object_id="candidate_skill",
        development_session_id="dev_candidate_01",
    )

    assert released["ok"] is True
    assert released["idempotent"] is False
    assert repeated["idempotent"] is True
    assert calls == ["candidate_skill"]
    persisted = service.get_session("skill", "candidate_skill")
    assert persisted is not None
    assert persisted["runtime_release"]["development_session_id"] == "dev_candidate_01"
    assert persisted["runtime_release"]["status"] == "released"


def test_terminal_candidate_native_runtime_cleanup_is_durable_and_retryable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation_candidate",
            "object_type": "skill",
            "object_id": "candidate_skill",
            "development_session_id": "dev_candidate_01",
            "status": "completed",
            "updated_at": "2026-08-20T00:00:00+00:00",
        }
    )
    outcomes: list[str] = ["locked", "released"]

    def cleanup(_skill_id: str) -> dict[str, object]:
        outcome = outcomes.pop(0)
        if outcome == "locked":
            raise PermissionError(13, "native DLL is still mapped")
        return {
            "runtime_existed": True,
            "runtime_removed": True,
            "purged_data": True,
        }

    monkeypatch.setattr(automation_module, "_cleanup_dev_skill_runtime", cleanup)

    pending = service.release_candidate_runtime(
        object_type="skill",
        object_id="candidate_skill",
        development_session_id="dev_candidate_01",
    )
    released = service.release_candidate_runtime(
        object_type="skill",
        object_id="candidate_skill",
        development_session_id="dev_candidate_01",
    )
    repeated = service.release_candidate_runtime(
        object_type="skill",
        object_id="candidate_skill",
        development_session_id="dev_candidate_01",
    )

    assert pending["ok"] is True
    assert pending["cleanup_pending"] is True
    assert pending["runtime_release"]["status"] == "cleanup_pending"
    assert pending["runtime_release"]["pending_reason"] == (
        "native_module_mapped_by_runtime_process"
    )
    assert released["runtime_release"]["status"] == "released"
    assert released["runtime_release"]["cleanup_attempts"] == 2
    assert repeated["idempotent"] is True
    assert outcomes == []


def test_terminal_candidate_release_preserves_runtime_diagnostics_as_builder_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation_candidate",
            "object_type": "skill",
            "object_id": "candidate_skill",
            "development_session_id": "dev_candidate_01",
            "status": "failed",
            "updated_at": "2026-08-20T00:00:00+00:00",
        }
    )
    runtime_root = service.dev_skills_root / ".runtime" / "candidate_skill"
    diagnostic = runtime_root / "diagnostics" / "candidate-tests" / "pytest.log"
    diagnostic.parent.mkdir(parents=True)
    diagnostic.write_text("one failed\n", encoding="utf-8")

    def cleanup(skill_id: str) -> dict[str, object]:
        assert skill_id == "candidate_skill"
        shutil.rmtree(runtime_root)
        return {
            "runtime_existed": True,
            "runtime_removed": True,
            "purged_data": True,
        }

    monkeypatch.setattr(automation_module, "_cleanup_dev_skill_runtime", cleanup)

    released = service.release_candidate_runtime(
        object_type="skill",
        object_id="candidate_skill",
        development_session_id="dev_candidate_01",
    )

    receipt = released["runtime_release"]
    evidence = receipt["diagnostics"]
    archived = Path(evidence["root"]) / "candidate-tests" / "pytest.log"
    assert runtime_root.exists() is False
    assert archived.read_text(encoding="utf-8") == "one failed\n"
    assert evidence["schema"] == "adaos.builder.runtime_diagnostics.v1"
    assert evidence["file_count"] == 1
    assert evidence["bytes"] == archived.stat().st_size
    assert evidence["files"][0]["path"] == "candidate-tests/pytest.log"
    assert evidence["files"][0]["digest"].startswith("sha256:")
    assert evidence["digest"].startswith("sha256:")


@pytest.mark.parametrize(
    ("status", "development_session_id", "error"),
    [
        ("in_progress", "dev_candidate_01", "only after terminal"),
        ("completed", "dev_other", "does not match"),
    ],
)
def test_candidate_runtime_release_rejects_unsafe_lifecycle_state(
    tmp_path: Path,
    status: str,
    development_session_id: str,
    error: str,
) -> None:
    service = _service(tmp_path)
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation_candidate",
            "object_type": "skill",
            "object_id": "candidate_skill",
            "development_session_id": "dev_candidate_01",
            "status": status,
            "updated_at": "2026-08-20T00:00:00+00:00",
        }
    )

    with pytest.raises(ValueError, match=error):
        service.release_candidate_runtime(
            object_type="skill",
            object_id="candidate_skill",
            development_session_id=development_session_id,
        )


def test_automation_materializes_governed_development_session_inputs(tmp_path: Path) -> None:
    service = _service(tmp_path)
    artifact_root = tmp_path / "admitted-artifacts"
    artifact_root.mkdir()
    (artifact_root / "notebook.ipynb").write_text("{}", encoding="utf-8")
    session_id = "dev_recipes_calibration"
    session_root = service.state_dir / "builder" / "development_sessions" / session_id
    instruction_root = session_root / "instructions"
    instruction_root.mkdir(parents=True)
    review = instruction_root / "reviewed_prose.md"
    review.write_text("Notebook results are exploratory only.\n", encoding="utf-8")
    review_digest = "sha256:" + hashlib.sha256(review.read_bytes()).hexdigest()
    session = {
        "schema": "adaos.builder.development_session.v1",
        "session_id": session_id,
        "project_ref": "project:recipe_program",
        "base_release": None,
        "focus": {"ref": "scenario:recipes"},
        "targets": {
            "primary": [
                {
                    "ref": "scenario:recipes",
                    "access": "read-write",
                    "context": "full",
                    "source_path": str(service.dev_scenarios_root / "recipes"),
                }
            ],
            "secondary": [],
        },
        "context_members": [],
        "artifact_inputs": [
            {
                "ref": "artifact://skill/source_direction/part0",
                "access": "read-only",
                "manifest_digest": "sha256:" + "1" * 64,
                "root_path": str(artifact_root),
                "audience": "research.calibration.c1_reviewed_prose",
                "context_digest": "sha256:" + "2" * 64,
            }
        ],
        "instruction_inputs": [
            {
                "ref": f"instruction://builder/{session_id}/reviewed_prose",
                "kind": "reviewed_prose",
                "access": "read-only",
                "media_type": "text/markdown",
                "digest_mode": "bytes",
                "content_digest": review_digest,
                "path": str(review),
            }
        ],
        "scratch": {
            "owner": "session",
            "access": "read-write",
            "path": str(session_root / "scratch"),
        },
        "handoff": {
            "automation_brief_digest": "sha256:" + "3" * 64,
            "research_prototype_digest": "sha256:" + "4" * 64,
            "artifact_manifest_digests": ["sha256:" + "1" * 64],
            "request": "Implement the frozen calibration request.",
            "execution_budget": {
                "budget_view": "fixed_downstream",
                "max_wall_seconds": 7200,
                "max_model_tokens": 80000,
                "max_attempts": 1,
                "max_human_interventions": 0,
            },
            "agent_profile": {
                "provider": "openai-codex-cli",
                "model": "gpt-5.4",
                "reasoning_effort": "high",
                "tool_profile": "adaos-local-bounded-v1",
            },
            "prohibited_actions": ["Do not inspect undeclared evaluator material."],
        },
        "status": "ready",
        "created_at": "2026-08-18T00:00:00Z",
        "created_by": "skill:research_calibration_runner_skill",
    }
    (session_root / "session.json").write_text(
        json.dumps(session, ensure_ascii=False), encoding="utf-8"
    )

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the frozen calibration request.",
        development_session_id=session_id,
    )

    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )
    receipt = _realize_content_artifact(service, task, "development_context")
    assert receipt["session_id"] == session_id
    assert receipt["request"] == "Implement the frozen calibration request."
    assert receipt["execution_budget"]["max_model_tokens"] == 80000
    assert receipt["agent_profile"]["reasoning_effort"] == "high"
    assert receipt["artifact_inputs"][0]["path"].startswith(".adaos_context/")
    assert receipt["instruction_inputs"][0]["content_digest"] == review_digest
    assert task["realize_request"]["links"]["development_context_digest"] == receipt["digest"]
    assert task["timeout_seconds"] == 7200
    assert task["max_attempts"] == 1
    assert task["realize_request"]["artifacts"]["execution_budget"]["budget_view"] == "fixed_downstream"
    assert task["realize_request"]["artifacts"]["agent_profile"]["model"] == "gpt-5.4"
    plan = service._contexts().get_plan(started["session"]["context_control"]["plan_id"])
    assert "project:recipe_program" in plan["subject_refs"]
    assert f"development-session:{session_id}" in plan["subject_refs"]
    assert "project:recipes" not in plan["subject_refs"]
    control = started["session"]["context_control"]
    assert control["development_session_ref"] == f"development-session:{session_id}"
    development_capsule = service._contexts().get_capsule(
        control["development_session_capsule_ref"]
    )
    assert development_capsule["kind"] == "development_session"
    capsule_content = service._contexts().get_artifact(
        development_capsule["artifact_ref"]
    )
    canonical_ref = capsule_content["index"][0]["ref"]
    cold_contexts = ContextControlService(state_dir=service.state_dir)
    assert cold_contexts.get_artifact(canonical_ref) == session
    inspection = cold_contexts.inspect(control["run_ref"])
    assert inspection["receipts"][0]["subject_refs"] == [
        control["run_ref"],
        f"development-session:{session_id}",
        "project:recipe_program",
    ]
    attachments = task["forge"]["source_snapshot"]["attachments"]
    assert {item["name"] for item in attachments} >= {
        "development_artifact_00",
        "development_instructions",
    }
    prompt = (
        tmp_path / "runs" / started["session"]["current_task_id"] / "input" / "task.md"
    ).read_text(encoding="utf-8")
    assert "Governed Development Session inputs" in prompt
    assert ".adaos_context/dev_recipes_calibration/instructions/reviewed_prose.md" in prompt


def test_terminal_followup_rebinds_to_new_digest_verified_development_session(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)

    def write_session(session_id: str, brief_digest: str) -> None:
        root = service.state_dir / "builder" / "development_sessions" / session_id
        instructions = root / "instructions"
        instructions.mkdir(parents=True)
        artifact_root = tmp_path / "artifacts" / session_id
        artifact_root.mkdir(parents=True)
        artifact_digest = "sha256:" + "3" * 64
        brief = {
            "schema": "adaos.research.automation_brief.v1",
            "digest": brief_digest,
            "objective": f"Implement {brief_digest}",
        }
        brief_path = instructions / "automation_brief.json"
        brief_path.write_text(json.dumps(brief), encoding="utf-8")
        content_digest = automation_module._canonical_digest(brief)
        session = {
            "schema": "adaos.builder.development_session.v1",
            "session_id": session_id,
            "project_ref": "project:recipes",
            "base_release": None,
            "focus": {"ref": "scenario:recipes"},
            "targets": {
                "primary": [
                    {
                        "ref": "scenario:recipes",
                        "access": "read-write",
                        "context": "full",
                        "source_path": str(service.dev_scenarios_root / "recipes"),
                    }
                ],
                "secondary": [],
            },
            "context_members": [],
            "artifact_inputs": [
                {
                    "ref": f"artifact://skill/source/{session_id}",
                    "access": "read-only",
                    "manifest_digest": artifact_digest,
                    "root_path": str(artifact_root),
                }
            ],
            "instruction_inputs": [
                {
                    "ref": f"instruction://builder/{session_id}/automation_brief",
                    "kind": "automation_brief",
                    "access": "read-only",
                    "media_type": "application/json",
                    "digest_mode": "canonical-json",
                    "content_digest": content_digest,
                    "path": str(brief_path),
                }
            ],
            "scratch": {
                "owner": "session",
                "access": "read-write",
                "path": str(root / "scratch"),
            },
            "handoff": {
                "automation_brief_digest": brief_digest,
                "research_prototype_digest": "sha256:" + "4" * 64,
                "artifact_manifest_digests": [artifact_digest],
                "request": "Implement the exact admitted brief.",
                "prohibited_actions": [],
            },
            "status": "ready",
            "created_at": "2026-08-20T00:00:00Z",
            "created_by": "skill:test",
        }
        (root / "session.json").write_text(json.dumps(session), encoding="utf-8")

    first_id = "dev_recipes_first"
    second_id = "dev_recipes_recompiled"
    first_digest = "sha256:" + "1" * 64
    second_digest = "sha256:" + "2" * 64
    write_session(first_id, first_digest)
    write_session(second_id, second_digest)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=json.dumps({"digest": first_digest}),
        development_session_id=first_id,
    )

    followed = service.submit_turn(
        text="Rebase to the newly compiled exact Development Session.",
        object_type="scenario",
        object_id="recipes",
        development_session_id=second_id,
    )

    session = followed["session"]
    assert session["development_session_id"] == second_id
    assert json.loads(session["implementation_brief"])["digest"] == second_digest
    assert session["development_session_history"][-1]["development_session_id"] == first_id
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == session["current_task_id"]
    )
    assert task["realize_request"]["links"]["development_session_id"] == second_id
    assert _realize_content_artifact(service, task, "development_context")[
        "session_id"
    ] == second_id
    assert (
        json.loads(task["realize_request"]["artifacts"]["implementation_brief"])["digest"]
        == second_digest
    )


def test_automation_projects_declared_and_observed_execution_budget(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    runtime_root = run_root / "runtime"
    runtime_root.mkdir(parents=True)
    journal = runtime_root / "codex-events.jsonl"
    journal.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1200,
                    "cached_input_tokens": 300,
                    "output_tokens": 400,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (runtime_root / "codex-events-repair-1.jsonl").write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 200,
                    "cached_input_tokens": 100,
                    "output_tokens": 50,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = {
        "status": "completed",
        "created_at": "2026-08-18T00:00:00Z",
        "task": {
            "created_at": "2026-08-18T00:00:00Z",
            "assigned_at": "2026-08-18T00:00:10Z",
            "updated_at": "2026-08-18T00:01:10Z",
            "realize_request": {
                "artifacts": {
                    "execution_budget": {
                        "budget_view": "fixed_downstream",
                        "max_wall_seconds": 7200,
                        "max_model_tokens": 80000,
                        "max_attempts": 1,
                        "max_human_interventions": 0,
                    }
                }
            },
        },
        "local_run": {"path": str(run_root), "events_path": str(journal)},
    }

    projected = BuilderAutomationService.project_session(session)

    assert projected["budget_usage"]["declared"]["max_model_tokens"] == 80000
    assert projected["budget_usage"]["observed"]["model_tokens"] == 1850
    assert projected["budget_usage"]["observed"]["cached_input_tokens"] == 400
    assert projected["budget_usage"]["observed"]["attempts"] == 2
    assert projected["budget_usage"]["observed"]["wall_seconds"] == 60.0
    assert projected["budget_usage"]["observed"]["terminal"] is True
    assert projected["budget_usage"]["status"] == "within_budget"
    assert projected["budget_usage"]["overrun_tokens"] == 0
    assert projected["created_at"] == "2026-08-18T00:00:00Z"


def test_automation_budget_projection_marks_legacy_max_tokens_overrun(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    runtime_root = run_root / "runtime"
    runtime_root.mkdir(parents=True)
    journal = runtime_root / "codex-events.jsonl"
    journal.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1200,
                    "output_tokens": 600,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = {
        "status": "completed",
        "task": {
            "assigned_at": "2026-08-18T00:00:00Z",
            "updated_at": "2026-08-18T00:00:30Z",
            "realize_request": {
                "artifacts": {
                    "execution_budget": {
                        "max_tokens": 1000,
                        "max_wall_seconds": 300,
                    }
                }
            },
        },
        "local_run": {"path": str(run_root), "events_path": str(journal)},
    }

    projected = BuilderAutomationService.project_session(session)

    assert projected["budget_usage"]["status"] == "exceeded"
    assert projected["budget_usage"]["overrun_tokens"] == 800


def test_automation_budget_projection_uses_fresh_plus_output_metric(tmp_path: Path) -> None:
    run_root = tmp_path / "run"
    runtime_root = run_root / "runtime"
    runtime_root.mkdir(parents=True)
    journal = runtime_root / "codex-events.jsonl"
    journal.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 46_600,
                    "cached_input_tokens": 40_192,
                    "output_tokens": 427,
                    "reasoning_output_tokens": 183,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = {
        "status": "completed",
        "task": {
            "assigned_at": "2026-09-01T23:00:00Z",
            "updated_at": "2026-09-01T23:01:00Z",
            "realize_request": {
                "artifacts": {
                    "execution_budget": {
                        "max_model_tokens": 12_000,
                        "token_budget_metric": "fresh_plus_output",
                    }
                }
            },
        },
        "local_run": {"path": str(run_root), "events_path": str(journal)},
    }

    projected = BuilderAutomationService.project_session(session)

    observed = projected["budget_usage"]["observed"]
    assert observed["model_tokens"] == 47_027
    assert observed["reasoning_tokens"] == 183
    assert observed["budget_metric"] == "fresh_plus_output"
    assert observed["budget_tokens"] == 6_835
    assert projected["budget_usage"]["status"] == "within_budget"
    assert projected["budget_usage"]["overrun_tokens"] == 0
    assert projected["budget_usage"]["billable_limit_tokens"] == 96_000
    assert projected["budget_usage"]["billable_status"] == "within_budget"


def test_terminal_codex_usage_is_reported_once_with_provider_counts(tmp_path: Path) -> None:
    service = _service(tmp_path)
    contexts = service._contexts()
    capsule = contexts.register_capsule(
        {
            "kind": "task",
            "subject_refs": ["builder-run:usage-test"],
            "authority_ref": "change:usage-test",
            "trust_class": "validated",
            "sensitivity": "workspace",
            "license": "internal",
            "retention_class": "episodic_run",
            "summary": "usage attribution test",
        }
    )
    contexts.bind_subject(
        subject_ref="builder-run:usage-test",
        capsule_id=capsule["capsule_id"],
        purpose="builder.automation",
        audience="builder",
    )
    resolution = contexts.resolve(
        {
            "subject_refs": ["builder-run:usage-test"],
            "purpose": "builder.automation",
            "audience": "builder",
        }
    )
    plan = contexts.plan({"resolution": resolution, "token_budget": 1_000})
    calls: list[dict] = []
    service.codex_usage_reporter = lambda event: (
        calls.append(dict(event))
        or {
            "ok": True,
            "duplicate": False,
            "event": {"event_id": "codex_usage_task_1"},
        }
    )
    run_root = tmp_path / "run"
    runtime_root = run_root / "runtime"
    runtime_root.mkdir(parents=True)
    journal = runtime_root / "codex-events.jsonl"
    journal.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 1200,
                    "cached_input_tokens": 300,
                    "output_tokens": 400,
                    "reasoning_tokens": 80,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.1",
        "updated_at": "2026-08-29T06:00:00+00:00",
        "local_run": {"path": str(run_root), "events_path": str(journal)},
        "context_control": {
            "run_ref": "builder-run:usage-test",
            "project_ref": "project:recipe_suite",
            "plan_ref": plan["plan_ref"],
            "selected_refs": [capsule["capsule_id"]],
        },
    }

    first = service._report_terminal_codex_usage(session, task_status="failed")
    second = service._report_terminal_codex_usage(first, task_status="failed")

    assert len(calls) == 1
    assert calls[0]["status"] == "failed"
    assert calls[0]["total_tokens"] == 1600
    assert calls[0]["reasoning_tokens"] == 80
    assert calls[0]["idempotency_key"].endswith(":task.1:codex-usage:v1")
    assert second["codex_usage_accounting"]["status"] == "reported"
    assert second["codex_usage_accounting"]["total_tokens"] == 1600
    assert second["codex_usage_accounting"]["model_tokens"] == 1600
    assert second["codex_usage_accounting"]["billable_tokens"] == 1600
    attribution = second["context_attribution_receipt"]
    assert attribution["status"] == "recorded"
    assert attribution["usage"]["provider_input_tokens"] == 1200
    assert attribution["usage"]["cached_input_tokens"] == 300
    assert attribution["usage"]["fresh_plus_output"] == 1300
    inspection = contexts.inspect("builder-run:usage-test")
    assert inspection["receipt_count"] == 1
    assert inspection["receipts"][0]["subject_refs"] == [
        "builder-run:usage-test",
        "project:recipe_suite",
    ]


def test_preserved_candidate_budget_projection_is_not_applicable() -> None:
    task = {
        "assigned_at": "2026-09-01T23:00:00Z",
        "updated_at": "2026-09-01T23:01:00Z",
        "realize_request": {
            "artifacts": {
                "execution_budget": {
                    "max_model_tokens": 12_000,
                    "token_budget_metric": "fresh_plus_output",
                },
                "continuation_checkpoint": {
                    "mode": "validate_preserved_candidate",
                    "source_task_id": "task.source",
                },
            }
        },
    }

    projected = BuilderAutomationService._budget_usage_projection(
        status="completed",
        task=task,
        local_run={},
    )

    assert projected is not None
    assert projected["observed"]["budget_tokens"] == 0
    assert projected["status"] == "not_applicable"


def test_compact_persisted_status_retains_budget_trial_and_unique_usage(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    receipt = {
        "schema": "adaos.builder.codex_usage_receipt.v1",
        "task_id": "task.compact",
        "status": "reported",
        "accuracy": "exact",
        "input_tokens": 0,
        "cached_input_tokens": 0,
        "output_tokens": 0,
        "reasoning_tokens": 0,
        "model_tokens": 0,
        "total_tokens": 0,
        "billable_tokens": 0,
        "idempotency_key": "builder:compact:usage:v1",
        "root_event_id": "codex_usage_compact",
        "execution_strategy": "structured_edits",
    }
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.demo",
        "object_type": "skill",
        "object_id": "demo",
        "status": "completed",
        "current_task_id": "task.compact",
        "task_history": ["task.compact"],
        "iteration": 1,
        "webspace_id": "desktop",
        "created_at": "2026-09-02T00:00:00Z",
        "updated_at": "2026-09-02T00:01:00Z",
        "execution_budget": {
            "max_model_tokens": 4_000,
            "token_budget_metric": "fresh_plus_output",
            "max_billable_tokens": 32_000,
        },
        "task": {
            "task_id": "task.compact",
            "assigned_at": "2026-09-02T00:00:00Z",
            "updated_at": "2026-09-02T00:01:00Z",
            "realize_request": {
                "schema": "adaos.builder.realize_request.v1",
                "artifacts": {
                    "execution_budget": {
                        "max_model_tokens": 4_000,
                        "token_budget_metric": "fresh_plus_output",
                        "max_billable_tokens": 32_000,
                    },
                    "structured_edits": [{"path": "skills/demo/webui.json"}],
                },
            },
        },
        "codex_usage_accounting": receipt,
        "codex_usage_history": [receipt],
        "completion_readiness": {
            "ok": True,
            "task_id": "task.compact",
            "checks": [],
            "aprobation": {
                "ok": True,
                "mode": "trial",
                "trial": {
                    "candidate_id": "candidate.demo",
                    "candidate_digest": "sha256:demo",
                    "version": "1.2.3",
                    "status": "trial",
                },
            },
        },
    }

    service._save_session(session)
    summary = json.loads(
        service._compact_status_path("skill", "demo").read_text(encoding="utf-8")
    )

    assert summary["automation"]["budget_usage"]["declared"]["max_model_tokens"] == 4_000
    assert summary["automation"]["budget_usage"]["observed"]["model_tokens"] == 0
    assert summary["automation"]["budget_usage"]["status"] == "not_applicable"
    assert summary["automation"]["budget_usage"]["billable_status"] == "not_applicable"
    assert summary["session"]["completion"]["trial"] == {
        "ok": True,
        "mode": "trial",
        "candidate_id": "candidate.demo",
        "candidate_digest": "sha256:demo",
        "version": "1.2.3",
        "status": "trial",
    }
    assert summary["session"]["usage"]["receipt_count"] == 1


def test_terminal_codex_usage_missing_journal_is_unavailable_not_zero(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.codex_usage_reporter = lambda _event: pytest.fail("empty usage must not be reported")
    session = {
        "session_id": "automation.skill.direction",
        "object_type": "skill",
        "object_id": "direction",
        "current_task_id": "task.empty",
        "local_run": {"path": str(tmp_path / "missing")},
    }

    result = service._report_terminal_codex_usage(session, task_status="completed")

    assert result["codex_usage_accounting"]["status"] == "unavailable"
    assert result["codex_usage_accounting"]["total_tokens"] is None


def test_terminal_codex_usage_marks_preserved_candidate_validation_as_exact_zero(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []
    service.codex_usage_reporter = lambda event: (
        calls.append(dict(event))
        or {
            "ok": True,
            "duplicate": False,
            "event": {"event_id": "codex_usage_zero"},
        }
    )
    session = {
        "session_id": "automation.skill.demo",
        "object_type": "skill",
        "object_id": "demo",
        "current_task_id": "task.finalizer",
        "local_run": {"path": str(tmp_path / "missing")},
        "continuation_history": [
            {
                "mode": "validate_preserved_candidate",
                "source_task_id": "task.source",
                "resumed_by_task_id": "task.finalizer",
            }
        ],
    }

    result = service._report_terminal_codex_usage(session, task_status="completed")

    receipt = result["codex_usage_accounting"]
    assert receipt["status"] == "reported"
    assert receipt["accuracy"] == "exact"
    assert receipt["total_tokens"] == 0
    assert receipt["billable_tokens"] == 0
    assert receipt["root_event_id"] == "codex_usage_zero"
    assert len(calls) == 1
    assert calls[0]["idempotency_key"] == receipt["idempotency_key"]
    assert calls[0]["run_id"] == "task.finalizer"
    assert calls[0]["project_id"] == "demo"
    assert calls[0]["metering_disposition"] == "zero_model"
    assert calls[0]["total_tokens"] == 0
    assert calls[0]["billable_tokens"] == 0
    assert calls[0]["note"] == (
        "builder_status=completed; deterministic_strategy=preserved_candidate"
    )
    assert result["codex_usage_history"] == [receipt]
    assert result["updated_at"]


def test_terminal_codex_usage_marks_structured_edits_as_exact_zero(tmp_path: Path) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []
    service.codex_usage_reporter = lambda event: (
        calls.append(dict(event))
        or {
            "ok": True,
            "duplicate": False,
            "event": {"event_id": "codex_usage_structured_zero"},
        }
    )
    session = {
        "session_id": "automation.skill.demo",
        "object_type": "skill",
        "object_id": "demo",
        "current_task_id": "task.structured",
        "local_run": {"path": str(tmp_path / "missing")},
        "last_result": {
            "execution_strategy": "structured_edits",
            "provenance": {"execution_strategy": "structured_edits"},
        },
    }

    result = service._report_terminal_codex_usage(session, task_status="completed")

    receipt = result["codex_usage_accounting"]
    assert receipt["status"] == "reported"
    assert receipt["execution_strategy"] == "structured_edits"
    assert receipt["total_tokens"] == 0
    assert receipt["billable_tokens"] == 0
    assert receipt["root_event_id"] == "codex_usage_structured_zero"
    assert len(calls) == 1
    assert calls[0]["idempotency_key"] == receipt["idempotency_key"]
    assert calls[0]["run_id"] == "task.structured"
    assert calls[0]["metering_disposition"] == "zero_model"
    assert calls[0]["total_tokens"] == 0
    assert calls[0]["billable_tokens"] == 0
    assert calls[0]["note"] == (
        "builder_status=completed; deterministic_strategy=structured_edits"
    )
    assert calls[0]["project_id"] == "demo"


def test_terminal_codex_usage_marks_validation_only_as_exact_zero(tmp_path: Path) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []
    service.codex_usage_reporter = lambda event: (
        calls.append(dict(event))
        or {
            "ok": True,
            "duplicate": False,
            "event": {"event_id": "codex_usage_validation_zero"},
        }
    )
    session = {
        "session_id": "automation.skill.demo",
        "object_type": "skill",
        "object_id": "demo",
        "current_task_id": "task.validation-only",
        "local_run": {"path": str(tmp_path / "missing")},
        "last_result": {
            "execution_strategy": "validation_only",
            "provenance": {"execution_strategy": "validation_only"},
        },
    }

    result = service._report_terminal_codex_usage(session, task_status="completed")

    receipt = result["codex_usage_accounting"]
    assert receipt["status"] == "reported"
    assert receipt["execution_strategy"] == "validation_only"
    assert receipt["total_tokens"] == 0
    assert receipt["root_event_id"] == "codex_usage_validation_zero"
    assert calls[0]["metering_disposition"] == "zero_model"
    assert calls[0]["note"] == (
        "builder_status=completed; deterministic_strategy=validation_only"
    )


def test_structured_edit_brief_supersedes_failed_model_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    brief = json.dumps(
        {
            "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
            "repair_hints": {
                "structured_edits": {
                    "schema": "adaos.builder.structured_edits.v1",
                    "operations": [
                        {
                            "operation": "replace_text",
                            "path": "skills/demo/webui.json",
                            "old": "Selected metric trend",
                            "new": "Current metric trend",
                            "expected_count": 2,
                        }
                    ],
                }
            },
        }
    )

    assert _brief_has_structured_edits(brief) is True
    assert _brief_has_structured_edits("Implement the requested change.") is False
    assert _brief_has_structured_edits(
        json.dumps({"repair_hints": {"structured_edits": {"operations": []}}})
    ) is False

    service = _service(tmp_path)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_budget_continuation_checkpoint",
        lambda _service, _session: {"mode": "validate_preserved_candidate"},
    )
    assert service._qualified_continuation_checkpoint(
        {"implementation_brief": brief}
    ) is None
    assert service._qualified_continuation_checkpoint(
        {"implementation_brief": "Implement the requested change."}
    ) == {"mode": "validate_preserved_candidate"}


def test_structured_edit_context_projection_keeps_authority_without_prompt_payload() -> None:
    brief = json.dumps(
        {
            "ticket_id": "dticket.demo",
            "repair_hints": {
                "profile": "surgical_ui",
                "target_files": ["skills/demo/webui.json"],
                "target_refs": ["widget:chart.title"],
                "acceptance_checks": ["The title is Current metric trend."],
                "structured_edits": {
                    "schema": "adaos.builder.structured_edit_set.v1",
                    "operations": [
                        {
                            "op": "replace_text",
                            "path": "skills/demo/webui.json",
                            "old": "Selected metric trend",
                            "new": "Current metric trend",
                            "expected_count": 2,
                        }
                    ],
                },
            },
        }
    )

    projection = _iteration_context_projection(
        {"digest": "sha256:packet", "large_payload": "x" * 100_000},
        implementation_brief=brief,
        packet_ref="artifact://context/sha256/packet",
        packet_digest="sha256:packet",
        kind="skill",
        project_id="demo",
    )

    assert projection["schema"] == "adaos.builder.deterministic_context_projection.v1"
    assert projection["context_packet"]["ref"] == "artifact://context/sha256/packet"
    assert projection["repair"]["operation_count"] == 1
    assert projection["authority"]["execution_strategy"] == "structured_edits"
    assert "large_payload" not in projection
    assert len(json.dumps(projection).encode("utf-8")) < 2_000
    assert _context_projection_brief(
        {"implementation_brief": brief},
        "Resume the requalified repair from its candidate.",
    ) == brief
    assert _context_projection_brief(
        {"implementation_brief": "General project brief."},
        "Apply the next iteration.",
    ) == "Apply the next iteration."


def test_validation_only_context_projection_keeps_hash_guard_without_prompt_payload() -> None:
    brief = json.dumps(
        {
            "ticket_id": "dticket.validation",
            "repair_hints": {
                "profile": "surgical_data",
                "validation_only": True,
                "target_files": ["skills/demo/handlers/main.py"],
                "target_refs": ["skill:demo"],
                "acceptance_checks": ["The prepared source passes validation."],
                "source_preconditions": [
                    {
                        "path": "skills/demo/handlers/main.py",
                        "sha256": "sha256:" + "a" * 64,
                        "size": 42,
                    }
                ],
            },
        }
    )

    projection = _iteration_context_projection(
        {"digest": "sha256:packet", "large_payload": "x" * 100_000},
        implementation_brief=brief,
        packet_ref="artifact://context/sha256/packet",
        packet_digest="sha256:packet",
        kind="skill",
        project_id="demo",
    )

    assert projection["schema"] == "adaos.builder.deterministic_context_projection.v1"
    assert projection["authority"]["execution_strategy"] == "validation_only"
    assert projection["repair"]["source_precondition_count"] == 1
    assert projection["repair"]["operation_count"] == 0
    assert "large_payload" not in projection
    assert _context_projection_brief(
        {"implementation_brief": brief},
        "Do more work.",
    ) == brief


def test_validation_only_continuation_keeps_no_model_context_semantics(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    source = service.dev_scenarios_root / "recipes" / "webui.json"
    content = source.read_bytes()
    brief = json.dumps(
        {
            "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
            "ticket_id": "dticket.validation-continuation",
            "summary": "Validate the prepared scenario source.",
            "repair_hints": {
                "profile": "surgical_ui",
                "validation_only": True,
                "target_files": ["scenarios/recipes/webui.json"],
                "source_preconditions": [
                    {
                        "path": "scenarios/recipes/webui.json",
                        "sha256": "sha256:" + hashlib.sha256(content).hexdigest(),
                        "size": len(content),
                    }
                ],
            },
        }
    )
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=brief,
        links={"development_ticket_id": "dticket.validation-continuation"},
        execution_budget={"max_context_tokens": 8_000, "max_model_tokens": 4_000},
    )

    service.submit_turn(
        text="Resume the requalified Dev Ticket repair from its preserved candidate.",
        object_type="scenario",
        object_id="recipes",
    )

    current = service.get_session("scenario", "recipes")
    assert current is not None
    assert current["context_control"]["model_call_expected"] is False
    assert current["context_control"]["context_budget_source"] == "explicit"
    assert current["context_control"]["required_estimated_tokens"] < 4_000
    task = service.factory.read_task(str(current["current_task_id"]))
    assert task["status"] == "completed"
    result = service._contexts().get_artifact(str(task["result_ref"]))
    assert result["execution_strategy"] == "validation_only"


def test_validation_only_budget_projection_is_not_applicable() -> None:
    task = {
        "assigned_at": "2026-09-01T23:00:00Z",
        "updated_at": "2026-09-01T23:01:00Z",
        "realize_request": {
            "artifacts": {
                "execution_budget": {
                    "max_model_tokens": 8_000,
                    "token_budget_metric": "fresh_plus_output",
                },
                "repair_hints": {"validation_only": True},
            }
        },
    }

    projected = BuilderAutomationService._budget_usage_projection(
        status="completed",
        task=task,
        local_run={},
    )

    assert projected is not None
    assert projected["observed"]["budget_tokens"] == 0
    assert projected["status"] == "not_applicable"


@pytest.mark.parametrize(
    ("validation_failure_message", "expected_reason"),
    [
        (
            "ValueError: Codex changed paths outside the exact repair files: "
            "['skills/demo/tests/test_actual.py']",
            "repair_envelope_requalified_after_path_guard",
        ),
        (
            "ValueError: repair requires successful Root MCP evidence for "
            "adaos_task_root:an admitted tool on the admitted target",
            "trusted_root_mcp_validation_retry",
        ),
    ],
)
def test_validation_failure_reuses_original_budget_candidate_after_requalification(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    validation_failure_message: str,
    expected_reason: str,
) -> None:
    service = _service(tmp_path)
    source_task_id = "task.source-budget-candidate"
    finalizer_task_id = "task.failed-path-guard"
    source_run = service.runs_root / source_task_id
    (source_run / "workspace" / ".git").mkdir(parents=True)
    (source_run / "input").mkdir(parents=True)
    (source_run / "input" / "assignment.json").write_text("{}", encoding="utf-8")
    finalizer_run = service.runs_root / finalizer_task_id
    (finalizer_run / "input").mkdir(parents=True)
    (finalizer_run / "input" / "assignment.json").write_text(
        json.dumps(
            {
                "realize_request": {
                    "artifacts": {
                        "continuation_checkpoint": {
                            "mode": "validate_preserved_candidate",
                            "source_task_id": source_task_id,
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    tasks = {
        source_task_id: {
            "task_id": source_task_id,
            "status": "failed",
            "failure_history": [
                {
                    "failure_id": "failure.source-budget",
                    "message": "Codex token budget exceeded: 50001 > 45000",
                }
            ],
        },
        finalizer_task_id: {
            "task_id": finalizer_task_id,
            "status": "failed",
            "failure_history": [
                {
                    "failure_id": "failure.path-guard",
                    "message": validation_failure_message,
                }
            ],
        },
    }
    service.factory = SimpleNamespace(read_task=lambda task_id: tasks[task_id])
    monkeypatch.setattr(
        automation_module,
        "_preserved_candidate_has_changes",
        lambda _run_root: True,
    )

    checkpoint = service._budget_continuation_checkpoint(
        {"current_task_id": finalizer_task_id}
    )

    assert checkpoint is not None
    assert checkpoint["mode"] == "validate_preserved_candidate"
    assert checkpoint["source_task_id"] == source_task_id
    assert checkpoint["failure_id"] == "failure.source-budget"
    assert checkpoint["trigger_failure_id"] == "failure.path-guard"
    assert checkpoint["reason"] == expected_reason


def test_budget_continuation_skips_unchanged_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    task_id = "task.empty-budget-candidate"
    run_root = service.runs_root / task_id
    (run_root / "workspace" / ".git").mkdir(parents=True)
    (run_root / "input").mkdir(parents=True)
    (run_root / "input" / "assignment.json").write_text("{}", encoding="utf-8")
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": task_id,
            "status": "failed",
            "failure_history": [
                {"message": "Codex token budget exceeded: observed 11 of 10 tokens."}
            ],
        }
    )
    monkeypatch.setattr(
        automation_module,
        "_preserved_candidate_has_changes",
        lambda _run_root: False,
    )

    assert service._budget_continuation_checkpoint({"current_task_id": task_id}) is None


def test_failed_dev_ticket_resume_updates_brief_before_submitting_continuation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation.skill.demo",
            "object_type": "skill",
            "object_id": "demo",
            "status": "failed",
            "implementation_brief": "stale brief",
            "links": {"development_ticket_id": "dticket.demo"},
            "created_at": "2026-09-01T00:00:00+00:00",
            "updated_at": "2026-09-01T00:00:00+00:00",
        }
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda _service, session: session,
    )
    submitted: list[dict] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "submit_turn",
        lambda _service, **kwargs: submitted.append(dict(kwargs))
        or {"ok": True, "status": "automation_queued"},
    )

    result = service.resume_failed_dev_ticket_repair(
        object_type="skill",
        object_id="demo",
        implementation_brief="requalified brief",
        links={
            "development_ticket_id": "dticket.demo",
            "builder_repair_id": "repair.demo",
        },
        execution_budget={"max_tokens": 45000, "max_wall_seconds": 600},
    )

    updated = service.get_session("skill", "demo")
    assert updated is not None
    assert updated["implementation_brief"] == "requalified brief"
    assert updated["links"]["builder_repair_id"] == "repair.demo"
    assert submitted[0]["text"] == "requalified brief"
    assert submitted[0]["execution_budget"]["max_tokens"] == 45000
    assert result["resumed_failed_dev_ticket"] is True


def test_terminal_codex_usage_reports_live_budget_estimate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []
    service.codex_usage_reporter = lambda event: (
        calls.append(dict(event))
        or {"ok": True, "duplicate": False, "event": {"event_id": "codex_usage_estimated"}}
    )
    run_root = tmp_path / "run"
    runtime_root = run_root / "runtime"
    runtime_root.mkdir(parents=True)
    journal = runtime_root / "codex-events.jsonl"
    journal.write_text('{"type":"item.completed","item":{"type":"command_execution"}}\n', encoding="utf-8")
    (runtime_root / "codex-token-budget.json").write_text(
        json.dumps(
            {
                "status": "exceeded",
                "usage": {
                    "accuracy": "estimated",
                    "input_tokens": 125000,
                    "cached_input_tokens": 0,
                    "output_tokens": 0,
                    "reasoning_tokens": 0,
                    "model_tokens": 125000,
                },
            }
        ),
        encoding="utf-8",
    )
    session = {
        "session_id": "automation.skill.demo",
        "object_type": "skill",
        "object_id": "demo",
        "current_task_id": "task.estimated",
        "updated_at": "2026-08-31T12:00:00+00:00",
        "local_run": {"path": str(run_root), "events_path": str(journal)},
    }

    result = service._report_terminal_codex_usage(session, task_status="failed")

    assert calls[0]["accuracy"] == "estimated"
    assert calls[0]["total_tokens"] == 125000
    assert result["codex_usage_accounting"]["accuracy"] == "estimated"
    assert result["codex_usage_accounting"]["status"] == "reported"


def test_followup_turn_can_replace_bounded_execution_budget(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        execution_budget={
            "schema": "adaos.builder.execution_budget.v1",
            "source": "test.initial",
            "max_model_tokens": 120000,
            "max_wall_seconds": 1200,
        },
    )

    followed = service.submit_turn(
        text="Continue from acceptance evidence with a bounded validation pass.",
        object_type="scenario",
        object_id="recipes",
        execution_budget={
            "source": "test.continuation",
            "max_model_tokens": 200000,
            "max_wall_seconds": 1800,
        },
    )

    session = followed["session"]
    assert session["execution_budget"]["max_model_tokens"] == 200000
    assert session["execution_budget_history"][-1]["max_model_tokens"] == 120000
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == session["current_task_id"]
    )
    assert task["realize_request"]["artifacts"]["execution_budget"]["max_model_tokens"] == 200000


def test_background_automation_launches_durable_worker_process(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.background = True
    monkeypatch.delenv("ADAOS_BUILDER_AUTOMATION_RESOURCE_PRIORITY", raising=False)
    launched: list[tuple[list[str], dict]] = []
    worker_root = (
        service.state_dir
        / "builder"
        / "automation_workers"
        / "automation.scenario.recipes"
    )

    def _popen(command, **kwargs):
        launched.append((list(command), dict(kwargs)))
        worker_root.mkdir(parents=True, exist_ok=True)
        (worker_root / "ready.json").write_text(
            json.dumps(
                {
                    "schema": "adaos.builder.automation_worker_ready.v1",
                    "session_id": "automation.scenario.recipes",
                    "status": "ready",
                    "pid": 4243,
                    "ready_at": "2026-08-20T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(pid=4242, poll=lambda: None)

    monkeypatch.setattr(automation_module.subprocess, "Popen", _popen)

    result = service._launch_worker_process("automation.scenario.recipes")

    assert result["pid"] == 4242
    assert result["status"] == "ready"
    assert result["worker_pid"] == 4243
    assert result["repo_root"] == str(service.repo_root.resolve())
    assert result["executable"]
    assert launched[0][0][-2:] == ["--session-id", "automation.scenario.recipes"]
    assert launched[0][1]["env"]["ADAOS_BASE_DIR"] == str(service.state_dir.parent.resolve())
    launch = json.loads(
        (
            service.state_dir
            / "builder"
            / "automation_workers"
            / "automation.scenario.recipes"
            / "launch.json"
        ).read_text(encoding="utf-8")
    )
    assert launch["session_id"] == "automation.scenario.recipes"
    assert launch["status"] == "ready"
    assert launch["resource_policy"]["mode"] == "background"
    assert launch["resource_policy"]["inherited_by_children"] is True
    if automation_module.os.name == "nt":
        breakaway = getattr(
            automation_module.subprocess, "CREATE_BREAKAWAY_FROM_JOB", 0
        )
        if breakaway:
            assert launched[0][1]["creationflags"] & breakaway
            assert launch["resource_policy"]["job_breakaway"] is True


def test_background_automation_accepts_booting_handshake(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.background = True
    worker_root = (
        service.state_dir
        / "builder"
        / "automation_workers"
        / "automation.skill.booting"
    )

    def _popen(command, **kwargs):
        worker_root.mkdir(parents=True, exist_ok=True)
        (worker_root / "ready.json").write_text(
            json.dumps(
                {
                    "schema": "adaos.builder.automation_worker_ready.v1",
                    "session_id": "automation.skill.booting",
                    "status": "booting",
                    "pid": 5253,
                    "recorded_at": "2026-09-03T00:00:00+00:00",
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(pid=5252, poll=lambda: None)

    monkeypatch.setattr(automation_module.subprocess, "Popen", _popen)

    result = service._launch_worker_process("automation.skill.booting")

    assert result["status"] == "booting"
    assert result["worker_pid"] == 5253
    assert result["booting_at"] == "2026-09-03T00:00:00+00:00"


def test_background_automation_does_not_relaunch_active_worker(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.background = True
    monkeypatch.setattr(
        BuilderAutomationService,
        "_detached_worker_is_active",
        lambda _self, _session_id: True,
    )
    launches: list[str] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_launch_worker_process",
        lambda _self, session_id: launches.append(session_id),
    )

    service._launch_worker("automation.skill.active")

    assert launches == []


def test_background_automation_rejects_worker_without_ready_handshake(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    service.background = True

    monkeypatch.setattr(
        automation_module.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SimpleNamespace(pid=4242, poll=lambda: 7),
    )

    with pytest.raises(RuntimeError, match="exited before readiness handshake"):
        service._launch_worker_process("automation.skill.failed")

    launch = json.loads(
        (
            service.state_dir
            / "builder"
            / "automation_workers"
            / "automation.skill.failed"
            / "launch.json"
        ).read_text(encoding="utf-8")
    )
    assert launch["status"] == "failed"
    assert "code 7" in launch["error"]


def test_automation_worker_uses_background_priority_on_windows(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_BUILDER_AUTOMATION_RESOURCE_PRIORITY", raising=False)
    monkeypatch.setattr(automation_module.subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0x4000, raising=False)

    command, creationflags, policy = automation_module._automation_worker_resource_policy(
        ["python", "-m", "worker"],
        platform_name="nt",
    )

    assert command == ["python", "-m", "worker"]
    assert creationflags & 0x4000
    assert policy["cpu_priority"] == "below_normal"
    assert policy["inherited_by_children"] is True


def test_automation_worker_uses_nice_and_idle_io_on_linux(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_BUILDER_AUTOMATION_RESOURCE_PRIORITY", raising=False)
    monkeypatch.setattr(
        automation_module.shutil,
        "which",
        lambda name: f"/usr/bin/{name}" if name in {"nice", "ionice"} else None,
    )

    command, creationflags, policy = automation_module._automation_worker_resource_policy(
        ["python", "-m", "worker"],
        platform_name="posix",
    )

    assert command == [
        "/usr/bin/ionice",
        "-c",
        "3",
        "/usr/bin/nice",
        "-n",
        "10",
        "python",
        "-m",
        "worker",
    ]
    assert creationflags == 0
    assert policy["cpu_priority"] == "nice:10"
    assert policy["io_priority"] == "idle"


def test_automation_rejects_unvalidated_prototype_handoff_before_mutation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    with pytest.raises(ValueError, match="invalid prototype handoff"):
        service.start_from_execute(
            object_type="scenario",
            object_id="recipes",
            implementation_brief="Implement the executable prototype requirements.",
            prototype_handoff={"schema": "adaos.builder.prototype_handoff.v1"},
        )
    assert service.get_session("scenario", "recipes") is None


def test_automation_worker_executes_its_submitted_task_not_an_older_queue_item(tmp_path: Path) -> None:
    service = _service(tmp_path)
    older = service.factory.submit_realize_request(
        {
            "request_id": "realize.test.older-builder-task",
            "target": {"type": "scenario", "id": "older_scenario"},
        }
    )["task"]

    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the approved recipe prototype.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    status = service.status(object_type="scenario", object_id="recipes")
    assert status["session"]["status"] == "completed"
    submitted_task_id = status["session"]["current_task_id"]
    tasks = {
        item["task_id"]: item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
    }
    assert tasks[older["task_id"]]["status"] == "queued"
    assert tasks[submitted_task_id]["status"] == "completed"


def test_automation_carries_active_change_set_into_isolated_codex_request(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-store-sync",
            "request": "Synchronize shopping items with the store API.",
            "issues": [
                {
                    "issue_id": "store-sync",
                    "title": "Implement transactional store synchronization",
                    "lane": "automation",
                    "acceptance_criteria": [
                        "A failed remote request leaves the local shopping list unchanged."
                    ],
                }
            ],
        },
    )

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the approved store synchronization change set.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert started["session"]["change_set_id"] == "CS-recipes-store-sync"
    assert started["automation"]["change_set_id"] == "CS-recipes-store-sync"
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )
    request = task["realize_request"]
    assert request["links"]["change_set_id"] == "CS-recipes-store-sync"
    assert request["links"]["canonical_change_id"] == "CS-recipes-store-sync"
    assert request["links"]["context_packet_digest"].startswith("sha256:")
    assert request["artifacts"]["change_set"]["issues"][0]["issue_id"] == "store-sync"
    packet = _realize_content_artifact(service, task, "context_packet")
    assert packet["schema"] == "adaos.builder.context_packet.v1"
    assert packet["digest"] == request["links"]["context_packet_digest"]
    assert packet["change"]["change_id"] == "CS-recipes-store-sync"
    assert started["session"]["canonical_change_id"] == "CS-recipes-store-sync"
    assert started["session"]["context_packet_digest"] == packet["digest"]
    serialized_packet = json.dumps(packet, ensure_ascii=False).lower()
    assert "raw_transcript" not in serialized_packet
    assert "secret" not in serialized_packet
    assert (
        "A failed remote request leaves the local shopping list unchanged."
        in request["acceptance"]["checks"]
    )
    workflow = service._workflow().describe("scenario", "recipes")
    assert started["session"]["change_id"] in workflow["change_set"]["member_change_ids"]
    automation_run = next(
        item
        for item in workflow["change"]["runs"]
        if item["run_id"] == started["session"]["current_task_id"]
    )
    assert automation_run["context_packet_digest"] == packet["digest"]
    assert automation_run["status"] == "running"
    assert automation_run["activity"] == "automation_started"


def test_automation_rejects_change_set_before_prototype_approval(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-layout",
            "request": "Add a favorites section.",
            "issues": [
                {
                    "issue_id": "favorites-layout",
                    "title": "Add a favorites section",
                    "lane": "prototype",
                    "acceptance_criteria": ["Favorites is visible in the navigation."],
                }
            ],
        },
    )

    with pytest.raises(ValueError, match="Prototype approval gate"):
        service.start_from_execute(
            object_type="scenario",
            object_id="recipes",
            implementation_brief="Implement favorites.",
            webspace_id="prompt-dev",
        )


def test_dev_ticket_followup_extends_active_trial_batch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = service._workflow()
    workflow.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-trial",
            "request": "Implement the first accepted repair.",
            "issues": [
                {
                    "issue_id": "first-repair",
                    "title": "Implement the first accepted repair",
                    "lane": "automation",
                    "acceptance_criteria": ["The first repair works."],
                }
            ],
        },
    )
    workflow.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"confirmed": True, "task_id": "task.first", "run_id": "task.first"},
    )
    workflow.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"confirmed": True, "task_id": "task.first", "version": "0.1.1"},
    )
    workflow.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "confirmed": True,
            "change_id": "checkpoint-first",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )

    followup_brief = json.dumps(
        {
            "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
            "summary": "Rename the visible metrics heading.",
            "repair_hints": {"profile": "surgical_ui"},
            "context": "x" * 5000,
        }
    )
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=followup_brief,
        webspace_id="prompt-dev",
        links={"development_ticket_id": "dticket.followup"},
    )

    assert started["session"]["change_set_id"] == "CS-recipes-trial"
    projected = workflow.describe("scenario", "recipes")
    followup = next(
        item
        for item in projected["change_set"]["issues"]
        if item["issue_id"] == "automation-followup-dticket.followup"
    )
    assert followup["lane"] == "automation"
    assert followup["source_message_ids"] == ["dticket.followup"]
    assert started["session"]["links"]["development_ticket_ids"] == [
        "dticket.followup"
    ]
    assert "dticket.followup" in projected["change_set"]["source_message_ids"]
    assert projected["change_set"]["request_addenda"][-1] == (
        "Rename the visible metrics heading."
    )
    assert started["session"]["current_task_id"] != "task.first"
    task = service.factory.read_task(started["session"]["current_task_id"])
    task_checks = task["realize_request"]["acceptance"]["checks"]
    assert any("Rename the visible metrics heading" in item for item in task_checks)
    assert not any("first repair works" in item.lower() for item in task_checks)
    execution_change = task["realize_request"]["artifacts"]["change_set"]
    assert [item["issue_id"] for item in execution_change["issues"]] == [
        "automation-followup-dticket.followup"
    ]


def test_followup_recovers_stale_trial_preparation_without_candidate(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the first accepted repair.",
        webspace_id="prompt-dev",
    )
    session = service.status(object_type="scenario", object_id="recipes")["session"]
    assert session["status"] == "completed"
    workflow = service._workflow()
    workflow.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": session["current_task_id"], "version": "0.1.1"},
    )
    workflow.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "confirmed": True,
            "change_id": session["change_id"],
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )
    before = workflow.describe("scenario", "recipes")
    assert before["governed"]["state"] == "trial_ready"
    workflow.transition(
        "scenario",
        "recipes",
        "candidate_preparation_started",
        metadata={
            "confirmed": True,
            "activity_attempt_id": "trial-attempt.interrupted",
            "idempotency_key": "trial-attempt.interrupted",
        },
    )
    state_path = workflow._state_path("scenario", "recipes")
    persisted = json.loads(state_path.read_text(encoding="utf-8"))
    persisted["workflow"]["delivery"]["activation_started_at"] = "2000-01-01T00:00:00+00:00"
    state_path.write_text(json.dumps(persisted), encoding="utf-8")

    result = service.start_followup_dev_ticket_repair(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Rename the selected metric heading.",
        links={"development_ticket_id": "dticket.followup"},
        webspace_id="prompt-dev",
    )

    projected = workflow.describe("scenario", "recipes")
    actions = [item["action"] for item in projected["history"]]
    assert result["followup_dev_ticket_repair"] is True
    assert "candidate_preparation_unknown" in actions
    assert "change_issues_added" in actions
    assert any(
        item["issue_id"] == "automation-followup-dticket.followup"
        for item in projected["change_set"]["issues"]
    )


def test_followup_does_not_interrupt_active_trial_preparation(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the first accepted repair.",
        webspace_id="prompt-dev",
    )
    session = service.status(object_type="scenario", object_id="recipes")["session"]
    assert session["status"] == "completed"
    workflow = service._workflow()
    workflow.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": session["current_task_id"], "version": "0.1.1"},
    )
    workflow.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "confirmed": True,
            "change_id": session["change_id"],
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )
    workflow.transition(
        "scenario",
        "recipes",
        "candidate_preparation_started",
        metadata={
            "confirmed": True,
            "activity_attempt_id": "trial-attempt.active",
            "idempotency_key": "trial-attempt.active",
        },
    )

    with pytest.raises(ValueError, match="waiting for active Trial preparation"):
        service.start_followup_dev_ticket_repair(
            object_type="scenario",
            object_id="recipes",
            implementation_brief="Rename the selected metric heading.",
            links={"development_ticket_id": "dticket.followup"},
            webspace_id="prompt-dev",
        )

    projected = workflow.describe("scenario", "recipes")
    assert projected["governed"]["state"] == "trial_waiting"
    assert not any(
        item["issue_id"] == "automation-followup-dticket.followup"
        for item in projected["change_set"]["issues"]
    )


def test_reopened_dev_ticket_adds_a_revision_to_active_trial_batch(tmp_path: Path) -> None:
    service = _service(tmp_path)
    workflow = service._workflow()
    workflow.transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": "CS-recipes-trial",
            "request": "Implement the accepted repair.",
            "issues": [
                {
                    "issue_id": "automation-followup-dticket.followup",
                    "title": "Implement the accepted repair",
                    "lane": "automation",
                    "status": "resolved",
                    "acceptance_criteria": ["The accepted repair works."],
                }
            ],
        },
    )
    workflow.transition(
        "scenario",
        "recipes",
        "automation_started",
        metadata={"confirmed": True, "task_id": "task.first", "run_id": "task.first"},
    )
    workflow.transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"confirmed": True, "task_id": "task.first", "version": "0.1.1"},
    )
    workflow.transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "confirmed": True,
            "change_id": "checkpoint-first",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief=json.dumps(
            {
                "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                "summary": "Correct the semantic CRUD views.",
                "repair_hints": {"profile": "resource_crud"},
            }
        ),
        webspace_id="prompt-dev",
        links={
            "development_ticket_id": "dticket.followup",
            "builder_repair_id": "repair.second",
        },
    )

    projected = workflow.describe("scenario", "recipes")
    revisions = [
        item
        for item in projected["change_set"]["issues"]
        if str(item["issue_id"]).startswith("automation-followup-dticket.followup")
    ]
    assert len(revisions) == 2
    assert revisions[-1]["issue_id"] != "automation-followup-dticket.followup"
    assert revisions[-1]["status"] == "open"
    assert projected["change_set"]["gate"] == "automation"
    assert started["session"]["current_task_id"] != "task.first"


def test_scenario_automation_uses_declared_runtime_skill_as_companion(tmp_path: Path) -> None:
    service = _service(tmp_path)
    scenario = service.dev_scenarios_root / "recipes" / "scenario.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.1.0",
                "depends": ["recipes_control_skill"],
                "runtime": {"skills": {"required": ["recipes_control_skill"]}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    companion = service._resolve_companion_skill_id("scenario", "recipes")

    assert companion == "recipes_control_skill"


def test_scenario_automation_retains_all_previous_automation_companions(tmp_path: Path) -> None:
    service = _service(tmp_path)
    snapshot = (
        service.state_dir
        / "builder"
        / "workflow_snapshots"
        / "scenario"
        / "recipes"
        / "automation"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.2.0",
                "depends": ["recipes_skill", "recipes_control_skill"],
                "runtime": {
                    "skills": {
                        "required": ["recipes_skill", "recipes_control_skill"],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    companions = service._resolve_companion_skill_ids("scenario", "recipes")

    assert companions == ["recipes_skill", "recipes_control_skill"]


def test_scenario_automation_retains_published_companions_as_immutable_baseline(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    assert service.workspace_service is not None
    assert service.workspace_service.scenarios_root is not None
    publication = Path(service.workspace_service.scenarios_root) / "recipes"
    publication.mkdir(parents=True)
    (publication / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.4.0",
                "depends": ["recipes_skill", "recipes_control_skill"],
                "runtime": {
                    "skills": {
                        "required": ["recipes_skill", "recipes_control_skill"],
                    }
                },
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (publication / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {}}}),
        encoding="utf-8",
    )

    companions = service._resolve_companion_skill_ids("scenario", "recipes")
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Preserve the installed behavior while applying the approved prototype.",
        webspace_id="prompt-dev",
    )
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )

    assert companions == ["recipes_skill", "recipes_control_skill"]
    assert task["realize_request"]["artifacts"]["companion_skill_ids"] == companions
    attachment = next(
        item
        for item in task["forge"]["source_snapshot"]["attachments"]
        if item["name"] == "current_publication"
    )
    assert attachment["target_path"] == "scenarios/recipes/.builder_current_publication"
    task_prompt = (
        tmp_path
        / "runs"
        / started["session"]["current_task_id"]
        / "input"
        / "task.md"
    ).read_text(encoding="utf-8")
    assert "immutable currently installed functional edition" in task_prompt
    assert not (
        service.dev_scenarios_root / "recipes" / ".builder_current_publication"
    ).exists()


def test_scenario_automation_keeps_installed_only_skill_outside_mutable_envelope(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    assert service.workspace_service is not None
    assert service.workspace_service.skills_root is not None
    workspace_skill = Path(service.workspace_service.skills_root) / "voice_chat_skill"
    workspace_skill.mkdir(parents=True)
    (workspace_skill / "skill.yaml").write_text(
        yaml.safe_dump({"name": "voice_chat_skill", "version": "1.0.0"}, sort_keys=False),
        encoding="utf-8",
    )
    scenario = service.dev_scenarios_root / "recipes" / "scenario.yaml"
    scenario.write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.1.0",
                "depends": ["recipes_skill", "voice_chat_skill"],
                "runtime": {"skills": {"required": ["recipes_skill", "voice_chat_skill"]}},
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the owned recipe behavior against installed chat APIs.",
        webspace_id="prompt-dev",
    )
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == started["session"]["current_task_id"]
    )

    assert started["session"]["companion_skill_ids"] == ["recipes_skill"]
    assert task["realize_request"]["artifacts"]["companion_skill_ids"] == ["recipes_skill"]
    assert "skills/voice_chat_skill/" not in task["forge"]["sparse_paths"]
    assert not (service.dev_skills_root / "voice_chat_skill").exists()


def test_followup_refreshes_companions_from_current_publication(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the first functional recipe edition.",
        webspace_id="prompt-dev",
    )
    assert service.workspace_service is not None
    assert service.workspace_service.scenarios_root is not None
    publication = Path(service.workspace_service.scenarios_root) / "recipes"
    publication.mkdir(parents=True)
    (publication / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": "recipes",
                "version": "0.4.0",
                "depends": ["recipes_skill", "recipes_control_skill"],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assert service.workspace_service is not None
    service.workspace_service.create_draft(
        kind="skill",
        artifact_id="recipes_control_skill",
        source_idea="Existing published control dependency.",
        template_id="skill_default",
    )

    followed = service.submit_turn(
        text="Apply the next approved prototype without dropping published behavior.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
    )
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == followed["session"]["current_task_id"]
    )

    assert followed["session"]["companion_skill_ids"] == [
        "recipes_skill",
        "recipes_control_skill",
    ]
    assert task["realize_request"]["artifacts"]["companion_skill_ids"] == [
        "recipes_skill",
        "recipes_control_skill",
    ]


@pytest.mark.parametrize("corrupted", ["???????? ??????", "Damaged \ufffd text"])
def test_automation_start_rejects_transport_corrupted_brief_before_writes(
    tmp_path: Path,
    corrupted: str,
) -> None:
    service = _service(tmp_path)

    with pytest.raises(ValueError, match="transport-corrupted"):
        service.start_from_execute(
            object_type="scenario",
            object_id="recipes",
            implementation_brief=corrupted,
            webspace_id="prompt-dev",
        )

    assert service.get_session("scenario", "recipes") is None


def test_automation_followup_rejects_transport_corrupted_text_before_iteration(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )

    with pytest.raises(ValueError, match="transport-corrupted"):
        service.submit_turn(
            text="???? broken follow-up",
            object_type="scenario",
            object_id="recipes",
            webspace_id="prompt-dev",
        )

    current = service.get_session("scenario", "recipes")
    assert current is not None
    assert current["iteration"] == 0
    assert current["change_id"] == started["session"]["change_id"]


def test_completed_automation_routes_chat_to_next_codex_iteration(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )

    turn = service.submit_turn(text="Add filtering by cooking time.", webspace_id="prompt-dev")

    assert turn["handled"] is True
    assert turn["status"] == "automation_queued"
    status = service.status(object_type="scenario", object_id="recipes")
    assert status["session"]["status"] == "completed"
    assert status["session"]["iteration"] == 1
    assert status["session"]["turns"][0]["text"] == "Add filtering by cooking time."
    assert len(status["session"]["task_history"]) == 2
    assert status["session"]["change_id"] != started["session"]["change_id"]
    assert status["session"]["change_history"] == [started["session"]["change_id"]]
    assert status["session"]["change_id"] in status["session"]["task"]["request_id"]
    workflow = service._workflow().describe("scenario", "recipes")
    assert workflow["automation"]["iteration"] == 2
    assert workflow["automation"]["status"] == "working"
    assert workflow["history"][-1]["action"] == "automation_iteration_started"


def test_completed_automation_rebinds_to_approved_successor_change(tmp_path: Path) -> None:
    service = _service(tmp_path)
    first_change_id = "CS-recipes-initial"
    second_change_id = "CS-recipes-reviewed-repair"
    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": first_change_id,
            "request": "Implement recipe search.",
            "issues": [
                {
                    "issue_id": "recipe-search",
                    "title": "Implement recipe search",
                    "lane": "automation",
                    "acceptance_criteria": ["A recipe can be found by name."],
                }
            ],
        },
    )
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    service._workflow().transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.initial", "version": "0.1.1"},
    )
    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": second_change_id,
            "supersedes_change_set_id": first_change_id,
            "request": "Repair the reviewed input policy defect.",
            "issues": [
                {
                    "issue_id": "input-policy",
                    "title": "Use the governed input policy",
                    "lane": "automation",
                    "acceptance_criteria": [
                        "The runner selects its input solely from profile_conditions.input_policy.source."
                    ],
                }
            ],
        },
    )

    followed = service.submit_turn(
        text="Apply the approved input-policy repair.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
    )

    assert followed["status"] == "automation_queued"
    assert followed["session"]["change_set_id"] == second_change_id
    assert followed["session"]["canonical_change_id"] == second_change_id
    assert followed["session"]["change_set_history"] == [first_change_id]
    task = next(
        item
        for item in service.factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == followed["session"]["current_task_id"]
    )
    request = task["realize_request"]
    assert request["links"]["change_set_id"] == second_change_id
    assert request["links"]["canonical_change_id"] == second_change_id
    assert _realize_content_artifact(service, task, "context_packet")["change"][
        "change_id"
    ] == second_change_id
    assert request["artifacts"]["change_set"]["issues"][0]["issue_id"] == "input-policy"


def test_fresh_change_replaces_terminal_session_from_canonical_ready_state(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    first_change_id = "CS-recipes-initial"
    second_change_id = "CS-recipes-successor"
    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": first_change_id,
            "request": "Implement recipe search.",
            "issues": [
                {
                    "issue_id": "recipe-search",
                    "title": "Implement recipe search",
                    "lane": "automation",
                    "acceptance_criteria": ["A recipe can be found by name."],
                }
            ],
        },
    )
    first = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
        change_set_id=first_change_id,
    )
    assert first["session"]["status"] == "queued"
    assert service.status(object_type="scenario", object_id="recipes")["session"][
        "status"
    ] == "completed"
    service._workflow().transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.initial", "version": "0.1.1"},
    )

    service._workflow().transition(
        "scenario",
        "recipes",
        "plan_change_set",
        metadata={
            "change_set_id": second_change_id,
            "supersedes_change_set_id": first_change_id,
            "request": "Implement the accepted successor contract.",
            "issues": [
                {
                    "issue_id": "successor-contract",
                    "title": "Implement the accepted successor contract",
                    "lane": "automation",
                    "acceptance_criteria": ["The successor contract passes verification."],
                }
            ],
        },
    )

    # Simulate a Change planned before the compatibility-head reset existed:
    # the canonical instance is automation_ready while legacy fields still
    # describe the published/completed predecessor.
    workflow_service = service._workflow()
    legacy_state = workflow_service._read_state("scenario", "recipes")
    legacy_state["workflow"]["active_phase"] = "automation"
    legacy_state["workflow"]["automation"] = {
        "status": "completed",
        "iteration": 1,
        "head_task_id": "task.predecessor",
    }
    legacy_state["workflow"]["delivery"] = {"status": "published"}
    workflow_service._write_state("scenario", "recipes", legacy_state)

    successor = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement the accepted successor contract.",
        webspace_id="prompt-dev",
        change_set_id=second_change_id,
    )

    assert successor["session"]["status"] == "queued"
    completed = service.status(object_type="scenario", object_id="recipes")["session"]
    assert completed["status"] == "completed"
    assert completed["change_set_id"] == second_change_id
    assert completed["implementation_brief"] == (
        "Implement the accepted successor contract."
    )
    service._workflow().transition(
        "scenario",
        "recipes",
        "automation_completed",
        metadata={"task_id": "task.successor", "version": "0.1.2"},
    )
    assert service._workflow().describe("scenario", "recipes")["governed"][
        "state"
    ] == "verification"


def test_followup_invalidates_checkpoint_before_queueing_next_iteration(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    service._workflow().transition(
        "scenario", "recipes", "automation_completed", metadata={"task_id": "task.1"}
    )
    service._workflow().transition(
        "scenario",
        "recipes",
        "checkpoint_recorded",
        metadata={
            "confirmed": True,
            "change_id": "checkpoint-1",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "a" * 40,
        },
    )

    turn = service.submit_turn(
        text="Repair the reviewed acceptance failure.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
    )

    workflow = service._workflow().describe("scenario", "recipes")
    assert turn["status"] == "automation_queued"
    assert [item["action"] for item in workflow["history"][-2:]] == [
        "candidate_stale",
        "automation_iteration_started",
    ]
    assert workflow["delivery"]["stale_reason"] == "automation_iteration_requested"


def test_duplicate_queued_start_relaunches_orphaned_worker(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "companion_skill_id": "recipes_skill",
        "webspace_id": "prompt-dev",
        "status": "queued",
        "current_task_id": "task.queued",
    }
    service._save_session(session)
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: (_ for _ in ()).throw(KeyError(_task_id))
    )
    launched: list[str] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_launch_worker",
        lambda self, session_id: launched.append(session_id),
    )

    result = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert result["duplicate"] is True
    assert result["worker_relaunched"] is True
    assert launched == ["automation.scenario.recipes"]
    assert result["session"]["conversation_id"] == "conv.builder.recipes"


def test_followup_backfills_conversation_before_terminal_notification(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    notified: list[str] = []

    def notify(self, session):
        notified.append(str(session.get("conversation_id") or ""))
        return dict(session)

    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", notify)

    service.submit_turn(
        text="Add filtering by cooking time.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert notified == ["conv.builder.recipes"]
    current = service.get_session("scenario", "recipes")
    assert current is not None
    assert current["conversation_id"] == "conv.builder.recipes"


def test_followup_turn_clears_stale_terminal_projection(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    previous = service.get_session("scenario", "recipes")
    assert previous is not None
    previous["completion_readiness"] = {"ok": True, "completed_at": "before"}
    previous["completion_notified_task_id"] = previous["current_task_id"]
    service._save_session(previous)

    turn = service.submit_turn(
        text="Add filtering by cooking time.",
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
    )

    assert turn["automation"]["summary"] is None
    assert "completion_readiness" not in turn["session"]
    assert turn["session"]["completion_history"][0]["completed_at"] == "before"


def test_automation_projection_is_render_safe_and_abi_valid(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )

    result = service.projection(webspace_id="prompt-dev")

    assert result["ok"] is True
    projection = result["automation"]
    assert projection["status"] == "completed"
    assert projection["phase"] == "completed"
    assert projection["can_submit"] is True
    assert projection["project"] == {
        "type": "scenario",
        "id": "recipes",
        "companion_skill_id": "recipes_skill",
        "companion_skill_ids": ["recipes_skill"],
    }
    assert projection["result_branch"] == result["session"]["last_result"]["branch"]
    assert projection["steps"][-1]["state"] == "completed"

    schema_path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / "builder.automation_projection.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(projection)


def test_empty_projection_disables_automation_input() -> None:
    projection = BuilderAutomationService.empty_projection(webspace_id="prompt-dev")

    assert projection["status"] == "idle"
    assert projection["can_submit"] is False
    assert projection["project"] is None


def test_failed_projection_exposes_actionable_diagnostics_and_retry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    session = {
        "session_id": "automation.scenario.builder",
        "object_type": "scenario",
        "object_id": "builder",
        "webspace_id": "dev1-dev",
        "status": "failed",
        "current_task_id": "task.1",
        "last_failure": {
            "message": "codex_executable_not_found",
            "failure_id": "failure.task.1.cli",
            "retryable": True,
            "stage": "in_progress",
        },
        "local_run": {
            "events_path": "run/codex-live.jsonl",
            "stderr_path": "run/codex-live.stderr.log",
            "result_path": "run/result.json",
        },
    }

    projection = service.project_session(session)

    assert projection["can_submit"] is True
    assert projection["error"] == "codex_executable_not_found"
    assert projection["failure_id"] == "failure.task.1.cli"
    assert projection["retryable"] is True
    assert projection["evidence"]["stderr_path"] == "run/codex-live.stderr.log"


def test_projection_event_is_not_reemitted_for_unchanged_status_reads(tmp_path: Path) -> None:
    service = _service(tmp_path)
    events: list[dict] = []
    service.event_sink = lambda payload: events.append(dict(payload))
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    event_count = len(events)
    statuses = [event["status"] for event in events]
    assert "workspace_preparing" in statuses
    assert "in_progress" in statuses
    assert "tests_running" in statuses
    assert statuses[-1] == "completed"

    service.status(object_type="scenario", object_id="recipes")
    service.status(object_type="scenario", object_id="recipes")

    assert len(events) == event_count


def test_completed_iteration_clears_stale_failure_from_session(tmp_path: Path) -> None:
    service = _service(tmp_path)
    started = service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    session = dict(started["session"])
    session["last_failure"] = {"message": "previous attempt failed"}

    refreshed = service.refresh_session(session)

    assert refreshed["status"] == "completed"
    assert "last_failure" not in refreshed


def test_refresh_recovers_terminal_orphan_once_and_finalizes_without_rerunning_codex(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    task_id = "task.orphan"
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "companion_skill_id": "recipes_skill",
        "webspace_id": "prompt-dev",
        "current_task_id": task_id,
        "status": "in_progress",
    }
    service._save_session(session)
    output_dir = Path(service.runs_root) / task_id / "output"
    output_dir.mkdir(parents=True)
    (output_dir / "codex-live.jsonl").write_text(
        '{"type":"turn.completed"}\n',
        encoding="utf-8",
    )
    recovered: list[str] = []

    class _Worker:
        def recover_orphaned_codex_run(self, value: str) -> dict:
            recovered.append(value)
            return {"ok": True}

    service.worker_factory = _Worker

    def read_task(_task_id):
        completed = bool(recovered)
        return {
            "task_id": task_id,
            "status": "completed" if completed else "in_progress",
            "updated_at": "2026-07-28T15:13:00+00:00",
            "result": {"summary": "Recovered result."} if completed else None,
            "progress": [],
        }

    service.factory = SimpleNamespace(read_task=read_task)
    finalized: list[dict] = []

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed["completion_readiness"] = {
            "ok": True,
            "task_id": task_id,
            "completed_at": "2026-07-28T15:14:00+00:00",
        }
        completed.pop("finalizing_task_id", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)

    refreshed = service.refresh_session(session)

    assert recovered == [task_id]
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["last_result"]["summary"] == "Recovered result."
    assert refreshed["status"] == "completed"
    assert refreshed["completion_readiness"]["ok"] is True


def test_refresh_resumes_detached_completed_task_finalization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    task_id = "task.detached-complete"
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.direction",
        "object_type": "skill",
        "object_id": "direction",
        "webspace_id": "builder-calibration",
        "current_task_id": task_id,
        "finalizing_task_id": task_id,
        "status": "commit_ready",
    }
    service._save_session(session)
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": task_id,
            "status": "completed",
            "updated_at": "2026-08-19T11:58:14+00:00",
            "result": {"summary": "Recovered validated result."},
            "progress": [],
        }
    )
    finalized: list[dict] = []

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed["completion_readiness"] = {
            "ok": True,
            "task_id": task_id,
            "completed_at": "2026-08-19T11:58:15+00:00",
        }
        completed.pop("finalizing_task_id", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)

    refreshed = service.refresh_session(session)

    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["last_result"]["summary"] == "Recovered validated result."
    assert refreshed["status"] == "completed"
    assert refreshed["completion_readiness"]["ok"] is True


def test_refresh_defers_finalization_while_detached_worker_owner_is_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    task_id = "task.detached-owned"
    session_id = "automation.skill.direction"
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": session_id,
        "object_type": "skill",
        "object_id": "direction",
        "webspace_id": "builder-calibration",
        "current_task_id": task_id,
        "finalizing_task_id": task_id,
        "status": "commit_ready",
    }
    service._save_session(session)
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": task_id,
            "status": "completed",
            "updated_at": "2026-08-19T14:47:38+00:00",
            "result": {"summary": "Validated result."},
            "progress": [],
        }
    )
    worker_root = (
        service.state_dir
        / "builder"
        / "automation_workers"
        / "automation.skill.direction"
    )
    worker_root.mkdir(parents=True)
    (worker_root / "launch.json").write_text(
        json.dumps(
            {
                "schema": "adaos.builder.automation_worker_launch.v1",
                "session_id": session_id,
                "pid": 4242,
                "create_time": 1234.5,
                "status": "launched",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        LocalSkillFactoryWorker,
        "_process_owner_is_active",
        staticmethod(lambda value: value["pid"] == 4242 and value["create_time"] == 1234.5),
    )
    finalized: list[dict] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_finalize_completed_session",
        lambda _service, value: finalized.append(dict(value)),
    )

    refreshed = service.refresh_session(session)

    assert finalized == []
    assert refreshed["status"] == "commit_ready"
    assert refreshed["finalizing_task_id"] == task_id
    assert refreshed["last_result"]["summary"] == "Validated result."


def test_refresh_never_projects_factory_completion_as_terminal_before_finalization(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    task_id = "task.factory-complete"
    session_id = "automation.skill.direction"
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": session_id,
        "object_type": "skill",
        "object_id": "direction",
        "webspace_id": "builder-calibration",
        "current_task_id": task_id,
        "status": "in_progress",
    }
    service._save_session(session)
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": task_id,
            "status": "completed",
            "updated_at": "2026-08-19T22:59:40+00:00",
            "result": {"summary": "Candidate validation completed."},
            "progress": [],
        }
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_detached_worker_is_active",
        lambda _service, _session_id: True,
    )
    finalized: list[dict] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_finalize_completed_session",
        lambda _service, value: finalized.append(dict(value)),
    )

    refreshed = service.refresh_session(session)
    projection = service.project_session(refreshed)

    assert finalized == []
    assert refreshed["status"] == "commit_ready"
    assert refreshed["finalizing_task_id"] == task_id
    assert projection["terminal"] is False
    assert projection["busy"] is True


def test_projection_backfills_missing_conversation_before_notification(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    notified: list[str] = []

    def notify(self, session):
        notified.append(str(session.get("conversation_id") or ""))
        return dict(session)

    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", notify)

    result = service.projection(
        object_type="scenario",
        object_id="recipes",
        webspace_id="prompt-dev",
        conversation_id="conv.builder.recipes",
    )

    assert result["ok"] is True
    assert notified == ["conv.builder.recipes"]
    assert service.get_session("scenario", "recipes")["conversation_id"] == "conv.builder.recipes"


def test_refresh_preserves_finalization_progress_after_worker_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    monkeypatch.setattr(
        BuilderAutomationService,
        "_detached_worker_is_active",
        lambda _service, _session_id: True,
    )
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": "task.1",
            "status": "completed",
            "updated_at": "2026-07-18T00:00:00+00:00",
            "progress": [{"status": "commit_ready", "message": "worker commit"}],
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "finalizing_task_id": "task.1",
            "status": "commit_ready",
            "progress": {"status": "commit_ready", "message": "Forge finalization"},
            "completion_readiness": {
                "ok": False,
                "task_id": "task.1",
                "stage": "activation",
                "stage_message": "Activating exact package",
                "completed_at": None,
            },
        }
    )

    assert refreshed["status"] == "commit_ready"
    assert refreshed["progress"]["message"] == "Forge finalization"
    assert "last_failure" not in refreshed


def test_finalization_stage_projects_durable_substage_and_heartbeat(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(automation_module, "FINALIZATION_HEARTBEAT_SECONDS", 0.01)
    events: list[dict] = []
    service.event_sink = lambda payload: events.append(dict(payload))
    current = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.direction",
        "object_type": "skill",
        "object_id": "direction",
        "current_task_id": "task.1",
        "finalizing_task_id": "task.1",
        "status": "commit_ready",
    }
    readiness = {"ok": False, "task_id": "task.1"}

    with service._finalization_stage(
        current,
        readiness,
        "activation",
        "Activating exact package",
    ):
        time.sleep(0.12)

    persisted = service.get_session("skill", "direction")
    assert persisted is not None
    assert persisted["progress"]["stage"] == "activation"
    assert persisted["progress"]["heartbeat"] >= 1
    assert persisted["completion_readiness"]["stage"] == "activation"
    assert any((event.get("progress") or {}).get("heartbeat", 0) >= 1 for event in events)


def test_refresh_preserves_terminal_orchestration_progress_after_worker_completion(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": "task.1",
            "status": "completed",
            "updated_at": "2026-07-18T00:00:00+00:00",
            "progress": [
                {"status": "commit_ready", "message": "worker commit"}
            ],
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "status": "completed",
            "progress": {
                "task_id": "task.1",
                "status": "completed",
                "message": "Automation result activated and checkpointed",
                "updated_at": "2026-07-18T00:01:00+00:00",
            },
            "completion_readiness": {
                "ok": True,
                "task_id": "task.1",
                "completed_at": "2026-07-18T00:01:00+00:00",
                "vcs_checkpoints": [
                    {"ok": True, "kind": "scenario", "name": "recipes"}
                ],
            },
        }
    )

    assert refreshed["status"] == "completed"
    assert refreshed["progress"]["status"] == "completed"
    assert refreshed["progress"]["message"] == "Automation result activated and checkpointed"
    assert refreshed["updated_at"] == "2026-07-18T00:01:00+00:00"


def test_session_store_rejects_stale_commit_ready_after_terminal_readiness(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    commit_ready = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.1",
        "finalizing_task_id": "task.1",
        "status": "commit_ready",
        "updated_at": "2026-08-18T02:38:55+00:00",
    }
    service._save_session(commit_ready)
    stale_projection = dict(commit_ready)
    completed = {
        **commit_ready,
        "status": "completed",
        "updated_at": "2026-08-18T02:40:45+00:00",
        "completion_readiness": {
            "ok": True,
            "task_id": "task.1",
            "completed_at": "2026-08-18T02:40:45+00:00",
            "vcs_checkpoints": [
                {"ok": True, "kind": "scenario", "name": "recipes"}
            ],
        },
    }
    completed.pop("finalizing_task_id")
    service._save_session(completed)

    service._save_session(stale_projection)

    persisted = service.get_session("scenario", "recipes")
    assert persisted is not None
    assert persisted["status"] == "completed"
    assert persisted["completion_readiness"]["ok"] is True
    assert stale_projection["status"] == "completed"


def test_session_store_allows_first_finalization_of_validated_task(tmp_path: Path) -> None:
    service = _service(tmp_path)
    validated = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.1",
        "status": "completed",
        "task": {"task_id": "task.1", "status": "completed"},
        "last_result": {"status": "completed"},
        "updated_at": "2026-08-18T02:38:55+00:00",
    }
    service._save_session(validated)
    finalizing = {
        **validated,
        "status": "commit_ready",
        "finalizing_task_id": "task.1",
        "updated_at": "2026-08-18T02:38:57+00:00",
    }

    service._save_session(finalizing)

    persisted = service.get_session("scenario", "recipes")
    assert persisted is not None
    assert persisted["status"] == "commit_ready"
    assert persisted["finalizing_task_id"] == "task.1"


def test_refresh_reconciles_legacy_false_positive_checkpoint_completion(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": "task.1",
            "status": "completed",
            "updated_at": "2026-07-18T00:00:00+00:00",
            "result": {"summary": "code complete"},
            "progress": [],
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "status": "completed",
            "completion_readiness": {
                "ok": True,
                "task_id": "task.1",
                "vcs_checkpoints": [
                    {"ok": False, "kind": "scenario", "name": "recipes", "error": "504"}
                ],
            },
        }
    )

    assert refreshed["status"] == "failed"
    assert refreshed["completion_readiness"]["ok"] is False
    assert refreshed["last_failure"]["stage"] == "forge_checkpoint"


def test_refresh_reconciles_completed_task_with_failed_live_readiness(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.factory = SimpleNamespace(
        read_task=lambda _task_id: {
            "task_id": "task.1",
            "status": "completed",
            "updated_at": "2026-07-18T00:00:00+00:00",
            "result": {"summary": "code complete"},
            "progress": [],
        }
    )

    refreshed = service.refresh_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "current_task_id": "task.1",
            "status": "completed",
            "completion_readiness": {
                "ok": False,
                "task_id": "task.1",
                "error": "ValueError: automation Preview is not available",
                "vcs_checkpoints": [{"ok": True, "kind": "scenario", "name": "recipes"}],
            },
        }
    )

    assert refreshed["status"] == "failed"
    assert refreshed["completion_readiness"]["ok"] is False
    assert refreshed["last_failure"]["stage"] == "live_readiness"
    assert "Preview is not available" in refreshed["last_failure"]["message"]


def test_completed_session_publishes_one_terminal_chat_message(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    published: list[dict] = []
    events: list[object] = []

    class _Bus:
        def publish(self, event) -> None:  # noqa: ANN001
            events.append(event)

    monkeypatch.setattr(
        "adaos.services.agent_context.get_ctx",
        lambda: SimpleNamespace(bus=_Bus()),
    )
    monkeypatch.setattr(
        "adaos.services.conversation_response.materialize_response",
        lambda response, **kwargs: published.append({"response": response, **kwargs}) or {"ok": True},
    )
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.scenario.recipes",
        "object_type": "scenario",
        "object_id": "recipes",
        "webspace_id": "desktop",
        "conversation_id": "conv.builder.recipes",
        "current_task_id": "task.1",
        "last_result": {"summary": "Implemented filters."},
    }

    first = service._notify_completed_session(session)
    second = service._notify_completed_session(first)

    assert len(published) == 1
    assert "Builder завершил доработку recipes" in published[0]["response"]["message"]
    assert published[0]["thread_id"] == "prompt-project:scenario:recipes"
    assert published[0]["meta"]["response_idempotency_key"] == "builder-automation:completed:task.1"
    assert len(events) == 1
    assert events[0].type == "ui.notify"
    assert events[0].payload["_meta"]["notification_scope"] == "subnet"
    assert second["completion_notified_task_id"] == "task.1"


def test_started_session_broadcasts_once_without_conversation(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    events: list[object] = []

    class _Bus:
        def publish(self, event) -> None:  # noqa: ANN001
            events.append(event)

    monkeypatch.setattr(
        "adaos.services.agent_context.get_ctx",
        lambda: SimpleNamespace(bus=_Bus()),
    )
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.demo_metrics_skill",
        "object_type": "skill",
        "object_id": "demo_metrics_skill",
        "webspace_id": "desktop",
        "current_task_id": "task.start.1",
        "iteration": 1,
    }

    first = service._notify_started_session(session)
    second = service._notify_started_session(first)

    assert len(events) == 1
    assert events[0].type == "ui.notify"
    assert "Итерация 1" in events[0].payload["text"]
    assert events[0].payload["_meta"]["automation_status"] == "started"
    assert second["started_notified_task_id"] == "task.start.1"


def test_finalize_prepares_materialized_runtime_then_notifies(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    calls: list[str] = []
    saved: list[dict] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: calls.append("checkpoint")
        or [
            {
                "ok": True,
                "kind": "scenario",
                "name": "recipes",
                "commit": "forge-1",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "forge-1",
            }
        ],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: calls.append(f"activate:{skill_id}")
        or {"ok": True, "slot": "B"},
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        async def ensure_dev_webspace(self, source_webspace_id, **kwargs):  # noqa: ARG002
            calls.append("ensure")
            return {
                "dev_webspace_id": "desktop-dev",
                "runtime": {"ok": True, "webspace_id": "desktop-dev"},
            }

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: calls.append("notify") or dict(value),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "status": "completed",
        }
    )

    assert calls == ["activate:recipes_skill", "checkpoint", "ensure", "notify"]
    assert saved[-1]["completion_readiness"]["ok"] is True
    assert saved[-1]["completion_readiness"]["materialization"]["preview_webspace_id"] == "desktop-dev"
    assert saved[-1]["completion_readiness"]["task_id"] == "task.1"
    assert saved[-1]["completion_readiness"]["vcs_checkpoints"][0]["commit"] == "forge-1"
    assert (
        saved[-1]["completion_readiness"]["workflow_checkpoint"]["workflow"]["delivery"]["status"]
        == "checkpoint"
    )
    assert saved[-1]["status"] == "completed"
    assert saved[-1]["progress"]["status"] == "completed"
    assert saved[-1]["progress"]["task_id"] == "task.1"


def test_finalize_activates_dev_ticket_aprobation_overlay_after_checkpoint(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[str] = []
    saved: list[dict] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: calls.append("checkpoint")
        or [
            {
                "ok": True,
                "kind": "scenario",
                "name": "recipes",
                "commit": "forge-1",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "forge-1",
            }
        ],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: calls.append(f"dev:{skill_id}")
        or {"ok": True, "slot": "B"},
    )

    def fake_overlay(self, session, *, skill_ids, scenario_id, webspace_id):  # noqa: ARG001
        calls.append("overlay")
        assert list(skill_ids) == ["recipes_skill"]
        assert scenario_id == "recipes"
        assert webspace_id == "desktop"
        return {"ok": True, "mode": "devspace_to_workspace_runtime_overlay"}

    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_aprobation_overlay",
        fake_overlay,
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_ensure_governed_aprobation_trial",
        lambda self, session, receipt, **kwargs: calls.append("trial")
        or {
            **dict(receipt),
            "trial": {
                "status": "trial",
                "candidate_id": "candidate.recipes",
                "candidate_digest": "sha256:" + "2" * 64,
            },
        },
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        async def ensure_dev_webspace(self, source_webspace_id, **kwargs):  # noqa: ARG002
            calls.append("ensure")
            return {
                "dev_webspace_id": "desktop-dev",
                "runtime": {"ok": True, "webspace_id": "desktop-dev"},
            }

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: calls.append("notify") or dict(value),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "status": "completed",
            "links": {"development_ticket_id": "dticket.demo"},
            "implementation_brief": json.dumps(
                {
                    "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                    "ticket_id": "dticket.demo",
                    "policy": {"publication_required": True},
                }
            ),
        }
    )

    assert calls == [
        "dev:recipes_skill",
        "checkpoint",
        "trial",
        "overlay",
        "trial",
        "notify",
    ]
    assert saved[-1]["completion_readiness"]["aprobation"]["ok"] is True
    assert (
        saved[-1]["completion_readiness"]["aprobation"]["mode"]
        == "devspace_to_workspace_runtime_overlay"
    )


def test_project_composition_checkpoint_reserves_one_unused_version(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.sdk.developer import compositions
    from adaos.services.root.service import RootDeveloperService

    service = _service(tmp_path)
    state = {
        "schema": "adaos.project.v1",
        "kind": "project",
        "id": "recipes_project",
        "version": "0.10.3",
        "components": {
            "owned": [{"ref": "scenario:recipes", "role": "primary"}],
            "dependencies": [],
        },
    }
    replacements: list[str] = []

    def current_project() -> dict:
        payload = dict(state)
        payload["ref"] = "project:recipes_project"
        payload["source_path"] = str(tmp_path / "dev" / "projects" / "recipes_project")
        payload["manifest_digest"] = service._project_manifest_digest(payload)
        return payload

    def replace_project(project_id, value, *, expected_manifest_digest):
        assert project_id == "recipes_project"
        assert expected_manifest_digest == current_project()["manifest_digest"]
        state.clear()
        state.update(dict(value))
        replacements.append(str(state["version"]))
        return current_project()

    monkeypatch.setattr(compositions, "get", lambda _project_id: current_project())
    monkeypatch.setattr(compositions, "validate", lambda value: dict(value))
    monkeypatch.setattr(compositions, "replace", replace_project)
    monkeypatch.setattr(
        RootDeveloperService,
        "project_release_versions",
        lambda self, project_id: {
            "0.10.4": "sha256:" + "4" * 64,
            "0.10.5": "sha256:" + "5" * 64,
        },
    )
    session = {
        "change_id": "builder_change.demo",
        "current_task_id": "task.demo",
        "links": {"development_ticket_project_ref": "project:recipes_project"},
    }
    checkpoints = [{"ok": True, "kind": "scenario", "name": "recipes"}]

    created = service._ensure_project_composition_checkpoint(
        session,
        checkpoints=checkpoints,
    )
    replayed = service._ensure_project_composition_checkpoint(
        session,
        checkpoints=checkpoints,
    )

    assert created["version"] == "0.10.6"
    assert created["duplicate"] is False
    assert replayed["version"] == "0.10.6"
    assert replayed["duplicate"] is True
    assert replacements == ["0.10.6"]


def test_project_composition_checkpoint_rejects_foreign_component(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.sdk.developer import compositions

    service = _service(tmp_path)
    monkeypatch.setattr(
        compositions,
        "get",
        lambda _project_id: {
            "ref": "project:recipes_project",
            "manifest_digest": "sha256:" + "1" * 64,
            "version": "0.1.0",
            "components": {
                "owned": [{"ref": "scenario:recipes", "role": "primary"}],
                "dependencies": [],
            },
        },
    )

    with pytest.raises(RuntimeError, match="does not own checkpointed components"):
        service._ensure_project_composition_checkpoint(
            {
                "change_id": "builder_change.demo",
                "links": {
                    "development_ticket_project_ref": "project:recipes_project"
                },
            },
            checkpoints=[
                {"ok": True, "kind": "skill", "name": "foreign_skill"}
            ],
        )


def test_finalize_scenario_only_repair_does_not_activate_unchanged_companion_skill(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    calls: list[str] = []
    saved: list[dict] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: calls.append(f"dev:{skill_id}"),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: calls.append("checkpoint")
        or [
            {
                "ok": True,
                "kind": "scenario",
                "name": "recipes",
                "commit": "forge-1",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "forge-1",
            }
        ],
    )

    def fake_overlay(self, session, *, skill_ids, scenario_id, webspace_id):  # noqa: ARG001
        calls.append("overlay")
        assert list(skill_ids) == []
        assert scenario_id == "recipes"
        assert webspace_id == "desktop"
        return {"ok": True, "mode": "devspace_to_workspace_runtime_overlay"}

    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_aprobation_overlay",
        fake_overlay,
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_ensure_governed_aprobation_trial",
        lambda self, session, receipt, **kwargs: {
            **dict(receipt),
            "trial": {
                "status": "trial",
                "candidate_id": "candidate.recipes",
                "candidate_digest": "sha256:" + "2" * 64,
            },
        },
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        async def ensure_dev_webspace(self, source_webspace_id, **kwargs):  # noqa: ARG002
            calls.append("ensure")
            return {
                "dev_webspace_id": "desktop-dev",
                "runtime": {"ok": True, "webspace_id": "desktop-dev"},
            }

    monkeypatch.setattr(
        "adaos.services.builder.workbench.BuilderWorkbenchService",
        FakeWorkbench,
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: None,
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "status": "completed",
            "last_result": {
                "changed_paths": [
                    "scenarios/recipes/scenario.yaml",
                    "scenarios/recipes/webui.json",
                ]
            },
            "links": {"development_ticket_id": "dticket.demo"},
            "implementation_brief": json.dumps(
                {
                    "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                    "ticket_id": "dticket.demo",
                    "policy": {"publication_required": True},
                }
            ),
        }
    )

    assert calls == ["checkpoint", "overlay"]
    assert saved[-1]["completion_readiness"]["ok"] is True
    assert saved[-1]["completion_readiness"]["skills"] == []
    assert (
        saved[-1]["completion_readiness"]["materialization"]["skipped"]
        == "governed_aprobation_overlay_active"
    )


def test_dev_ticket_repair_defaults_to_aprobation_overlay() -> None:
    assert BuilderAutomationService._session_requires_aprobation_overlay(
        {
            "links": {"development_ticket_id": "dticket.1"},
            "implementation_brief": json.dumps(
                {
                    "execution_mode": "surgical_dev_ticket_repair",
                    "policy": {},
                }
            ),
        }
    )
    assert not BuilderAutomationService._session_requires_aprobation_overlay(
        {
            "links": {"development_ticket_id": "dticket.1"},
            "implementation_brief": json.dumps(
                {
                    "execution_mode": "surgical_dev_ticket_repair",
                    "policy": {"publication_required": False},
                }
            ),
        }
    )


@pytest.mark.parametrize("delivery_status", ["checkpoint", "activating"])
def test_governed_aprobation_trial_binds_candidate_and_changelog(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delivery_status: str,
) -> None:
    from adaos.sdk.builder import lifecycle

    service = _service(tmp_path)
    checkpoint = {
        "generation": 4,
        "delivery": {
            "status": delivery_status,
            "package_digest": "sha256:" + "1" * 64,
        },
    }
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(describe=lambda *_: checkpoint),
    )
    monkeypatch.setattr(
        lifecycle,
        "prepare_trial",
        lambda *args, **kwargs: {
            "ok": True,
            "candidate": {
                "candidate_id": "candidate.demo",
                "package_digest": "sha256:" + "2" * 64,
                "release_digest": "sha256:" + "3" * 64,
            },
            "release": {"version": "0.2.0"},
            "trial_workspace": "trials/demo",
            "workflow": {
                "generation": 6,
                "delivery": {
                    "status": "trial",
                    "candidate_id": "candidate.demo",
                    "package_digest": "sha256:" + "2" * 64,
                    "release_digest": "sha256:" + "3" * 64,
                    "version": "0.2.0",
                },
            },
        },
    )

    receipt = service._ensure_governed_aprobation_trial(
        {
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "current_task_id": "task.demo",
            "webspace_id": "desktop",
            "links": {
                "development_ticket_id": "dticket.1",
                "development_ticket_ids": ["dticket.1", "dticket.2"],
            },
            "implementation_brief": json.dumps(
                {
                    "summary": "Improve Demo Metrics",
                    "issues": [
                        {"summary": "Rename the Metrics table."},
                        {"summary": "Move Refresh before Create."},
                    ],
                }
            ),
        },
        {"ok": True, "mode": "devspace_to_workspace_runtime_overlay"},
    )

    assert receipt["trial"]["status"] == "trial"
    assert receipt["trial"]["candidate_id"] == "candidate.demo"
    assert receipt["trial"]["version"] == "0.2.0"
    assert receipt["audience"] == "alpha"
    assert receipt["changelog"]["ticket_ids"] == ["dticket.1", "dticket.2"]
    assert receipt["changelog"]["changes"] == [
        "Rename the Metrics table.",
        "Move Refresh before Create.",
    ]
    assert receipt["component_update"]["stage"] == "alpha"
    assert receipt["component_update"]["version"] == "0.2.0"


def test_existing_trial_uses_project_version_when_delivery_omits_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(
            describe=lambda *_: {
                "generation": 7,
                "delivery": {
                    "status": "trial",
                    "candidate_id": "candidate.demo",
                    "package_digest": "sha256:" + "2" * 64,
                    "release_digest": "sha256:" + "3" * 64,
                },
            }
        ),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_project_version",
        lambda self, object_type, object_id: "0.11.4",
    )

    receipt = service._ensure_governed_aprobation_trial(
        {
            "object_type": "scenario",
            "object_id": "taiga_ui_demo_scenario",
            "current_task_id": "task.demo",
            "webspace_id": "desktop",
            "links": {"development_ticket_id": "dticket.1"},
            "implementation_brief": json.dumps({"summary": "Improve Demo Metrics"}),
        },
        {"ok": True, "mode": "devspace_to_workspace_runtime_overlay"},
    )

    assert receipt["trial"]["version"] == "0.11.4"
    assert receipt["component_update"]["version"] == "0.11.4"


def test_project_trial_idempotency_includes_composition_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.sdk.developer import compositions

    monkeypatch.setattr(
        compositions,
        "get",
        lambda project_id: {
            "id": project_id,
            "manifest_digest": "sha256:" + "a" * 64,
        },
    )

    key = BuilderAutomationService._aprobation_trial_idempotency_key(
        task_id="task.demo",
        package_digest="sha256:" + "b" * 64,
        publication_project_ref="project:demo_suite",
        checkpoint_epoch="2026-09-02T12:00:00+00:00",
    )

    assert key.startswith(
        "dev-ticket-trial:task.demo:bbbbbbbbbbbbbbbbbbbbbbbb:epoch:"
    )
    assert key.endswith(":project:aaaaaaaaaaaaaaaaaaaaaaaa")


@pytest.mark.parametrize(
    ("trial", "expected_source", "expected_skill_mode"),
    [
        ({"status": "trial"}, "devspace_runtime_overlay", "dev"),
        (
            {"status": "published", "decision": "accept"},
            "component_update_notice",
            "workspace",
        ),
    ],
)
def test_component_update_notice_refreshes_user_runtime_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    trial: dict[str, str],
    expected_source: str,
    expected_skill_mode: str | None,
) -> None:
    from adaos.services import runtime_refresh
    from adaos.services.scenario import webspace_runtime

    service = _service(tmp_path)
    invalidations: list[dict[str, object]] = []
    rebuilds: list[dict[str, object]] = []
    monkeypatch.setattr(
        webspace_runtime,
        "invalidate_webspace_materialization_cache",
        lambda webspace_id, **kwargs: invalidations.append(
            {"webspace_id": webspace_id, **kwargs}
        ),
    )
    monkeypatch.setattr(
        runtime_refresh,
        "rebuild_webspace_projection_sync",
        lambda **kwargs: rebuilds.append(dict(kwargs))
        or {"ok": True, "materialization": {"ready": True}},
    )

    result = service._refresh_component_update_projection(
        {
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "webspace_id": "desktop",
        },
        {
            "mode": "devspace_to_workspace_runtime_overlay",
            "webspace_id": "desktop",
            "skills": [{"id": "demo_metrics_skill"}],
            "trial": trial,
        },
    )

    assert result is not None and result["ok"] is True
    assert result["recovered"] is False
    assert result["attempts"] == [
        {"attempt": 1, "ok": True, "error": None, "request_id": None}
    ]
    assert invalidations[0]["reason"] == "component_update_notice_changed"
    assert rebuilds[0]["source_of_truth"] == expected_source
    assert rebuilds[0]["skill_source_mode"] == expected_skill_mode


def test_component_update_notice_retries_transient_webspace_rebuild(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services import runtime_refresh
    from adaos.services.scenario import webspace_runtime

    service = _service(tmp_path)
    rebuilds: list[dict[str, object]] = []
    sleeps: list[float] = []
    projections = iter(
        [
            {
                "ok": False,
                "error": "webspace_rebuild_failed",
                "request_id": "rebuild.first",
                "materialization": {"ready": False},
            },
            {
                "ok": True,
                "request_id": "rebuild.second",
                "materialization": {"ready": True},
            },
        ]
    )
    monkeypatch.setattr(
        webspace_runtime,
        "invalidate_webspace_materialization_cache",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runtime_refresh,
        "rebuild_webspace_projection_sync",
        lambda **kwargs: rebuilds.append(dict(kwargs)) or next(projections),
    )
    monkeypatch.setattr("adaos.services.builder.automation.time.sleep", sleeps.append)

    result = service._refresh_component_update_projection(
        {
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "webspace_id": "desktop",
        },
        {
            "mode": "devspace_to_workspace_runtime_overlay",
            "webspace_id": "desktop",
            "skills": [{"id": "demo_metrics_skill"}],
            "trial": {"status": "published", "decision": "accept"},
        },
    )

    assert result is not None and result["ok"] is True
    assert result["recovered"] is True
    assert result["attempts"] == [
        {
            "attempt": 1,
            "ok": False,
            "error": "webspace_rebuild_failed",
            "request_id": "rebuild.first",
        },
        {
            "attempt": 2,
            "ok": True,
            "error": None,
            "request_id": "rebuild.second",
        },
    ]
    assert len(rebuilds) == 2
    assert sleeps == [1.0]


def test_component_update_notice_defers_exhausted_transient_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services import runtime_refresh
    from adaos.services.scenario import webspace_runtime

    service = _service(tmp_path)
    sleeps: list[float] = []
    monkeypatch.setattr(
        webspace_runtime,
        "invalidate_webspace_materialization_cache",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        runtime_refresh,
        "rebuild_webspace_projection_sync",
        lambda **kwargs: {
            "ok": False,
            "error": "webspace_rebuild_failed",
            "request_id": "rebuild.deferred",
            "materialization": {"ready": False},
        },
    )
    monkeypatch.setattr("adaos.services.builder.automation.time.sleep", sleeps.append)

    result = service._refresh_component_update_projection(
        {
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "webspace_id": "desktop",
        },
        {
            "mode": "devspace_to_workspace_runtime_overlay",
            "webspace_id": "desktop",
            "skills": [{"id": "demo_metrics_skill"}],
            "trial": {"status": "trial"},
        },
    )

    assert result is not None and result["ok"] is False
    assert result["retryable"] is True
    assert result["error"] == "webspace_rebuild_failed"
    assert len(result["attempts"]) == 3
    assert sleeps == [1.0, 1.0]


def test_completed_session_reconciles_retryable_component_update_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.demo_metrics_skill",
        "object_type": "skill",
        "object_id": "demo_metrics_skill",
        "status": "completed",
        "current_task_id": "task.demo",
        "links": {"development_ticket_id": "dticket.1"},
        "implementation_brief": json.dumps(
            {
                "execution_mode": "surgical_dev_ticket_repair",
                "policy": {"publication_required": True},
            }
        ),
        "completion_readiness": {
            "ok": True,
            "aprobation": {
                "ok": True,
                "mode": "devspace_to_workspace_runtime_overlay",
                "webspace_id": "desktop",
                "skills": [
                    {
                        "id": "demo_metrics_skill",
                        "webspace_projection": {
                            "ok": True,
                            "materialization": {"ready": True},
                        },
                    }
                ],
                "trial": {
                    "status": "trial",
                    "candidate_id": "candidate.demo",
                    "candidate_digest": "sha256:" + "2" * 64,
                    "version": "0.2.0",
                },
                "component_update_projection": {
                    "ok": False,
                    "retryable": True,
                    "error": "webspace_rebuild_failed",
                },
            },
        },
    }
    monkeypatch.setattr(
        BuilderAutomationService,
        "_record_component_update",
        lambda self, current, aprobation: {
            "notice_id": "cupdate.demo",
            "stage": "alpha",
        },
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_refresh_component_update_projection",
        lambda self, current, aprobation: {
            "ok": True,
            "materialization": {"ready": True},
        },
    )

    reconciled = service._reconcile_required_aprobation(session)

    aprobation = reconciled["completion_readiness"]["aprobation"]
    assert aprobation["component_update"]["notice_id"] == "cupdate.demo"
    assert aprobation["component_update_projection"]["ok"] is True
    persisted = service.get_session("skill", "demo_metrics_skill")
    assert persisted["completion_readiness"]["aprobation"]["component_update_projection"]["ok"] is True


@pytest.mark.parametrize(
    ("expected_candidate_id", "expected_candidate_digest", "message"),
    [
        (
            "candidate.previous",
            "sha256:" + "2" * 64,
            "candidate changed",
        ),
        (
            "candidate.current",
            "sha256:" + "9" * 64,
            "candidate digest changed",
        ),
    ],
)
def test_aprobation_decision_rejects_stale_reviewed_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    expected_candidate_id: str,
    expected_candidate_digest: str,
    message: str,
) -> None:
    from adaos.sdk.builder import lifecycle

    service = _service(tmp_path)
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation.skill.demo_metrics_skill",
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "status": "completed",
            "completion_readiness": {
                "ok": True,
                "aprobation": {
                    "ok": True,
                    "trial": {
                        "status": "trial",
                        "candidate_id": "candidate.current",
                        "candidate_digest": "sha256:" + "2" * 64,
                    },
                },
            },
        }
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda self, value: dict(value),
    )
    monkeypatch.setattr(
        lifecycle,
        "decide_trial",
        lambda *args, **kwargs: pytest.fail("stale decisions must have no side effects"),
    )

    with pytest.raises(ValueError, match=message):
        service.decide_aprobation(
            object_type="skill",
            object_id="demo_metrics_skill",
            decision="accept",
            actor="user:owner",
            expected_candidate_id=expected_candidate_id,
            expected_candidate_digest=expected_candidate_digest,
        )

    persisted = service.get_session("skill", "demo_metrics_skill")
    assert persisted["completion_readiness"]["aprobation"]["trial"]["status"] == "trial"


def test_accepting_aprobation_publishes_and_closes_resolved_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.sdk.builder import lifecycle
    from adaos.services.development_tickets import DevelopmentTicketService

    service = _service(tmp_path)
    tickets = DevelopmentTicketService(state_dir=service.state_dir)
    signal = tickets.capture_signal(
        kind="development_request",
        summary="Improve Demo Metrics",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = tickets.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="accepted",
        owner_area="skill",
    )["ticket"]
    tickets.record_resolution(
        ticket["ticket_id"],
        actor="builder.automation",
        evidence_refs=[{"type": "test", "id": "demo-focused", "status": "passed"}],
        resolved_by_overlay="candidate.demo",
    )
    gate_failure = tickets.report_publication_gate_failure(
        component_type="skill",
        component_id="demo_metrics_skill",
        gate="activation",
        error="runtime health check failed",
        related_ticket_ids=[ticket["ticket_id"]],
        candidate_id="candidate.demo",
    )["ticket"]
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.demo_metrics_skill",
        "object_type": "skill",
        "object_id": "demo_metrics_skill",
        "status": "completed",
        "links": {"development_ticket_id": ticket["ticket_id"]},
        "completion_readiness": {
            "ok": True,
            "aprobation": {
                "ok": True,
                "trial": {
                    "status": "trial",
                    "candidate_id": "candidate.demo",
                    "candidate_digest": "sha256:" + "2" * 64,
                    "release_digest": "sha256:" + "3" * 64,
                    "version": "0.2.0",
                },
            },
        },
        "created_at": "2026-09-01T10:00:00+00:00",
        "updated_at": "2026-09-01T10:00:00+00:00",
    }
    service._save_session(session)
    calls: list[str] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda self, value: dict(value),
    )
    monkeypatch.setattr(
        lifecycle,
        "decide_trial",
        lambda *args, **kwargs: {"ok": True, "accepted": True},
    )
    monkeypatch.setattr(
        lifecycle,
        "publish_candidate",
        lambda *args, **kwargs: calls.append("publish")
        or {"ok": True, "version": "0.2.0"},
    )
    original_project = service._project_aprobation_state
    monkeypatch.setattr(
        BuilderAutomationService,
        "_project_aprobation_state",
        lambda self, current, aprobation: calls.append("project")
        or original_project(current, aprobation),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(
            describe=lambda *_: (
                {
                    "delivery": {
                        "status": "published",
                        "candidate_id": "candidate.demo",
                        "release": "demo_metrics_skill@0.2.0",
                        "release_digest": "sha256:" + "3" * 64,
                    },
                    "publication": {
                        "status": "published",
                        "version": "0.2.0",
                        "release_record": {"candidate_id": "candidate.demo"},
                    },
                }
                if "publish" in calls
                else {
                    "delivery": {
                        "status": "trial",
                        "candidate_id": "candidate.demo",
                    },
                    "publication": {"status": "not_started"},
                }
            ),
        ),
    )

    result = service.decide_aprobation(
        object_type="skill",
        object_id="demo_metrics_skill",
        decision="accept",
        actor="user:owner",
    )

    assert result["decision"] == "accept"
    assert calls == ["publish", "project"]
    assert result["tickets"][0]["status"] == "closed"
    assert result["closed_publication_gate_failures"][0]["ticket_id"] == gate_failure["ticket_id"]
    assert result["closed_publication_gate_failures"][0]["status"] == "closed"
    assert tickets.get_ticket(ticket["ticket_id"])["verification"]["kind"] == "verified"
    persisted = service.get_session("skill", "demo_metrics_skill")
    assert persisted["completion_readiness"]["aprobation"]["trial"]["status"] == "published"


def test_accepting_aprobation_resumes_an_already_accepted_unknown_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.sdk.builder import lifecycle

    service = _service(tmp_path)
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation.skill.demo_metrics_skill",
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "status": "completed",
            "completion_readiness": {
                "ok": True,
                "aprobation": {
                    "ok": True,
                    "trial": {
                        "status": "publication_unknown",
                        "candidate_id": "candidate.demo",
                        "candidate_digest": "sha256:" + "2" * 64,
                        "release_digest": "sha256:" + "3" * 64,
                        "version": "0.2.0",
                    },
                },
            },
        }
    )
    calls: list[str] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda self, value: dict(value),
    )
    monkeypatch.setattr(
        lifecycle,
        "decide_trial",
        lambda *args, **kwargs: pytest.fail("accepted trial must not be decided twice"),
    )
    monkeypatch.setattr(
        lifecycle,
        "publish_candidate",
        lambda *args, **kwargs: calls.append("publish")
        or {"ok": True, "version": "0.2.0"},
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_project_aprobation_state",
        lambda self, current, aprobation: (
            dict(current),
            {**dict(aprobation), "component_update": {"notice_id": "update.demo"}},
            {"ok": True},
        ),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(
            describe=lambda *_: (
                {
                    "governed": {"state": "published"},
                    "delivery": {
                        "status": "published",
                        "candidate_id": "candidate.demo",
                        "release": "demo_metrics_skill@0.2.0",
                        "release_digest": "sha256:" + "3" * 64,
                    },
                    "publication": {
                        "status": "published",
                        "version": "0.2.0",
                        "release_record": {"candidate_id": "candidate.demo"},
                    },
                }
                if calls
                else {
                    "governed": {"state": "reconciliation_required"},
                    "delivery": {
                        "status": "unknown",
                        "candidate_id": "candidate.demo",
                        "decision_observations": [{"status": "accepted"}],
                    },
                    "publication": {"status": "unknown"},
                }
            ),
        ),
    )

    result = service.decide_aprobation(
        object_type="skill",
        object_id="demo_metrics_skill",
        decision="accept",
        actor="user:owner",
    )

    assert calls == ["publish"]
    assert result["decision_result"]["duplicate"] is True
    assert result["publication"]["ok"] is True


def test_accepting_aprobation_does_not_close_for_stale_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.sdk.builder import lifecycle
    from adaos.services.component_updates import ComponentUpdateService
    from adaos.services.development_tickets import DevelopmentTicketService

    service = _service(tmp_path)
    tickets = DevelopmentTicketService(state_dir=service.state_dir)
    signal = tickets.capture_signal(
        kind="development_request",
        summary="Improve Demo Metrics",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = tickets.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="accepted",
        owner_area="skill",
    )["ticket"]
    tickets.record_resolution(
        ticket["ticket_id"],
        actor="builder.automation",
        evidence_refs=[{"type": "test", "id": "demo-focused", "status": "passed"}],
        resolved_by_overlay="candidate.current",
    )
    service._save_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation.skill.demo_metrics_skill",
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "status": "completed",
            "links": {"development_ticket_id": ticket["ticket_id"]},
            "completion_readiness": {
                "ok": True,
                "aprobation": {
                    "ok": True,
                    "trial": {
                        "status": "trial",
                        "candidate_id": "candidate.current",
                        "candidate_digest": "sha256:" + "2" * 64,
                    },
                },
            },
        }
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda self, value: dict(value),
    )
    monkeypatch.setattr(
        lifecycle,
        "decide_trial",
        lambda *args, **kwargs: {"ok": True, "accepted": True},
    )
    monkeypatch.setattr(
        lifecycle,
        "publish_candidate",
        lambda *args, **kwargs: {"ok": True, "status": "published"},
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(
            describe=lambda *_: {
                "delivery": {
                    "status": "accepted",
                    "candidate_id": "candidate.current",
                },
                "publication": {
                    "status": "published",
                    "release_record": {"candidate_id": "candidate.previous"},
                },
            }
        ),
    )

    with pytest.raises(RuntimeError, match="did not durably publish"):
        service.decide_aprobation(
            object_type="skill",
            object_id="demo_metrics_skill",
            decision="accept",
            actor="user:owner",
        )

    assert tickets.get_ticket(ticket["ticket_id"])["status"] == "resolved"
    failures = tickets.list_tickets(
        kind="runtime_failure",
        component_ref="skill:demo_metrics_skill",
    )
    assert len(failures) == 1
    assert failures[0]["status"] == "accepted"
    persisted = service.get_session("skill", "demo_metrics_skill")
    failed_trial = persisted["completion_readiness"]["aprobation"]["trial"]
    assert failed_trial["status"] == "publication_failed"
    assert failed_trial["failure"]["ticket_id"] == failures[0]["ticket_id"]
    notices = ComponentUpdateService(state_dir=service.state_dir).list_notices(
        component_type="skill",
        component_id="demo_metrics_skill",
    )
    assert notices[0]["stage"] == "beta"
    assert notices[0]["review_state"] == "publication_failed"


def test_revising_aprobation_rolls_back_and_reopens_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.sdk.builder import lifecycle
    from adaos.services.development_tickets import DevelopmentTicketService

    service = _service(tmp_path)
    tickets = DevelopmentTicketService(state_dir=service.state_dir)
    signal = tickets.capture_signal(
        kind="development_request",
        summary="Move Demo Metrics Refresh",
        target_scope={"type": "skill", "id": "demo_metrics_skill", "source": "dev"},
        source="client_feedback",
        owner_area="skill",
    )["signal"]
    ticket = tickets.ensure_ticket_for_signal(
        signal,
        kind="development_request",
        status="accepted",
        owner_area="skill",
    )["ticket"]
    tickets._link_builder_repair(
        ticket["ticket_id"],
        {
            "repair_id": "repair.demo",
            "status": "in_progress",
            "created_at": "2026-09-01T10:00:00+00:00",
        },
        mode="autonomous",
        actor="builder.automation",
    )
    tickets.record_resolution(
        ticket["ticket_id"],
        actor="builder.automation",
        evidence_refs=[{"type": "test", "id": "demo-focused", "status": "passed"}],
        resolved_by_overlay="candidate.demo",
    )
    session = {
        "schema": "adaos.builder.automation_session.v1",
        "session_id": "automation.skill.demo_metrics_skill",
        "object_type": "skill",
        "object_id": "demo_metrics_skill",
        "status": "completed",
        "links": {"development_ticket_id": ticket["ticket_id"]},
        "completion_readiness": {
            "ok": True,
            "aprobation": {
                "ok": True,
                "trial": {
                    "status": "trial",
                    "candidate_id": "candidate.demo",
                    "candidate_digest": "sha256:" + "2" * 64,
                },
            },
        },
        "created_at": "2026-09-01T10:00:00+00:00",
        "updated_at": "2026-09-01T10:00:00+00:00",
    }
    service._save_session(session)
    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda self, value: dict(value),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_rollback_aprobation_overlay",
        lambda self, current, receipt: {"ok": True, "mode": "restore_workspace_runtime"},
    )
    monkeypatch.setattr(
        lifecycle,
        "decide_trial",
        lambda *args, **kwargs: {"ok": True, "accepted": False},
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(
            describe=lambda *_: {"delivery": {"status": "rejected"}}
        ),
    )

    result = service.decide_aprobation(
        object_type="skill",
        object_id="demo_metrics_skill",
        decision="revise",
        actor="user:owner",
        reason="Refresh must stay visible on a narrow screen.",
    )

    assert result["rollback"]["ok"] is True
    assert result["tickets"][0]["status"] == "in_progress"
    assert result["tickets"][0]["comments"][-1]["body"] == (
        "Refresh must stay visible on a narrow screen."
    )


def test_aprobation_overlay_requires_ready_webspace_materialization() -> None:
    assert not BuilderAutomationService._aprobation_overlay_ready(
        {
            "ok": True,
            "trial": {
                "status": "trial",
                "candidate_id": "candidate.demo",
                "candidate_digest": "sha256:" + "1" * 64,
            },
            "skills": [
                {
                    "id": "demo_metrics_skill",
                    "webspace_projection": {"ok": True, "accepted": True},
                    "materialization_cache": {
                        "pending": True,
                        "materialization": {"ready": False},
                    },
                }
            ],
        }
    )
    assert BuilderAutomationService._aprobation_overlay_ready(
        {
            "ok": True,
            "trial": {
                "status": "trial",
                "candidate_id": "candidate.demo",
                "candidate_digest": "sha256:" + "1" * 64,
            },
            "skills": [
                {
                    "id": "demo_metrics_skill",
                    "webspace_projection": {
                        "ok": True,
                        "accepted": True,
                        "materialization": {"ready": True},
                    },
                }
            ],
        }
    )


def test_aprobation_scenario_validation_blocks_runtime_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services import agent_context
    from adaos.services.scenario import validation
    from adaos.services.scenarios import loader as scenarios_loader

    service = _service(tmp_path)
    source = tmp_path / "dev" / "scenarios" / "invalid_scenario"
    source.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        scenarios_loader,
        "scenario_root_for_space",
        lambda *args, **kwargs: source,
    )
    monkeypatch.setattr(
        validation,
        "validate_scenario_path",
        lambda *args, **kwargs: SimpleNamespace(
            ok=False,
            issues=[
                SimpleNamespace(
                    level="error",
                    code="scenario.invalid",
                    message="Scenario contract is invalid",
                )
            ],
        ),
    )
    monkeypatch.setattr(
        agent_context,
        "get_ctx",
        lambda: SimpleNamespace(
            paths=SimpleNamespace(skills_dir=lambda: tmp_path / "workspace" / "skills")
        ),
    )

    with pytest.raises(RuntimeError, match="scenario.invalid"):
        service._prepare_and_activate_aprobation_scenario(
            "invalid_scenario",
            webspace_id="desktop",
        )


def test_completed_workflow_reconciliation_backfills_aprobation_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    overlay_calls: list[dict] = []
    composition_calls: list[list[dict]] = []
    workflow = {
        "generation": 4,
        "automation": {
            "status": "completed",
            "head_task_id": "task.1",
            "completed_at": "2026-08-31T12:00:00+00:00",
        },
        "delivery": {
            "status": "checkpoint",
            "checkpoint_change_id": "change.1",
            "package_digest": "sha256:" + "1" * 64,
            "source_revision": "commit.1",
            "version": "0.1.1",
        },
    }
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(describe=lambda *_: workflow),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)),
    )

    def activate(self, session, *, skill_ids, scenario_id, webspace_id):  # noqa: ARG001
        overlay_calls.append(
            {
                "skill_ids": list(skill_ids),
                "scenario_id": scenario_id,
                "webspace_id": webspace_id,
            }
        )
        return {"ok": True, "mode": "devspace_to_workspace_runtime_overlay"}

    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_aprobation_overlay",
        activate,
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_ensure_project_composition_checkpoint",
        lambda self, session, *, checkpoints: composition_calls.append(
            [dict(item) for item in checkpoints]
        )
        or {"ok": True, "version": "0.4.2"},
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_ensure_governed_aprobation_trial",
        lambda self, session, receipt, **kwargs: (
            {
                **dict(receipt),
                "trial": {
                    "status": "trial",
                    "candidate_id": "candidate.subscription",
                    "candidate_digest": "sha256:" + "2" * 64,
                },
            }
            if composition_calls
            else pytest.fail("Project checkpoint must precede Trial reconciliation")
        ),
    )

    reconciled = service._reconcile_completed_workflow(
        {
            "object_type": "skill",
            "object_id": "subscription_status_skill",
            "companion_skill_id": "subscription_status_skill",
            "current_task_id": "task.1",
            "change_id": "change.1",
            "webspace_id": "desktop",
            "links": {
                "development_ticket_id": "dticket.1",
                "development_ticket_project_ref": "project:subscription_status",
            },
            "implementation_brief": json.dumps(
                {"execution_mode": "surgical_dev_ticket_repair", "policy": {}}
            ),
            "completion_readiness": {
                "vcs_checkpoints": [{"ok": True, "commit": "commit.1"}],
            },
        }
    )

    assert overlay_calls == [
        {
            "skill_ids": ["subscription_status_skill"],
            "scenario_id": None,
            "webspace_id": "desktop",
        }
    ]
    assert reconciled["completion_readiness"]["aprobation"]["ok"] is True
    assert reconciled["completion_readiness"]["project_composition_checkpoint"][
        "version"
    ] == "0.4.2"
    assert saved[-1]["status"] == "completed"
    assert saved[-1]["updated_at"] != "2026-08-31T12:00:00+00:00"


def test_completed_trial_reconciliation_recovers_only_missing_runtime_overlay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    overlay_calls: list[dict] = []
    trial_calls: list[dict] = []
    candidate_id = "subscription-status-0-1-20"
    candidate_digest = "sha256:" + "2" * 64
    workflow = {
        "generation": 38,
        "automation": {
            "status": "completed",
            "head_task_id": "task.1",
            "completed_at": "2026-09-02T21:50:43+00:00",
        },
        "change": {"change_id": "change.workflow", "status": "trial"},
        "delivery": {
            "status": "trial",
            "candidate_id": candidate_id,
            "package_digest": candidate_digest,
            "prepared_at": "2026-09-02T21:54:42+00:00",
        },
    }
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda self: SimpleNamespace(describe=lambda *_: workflow),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_ensure_project_composition_checkpoint",
        lambda *args, **kwargs: pytest.fail("an existing Trial must not create a new Project checkpoint"),
    )

    def activate(self, session, *, skill_ids, scenario_id, webspace_id):  # noqa: ARG001
        overlay_calls.append(
            {
                "skill_ids": list(skill_ids),
                "scenario_id": scenario_id,
                "webspace_id": webspace_id,
            }
        )
        return {
            "ok": True,
            "mode": "devspace_to_workspace_runtime_overlay",
            "skills": [{"id": "subscription_status_skill", "ok": True}],
        }

    def project_trial(self, session, receipt, **kwargs):  # noqa: ARG001
        trial_calls.append({"receipt": dict(receipt), "kwargs": dict(kwargs)})
        return {
            **dict(receipt),
            "ok": bool(receipt.get("ok")),
            "trial": {
                "status": "trial",
                "candidate_id": candidate_id,
                "candidate_digest": candidate_digest,
                "workflow_generation": 38,
            },
        }

    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_aprobation_overlay",
        activate,
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_ensure_governed_aprobation_trial",
        project_trial,
    )

    reconciled = service._reconcile_completed_workflow(
        {
            "object_type": "skill",
            "object_id": "subscription_status_skill",
            "companion_skill_id": "subscription_status_skill",
            "current_task_id": "task.1",
            "change_id": "change.session",
            "webspace_id": "desktop",
            "links": {
                "development_ticket_id": "dticket.1",
                "development_ticket_project_ref": "project:subscription_status",
            },
            "implementation_brief": json.dumps(
                {"execution_mode": "surgical_dev_ticket_repair", "policy": {}}
            ),
            "completion_readiness": {
                "ok": True,
                "task_id": "task.1",
                "vcs_checkpoints": [
                    {
                        "ok": True,
                        "kind": "skill",
                        "name": "subscription_status_skill",
                        "commit": "commit.1",
                        "source_revision": "commit.1",
                        "package_digest": "sha256:" + "1" * 64,
                    }
                ],
                "project_composition_checkpoint": {"ok": True, "version": "0.1.2"},
                "aprobation": {
                    "ok": False,
                    "trial": {
                        "status": "trial",
                        "candidate_id": candidate_id,
                        "candidate_digest": candidate_digest,
                        "workflow_generation": 38,
                    },
                },
            },
        }
    )

    assert overlay_calls == [
        {
            "skill_ids": ["subscription_status_skill"],
            "scenario_id": None,
            "webspace_id": "desktop",
        }
    ]
    assert len(trial_calls) == 2
    assert trial_calls[0]["kwargs"] == {"record_update": False}
    assert reconciled["completion_readiness"]["aprobation"]["ok"] is True
    assert reconciled["completion_readiness"]["workflow_reconciliation"]["status"] == (
        "existing_trial_overlay_recovered"
    )
    assert reconciled["completion_readiness"]["workflow_reconciliation"][
        "candidate_id"
    ] == candidate_id
    assert saved[-1]["status"] == "completed"


def test_completed_automation_synchronizes_linked_dev_ticket_without_status_recursion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services.development_tickets import DevelopmentTicketService

    service = _service(tmp_path)
    saved: list[dict] = []
    calls: list[dict] = []

    def sync(self, ticket_id, **kwargs):  # noqa: ARG001
        calls.append({"ticket_id": ticket_id, **kwargs})
        assert kwargs.get("automation_service") is None
        assert kwargs["automation_result"]["automation"]["task_id"] == "task.1"
        return {"ok": True, "synchronized": True, "resolved": True}

    monkeypatch.setattr(DevelopmentTicketService, "sync_builder_repair", sync)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)),
    )
    current = service._sync_linked_development_ticket_tasks(
        {
            "object_type": "skill",
            "object_id": "subscription_status_skill",
            "status": "completed",
            "current_task_id": "task.1",
            "links": {
                "development_ticket_id": "dticket.1",
                "builder_repair_id": "repair.1",
            },
            "completion_readiness": {"ok": True, "task_id": "task.1"},
            "development_ticket_synced_task_ids": ["task.1"],
        }
    )

    assert calls[0]["repair_id"] == "repair.1"
    assert current["development_ticket_synced_task_id"] == "task.1"
    assert current["development_ticket_synced_task_ids"] == ["task.1"]
    assert (
        current["development_ticket_sync_schema"]
        == "adaos.builder.dev_ticket_task_sync.v4"
    )
    assert current["development_ticket_sync_revision"] == 3
    assert current["development_ticket_sync"]["resolved"] is True
    assert saved[-1]["development_ticket_synced_task_id"] == "task.1"


def test_completed_automation_synchronizes_every_ticket_in_package(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from adaos.services.development_tickets import DevelopmentTicketService

    service = _service(tmp_path)
    saved: list[dict] = []
    calls: list[tuple[str, str]] = []

    def sync(self, ticket_id, **kwargs):  # noqa: ARG001
        task_id = kwargs["automation_result"]["automation"]["task_id"]
        calls.append((ticket_id, task_id))
        return {
            "ok": True,
            "synchronized": True,
            "resolved": True,
            "ticket": {"ticket_id": ticket_id, "status": "resolved"},
        }

    monkeypatch.setattr(DevelopmentTicketService, "sync_builder_repair", sync)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_session_for_linked_task",
        lambda self, session, task_id: (
            {
                **dict(session),
                "current_task_id": "task.1",
                "status": "completed",
                "task": {"task_id": "task.1", "status": "completed"},
            }
            if task_id == "task.1"
            else dict(session)
        ),
    )

    current = service._sync_linked_development_ticket_tasks(
        {
            "object_type": "skill",
            "object_id": "demo_metrics_skill",
            "status": "completed",
            "current_task_id": "task.2",
            "task_history": ["task.1"],
            "links": {
                "development_ticket_id": "dticket.1",
                "development_ticket_ids": ["dticket.1", "dticket.2"],
                "builder_repair_id": "repair.package",
                "builder_package_id": "bpackage.1",
            },
            "completion_readiness": {"ok": True, "task_id": "task.2"},
            "task_results": {
                "task.1": {
                    "session": {
                        "current_task_id": "task.1",
                        "status": "completed",
                    },
                    "automation": {"task_id": "task.1", "status": "completed"},
                }
            },
        }
    )

    assert calls == [
        ("dticket.1", "task.1"),
        ("dticket.2", "task.1"),
        ("dticket.1", "task.2"),
        ("dticket.2", "task.2"),
    ]
    assert current["development_ticket_sync"]["ticket_count"] == 2
    assert current["development_ticket_sync"]["resolved"] is True
    assert current["development_ticket_synced_refs"] == [
        "dticket.1:task.1",
        "dticket.1:task.2",
        "dticket.2:task.1",
        "dticket.2:task.2",
    ]
    assert saved[-1]["development_ticket_sync_revision"] == 3


def test_failed_worker_synchronizes_linked_dev_ticket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    synced: list[dict] = []
    failed = {
        "session_id": "automation.skill.demo_metrics_skill",
        "object_type": "skill",
        "object_id": "demo_metrics_skill",
        "status": "failed",
        "current_task_id": "task.failed",
        "last_failure": {"message": "Codex execution failed"},
        "links": {
            "development_ticket_id": "dticket.failed",
            "builder_repair_id": "repair.failed",
        },
    }

    class FailingWorker:
        def run_once(self, *, task_id=None):  # noqa: ARG002
            return {"ok": False, "status": "failed"}

    class FakeWorkflow:
        def transition(self, *args, **kwargs):  # noqa: ARG002
            return {"ok": True}

    service.worker_factory = FailingWorker
    monkeypatch.setattr(
        BuilderAutomationService,
        "_find_session_by_id",
        lambda self, session_id: dict(failed),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda self, session: dict(failed),
    )
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: None)
    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(
        BuilderAutomationService,
        "_sync_linked_development_ticket_tasks",
        lambda self, value: synced.append(dict(value)) or dict(value),
    )

    service._run_worker(failed["session_id"])

    assert len(synced) == 1
    assert synced[0]["status"] == "failed"
    assert synced[0]["current_task_id"] == "task.failed"


def test_finalize_reconciles_exact_canonical_checkpoint_without_replay(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    saved: list[dict] = []
    notified: list[dict] = []

    class FakeWorkflow:
        def describe(self, object_type, object_id):  # noqa: ARG002
            return {
                "generation": 4,
                "automation": {
                    "status": "completed",
                    "head_task_id": "task.1",
                    "completed_at": "2026-08-18T02:40:45+00:00",
                },
                "delivery": {
                    "status": "checkpoint",
                    "checkpoint_change_id": "change-1",
                    "package_digest": "sha256:" + "1" * 64,
                    "source_revision": "forge-1",
                    "version": "0.1.1",
                },
            }

    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda *args, **kwargs: pytest.fail("Forge checkpoint must not be replayed"),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda *args, **kwargs: pytest.fail("DEV activation must not be replayed"),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)) or dict(value),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: notified.append(dict(value)) or dict(value),
    )

    service._finalize_completed_session(
        {
            "schema": "adaos.builder.automation_session.v1",
            "session_id": "automation.skill.research_skill",
            "object_type": "skill",
            "object_id": "research_skill",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "status": "commit_ready",
            "finalizing_task_id": "task.1",
        }
    )

    assert saved[-1]["status"] == "completed"
    assert saved[-1]["completion_readiness"]["ok"] is True
    checkpoint = saved[-1]["completion_readiness"]["vcs_checkpoints"][0]
    assert checkpoint["reconciled_from"] == "canonical_builder_workflow"
    assert checkpoint["package_digest"] == "sha256:" + "1" * 64
    assert notified[-1]["status"] == "completed"


def test_finalize_reenters_failed_workflow_for_checkpoint_reconciliation(
    tmp_path: Path, monkeypatch
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    transitions: list[tuple[str, dict]] = []

    class FakeWorkflow:
        def snapshot_current_automation(self, *args, **kwargs):  # noqa: ARG002
            return {"path": "automation/task.1"}

        def describe(self, *args, **kwargs):  # noqa: ARG002
            return {"active_phase": "automation", "automation": {"status": "failed"}}

        def transition(self, object_type, object_id, event, **kwargs):  # noqa: ARG002
            transitions.append((event, dict(kwargs.get("metadata") or {})))
            return {"workflow": {"delivery": {"status": "checkpoint"}}}

    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [
            {
                "ok": True,
                "kind": "skill",
                "name": "research_skill",
                "commit": "forge-1",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "forge-1",
            }
        ],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: {"ok": True, "id": skill_id},
    )
    monkeypatch.setattr(BuilderAutomationService, "_project_version", lambda *args: "0.1.1")
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", lambda self, value: dict(value))

    service._finalize_completed_session(
        {
            "session_id": "automation.skill.research_skill",
            "object_type": "skill",
            "object_id": "research_skill",
            "companion_skill_id": "research_skill",
            "webspace_id": "desktop-dev",
            "current_task_id": "task.1",
            "change_id": "change-reconciled",
            "status": "commit_ready",
        }
    )

    assert [event for event, _metadata in transitions] == [
        "automation_iteration_started",
        "automation_completed",
        "checkpoint_recorded",
    ]
    assert transitions[0][1]["reconciliation"] is True
    assert saved[-1]["status"] == "completed"


@pytest.mark.parametrize(
    ("binding_updated_at", "expected_preview_calls", "expected_transition"),
    [
        ("2026-07-29T03:45:00+00:00", 1, "followed_completed_work"),
        ("2026-07-29T03:50:00+00:00", 0, "preserved_user_selection"),
    ],
)
def test_finalize_follows_completed_automation_only_when_preview_choice_is_unchanged(
    tmp_path: Path,
    monkeypatch,
    binding_updated_at: str,
    expected_preview_calls: int,
    expected_transition: str,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    preview_calls: list[dict] = []
    public_target = {
        "schema": "adaos.builder.preview_target.v1",
        "object_type": "scenario",
        "object_id": "recipes",
        "stage": "publication",
        "revision": "0.1.0",
        "follow_active": False,
    }

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [
            {
                "ok": True,
                "kind": "scenario",
                "name": "recipes",
                "commit": "forge-1",
                "package_digest": "sha256:" + "1" * 64,
                "source_revision": "forge-1",
            }
        ],
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        def get_workspace_binding(self, source_webspace_id):  # noqa: ARG002
            return {
                "preview_webspace_id": "desktop-dev",
                "updated_at": binding_updated_at,
                "preview_target": public_target,
            }

    class FakeWorkflow:
        def snapshot_current_automation(self, *args, **kwargs):  # noqa: ARG002
            return {"path": "automation/0.1.1"}

        def describe(self, *args, **kwargs):  # noqa: ARG002
            return {"active_phase": "automation"}

        def transition(self, *args, **kwargs):  # noqa: ARG002
            return {"delivery": {"status": "checkpoint"}}

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(
        "adaos.sdk.builder.preview.select_target",
        lambda *args, **kwargs: preview_calls.append(dict(kwargs))
        or {"ok": True, "preview_webspace_id": "desktop-dev"},
    )
    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(BuilderAutomationService, "_notify_completed_session", lambda self, value: dict(value))

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "preview_binding_at_submit": {
                "captured": True,
                "updated_at": "2026-07-29T03:45:00+00:00",
                "target": public_target,
            },
        }
    )

    assert len(preview_calls) == expected_preview_calls
    if preview_calls:
        assert preview_calls[0]["stage"] == "automation"
        assert preview_calls[0]["follow_active"] is True
    assert saved[-1]["completion_readiness"]["preview_transition"]["status"] == expected_transition
    assert saved[-1]["status"] == "completed"


@pytest.mark.parametrize(
    ("failure_message", "expected_stage"),
    [
        ("activation failed", "activation"),
        ("skill tests failed: test_demo", "tests"),
    ],
)
def test_finalize_records_live_readiness_failure_without_success_chat(
    tmp_path: Path,
    monkeypatch,
    failure_message: str,
    expected_stage: str,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    notified: list[dict] = []
    synchronized: list[dict] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [{"ok": True, "commit": "forge-1"}],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError(failure_message)),
    )
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: notified.append(dict(value)) or dict(value),
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_sync_linked_development_ticket_tasks",
        lambda self, value: synchronized.append(dict(value)) or dict(value),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "status": "completed",
        }
    )

    assert saved[-1]["status"] == "failed"
    assert saved[-1]["last_failure"]["stage"] == expected_stage
    assert saved[-1]["completion_readiness"]["publication_gate_failure"]["ticket_id"]
    assert saved[-1]["progress"]["status"] == "failed"
    assert notified == []
    assert synchronized[-1]["status"] == "failed"


def test_prepare_dev_runtime_runs_slot_shaped_tests_before_activation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[tuple] = []

    class FakeManager:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        def prepare_dev_runtime(self, skill_id, *, run_tests):
            calls.append(("prepare", skill_id, run_tests))
            return SimpleNamespace(
                version="0.1.0",
                slot="B",
                resolved_manifest=tmp_path / "resolved.manifest.json",
            )

        def activate_for_space(self, skill_id, **kwargs):
            calls.append(("activate", skill_id, kwargs["slot"]))
            return kwargs["slot"]

        def dev_runtime_status(self, skill_id):
            calls.append(("status", skill_id))
            return {"ready": True, "active": True}

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        def get_workspace_binding(self, webspace_id):  # noqa: ARG002
            return {"preview_webspace_id": "desktop-dev"}

    fake_ctx = SimpleNamespace(
        skills_repo=object(),
        sql=object(),
        git=object(),
        paths=object(),
        bus=None,
        caps=object(),
        settings=object(),
    )
    monkeypatch.setattr("adaos.services.agent_context.get_ctx", lambda: fake_ctx)
    monkeypatch.setattr("adaos.adapters.db.SqliteSkillRegistry", lambda sql: object())
    monkeypatch.setattr("adaos.services.skill.manager.SkillManager", FakeManager)
    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)

    result = service._prepare_and_activate_dev_skill("research_skill", webspace_id="builder")

    assert result["ok"] is True
    assert calls == [
        ("prepare", "research_skill", True),
        ("activate", "research_skill", "B"),
        ("status", "research_skill"),
    ]


def test_finalize_stops_before_checkpoint_when_consumer_acceptance_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    checkpoints: list[str] = []
    class FakeWorkflow:
        def snapshot_current_automation(self, *args, **kwargs):  # noqa: ARG002
            return {"path": "automation/task.1"}

        def transition(self, *args, **kwargs):  # noqa: ARG002
            return {"ok": True}

    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: {"ok": True, "id": skill_id, "version": "0.1.0"},
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_run_development_acceptance",
        lambda self, session, **kwargs: {
            "schema": "adaos.builder.acceptance_summary.v1",
            "ok": False,
            "errors": ["consumer.contracts: prepare_attempt is incompatible"],
            "receipts": [],
        },
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: checkpoints.append("checkpoint") or [],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_save_session",
        lambda self, value: saved.append(dict(value)),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.skill.research_skill",
            "development_session_id": "dev_research_skill",
            "object_type": "skill",
            "object_id": "research_skill",
            "companion_skill_id": "research_skill",
            "webspace_id": "research-dev",
            "current_task_id": "task.1",
            "change_id": "change-1",
            "status": "completed",
        }
    )

    assert checkpoints == []
    assert saved[-1]["status"] == "failed"
    assert saved[-1]["last_failure"]["stage"] == "consumer_acceptance", [
        item.get("last_failure") for item in saved
    ]
    assert saved[-1]["completion_readiness"]["acceptance"]["ok"] is False


def test_consumer_acceptance_passes_declared_parameters_without_overriding_envelope(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []
    policy = {
        "project_ref": "project:research",
        "acceptance_profiles": ["research.consumer-contracts"],
        "acceptance_requirements": [
            {
                "id": "research.consumer-contracts",
                "profile": "research.consumer-contracts",
                "provider_ref": "skill:research_manager_skill",
                "operation": "validate_development_candidate",
                "required": True,
                "parameters": {"execute_workflow_smoke": True},
            }
        ],
        "context_members": [
            {
                "ref": "skill:research_manager_skill",
                "relation": "contract-consumer",
            }
        ],
        "instruction_inputs": [],
        "subject_refs": [],
        "contract_inputs": [],
    }

    from adaos.sdk.builder import development_sessions

    monkeypatch.setattr(development_sessions, "get", lambda _session_id: policy)

    class FakeManager:
        def __init__(self, **_kwargs):
            pass

        def run_tool(self, _provider_id, _operation, payload, **_kwargs):
            calls.append(dict(payload["request"]))
            return {
                "schema": "adaos.builder.acceptance_receipt.v1",
                "profile": "research.consumer-contracts",
                "ok": True,
                "checks": [],
                "errors": [],
            }

    fake_ctx = SimpleNamespace(
        skills_repo=object(),
        sql=object(),
        git=object(),
        paths=object(),
        bus=None,
        caps=object(),
        settings=object(),
    )
    monkeypatch.setattr("adaos.services.agent_context.get_ctx", lambda: fake_ctx)
    monkeypatch.setattr("adaos.adapters.db.SqliteSkillRegistry", lambda _sql: object())
    monkeypatch.setattr("adaos.services.skill.manager.SkillManager", FakeManager)

    result = service._run_development_acceptance(
        {
            "development_session_id": "dev-research",
            "object_type": "skill",
            "object_id": "research_candidate",
        },
        activations=[{"id": "research_candidate", "version": "0.1.0"}],
    )

    assert result["ok"] is True
    assert calls[0]["candidate_ref"] == "skill:research_candidate"
    assert calls[0]["profile"] == "research.consumer-contracts"
    assert calls[0]["execute_workflow_smoke"] is True


def test_finalize_compensates_failed_follow_active_preview_after_workflow_completion(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    notified: list[dict] = []
    transitions: list[str] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [{"ok": True, "commit": "forge-1"}],
    )

    class FakeWorkbench:
        def __init__(self, **kwargs):  # noqa: ARG002
            pass

        def get_workspace_binding(self, source_webspace_id):  # noqa: ARG002
            return {
                "preview_target": {
                    "stage": "prototype",
                    "revision": "UI 005",
                    "follow_active": True,
                }
            }

    class FakeWorkflow:
        def snapshot_current_automation(self, *args, **kwargs):  # noqa: ARG002
            return {"path": "automation/0.2.11"}

        def describe(self, object_type, object_id):  # noqa: ARG002
            return {"active_phase": "automation"}

        def transition(self, object_type, object_id, event, **kwargs):  # noqa: ARG002
            transitions.append(event)
            return {"active_phase": "automation"}

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(
        "adaos.sdk.builder.preview.select_target",
        lambda *args, **kwargs: {  # noqa: ARG005
            "ok": False,
            "error": "webspace_rebuild_failed",
            "error_detail": "ValueError: invalid runtime projection",
        },
    )
    monkeypatch.setattr(BuilderAutomationService, "_workflow", lambda self: FakeWorkflow())
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_notify_completed_session",
        lambda self, value: notified.append(dict(value)) or dict(value),
    )

    service._finalize_completed_session(
        {
            "session_id": "automation.scenario.recipes",
            "object_type": "scenario",
            "object_id": "recipes",
            "webspace_id": "desktop",
            "current_task_id": "task.1",
            "status": "completed",
        }
    )

    assert saved[-1]["status"] == "failed"
    assert saved[-1]["completion_readiness"]["ok"] is False
    assert saved[-1]["completion_readiness"]["materialization"]["error"] == "webspace_rebuild_failed"
    assert "invalid runtime projection" in saved[-1]["last_failure"]["message"]
    assert transitions == ["automation_completed", "automation_failed"]
    assert notified == []


def test_finalize_fails_when_forge_checkpoint_is_not_confirmed(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    activations: list[str] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [
            {"ok": False, "kind": "scenario", "name": "recipes", "error": "504"}
        ],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, skill_id, **kwargs: activations.append(skill_id) or {"ok": True},
    )
    monkeypatch.setattr(BuilderAutomationService, "_save_session", lambda self, value: saved.append(dict(value)))

    service._finalize_completed_session(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "webspace_id": "prompt-dev",
            "current_task_id": "task.1",
            "iteration": 1,
        }
    )

    assert saved[-1]["status"] == "failed"
    assert saved[-1]["completion_readiness"]["ok"] is False
    assert "Forge checkpoint failed" in saved[-1]["completion_readiness"]["error"]
    assert saved[-1]["last_failure"]["stage"] == "forge_checkpoint"
    assert activations == ["recipes_skill"]


def test_explicit_checkpoint_reconciliation_does_not_rerun_codex(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    failed = service.get_session("scenario", "recipes")
    assert failed is not None
    previous_change_id = failed["change_id"]
    failed["status"] = "failed"
    failed["completion_readiness"] = {
        "ok": False,
        "task_id": failed["current_task_id"],
        "vcs_checkpoints": [
            {"ok": False, "kind": "skill", "name": "recipes_skill", "error": "preflight"},
            {"ok": False, "kind": "scenario", "name": "recipes", "error": "preflight"},
        ],
    }
    failed["last_failure"] = {"stage": "forge_checkpoint", "message": "preflight"}
    service._save_session(failed)
    finalized: list[dict] = []

    def finalize(_service, session):
        finalized.append(dict(session))
        completed = dict(session)
        completed["status"] = "completed"
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Codex must not be submitted")),
    )

    result = service.reconcile_checkpoint(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["change_id"] != previous_change_id
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["current_task_id"] == failed["current_task_id"]
    assert finalized[0]["reconciliation_history"][-1]["previous_change_id"] == previous_change_id


def test_checkpoint_reconciliation_reuses_change_id_for_partially_committed_pair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
        object_type="scenario",
        object_id="recipes",
        implementation_brief="Implement recipe search.",
        webspace_id="prompt-dev",
    )
    failed = service.get_session("scenario", "recipes")
    assert failed is not None
    previous_change_id = failed["change_id"]
    failed["status"] = "failed"
    failed["completion_readiness"] = {
        "ok": False,
        "task_id": failed["current_task_id"],
        "vcs_checkpoints": [
            {"ok": True, "kind": "skill", "name": "recipes_skill", "commit": "abc"},
            {"ok": False, "kind": "scenario", "name": "recipes", "error": "timeout"},
        ],
    }
    failed["last_failure"] = {"stage": "forge_checkpoint", "message": "timeout"}
    service._save_session(failed)
    finalized: list[dict] = []

    def finalize(_service, session):
        finalized.append(dict(session))
        completed = dict(session)
        completed["status"] = "completed"
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    monkeypatch.setattr(
        BuilderAutomationService,
        "_submit",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("Codex must not be submitted")),
    )

    result = service.reconcile_checkpoint(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["change_id"] == previous_change_id
    assert finalized[0]["reconciliation_history"][-1]["mode"] == "resume_partial"


def test_validated_result_recovery_reuses_completed_task_after_live_readiness_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.1",
        "status": "failed",
        "task": {"task_id": "task.1", "status": "completed", "result": {"summary": "ready"}},
        "last_result": {"summary": "ready"},
        "last_failure": {"stage": "live_readiness", "message": "preview failed"},
        "completion_readiness": {
            "ok": False,
            "task_id": "task.1",
            "vcs_checkpoints": [{"ok": True, "kind": "scenario", "commit": "forge-1"}],
        },
    }
    service._save_session(session)
    finalized: list[dict] = []

    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("reuse_confirmed_checkpoints", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["reuse_confirmed_checkpoints"] is True


def test_validated_result_recovery_records_missing_workflow_checkpoint_without_rerunning_codex(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "change_id": "change-1",
        "current_task_id": "task.1",
        "status": "completed",
        "task": {"task_id": "task.1", "status": "completed"},
        "last_result": {"summary": "ready"},
        "completion_readiness": {
            "ok": True,
            "task_id": "task.1",
            "vcs_checkpoints": [
                {
                    "ok": True,
                    "kind": "scenario",
                    "name": "recipes",
                    "commit": "forge-1",
                    "package_digest": "sha256:" + "1" * 64,
                    "source_revision": "forge-1",
                }
            ],
        },
    }
    service._save_session(session)
    finalized: list[dict] = []

    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("reuse_confirmed_checkpoints", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert result["worker"]["recovery_stage"] == "workflow_checkpoint"
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["reuse_confirmed_checkpoints"] is True


@pytest.mark.parametrize("failure_stage", ["snapshot", "project_checkpoint"])
def test_validated_result_recovery_retries_snapshot_without_rerunning_codex(
    tmp_path: Path,
    monkeypatch,
    failure_stage: str,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.snapshot",
        "status": "failed",
        "task": {
            "task_id": "task.snapshot",
            "status": "completed",
            "result": {"summary": "validated"},
        },
        "last_result": {"summary": "validated"},
        "last_failure": {
            "stage": failure_stage,
            "message": "project-owned UI was not resolved",
        },
        "completion_readiness": {
            "ok": False,
            "task_id": "task.snapshot",
            "stage": failure_stage,
            "vcs_checkpoints": [],
        },
    }
    service._save_session(session)
    finalized: list[dict] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "refresh_session",
        lambda self, value: dict(value),
    )

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        _service._save_session(completed)

    monkeypatch.setattr(
        BuilderAutomationService,
        "_finalize_completed_session",
        finalize,
    )
    service.worker_factory = lambda: (_ for _ in ()).throw(
        AssertionError("worker must not run")
    )

    result = service.recover_validated_result(
        object_type="scenario",
        object_id="recipes",
    )

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert result["worker"]["recovery_stage"] == failure_stage
    assert finalized[0]["status"] == "commit_ready"
    assert "reuse_confirmed_checkpoints" not in finalized[0]


def test_validated_result_recovery_resumes_interrupted_finalization_without_codex(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.interrupted",
        "finalizing_task_id": "task.interrupted",
        "status": "commit_ready",
        "task": {
            "task_id": "task.interrupted",
            "status": "completed",
            "result": {"summary": "validated"},
        },
        "last_result": {"summary": "validated"},
        "completion_readiness": {
            "ok": False,
            "task_id": "task.interrupted",
            "stage": "aprobation_activation",
            "vcs_checkpoints": [
                {
                    "ok": True,
                    "kind": "scenario",
                    "name": "recipes",
                    "commit": "forge-1",
                    "package_digest": "sha256:" + "1" * 64,
                    "source_revision": "forge-1",
                }
            ],
        },
    }
    service._save_session(session)
    finalized: list[dict] = []

    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("reuse_confirmed_checkpoints", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(
        object_type="scenario",
        object_id="recipes",
    )

    assert result["ok"] is True
    assert result["worker"]["recovery_stage"] == "interrupted_finalization"
    assert result["worker"]["reused_validated_result"] is True
    assert finalized[0]["reuse_confirmed_checkpoints"] is True


def test_validated_result_recovery_rebinds_checkpoint_after_unknown_trial_reconciliation(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "skill",
        "object_id": "experiment_skill",
        "change_id": "change-1",
        "current_task_id": "task.1",
        "status": "completed",
        "task": {"task_id": "task.1", "status": "completed"},
        "last_result": {"summary": "ready"},
        "completion_readiness": {
            "ok": True,
            "task_id": "task.1",
            "vcs_checkpoints": [
                {
                    "ok": True,
                    "kind": "skill",
                    "name": "experiment_skill",
                    "commit": "forge-1",
                    "package_digest": "sha256:" + "1" * 64,
                    "source_revision": "forge-1",
                    "version": "0.1.1",
                }
            ],
            "workflow_checkpoint": {"ok": True, "generation": 8},
        },
    }
    service._save_session(session)
    finalized: list[dict] = []

    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))
    monkeypatch.setattr(
        BuilderAutomationService,
        "_workflow",
        lambda _self: SimpleNamespace(
            describe=lambda *_args: {
                "automation": {"status": "completed", "head_task_id": "task.1"},
                "delivery": {
                    "status": "idle",
                    "reconciled_at": "2026-08-21T06:00:00+00:00",
                    "checkpoint_change_id": "change-1",
                    "package_digest": "sha256:" + "1" * 64,
                    "source_revision": "forge-1",
                },
            }
        ),
    )

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("reuse_confirmed_checkpoints", None)
        completed.pop("rebind_confirmed_checkpoint", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(
        object_type="skill",
        object_id="experiment_skill",
    )

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert result["worker"]["recovery_stage"] == "trial_checkpoint_rebind"
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["reuse_confirmed_checkpoints"] is True
    assert finalized[0]["rebind_confirmed_checkpoint"] is True


def test_refresh_restores_recovered_return_to_prototype_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.prototype",
        "status": "failed",
        "last_failure": {"message": "worker failed before finalization"},
    }
    task = {
        "task_id": "task.prototype",
        "status": "completed",
        "updated_at": "2026-07-28T12:00:00+00:00",
        "realize_request": {
            "artifacts": {"workflow_transition": "return_to_prototype"},
        },
        "result": {"summary": "Safe prototype recovered."},
    }
    monkeypatch.setattr(
        type(service.factory),
        "read_task",
        lambda _self, _task_id: task,
    )

    refreshed = service.refresh_session(session)

    assert refreshed["status"] == "completed"
    assert refreshed["pending_workflow_transition"] == "return_to_prototype"
    assert refreshed["last_result"]["summary"] == "Safe prototype recovered."


def test_refresh_reports_structured_edit_zero_usage_on_first_terminal_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []
    service.codex_usage_reporter = lambda event: (
        calls.append(dict(event))
        or {
            "ok": True,
            "duplicate": False,
            "event": {"event_id": "codex_usage_first_terminal_zero"},
        }
    )
    session = {
        "session_id": "automation.skill.demo",
        "object_type": "skill",
        "object_id": "demo",
        "current_task_id": "task.structured.first",
        "status": "in_progress",
    }
    task = {
        "task_id": "task.structured.first",
        "status": "completed",
        "updated_at": "2026-09-01T18:00:00+00:00",
        "result": {
            "summary": "Applied structured edit.",
            "execution_strategy": "structured_edits",
            "provenance": {"execution_strategy": "structured_edits"},
        },
    }
    monkeypatch.setattr(type(service.factory), "read_task", lambda _self, _task_id: task)

    refreshed = service.refresh_session(session)

    assert refreshed["codex_usage_accounting"]["status"] == "reported"
    assert refreshed["codex_usage_accounting"]["total_tokens"] == 0
    assert refreshed["codex_usage_accounting"]["root_event_id"] == (
        "codex_usage_first_terminal_zero"
    )
    assert len(calls) == 1


def test_validated_result_recovery_finalizes_recovered_workflow_transition(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "scenario",
        "object_id": "recipes",
        "current_task_id": "task.prototype",
        "status": "completed",
        "task": {"task_id": "task.prototype", "status": "completed"},
        "last_result": {"summary": "Safe prototype recovered."},
        "pending_workflow_transition": "return_to_prototype",
    }
    service._save_session(session)
    finalized: list[dict] = []
    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        completed.pop("pending_workflow_transition", None)
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not run"))

    result = service.recover_validated_result(object_type="scenario", object_id="recipes")

    assert result["ok"] is True
    assert result["worker"]["recovery_stage"] == "workflow_transition"
    assert finalized[0]["status"] == "commit_ready"
    assert finalized[0]["pending_workflow_transition"] == "return_to_prototype"


def test_validated_result_recovery_finalizes_externally_repaired_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "skill",
        "object_id": "builder_sdk_control_skill",
        "current_task_id": "task.repaired",
        "status": "completed",
        "task": {"task_id": "task.repaired", "status": "completed"},
        "last_result": {"summary": "Preserved worktree repaired and validated."},
    }
    service._save_session(session)
    finalized: list[dict] = []
    monkeypatch.setattr(BuilderAutomationService, "refresh_session", lambda self, value: dict(value))

    def finalize(_service, value):
        finalized.append(dict(value))
        completed = dict(value)
        completed["status"] = "completed"
        _service._save_session(completed)

    monkeypatch.setattr(BuilderAutomationService, "_finalize_completed_session", finalize)
    service.worker_factory = lambda: (_ for _ in ()).throw(AssertionError("worker must not rerun"))

    result = service.recover_validated_result(
        object_type="skill",
        object_id="builder_sdk_control_skill",
    )

    assert result["ok"] is True
    assert result["worker"]["reused_validated_result"] is True
    assert result["worker"]["recovery_stage"] == "validated_activation"
    assert finalized[0]["status"] == "commit_ready"
    assert "reuse_confirmed_checkpoints" not in finalized[0]


def test_automation_checkpoints_scenario_and_companion_skill_with_result_summary(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"], "commit": f"{kwargs['kind']}-sha"}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Workspace)

    checkpoints = service._checkpoint_completed_artifacts(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "last_result": {
                "summary": "Implemented recipe filters and details.",
                "changed_paths": [
                    "skills/recipes_skill/handlers/main.py",
                    "scenarios/recipes/webui.json",
                ],
            },
        }
    )

    assert calls == [
        {
            "kind": "skill",
            "artifact_id": "recipes_skill",
            "message": "Implemented recipe filters and details.",
        },
        {
            "kind": "scenario",
            "artifact_id": "recipes",
            "message": "Implemented recipe filters and details.",
        },
    ]
    assert [item["commit"] for item in checkpoints] == ["skill-sha", "scenario-sha"]


def test_automation_does_not_checkpoint_unchanged_companion_skill(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"]}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Workspace)

    checkpoints = service._checkpoint_completed_artifacts(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "last_result": {
                "summary": "Aligned derived scenario projections.",
                "changed_paths": [
                    ".adaos/tasks/task.1/result.json",
                    "scenarios/recipes/scenario.json",
                    "scenarios/recipes/webui.json",
                ],
            },
        }
    )

    assert calls == [
        {
            "kind": "scenario",
            "artifact_id": "recipes",
            "message": "Aligned derived scenario projections.",
        }
    ]
    assert checkpoints == [{"ok": True, "kind": "scenario", "name": "recipes"}]


def test_validation_only_checkpoints_artifact_guarded_before_worker_run(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"]}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Workspace)

    checkpoints = service._checkpoint_completed_artifacts(
        {
            "object_type": "skill",
            "object_id": "subscription_status_skill",
            "last_result": {
                "summary": "Validated the prepared subscription projection.",
                "changed_paths": [],
                "no_source_change": True,
                "execution_strategy": "validation_only",
                "provenance": {
                    "execution_strategy": "validation_only",
                    "validation_only": {
                        "guarded_paths": [
                            "skills/subscription_status_skill/handlers/main.py",
                            "skills/subscription_status_skill/webui.json",
                        ]
                    },
                },
            },
        }
    )

    assert calls == [
        {
            "kind": "skill",
            "artifact_id": "subscription_status_skill",
            "message": "Validated the prepared subscription projection.",
        }
    ]
    assert checkpoints == [
        {"ok": True, "kind": "skill", "name": "subscription_status_skill"}
    ]


def test_validation_only_marks_guarded_companion_skill_for_runtime_overlay(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    session = {
        "object_type": "skill",
        "object_id": "subscription_status_skill",
        "companion_skill_ids": ["subscription_status_skill"],
        "last_result": {
            "changed_paths": [],
            "no_source_change": True,
            "execution_strategy": "validation_only",
            "provenance": {
                "execution_strategy": "validation_only",
                "validation_only": {
                    "guarded_paths": [
                        "skills/subscription_status_skill/handlers/main.py",
                        "skills/subscription_status_skill/webui.json",
                    ]
                },
            },
        },
    }

    assert service._session_changed_companion_skill_ids(session) == [
        "subscription_status_skill"
    ]


def test_automation_checkpoints_primary_scenario_when_only_companion_skill_changed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    service = _service(tmp_path)
    calls: list[dict] = []

    class _Workspace:
        @classmethod
        def from_context(cls):
            return cls()

        def checkpoint_artifact(self, **kwargs):
            calls.append(dict(kwargs))
            return {"ok": True, "kind": kwargs["kind"], "name": kwargs["artifact_id"]}

    import adaos.services.builder.workspace as workspace

    monkeypatch.setattr(workspace, "BuilderWorkspaceService", _Workspace)

    checkpoints = service._checkpoint_completed_artifacts(
        {
            "object_type": "scenario",
            "object_id": "recipes",
            "companion_skill_id": "recipes_skill",
            "last_result": {
                "summary": "Implemented the scenario dependency in its companion skill.",
                "changed_paths": ["skills/recipes_skill/handlers/main.py"],
            },
        }
    )

    assert [(item["kind"], item["artifact_id"]) for item in calls] == [
        ("skill", "recipes_skill"),
        ("scenario", "recipes"),
    ]
    assert [(item["kind"], item["name"]) for item in checkpoints] == [
        ("skill", "recipes_skill"),
        ("scenario", "recipes"),
    ]
