from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from jsonschema import Draft202012Validator

from adaos.services.builder.automation import BuilderAutomationService
from adaos.services.skill_factory_worker import CodexRunResult, LocalSkillFactoryWorker


def _service(tmp_path: Path) -> BuilderAutomationService:
    repo_root = Path(__file__).resolve().parents[1]
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    scenario = dev_scenarios / "recipes"
    scenario.mkdir(parents=True)
    dev_skills.mkdir(parents=True)
    (scenario / "scenario.json").write_text(
        json.dumps({"id": "recipes", "version": "0.1.0", "depends": []}), encoding="utf-8"
    )
    (scenario / "webui.json").write_text(json.dumps({"schema": "adaos.webui.v1"}), encoding="utf-8")

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
        background=False,
        materialize_on_completion=False,
    )


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
    assert status["session"]["standard_prompt_version"].startswith("adaos-skill-realization/")
    assert (service.dev_skills_root / "recipes_skill" / "skill.yaml").exists()
    assert status["session"]["local_run"]["events_path"].endswith("codex-live.jsonl")


def test_completed_automation_routes_chat_to_next_codex_iteration(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.start_from_execute(
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
    }
    assert projection["steps"][-1]["state"] == "completed"

    schema_path = Path(__file__).resolve().parents[1] / "src" / "adaos" / "abi" / "builder.automation_projection.v1.schema.json"
    schema = json.loads(schema_path.read_text(encoding="utf-8"))
    Draft202012Validator(schema).validate(projection)


def test_empty_projection_disables_automation_input() -> None:
    projection = BuilderAutomationService.empty_projection(webspace_id="prompt-dev")

    assert projection["status"] == "idle"
    assert projection["can_submit"] is False
    assert projection["project"] is None


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


def test_completed_session_publishes_one_terminal_chat_message(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    published: list[dict] = []
    monkeypatch.setattr(
        "adaos.services.agent_context.get_ctx",
        lambda: SimpleNamespace(bus=object()),
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
    assert "Локальный Codex завершил работу" in published[0]["response"]["message"]
    assert published[0]["thread_id"] == "prompt-project:scenario:recipes"
    assert second["completion_notified_task_id"] == "task.1"


def test_finalize_prepares_runtime_forces_reload_then_notifies(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    service.materialize_on_completion = True
    calls: list[str] = []
    saved: list[dict] = []

    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: calls.append("checkpoint") or [{"ok": True, "commit": "forge-1"}],
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
            return {"dev_webspace_id": "desktop-dev"}

    async def fake_reload(webspace_id, **kwargs):  # noqa: ARG001
        calls.append("reload")
        return {"ok": True, "webspace_id": webspace_id}

    monkeypatch.setattr("adaos.services.builder.workbench.BuilderWorkbenchService", FakeWorkbench)
    monkeypatch.setattr(
        "adaos.services.scenario.webspace_runtime.reload_webspace_from_scenario",
        fake_reload,
    )
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
            "status": "completed",
        }
    )

    assert calls == ["checkpoint", "activate:recipes_skill", "ensure", "reload", "notify"]
    assert saved[-1]["completion_readiness"]["ok"] is True
    assert saved[-1]["completion_readiness"]["vcs_checkpoints"][0]["commit"] == "forge-1"


def test_finalize_records_live_readiness_failure_without_success_chat(tmp_path: Path, monkeypatch) -> None:
    service = _service(tmp_path)
    saved: list[dict] = []
    notified: list[dict] = []
    monkeypatch.setattr(
        BuilderAutomationService,
        "_checkpoint_completed_artifacts",
        lambda self, session: [{"ok": True, "commit": "forge-1"}],
    )
    monkeypatch.setattr(
        BuilderAutomationService,
        "_prepare_and_activate_dev_skill",
        lambda self, *args, **kwargs: (_ for _ in ()).throw(RuntimeError("activation failed")),
    )
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
            "companion_skill_id": "recipes_skill",
            "status": "completed",
        }
    )

    assert saved[-1]["status"] == "failed"
    assert saved[-1]["last_failure"]["stage"] == "live_readiness"
    assert notified == []


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
            "last_result": {"summary": "Implemented recipe filters and details."},
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
