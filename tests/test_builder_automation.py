from __future__ import annotations

import json
from pathlib import Path

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
