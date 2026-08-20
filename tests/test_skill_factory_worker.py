from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.services.root.service import _rewrite_skill_template_identity
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_sources import capture_source_snapshot, materialize_source_snapshot
from adaos.services.skill_factory_worker import CodexRunResult, LocalSkillFactoryWorker, SubprocessCodexExecutor


def test_source_snapshot_keeps_reserved_artifacts_out_of_codex_workspace(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    skill = tmp_path / "direction_skill"
    (skill / "artifacts" / "part0").mkdir(parents=True)
    (skill / "skill.yaml").write_text("name: direction_skill\nversion: 0.1.0\n", encoding="utf-8")
    (skill / "artifacts" / "part0" / "formulation-only.md").write_text(
        "hidden review", encoding="utf-8"
    )
    admitted = tmp_path / "admitted"
    admitted.mkdir()
    (admitted / "notebook.ipynb").write_text("{}", encoding="utf-8")

    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(("skill", "direction_skill", skill),),
        attachments=(("admitted", admitted, ".adaos_context/session/artifacts/00"),),
        created_at="2026-08-19T00:00:00Z",
    )
    artifact = snapshot["artifacts"][0]
    assert artifact["source_projection"]["excluded_paths"] == ["artifacts/"]

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    materialize_source_snapshot(
        state_dir=state_dir,
        reference=snapshot,
        workspace=workspace,
    )

    assert (workspace / "skills" / "direction_skill" / "skill.yaml").is_file()
    assert not (workspace / "skills" / "direction_skill" / "artifacts").exists()
    assert (
        workspace / ".adaos_context" / "session" / "artifacts" / "00" / "notebook.ipynb"
    ).is_file()


def test_projected_snapshot_activation_preserves_owner_artifacts(tmp_path: Path) -> None:
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    skill = dev_skills / "direction_skill"
    (skill / "artifacts" / "part0").mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: direction_skill\nversion: 0.1.0\n", encoding="utf-8"
    )
    (skill / "artifacts" / "part0" / "source.md").write_text(
        "owner evidence", encoding="utf-8"
    )
    (skill / "prompt_state.json").write_text("{}\n", encoding="utf-8")
    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(("skill", "direction_skill", skill),),
        created_at="2026-08-19T00:00:00Z",
    )
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    materialize_source_snapshot(
        state_dir=state_dir,
        reference=snapshot,
        workspace=workspace,
    )
    (workspace / "skills" / "direction_skill" / "skill.yaml").write_text(
        "name: direction_skill\nversion: 0.1.1\n", encoding="utf-8"
    )
    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=tmp_path / "runs",
    )

    worker._sync_artifacts(
        {
            "target": {"type": "skill", "id": "direction_skill"},
            "forge": {"source_snapshot": snapshot},
        },
        workspace,
    )

    assert "0.1.1" in (skill / "skill.yaml").read_text(encoding="utf-8")
    assert (skill / "artifacts" / "part0" / "source.md").read_text(
        encoding="utf-8"
    ) == "owner evidence"
    assert (skill / "prompt_state.json").is_file()


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
    evidence = task["result"]["evidence"]
    assert evidence["storage"] == "worker_task_envelope"
    assert {item["kind"] for item in evidence["artifacts"]} == {
        "result",
        "test_report",
        "changed_files",
        "provenance",
    }
    run_root = Path(task["result"]["local_run_dir"])
    assert (run_root / "evidence" / "provenance.json").is_file()
    tracked = subprocess.run(
        ["git", "ls-files"],
        cwd=run_root / "workspace",
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    assert not any(path.startswith(".adaos/tasks/") for path in tracked)


def test_local_worker_does_not_apply_result_after_task_cancellation(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    scenario_dir = _scenario(dev_scenarios, "cancelled_recipe")
    _core_created_skill_fixture(repo_root, dev_skills, "cancelled_recipe_skill")
    original_manifest = (scenario_dir / "scenario.yaml").read_text(encoding="utf-8")

    factory = SkillFactoryService(state_dir=state_dir)
    submitted = factory.submit_realize_request(
        {"target": {"type": "scenario", "id": "cancelled_recipe"}}
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        manifest = workspace / "scenarios" / "cancelled_recipe" / "scenario.yaml"
        manifest.write_text(manifest.read_text(encoding="utf-8") + "\n# must not be applied\n", encoding="utf-8")
        cancelled = factory.cancel_task(submitted["task"]["task_id"], reason="test cancellation", actor="test")
        assert cancelled["ok"] is True
        return CodexRunResult(returncode=0, final_message="late result")

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
    assert result["status"] == "cancelled", result
    assert (scenario_dir / "scenario.yaml").read_text(encoding="utf-8") == original_manifest
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == submitted["task"]["task_id"]
    )
    assert task["status"] == "cancelled"
    assert not task.get("result")


def test_local_worker_materializes_and_syncs_all_companion_skills(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    _scenario(dev_scenarios, "recipe_book")
    for skill_id in ("recipe_book_skill", "recipe_book_control_skill"):
        _core_created_skill_fixture(repo_root, dev_skills, skill_id)

    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {
                "implementation_brief": "Implement both declared recipe capabilities.",
                "companion_skill_id": "recipe_book_skill",
                "companion_skill_ids": ["recipe_book_skill", "recipe_book_control_skill"],
            },
            "repo": {
                "sparse_paths": [
                    "scenarios/recipe_book/",
                    "skills/recipe_book_skill/",
                    "skills/recipe_book_control_skill/",
                ]
            },
        }
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        assert "recipe_book_skill, recipe_book_control_skill" in prompt
        for skill_id in ("recipe_book_skill", "recipe_book_control_skill"):
            handler = workspace / "skills" / skill_id / "handlers" / "main.py"
            handler.write_text(
                handler.read_text(encoding="utf-8") + f"\n# realized {skill_id}\n",
                encoding="utf-8",
            )
        return CodexRunResult(returncode=0, final_message="Implemented both skills.")

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
    for skill_id in ("recipe_book_skill", "recipe_book_control_skill"):
        assert f"realized {skill_id}" in (
            dev_skills / skill_id / "handlers" / "main.py"
        ).read_text(encoding="utf-8")


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


def test_local_worker_recovers_precommit_result_without_rerunning_codex(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    scenario_root = _scenario(dev_scenarios, "recipe_book")
    skill_root = _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(("scenario", "recipe_book", scenario_root), ("skill", "recipe_book_skill", skill_root)),
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
        handler.write_text(handler.read_text(encoding="utf-8") + "\n# preserved result\n", encoding="utf-8")
        return CodexRunResult(returncode=0, final_message="Implemented once.")

    class ValidationCrashWorker(LocalSkillFactoryWorker):
        def _validate_workspace(self, assignment, workspace):  # type: ignore[no-untyped-def]
            raise RuntimeError("simulated worker validation crash")

    runs_root = tmp_path / "runs"
    failed = ValidationCrashWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=runs_root,
        executor=fake_codex,
        max_repair_attempts=0,
    ).run_once()

    assert failed["ok"] is False
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
    assert len(codex_calls) == 1
    assert "preserved result" in (skill_root / "handlers" / "main.py").read_text(encoding="utf-8")
    provenance = recovered["result"]["provenance"]
    assert provenance["recovery"]["mode"] == "pre_commit_deterministic_resume"


def test_local_worker_recovers_terminal_orphan_after_api_restart_without_rerunning_codex(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    scenario_root = _scenario(dev_scenarios, "recipe_book")
    skill_root = _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(("scenario", "recipe_book", scenario_root), ("skill", "recipe_book_skill", skill_root)),
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
    runs_root = tmp_path / "runs"
    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=runs_root,
        executor=lambda **_kwargs: (_ for _ in ()).throw(AssertionError("Codex must not rerun")),
    )
    worker.ensure_registered()
    polled = worker.factory.poll_assignment(worker.node_id)
    assert polled["assigned"] is True
    assignment = dict(polled["assignment"])
    task_id = submitted["task"]["task_id"]
    run_root = runs_root / task_id
    input_dir = run_root / "input"
    output_dir = run_root / "output"
    workspace = run_root / "workspace"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    workspace.mkdir(parents=True)
    worker._materialize_sources(assignment, workspace)
    (input_dir / "assignment.json").write_text(
        json.dumps(assignment, ensure_ascii=False),
        encoding="utf-8",
    )
    worker._build_packet(assignment, workspace, input_dir)
    worker._init_git_workspace(workspace, f"realize/{task_id}")

    handler = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
    handler.write_text(handler.read_text(encoding="utf-8") + "\n# completed after parent restart\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "codex child result"], cwd=workspace, check=True)
    (output_dir / "last_message.md").write_text("Implemented after parent restart.", encoding="utf-8")
    (output_dir / "codex-live.jsonl").write_text(
        '{"type":"item.completed"}\n{"type":"turn.completed"}\n',
        encoding="utf-8",
    )

    recovered = worker.recover_orphaned_codex_run(task_id)

    assert recovered["ok"] is True
    assert "completed after parent restart" in (skill_root / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == task_id
    )
    assert task["status"] == "completed"
    runtime_state = json.loads((run_root / "runtime" / "state.json").read_text(encoding="utf-8"))
    assert runtime_state["status"] == "completed"
    assert runtime_state["recovered"] is True


def test_local_worker_repairs_preserved_precommit_result_once(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    scenario_root = _scenario(dev_scenarios, "recipe_book")
    skill_root = _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(("scenario", "recipe_book", scenario_root), ("skill", "recipe_book_skill", skill_root)),
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
    calls = 0

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        nonlocal calls
        calls += 1
        handler = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        if calls == 1:
            handler.write_text(handler.read_text(encoding="utf-8") + "\nthis is invalid python\n", encoding="utf-8")
            return CodexRunResult(returncode=0, final_message="Initial invalid result.")
        assert "Deterministic validation repair" in prompt
        handler.write_text(
            handler.read_text(encoding="utf-8").replace("this is invalid python", "# repaired"),
            encoding="utf-8",
        )
        return CodexRunResult(returncode=0, final_message="Repaired deterministically.")

    runs_root = tmp_path / "runs"
    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=runs_root,
        executor=fake_codex,
        max_repair_attempts=0,
    )
    failed = worker.run_once()
    assert failed["ok"] is False

    repair_worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=runs_root,
        executor=fake_codex,
        max_repair_attempts=1,
    )
    recovered = repair_worker.repair_preserved_run(submitted["task"]["task_id"])

    assert recovered["ok"] is True
    assert calls == 2
    assert "# repaired" in (skill_root / "handlers" / "main.py").read_text(encoding="utf-8")


def test_orphaned_completion_resumes_bounded_deterministic_repair(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task_id = "task.orphan-repair"
    runs_root = tmp_path / "runs"
    run_root = runs_root / task_id
    input_dir = run_root / "input"
    output_dir = run_root / "output"
    input_dir.mkdir(parents=True)
    output_dir.mkdir(parents=True)
    (input_dir / "assignment.json").write_text(
        json.dumps({"task_id": task_id}),
        encoding="utf-8",
    )
    (output_dir / "last_message.md").write_text("Initial result.", encoding="utf-8")
    (output_dir / "codex-live.jsonl").write_text(
        '{"type":"turn.completed"}\n',
        encoding="utf-8",
    )
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=runs_root,
    )
    failures: list[dict] = []
    worker.factory = SimpleNamespace(fail_task=lambda value: failures.append(dict(value)))
    repairs: list[str] = []
    monkeypatch.setattr(
        worker,
        "recover_validated_run",
        lambda _value: (_ for _ in ()).throw(
            ValueError("result recovery requires a passed deterministic test report")
        ),
    )
    monkeypatch.setattr(
        worker,
        "repair_preserved_run",
        lambda value: repairs.append(value) or {"ok": True, "repaired": True},
    )

    recovered = worker.recover_orphaned_codex_run(task_id)

    assert recovered == {"ok": True, "repaired": True}
    assert repairs == [task_id]
    assert failures[0]["retryable"] is True
    assert not any(item.get("retryable") is False for item in failures)


def test_orphaned_recovery_refuses_a_live_durable_worker_owner(tmp_path: Path) -> None:
    task_id = "task.live-owner"
    runs_root = tmp_path / "runs"
    run_root = runs_root / task_id
    input_dir = run_root / "input"
    output_dir = run_root / "output"
    runtime_dir = run_root / "runtime"
    for path in (input_dir, output_dir, runtime_dir):
        path.mkdir(parents=True)
    (input_dir / "assignment.json").write_text(
        json.dumps({"task_id": task_id}),
        encoding="utf-8",
    )
    (output_dir / "last_message.md").write_text("Finished turn.", encoding="utf-8")
    (output_dir / "codex-live.jsonl").write_text(
        '{"type":"turn.completed"}\n',
        encoding="utf-8",
    )
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=runs_root,
    )
    (runtime_dir / "state.json").write_text(
        json.dumps(
            {
                "schema": "adaos.skill_factory.local_run.v1",
                "status": "in_progress",
                "owner": worker._current_process_owner(),
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="original worker process is still active"):
        worker.recover_orphaned_codex_run(task_id)


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


def test_automation_cannot_modify_current_publication_baseline(tmp_path: Path) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    assignment = {
        "target": {"type": "scenario", "id": "recipe_book"},
        "forge": {
            "sparse_paths": [
                "scenarios/recipe_book/",
                "skills/recipe_book_skill/",
            ]
        },
        "realize_request": {
            "artifacts": {"companion_skill_id": "recipe_book_skill"},
        },
    }

    with pytest.raises(ValueError, match="current Publication baseline"):
        worker._validate_changed_paths(
            assignment,
            ["scenarios/recipe_book/.builder_current_publication/webui.json"],
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


def test_codex_executor_uses_current_sdk_and_utf8_python(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("PATH", "C:/global-bin")
    repo_root = tmp_path / "adaos"
    executor = SubprocessCodexExecutor(repo_root=repo_root)

    environment = executor._execution_environment()

    assert environment["ADAOS_REPO_ROOT"] == str(repo_root.resolve())
    assert environment["PYTHONPATH"] == str(repo_root.resolve() / "src")
    assert environment["ADAOS_PYTHON"] == str(Path(sys.executable).resolve())
    assert environment["PATH"].split(os.pathsep)[0] == str(Path(sys.executable).resolve().parent)
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert "OPENAI_API_KEY" not in environment


def test_codex_executor_scopes_mutable_adaos_runtime_to_task(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", "C:/host-adaos")
    executor = SubprocessCodexExecutor(repo_root=tmp_path / "repo")
    task_runtime = tmp_path / "workspace" / ".adaos" / "tasks" / "task.example" / "adaos-runtime"

    environment = executor._execution_environment(runtime_base_dir=task_runtime)

    assert environment["ADAOS_BASE_DIR"] == str(task_runtime.resolve())
    assert environment["ADAOS_TASK_RUNTIME_DIR"] == str(task_runtime.resolve())
    assert environment["ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC"] == "1"
    assert environment["ADAOS_DISABLE_ACTIVE_SLOT_ENV_APPLY"] == "1"
    assert environment["ADAOS_BASE_DIR"] != "C:/host-adaos"


def test_codex_task_runtime_is_outside_candidate_worktree(tmp_path: Path) -> None:
    workspace = tmp_path / "run" / "workspace"
    output_dir = tmp_path / "run" / "output"

    task_runtime = SubprocessCodexExecutor._task_runtime_root(output_dir)

    assert workspace not in task_runtime.parents
    assert task_runtime.parent == output_dir.parent


def test_generated_tests_receive_task_owned_runtime_outside_candidate(tmp_path: Path) -> None:
    workspace = tmp_path / "run" / "workspace"
    tests_dir = workspace / "skills" / "candidate" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_runtime_boundary.py").write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "def test_runtime_boundary():\n"
        "    runtime = Path(os.environ['ADAOS_BASE_DIR']).resolve()\n"
        "    workspace = Path.cwd().resolve()\n"
        "    assert runtime != workspace\n"
        "    assert workspace not in runtime.parents\n"
        "    (runtime / 'validation-marker.txt').parent.mkdir(parents=True, exist_ok=True)\n"
        "    (runtime / 'validation-marker.txt').write_text('ok', encoding='utf-8')\n",
        encoding="utf-8",
    )
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    checks: list[dict] = []
    errors: list[str] = []

    worker._run_generated_tests(workspace, checks, errors)

    assert errors == []
    assert checks[0]["ok"] is True
    assert (workspace.parent / "adaos-runtime-packaged" / "validation-marker.txt").is_file()
    assert not (workspace.parent / "package-validation").exists()
    assert not (workspace / "skills" / ".runtime").exists()


def test_generated_cleanup_removes_only_reserved_runtime_projection(tmp_path: Path) -> None:
    reserved = tmp_path / "skills" / ".runtime" / "candidate" / "state.json"
    arbitrary = tmp_path / ".adaos_validation_base" / "state.json"
    reserved.parent.mkdir(parents=True)
    arbitrary.parent.mkdir(parents=True)
    reserved.write_text("{}", encoding="utf-8")
    arbitrary.write_text("{}", encoding="utf-8")

    LocalSkillFactoryWorker._cleanup_generated_files(tmp_path)

    assert not (tmp_path / "skills" / ".runtime").exists()
    assert arbitrary.is_file()


def test_worker_applies_frozen_agent_profile_to_codex_executor(monkeypatch, tmp_path: Path) -> None:
    captured: dict[str, str | None] = {}

    def fake_call(self, **_kwargs):
        captured["model"] = self.model
        captured["reasoning_effort"] = self.reasoning_effort
        return CodexRunResult(returncode=0)

    monkeypatch.setattr(SubprocessCodexExecutor, "__call__", fake_call)
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=tmp_path / "repo",
        dev_skills_root=tmp_path / "skills",
        dev_scenarios_root=tmp_path / "scenarios",
        runs_root=tmp_path / "runs",
        executor=SubprocessCodexExecutor(model="fallback"),
    )

    worker._execute_codex(
        task_id="task.profile",
        workspace=tmp_path,
        prompt="bounded task",
        output_dir=tmp_path / "output",
        agent_profile={
            "provider": "openai-codex-cli",
            "model": "gpt-5.4",
            "reasoning_effort": "high",
            "tool_profile": "adaos-local-bounded-v1",
        },
    )

    assert captured == {"model": "gpt-5.4", "reasoning_effort": "high"}


def test_worker_prompt_requires_authoritative_sdk_and_utf8_transport(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
        executor=lambda **_kwargs: CodexRunResult(returncode=0),
    )
    assignment = {
        "task_id": "task.prompt",
        "target": {"type": "skill", "id": "demo"},
        "realize_request": {
            "target": {"type": "skill", "id": "demo"},
            "artifacts": {
                "implementation_brief": "Keep Russian text intact.",
                "context_packet": {
                    "schema": "adaos.builder.context_packet.v1",
                    "digest": "sha256:" + "a" * 64,
                    "project": {"ref": "skill:demo"},
                    "change": {
                        "change_id": "change.demo",
                        "intent": "Correct the declarative workflow.",
                        "issues": [
                            {
                                "issue_id": "issue.workflow",
                                "title": "Keep the workflow definition data-driven",
                                "lane": "automation",
                                "status": "open",
                                "acceptance_criteria": ["workflow.json validates"],
                            }
                        ],
                    },
                    "facets": {
                        "execution_authority": {"status": "present"},
                        "workflow_definition": {
                            "status": "present",
                            "definition_digest": "sha256:" + "b" * 64,
                            "authoring": {
                                "status": "present",
                                "definition_path": "workflow.json",
                                "adapter_catalog": [{"implementation": "irrelevant.full.catalog"}],
                            },
                        },
                    },
                    "coverage": {"ready": True},
                },
            },
        },
        "forge": {"sparse_paths": ["skills/demo/"]},
    }
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    (workspace / "skills" / "demo").mkdir(parents=True)

    worker._build_packet(assignment, workspace, input_dir)
    prompt = (input_dir / "task.md").read_text(encoding="utf-8")
    packet = json.loads((input_dir / "packet.json").read_text(encoding="utf-8"))

    assert "ADAOS_PYTHON" in prompt
    assert "authoritative SDK" in prompt
    assert "PowerShell string pipeline" in prompt
    assert "every textual `Get-Content`" in prompt
    assert "`-Encoding UTF8`" in prompt
    assert "UTF-8" in prompt
    assert "Governed Change context" in prompt
    assert "change.demo" in prompt
    assert "workflow.json validates" in prompt
    assert "complete TransitionDescriptor contract" in prompt
    assert "fabricated metrics" in prompt
    assert "Resolve skill-owned runtime storage through AdaOS SDK" in prompt
    assert "does not permit omitting the executable scientific path" in prompt
    assert "60-second lifecycle budget" in prompt
    assert "Do not execute a scientific smoke or confirmatory workload from packaged tests" in prompt
    assert "exact declared name" in prompt
    assert "skill_schema.json" in prompt
    assert "allow_heavy_dependencies" in prompt
    assert "install-strict" in prompt
    assert "trusted worker finalizer owns package" in prompt
    assert "ADAOS_TASK_RUNTIME_DIR" in prompt
    assert "never create repository-relative `.adaos*` runtime directories" in prompt
    assert "do not copy into or mutate the canonical workspace/runtime" in prompt
    assert "workflow.json" in prompt
    assert "irrelevant.full.catalog" not in prompt
    assert packet["context_packet_digest"] == "sha256:" + "a" * 64
    assert packet["context_packet"]["change"]["change_id"] == "change.demo"
    assert packet["context_packet"]["facets"]["workflow_definition"]["authoring"]["adapter_catalog"]


def test_worker_compiles_manifest_bound_workflow_definition(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    skill = _core_created_skill_fixture(repo_root, workspace / "skills", "demo")
    manifest_path = skill / "skill.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["workflow"] = {"manifest": "workflow.json"}
    manifest_path.write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")
    workflow_path = skill / "workflow.json"
    workflow_path.write_text("{}\n", encoding="utf-8")
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=workspace / "skills",
        dev_scenarios_root=workspace / "scenarios",
        runs_root=tmp_path / "runs",
    )
    assignment = {
        "target": {"type": "skill", "id": "demo"},
        "forge": {"sparse_paths": ["skills/demo/"]},
    }

    invalid = worker._validate_workspace(assignment, workspace)

    assert invalid["ok"] is False
    assert any("workflow definition" in error for error in invalid["errors"])

    workflow_path.write_text(
        (repo_root / "src" / "adaos" / "services" / "builder" / "builder_change.workflow.json").read_text(
            encoding="utf-8"
        ),
        encoding="utf-8",
    )

    valid = worker._validate_workspace(assignment, workspace)

    assert valid["ok"] is True, valid["errors"]
    workflow_check = next(
        item for item in valid["checks"] if item.get("kind") == "workflow.definition.v1"
    )
    assert workflow_check["path"] == "skills/demo/workflow.json"
    assert workflow_check["definition_digest"].startswith("sha256:")


def test_worker_treats_browser_data_route_warnings_as_strict_errors(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_root = workspace / "skills" / "demo"
    skill_root.mkdir(parents=True)
    (skill_root / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "tools": [{"name": "list_items", "input_schema": {"type": "object"}}],
                "data_routes": [
                    {
                        "surface": "widget:items",
                        "route": "tool/details",
                        "tool": "list_items",
                        "first_paint": "empty list",
                        "recovery": "retry",
                        "guard_visibility": "show unavailable",
                    }
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_skill_data_routes(workspace, checks, errors)

    assert any("data_routes.budget_missing" in error for error in errors)
    assert any("data_routes.read_policy_missing" in error for error in errors)
    assert checks == []


def test_worker_rejects_skill_manifest_that_runtime_dependency_policy_will_refuse(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_root = workspace / "skills" / "demo"
    skill_root.mkdir(parents=True)
    (skill_root / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "dependencies": ["torch>=2.2.0"],
                "tools": [],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_skill_dependency_isolation(workspace, checks, errors)

    assert any("runtime.dependencies.heavy_isolation" in error for error in errors)
    assert checks == []


def test_worker_enforces_structured_brief_provider_contract(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_root = workspace / "skills" / "demo"
    skill_root.mkdir(parents=True)
    (skill_root / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "demo",
                "provider_contracts": [
                    {
                        "contract": "example.runner.v1",
                        "capability": "example.runner",
                        "operations": ["prepare_attempt"],
                    }
                ],
                "tools": [{"name": "prepare_attempt", "input_schema": {"type": "object"}}],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    assignment = {
        "realize_request": {
            "artifacts": {
                "implementation_brief": json.dumps(
                    {
                        "contract_requirements": [
                            {
                                "id": "runner",
                                "role": "provider",
                                "contract": "example.runner.v1",
                                "capability": "example.runner",
                                "operations": ["prepare_attempt", "collect_attempt"],
                            }
                        ]
                    }
                )
            }
        }
    }
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_brief_contract_requirements(assignment, workspace, checks, errors)

    assert errors == ["implementation brief provider requirement runner is missing operations: collect_attempt"]
    assert checks == []


def _document_contract_assignment(workspace: Path) -> dict:
    instruction = workspace / ".adaos_context" / "dev-1" / "instructions" / "contract.json"
    instruction.parent.mkdir(parents=True)
    instruction.write_text(
        json.dumps(
            {
                "schema": "adaos.contract.operation_set.v1",
                "contract": "example.runner.v1",
                "conformance_fixtures": [
                    {
                        "id": "bounded-output",
                        "kind": "document_set",
                        "required": True,
                        "required_documents": ["run_log.json", "index.json"],
                        "documents": {
                            "run_log.json": {
                                "type": "object",
                                "required": ["network"],
                                "properties": {
                                    "network": {
                                        "type": "object",
                                        "required": ["mode", "accessed"],
                                        "properties": {
                                            "mode": {"const": "offline"},
                                            "accessed": {"const": False},
                                        },
                                        "additionalProperties": False,
                                    }
                                },
                                "additionalProperties": False,
                            },
                            "index.json": {
                                "type": "object",
                                "required": ["files"],
                                "properties": {"files": {"type": "array"}},
                                "additionalProperties": False,
                            },
                        },
                    }
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {
        "realize_request": {
            "artifacts": {
                "development_context": {
                    "instruction_inputs": [
                        {
                            "kind": "consumer_contract",
                            "media_type": "application/json",
                            "path": instruction.relative_to(workspace).as_posix(),
                        }
                    ]
                }
            }
        }
    }


def test_worker_validates_admitted_contract_runtime_documents(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _document_contract_assignment(workspace)
    attempt = tmp_path / "runtime" / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "run_log.json").write_text(
        json.dumps(
            {
                "network": {
                    "mode": "offline",
                    "accessed": False,
                    "blocked_calls": [],
                }
            }
        ),
        encoding="utf-8",
    )
    (attempt / "index.json").write_text('{"files": []}\n', encoding="utf-8")
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_contract_documents(
        assignment,
        workspace,
        runtime_dir=tmp_path / "runtime",
        checks=checks,
        errors=errors,
    )

    assert checks == []
    assert len(errors) == 1
    assert "run_log.json at /network" in errors[0]
    assert "blocked_calls" in errors[0]


def test_worker_selects_newest_complete_contract_document_set(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _document_contract_assignment(workspace)
    runtime = tmp_path / "runtime"
    old = runtime / "attempt-old"
    old.mkdir(parents=True)
    (old / "run_log.json").write_text(
        '{"network": {"mode": "offline", "accessed": false, "blocked_calls": []}}\n',
        encoding="utf-8",
    )
    (old / "index.json").write_text('{"files": []}\n', encoding="utf-8")
    valid = runtime / "attempt-repair"
    valid.mkdir(parents=True)
    (valid / "run_log.json").write_text(
        '{"network": {"mode": "offline", "accessed": false}}\n',
        encoding="utf-8",
    )
    (valid / "index.json").write_text('{"files": []}\n', encoding="utf-8")
    newer = max(path.stat().st_mtime_ns for path in old.iterdir()) + 10_000_000
    for path in valid.iterdir():
        os.utime(path, ns=(newer, newer))
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_contract_documents(
        assignment,
        workspace,
        runtime_dir=runtime,
        checks=checks,
        errors=errors,
    )

    assert errors == []
    assert checks == [
        {
            "kind": "admitted_contract.document_set",
            "contract": "example.runner.v1",
            "fixture_id": "bounded-output",
            "runtime_path": "attempt-repair",
            "documents": ["run_log.json", "index.json"],
            "ok": True,
        }
    ]


def test_worker_requires_admitted_contract_document_set(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _document_contract_assignment(workspace)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_contract_documents(
        assignment,
        workspace,
        runtime_dir=runtime,
        checks=checks,
        errors=errors,
    )

    assert checks == []
    assert errors == [
        "admitted contract fixture example.runner.v1:bounded-output produced no complete "
        "runtime document set; required: run_log.json, index.json"
    ]


def test_worker_rejects_tests_that_pin_checkpoint_owned_versions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    tests_dir = workspace / "skills" / "demo" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_manifest.py").write_text(
        "def test_version(manifest):\n"
        "    assert manifest['version'] == '0.2.3'\n"
        "    assert manifest.get('updated_at') != '2026-01-01T00:00:00Z'\n",
        encoding="utf-8",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_tests_do_not_pin_checkpoint_metadata(workspace, checks, errors)

    assert any("manifest version" in error for error in errors)
    assert any("manifest updated_at" in error for error in errors)
    assert checks == []


def test_worker_allows_semantic_manifest_version_checks(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    tests_dir = workspace / "scenarios" / "demo" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_manifest.py").write_text(
        "import re\n\n"
        "def test_version(manifest):\n"
        "    assert re.fullmatch(r'\\d+\\.\\d+\\.\\d+', manifest['version'])\n"
        "    assert manifest.get('updated_at')\n",
        encoding="utf-8",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_tests_do_not_pin_checkpoint_metadata(workspace, checks, errors)

    assert errors == []
    assert checks == [
        {
            "kind": "checkpoint_test_contract",
            "path": "scenarios/demo/tests/test_manifest.py",
            "ok": True,
        }
    ]


def test_worker_rejects_package_tests_bound_to_development_context(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    test_path = workspace / "skills" / "demo" / "tests" / "test_context.py"
    test_path.parent.mkdir(parents=True)
    test_path.write_text(
        "from pathlib import Path\n\n"
        "def test_fixture():\n"
        "    assert (Path.cwd() / '.adaos_context' / 'devcal-001' / 'fixture.json').exists()\n",
        encoding="utf-8",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_tests_do_not_depend_on_development_context(
        workspace,
        checks,
        errors,
        changed_paths={"skills/demo/tests/test_context.py"},
    )

    assert checks == []
    assert len(errors) == 1
    assert "authoring-only" not in errors[0]
    assert ".adaos_context" in errors[0]


def test_worker_runs_generated_tests_from_package_shaped_projection(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "run" / "workspace"
    tests_dir = workspace / "skills" / "demo" / "tests"
    tests_dir.mkdir(parents=True)
    (workspace / ".adaos_context" / "session").mkdir(parents=True)
    (workspace / ".adaos_context" / "session" / "fixture.txt").write_text(
        "authoring-only",
        encoding="utf-8",
    )
    (tests_dir / "test_context.py").write_text(
        "from pathlib import Path\n\n"
        "def test_fixture():\n"
        "    assert (Path.cwd() / '.adaos_context' / 'session' / 'fixture.txt').exists()\n",
        encoding="utf-8",
    )
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    checks: list[dict] = []
    errors: list[str] = []

    worker._run_generated_tests(workspace, checks, errors)

    assert checks[0]["kind"] == "pytest.packaged"
    assert checks[0]["ok"] is False
    assert any("packaged pytest failed" in error for error in errors)


def test_worker_ignores_unchanged_baseline_version_pins(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    baseline_tests = workspace / "skills" / "dependency" / "tests"
    changed_tests = workspace / "skills" / "target" / "tests"
    baseline_tests.mkdir(parents=True)
    changed_tests.mkdir(parents=True)
    (baseline_tests / "test_manifest.py").write_text(
        "def test_version(manifest):\n"
        "    assert manifest['version'] == '0.1.0'\n",
        encoding="utf-8",
    )
    (changed_tests / "test_manifest.py").write_text(
        "import re\n\n"
        "def test_version(manifest):\n"
        "    assert re.fullmatch(r'\\d+\\.\\d+\\.\\d+', manifest['version'])\n",
        encoding="utf-8",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_tests_do_not_pin_checkpoint_metadata(
        workspace,
        checks,
        errors,
        changed_paths={"skills/target/tests/test_manifest.py"},
    )

    assert errors == []
    assert checks == [
        {
            "kind": "checkpoint_test_contract",
            "path": "skills/target/tests/test_manifest.py",
            "ok": True,
        }
    ]


def test_worker_changed_paths_supports_single_commit_dirty_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=workspace, check=True)
    tracked = workspace / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=workspace, check=True, capture_output=True)
    tracked.write_text("changed\n", encoding="utf-8")
    (workspace / "new.txt").write_text("new\n", encoding="utf-8")

    worker = object.__new__(LocalSkillFactoryWorker)

    assert worker._changed_from_baseline(workspace) == ["tracked.txt", "new.txt"]


def test_worker_changed_paths_differs_from_root_after_commit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=workspace, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.invalid"], cwd=workspace, check=True)
    tracked = workspace / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "baseline"], cwd=workspace, check=True, capture_output=True)
    tracked.write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(["git", "commit", "-m", "result"], cwd=workspace, check=True, capture_output=True)

    worker = object.__new__(LocalSkillFactoryWorker)

    assert worker._changed_from_baseline(workspace) == ["tracked.txt"]


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


def test_worker_reasks_codex_to_repair_source_boundary_violation(tmp_path: Path) -> None:
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

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        calls.append(prompt)
        runtime_file = workspace / ".adaos_validation_base" / "state" / "adaos.db"
        if len(calls) == 1:
            runtime_file.parent.mkdir(parents=True)
            runtime_file.write_text("ephemeral", encoding="utf-8")
        else:
            runtime_file.unlink()
            runtime_file.parent.rmdir()
            runtime_file.parent.parent.rmdir()
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
    assert "outside the task scope" in calls[1]


def test_worker_isolates_generated_test_side_effects_from_candidate_source(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    _core_created_skill_fixture(repo_root, dev_skills, "boundary_skill")
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "skill", "id": "boundary_skill"},
            "repo": {"sparse_paths": ["skills/boundary_skill/"]},
        }
    )
    calls: list[str] = []

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        calls.append(prompt)
        test_file = workspace / "skills" / "boundary_skill" / "tests" / "test_side_effect.py"
        test_file.parent.mkdir(parents=True, exist_ok=True)
        escaped = workspace / "escaped-validation.txt"
        if len(calls) == 1:
            test_file.write_text(
                "from pathlib import Path\n\n"
                "def test_side_effect():\n"
                "    (Path.cwd() / 'escaped-validation.txt').write_text('runtime', encoding='utf-8')\n",
                encoding="utf-8",
            )
        else:
            test_file.write_text("def test_side_effect():\n    assert True\n", encoding="utf-8")
            escaped.unlink(missing_ok=True)
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
    assert len(calls) == 1
    assert not (Path(result["result"]["local_run_dir"]) / "workspace" / "escaped-validation.txt").exists()
    assert not (Path(result["result"]["local_run_dir"]) / "package-validation").exists()


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
