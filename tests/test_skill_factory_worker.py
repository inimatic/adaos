from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from adaos.services.root.service import _rewrite_skill_template_identity
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_worker import CodexRunResult, LocalSkillFactoryWorker, SubprocessCodexExecutor


def _scenario(root: Path, scenario_id: str) -> Path:
    target = root / scenario_id
    target.mkdir(parents=True)
    (target / "scenario.json").write_text(
        json.dumps(
            {
                "id": scenario_id,
                "version": "0.1.0",
                "depends": [],
                "description": "Recipe book interface prototype",
            }
        ),
        encoding="utf-8",
    )
    (target / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {}}}),
        encoding="utf-8",
    )
    (target / "builder.draft.json").write_text(
        json.dumps({"draft_id": "draft.recipe", "source": {"utterance": "Create a recipe book"}}),
        encoding="utf-8",
    )
    return target


def _core_created_skill_fixture(repo_root: Path, root: Path, skill_id: str) -> Path:
    target = root / skill_id
    shutil.copytree(repo_root / "src" / "adaos" / "skills_templates" / "skill_default", target)
    _rewrite_skill_template_identity(target, skill_id)
    return target


def test_local_worker_realizes_scenario_and_companion_skill(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    _scenario(dev_scenarios, "recipe_book")
    _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")

    factory = SkillFactoryService(state_dir=state_dir)
    submitted = factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "source": {"type": "prompt_ide", "text": "Implement the approved recipe book prototype."},
            "artifacts": {
                "implementation_brief": "Recipes must be searchable and open a detailed view.",
                "companion_skill_id": "recipe_book_skill",
            },
            "repo": {
                "sparse_paths": [
                    "scenarios/recipe_book/",
                    "skills/recipe_book_skill/",
                    "docs/requirements/recipe_book/",
                ]
            },
        }
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:
        assert "Recipes must be searchable" in prompt
        skill_handler = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        skill_handler.write_text(skill_handler.read_text(encoding="utf-8") + "\n# realized by test\n", encoding="utf-8")
        scenario_path = workspace / "scenarios" / "recipe_book" / "scenario.json"
        scenario = json.loads(scenario_path.read_text(encoding="utf-8"))
        scenario["depends"] = ["recipe_book_skill"]
        scenario_path.write_text(json.dumps(scenario, indent=2), encoding="utf-8")
        return CodexRunResult(returncode=0, events='{"type":"done"}\n', final_message="Implemented recipe skill.")

    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        executor=fake_codex,
    )
    result = worker.run_once()

    assert result["ok"] is True, result
    assert result["assignment"]["realize_request"]["artifacts"]["implementation_brief"].startswith("Recipes")
    assert (dev_skills / "recipe_book_skill" / "skill.yaml").exists()
    assert "realized by test" in (dev_skills / "recipe_book_skill" / "handlers" / "main.py").read_text(encoding="utf-8")
    scenario = json.loads((dev_scenarios / "recipe_book" / "scenario.json").read_text(encoding="utf-8"))
    assert scenario["depends"] == ["recipe_book_skill"]
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == submitted["task"]["task_id"]
    )
    assert task["status"] == "completed"
    assert task["result"]["commit_hash"]
    assert task["result"]["provenance"]["runner_version"].startswith("adaos-local-codex-worker/")


def test_local_worker_rejects_out_of_scope_codex_change(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    _scenario(dev_scenarios, "recipe_book")
    _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {"companion_skill_id": "recipe_book_skill"},
            "repo": {"sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]},
        }
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        path = workspace / "outside.txt"
        path.write_text("not allowed", encoding="utf-8")
        return CodexRunResult(returncode=0)

    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        executor=fake_codex,
    )
    result = worker.run_once()

    assert result["ok"] is False
    assert "outside the task scope" in result["error"]
    assert not (tmp_path / "outside.txt").exists()


def test_codex_executor_environment_does_not_inherit_api_or_adaos_secrets(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ADAOS_TOKEN", "secret")
    monkeypatch.setenv("CODEX_HOME", "C:/codex-home")
    monkeypatch.setenv("PATH", "C:/bin")

    environment = SubprocessCodexExecutor._bounded_environment()

    assert environment["CODEX_HOME"] == "C:/codex-home"
    assert environment["PATH"] == "C:/bin"
    assert "OPENAI_API_KEY" not in environment
    assert "ADAOS_TOKEN" not in environment


def test_codex_executor_discovers_vscode_bundled_cli(monkeypatch, tmp_path: Path) -> None:
    executable = (
        tmp_path
        / ".vscode"
        / "extensions"
        / "openai.chatgpt-26.7.0-win32-x64"
        / "bin"
        / "windows-x86_64"
        / "codex.exe"
    )
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"")
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("ADAOS_CODEX_EXECUTABLE", raising=False)
    monkeypatch.setenv("PATH", "")

    assert SubprocessCodexExecutor()._resolve_executable() == str(executable.resolve())


def test_codex_executor_reports_actionable_missing_cli(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("ADAOS_CODEX_EXECUTABLE", raising=False)
    monkeypatch.setenv("PATH", "")

    with pytest.raises(RuntimeError, match="ADAOS_CODEX_EXECUTABLE"):
        SubprocessCodexExecutor()._resolve_executable()


def test_worker_reasks_codex_to_repair_validation_failure(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    _scenario(dev_scenarios, "recipe_book")
    _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {"companion_skill_id": "recipe_book_skill"},
            "repo": {"sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]},
        }
    )
    calls: list[str] = []
    original_handler: list[str] = []

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        calls.append(prompt)
        handler = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        if len(calls) == 1:
            original_handler.append(handler.read_text(encoding="utf-8"))
            handler.unlink()
        else:
            handler.parent.mkdir(parents=True, exist_ok=True)
            handler.write_text(original_handler[0] + "\n# repaired\n", encoding="utf-8")
        return CodexRunResult(returncode=0, final_message="done")

    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        executor=fake_codex,
        max_repair_attempts=1,
    )

    result = worker.run_once()

    assert result["ok"] is True, result
    assert len(calls) == 2
    assert "Deterministic validation repair" in calls[1]
    assert "required file missing" in calls[1]


def test_worker_reports_progress_to_automation_callback(tmp_path: Path) -> None:
    reported: list[tuple[str, dict]] = []
    projected: list[tuple[str, str, str]] = []

    class _Factory:
        def report_progress(self, task_id, payload):
            reported.append((task_id, dict(payload)))

    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=tmp_path / "repo",
        dev_skills_root=tmp_path / "skills",
        dev_scenarios_root=tmp_path / "scenarios",
        progress_callback=lambda task_id, status, message: projected.append((task_id, status, message)),
    )
    worker.factory = _Factory()

    worker._progress("task.1", "tests_running", "Running validation")

    assert reported == [
        (
            "task.1",
            {
                "node_id": "devnode.local-codex",
                "status": "tests_running",
                "stage": "tests_running",
                "message": "Running validation",
            },
        )
    ]
    assert projected == [("task.1", "tests_running", "Running validation")]
