from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
import yaml

from adaos.services.root.service import _rewrite_skill_template_identity
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_sources import capture_source_snapshot
from adaos.services.skill_factory_worker import CodexRunResult, LocalSkillFactoryWorker, SubprocessCodexExecutor


def _scenario(root: Path, scenario_id: str) -> Path:
    target = root / scenario_id
    target.mkdir(parents=True)
    (target / "scenario.yaml").write_text(
        yaml.safe_dump(
            {
                "id": scenario_id,
                "version": "0.1.0",
                "depends": [],
                "description": "Recipe book interface prototype",
            },
            sort_keys=False,
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
        scenario_path = workspace / "scenarios" / "recipe_book" / "scenario.yaml"
        scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        scenario["depends"] = ["recipe_book_skill"]
        scenario_path.write_text(yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8")
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
    scenario = yaml.safe_load((dev_scenarios / "recipe_book" / "scenario.yaml").read_text(encoding="utf-8"))
    assert scenario["depends"] == ["recipe_book_skill"]
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == submitted["task"]["task_id"]
    )
    assert task["status"] == "completed"
    assert task["result"]["commit_hash"]
    assert task["result"]["provenance"]["runner_version"].startswith("adaos-local-codex-worker/")


def test_worker_rejects_codex_changes_to_checkpoint_owned_manifest_metadata(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    scenario = _scenario(workspace / "scenarios", "recipe_book")
    skill = _core_created_skill_fixture(repo_root, workspace / "skills", "recipe_book_skill")
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    worker._init_git_workspace(workspace, "test/checkpoint-metadata")

    scenario_manifest = yaml.safe_load((scenario / "scenario.yaml").read_text(encoding="utf-8"))
    scenario_manifest["version"] = "9.9.9"
    (scenario / "scenario.yaml").write_text(
        yaml.safe_dump(scenario_manifest, sort_keys=False),
        encoding="utf-8",
    )
    skill_manifest = yaml.safe_load((skill / "skill.yaml").read_text(encoding="utf-8"))
    skill_manifest["updated_at"] = "2099-01-01T00:00:00Z"
    (skill / "skill.yaml").write_text(
        yaml.safe_dump(skill_manifest, sort_keys=False),
        encoding="utf-8",
    )
    checks: list[dict] = []
    errors: list[str] = []

    worker._validate_checkpoint_owned_manifest_metadata(workspace, checks, errors)

    assert any("scenario.yaml" in item and "version" in item for item in errors)
    assert any("skill.yaml" in item and "updated_at" in item for item in errors)


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


def test_local_worker_does_not_overwrite_dev_that_changed_after_task_snapshot(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    scenario_root = _scenario(dev_scenarios, "recipe_book")
    skill_root = _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(
            ("scenario", "recipe_book", scenario_root),
            ("skill", "recipe_book_skill", skill_root),
        ),
        created_at="2026-07-24T12:00:00+00:00",
    )
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {"companion_skill_id": "recipe_book_skill"},
            "repo": {
                "base_revision": snapshot["digest"],
                "source_snapshot": snapshot,
                "sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"],
            },
        }
    )
    scenario_path = scenario_root / "scenario.yaml"
    scenario_path.write_text(
        scenario_path.read_text(encoding="utf-8") + "\n# concurrent user edit\n",
        encoding="utf-8",
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        task_scenario = workspace / "scenarios" / "recipe_book" / "scenario.yaml"
        assert "concurrent user edit" not in task_scenario.read_text(encoding="utf-8")
        handler = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        handler.write_text(handler.read_text(encoding="utf-8") + "\n# task result\n", encoding="utf-8")
        return CodexRunResult(returncode=0, final_message="Implemented from exact base.")

    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        executor=fake_codex,
        max_repair_attempts=0,
    )

    result = worker.run_once()

    assert result["ok"] is False
    assert "DEV source changed while Codex was running" in result["error"]
    assert "concurrent user edit" in scenario_path.read_text(encoding="utf-8")
    assert "task result" not in (skill_root / "handlers" / "main.py").read_text(encoding="utf-8")
    task_workspace = Path(result["run_dir"]) / "workspace"
    assert "task result" in (
        task_workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
    ).read_text(encoding="utf-8")


def test_local_worker_recovers_committed_validated_result_without_rerunning_codex(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    scenario_root = _scenario(dev_scenarios, "recipe_book")
    skill_root = _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(
            ("scenario", "recipe_book", scenario_root),
            ("skill", "recipe_book_skill", skill_root),
        ),
        created_at="2026-07-28T12:00:00+00:00",
    )
    factory = SkillFactoryService(state_dir=state_dir)
    submitted = factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {"companion_skill_id": "recipe_book_skill"},
            "repo": {
                "base_revision": snapshot["digest"],
                "source_snapshot": snapshot,
                "sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"],
            },
        }
    )
    codex_calls: list[str] = []

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        codex_calls.append(prompt)
        handler = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        handler.write_text(handler.read_text(encoding="utf-8") + "\n# recovered task result\n", encoding="utf-8")
        return CodexRunResult(returncode=0, final_message="Implemented once.")

    runs_root = tmp_path / "runs"
    wrong_worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=tmp_path / "wrong" / "skills",
        dev_scenarios_root=tmp_path / "wrong" / "scenarios",
        runs_root=runs_root,
        executor=fake_codex,
        max_repair_attempts=0,
    )

    failed = wrong_worker.run_once()

    assert failed["ok"] is False
    assert "source directory does not exist" in failed["error"]
    recovery_worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=runs_root,
        executor=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Codex must not rerun")),
    )

    recovered = recovery_worker.recover_validated_run(submitted["task"]["task_id"])

    assert recovered["ok"] is True
    assert codex_calls and len(codex_calls) == 1
    assert "recovered task result" in (skill_root / "handlers" / "main.py").read_text(encoding="utf-8")
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == submitted["task"]["task_id"]
    )
    assert task["status"] == "completed"
    assert task["attempts"] == 1
    assert task["result_recovery_history"][-1]["failure_id"]


def test_return_to_prototype_uses_snapshot_but_cannot_modify_automation_skill(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    _scenario(dev_scenarios, "recipe_book")
    _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    snapshot = state_dir / "builder" / "workflow_snapshots" / "scenario" / "recipe_book" / "automation"
    snapshot.mkdir(parents=True)
    (snapshot / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {}}}),
        encoding="utf-8",
    )
    (snapshot / "snapshot.json").write_text(json.dumps({"task_id": "task.previous"}), encoding="utf-8")
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {
                "companion_skill_id": "recipe_book_skill",
                "workflow_transition": "return_to_prototype",
            },
            "repo": {"sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]},
        }
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        assert "returns the completed Automation result to Prototype" in prompt
        assert (workspace / "scenarios" / "recipe_book" / ".builder_previous_automation" / "webui.json").is_file()
        skill = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        skill.write_text(skill.read_text(encoding="utf-8") + "\n# forbidden change\n", encoding="utf-8")
        return CodexRunResult(returncode=0)

    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        executor=fake_codex,
        max_repair_attempts=0,
    )

    result = worker.run_once()

    assert result["ok"] is False
    assert "may not modify the frozen Automation implementation" in result["error"]
    assert "forbidden change" not in (dev_skills / "recipe_book_skill" / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )


def test_return_to_prototype_skips_frozen_skill_tests_but_enforces_safe_ui(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    scenarios = workspace / "scenarios"
    skills = workspace / "skills"
    scenario = _scenario(scenarios, "recipe_book")
    skill = _core_created_skill_fixture(repo_root, skills, "recipe_book_skill")
    tests_dir = skill / "tests"
    tests_dir.mkdir(exist_ok=True)
    (tests_dir / "test_frozen_integration.py").write_text(
        "def test_old_automation_contract():\n    assert False, 'must not run for immutable skill input'\n",
        encoding="utf-8",
    )
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=skills,
        dev_scenarios_root=scenarios,
        runs_root=tmp_path / "runs",
    )
    assignment = {
        "target": {"type": "scenario", "id": "recipe_book"},
        "realize_request": {
            "artifacts": {
                "companion_skill_id": "recipe_book_skill",
                "workflow_transition": "return_to_prototype",
            }
        },
    }

    safe = worker._validate_workspace(assignment, workspace)

    assert safe["ok"] is True
    skipped = next(check for check in safe["checks"] if check.get("status") == "skipped")
    assert skipped["path"] == "skills/recipe_book_skill/tests"

    manifest = yaml.safe_load((scenario / "scenario.yaml").read_text(encoding="utf-8"))
    manifest["depends"] = ["recipe_book_skill"]
    (scenario / "scenario.yaml").write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    (scenario / "webui.json").write_text(
        json.dumps(
            {
                "schema": "adaos.webui.v1",
                "ui": {
                    "application": {
                        "desktop": {
                            "pageSchema": {
                                "widgets": [
                                    {
                                        "id": "recipes",
                                        "type": "ui.list",
                                        "dataSource": {"kind": "skill", "name": "recipe_book_skill.list"},
                                    }
                                ]
                            }
                        }
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    unsafe = worker._validate_workspace(assignment, workspace)

    assert unsafe["ok"] is False
    assert any("left functional or external bindings" in error for error in unsafe["errors"])


def test_return_to_prototype_ignores_preexisting_generated_skill_caches(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    scenario = _scenario(dev_scenarios, "recipe_book")
    skill = _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    cache = skill / "handlers" / "__pycache__" / "main.cpython-311.pyc"
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_bytes(b"generated")
    snapshot = state_dir / "builder" / "workflow_snapshots" / "scenario" / "recipe_book" / "automation"
    snapshot.mkdir(parents=True)
    (snapshot / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {}}}),
        encoding="utf-8",
    )
    (snapshot / "snapshot.json").write_text(json.dumps({"task_id": "task.previous"}), encoding="utf-8")
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {
                "companion_skill_id": "recipe_book_skill",
                "workflow_transition": "return_to_prototype",
            },
            "repo": {"sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]},
        }
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        manifest_path = workspace / "scenarios" / "recipe_book" / "scenario.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["description"] = "Safe local recipe prototype"
        manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
        return CodexRunResult(returncode=0, final_message="Created safe prototype.")

    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
        executor=fake_codex,
        max_repair_attempts=0,
    )

    result = worker.run_once()

    assert result["ok"] is True, result
    assert not cache.exists()
    assert all(not path.startswith("skills/") for path in result["result"]["changed_paths"])
    assert "Safe local recipe prototype" in (scenario / "scenario.yaml").read_text(encoding="utf-8")


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
