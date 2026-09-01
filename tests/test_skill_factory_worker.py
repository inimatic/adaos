from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import yaml

from adaos.services import skill_factory_worker as worker_module
from adaos.services.root.service import _rewrite_skill_template_identity
from adaos.services.skill_factory import SkillFactoryService
from adaos.services.skill_factory_sources import (
    SourceSnapshotError,
    capture_source_snapshot,
    materialize_source_snapshot,
    verify_source_snapshot,
)
from adaos.services.skill_factory_worker import (
    CodexRunResult,
    LocalSkillFactoryWorker,
    SubprocessCodexExecutor,
    _codex_failure_detail,
    _codex_budget_observed_tokens,
    _codex_jsonl_usage,
    _codex_jsonl_live_budget_estimate,
    _codex_prompt_budget_check,
    _context_packet_prompt_projection,
    _root_mcp_profile_from_assignment,
)


def test_codex_jsonl_usage_accepts_reasoning_output_tokens(tmp_path: Path) -> None:
    journal = tmp_path / "codex.jsonl"
    journal.write_text(
        json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 100,
                    "cached_input_tokens": 80,
                    "output_tokens": 20,
                    "reasoning_output_tokens": 7,
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )

    assert _codex_jsonl_usage(journal) == {
        "input_tokens": 100,
        "cached_input_tokens": 80,
        "output_tokens": 20,
        "reasoning_tokens": 7,
        "model_tokens": 120,
    }


def test_codex_failure_detail_prefers_structured_jsonl_errors() -> None:
    result = CodexRunResult(
        returncode=1,
        events=(
            '{"type":"error","message":"unsupported model"}\n'
            '{"type":"turn.failed","error":{"message":"request rejected"}}\n'
        ),
        stderr="provider warning only",
    )

    assert _codex_failure_detail(result) == (
        "unsupported model | request rejected | provider warning only"
    )


def test_contract_execution_checklist_surfaces_every_exact_sequence_assertion(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    contract_path = workspace / ".adaos_context" / "session" / "runner.json"
    contract_path.parent.mkdir(parents=True)
    contract_path.write_text(
        json.dumps(
            {
                "schema": "adaos.contract.operation_set.v1",
                "contract": "example.runner.v1",
                "capability": "example.runner",
                "candidate_role": "provider",
                "operations": {
                    "collect_attempt": {
                        "input_schema": {
                            "type": "object",
                            "required": ["output_ref"],
                            "properties": {
                                "output_ref": {
                                    "type": "string",
                                    "pattern": "^content://",
                                }
                            },
                            "additionalProperties": False,
                        },
                        "output_schema": {
                            "type": "object",
                            "required": ["result", "observations"],
                            "properties": {
                                "result": {
                                    "type": "object",
                                    "required": ["evidence_class"],
                                },
                                "observations": {"type": "array"},
                            },
                            "additionalProperties": False,
                        },
                        "invariants": ["observation repeats the result metric"],
                    }
                },
                "conformance_fixtures": [
                    {
                        "id": "evidence_documents",
                        "kind": "document_set",
                        "required": True,
                        "required_documents": ["run_log.json"],
                        "documents": {
                            "run_log.json": {
                                "type": "object",
                                "required": ["status"],
                                "properties": {"status": {"const": "complete"}},
                            }
                        },
                    },
                    {
                        "id": "production_sequence",
                        "kind": "operation_sequence",
                        "required": True,
                        "steps": [
                            {
                                "id": "collect",
                                "operation": "collect_attempt",
                                "input": {"output_ref": "opaque"},
                                "assert": [
                                    {
                                        "pointer": "/observations",
                                        "contains": [
                                            {
                                                "pointer": "/evidence_role",
                                                "equals_root_pointer": "/result/evidence_class",
                                            }
                                        ],
                                    }
                                ],
                            }
                        ],
                    }
                ],
                "lifecycle": {"execution": "candidate"},
                "workflow_smoke_evidence": {
                    "required_expected_outputs": ["run_log.json"]
                },
                "domain_conformance": {
                    "initial_equivalence": {
                        "required": True,
                        "tolerance": 1e-6,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    development = {
        "instruction_inputs": [
            {
                "path": ".adaos_context/session/runner.json",
                "media_type": "application/json",
                "content_digest": "sha256:" + "a" * 64,
            }
        ]
    }

    checklist = worker_module._contract_execution_checklist(development, workspace)

    assert checklist["schema"] == "adaos.builder.contract_execution_checklist.v2"
    assert checklist["digest"].startswith("sha256:")
    projected = checklist["contracts"][0]
    assert projected["authoritative_path"] == ".adaos_context/session/runner.json"
    assert projected["required_provider_declaration"] == {
        "contract": "example.runner.v1",
        "capability": "example.runner",
    }
    assert projected["operations"][0]["input_schema"]["properties"][
        "output_ref"
    ]["pattern"] == "^content://"
    assert projected["operations"][0]["output_schema"]["properties"][
        "result"
    ]["required"] == ["evidence_class"]
    assert projected["operations"][0]["input_required"] == ["output_ref"]
    assert projected["operations"][0]["output_required"] == [
        "result",
        "observations",
    ]
    assertion = projected["operation_sequences"][0]["steps"][0]["assert"][0]
    assert assertion["contains"][0] == {
        "pointer": "/evidence_role",
        "equals_root_pointer": "/result/evidence_class",
    }
    assert projected["operation_sequences"][0]["steps"][0]["input"] == {
        "output_ref": "opaque"
    }
    assert projected["conformance_fixtures"][0]["documents"]["run_log.json"][
        "properties"
    ]["status"] == {"const": "complete"}
    assert projected["lifecycle"] == {"execution": "candidate"}
    assert projected["workflow_smoke_evidence"]["required_expected_outputs"] == [
        "run_log.json"
    ]
    assert projected["domain_conformance"]["initial_equivalence"] == {
        "required": True,
        "tolerance": 1e-6,
    }
    assert "opaque" in json.dumps(checklist)


def test_source_snapshot_keeps_reserved_artifacts_out_of_codex_workspace(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    skill = tmp_path / "direction_skill"
    (skill / "artifacts" / "part0").mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        "name: direction_skill\nversion: 0.1.0\n", encoding="utf-8"
    )
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
    assert snapshot["archive"]["format"] == "zip"
    snapshot_root = (
        state_dir / "skill_factory" / "source_snapshots" / snapshot["snapshot_id"]
    )
    assert (snapshot_root / "payload.zip").is_file()
    assert not (snapshot_root / "skills").exists()

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


def test_source_snapshot_archive_fails_closed_after_byte_mutation(
    tmp_path: Path,
) -> None:
    state_dir = tmp_path / "state"
    skill = tmp_path / "direction_skill"
    skill.mkdir()
    (skill / "skill.yaml").write_text(
        "name: direction_skill\nversion: 0.1.0\n", encoding="utf-8"
    )
    snapshot = capture_source_snapshot(
        state_dir=state_dir,
        artifacts=(("skill", "direction_skill", skill),),
        created_at="2026-08-21T00:00:00Z",
    )
    archive = (
        state_dir
        / "skill_factory"
        / "source_snapshots"
        / snapshot["snapshot_id"]
        / "payload.zip"
    )
    archive.write_bytes(archive.read_bytes() + b"corrupt")

    with pytest.raises(SourceSnapshotError, match="archive digest mismatch"):
        verify_source_snapshot(state_dir=state_dir, reference=snapshot)


def test_projected_snapshot_activation_preserves_owner_artifacts(
    tmp_path: Path,
) -> None:
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
        json.dumps(
            {
                "draft_id": "draft.recipe",
                "source": {"utterance": "Create a recipe book"},
            }
        ),
        encoding="utf-8",
    )
    return target


def _core_created_skill_fixture(repo_root: Path, root: Path, skill_id: str) -> Path:
    target = root / skill_id
    shutil.copytree(
        repo_root / "src" / "adaos" / "skills_templates" / "skill_default", target
    )
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
            "source": {
                "type": "prompt_ide",
                "text": "Implement the approved recipe book prototype.",
            },
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
        skill_handler = (
            workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        )
        skill_handler.write_text(
            skill_handler.read_text(encoding="utf-8") + "\n# realized by test\n",
            encoding="utf-8",
        )
        scenario_path = workspace / "scenarios" / "recipe_book" / "scenario.yaml"
        scenario = yaml.safe_load(scenario_path.read_text(encoding="utf-8"))
        scenario["depends"] = ["recipe_book_skill"]
        scenario_path.write_text(
            yaml.safe_dump(scenario, sort_keys=False), encoding="utf-8"
        )
        return CodexRunResult(
            returncode=0,
            events='{"type":"done"}\n',
            final_message="Implemented recipe skill.",
        )

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
    assert result["assignment"]["realize_request"]["artifacts"][
        "implementation_brief"
    ].startswith("Recipes")
    assert (dev_skills / "recipe_book_skill" / "skill.yaml").exists()
    assert "realized by test" in (
        dev_skills / "recipe_book_skill" / "handlers" / "main.py"
    ).read_text(encoding="utf-8")
    scenario = yaml.safe_load(
        (dev_scenarios / "recipe_book" / "scenario.yaml").read_text(encoding="utf-8")
    )
    assert scenario["depends"] == ["recipe_book_skill"]
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == submitted["task"]["task_id"]
    )
    assert task["status"] == "completed"
    assert task["result"]["commit_hash"]
    assert task["result"]["provenance"]["runner_version"].startswith(
        "adaos-local-codex-worker/"
    )
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


def test_local_worker_does_not_apply_result_after_task_cancellation(
    tmp_path: Path,
) -> None:
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
        manifest.write_text(
            manifest.read_text(encoding="utf-8") + "\n# must not be applied\n",
            encoding="utf-8",
        )
        cancelled = factory.cancel_task(
            submitted["task"]["task_id"], reason="test cancellation", actor="test"
        )
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
    assert (scenario_dir / "scenario.yaml").read_text(
        encoding="utf-8"
    ) == original_manifest
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == submitted["task"]["task_id"]
    )
    assert task["status"] == "cancelled"
    assert not task.get("result")


def test_local_worker_materializes_and_syncs_all_companion_skills(
    tmp_path: Path,
) -> None:
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
                "companion_skill_ids": [
                    "recipe_book_skill",
                    "recipe_book_control_skill",
                ],
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


def test_worker_rejects_codex_changes_to_checkpoint_owned_manifest_metadata(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    scenario = _scenario(workspace / "scenarios", "recipe_book")
    skill = _core_created_skill_fixture(
        repo_root, workspace / "skills", "recipe_book_skill"
    )
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    worker._init_git_workspace(workspace, "test/checkpoint-metadata")

    scenario_manifest = yaml.safe_load(
        (scenario / "scenario.yaml").read_text(encoding="utf-8")
    )
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
            "repo": {
                "sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]
            },
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


def test_local_worker_does_not_overwrite_dev_that_changed_after_task_snapshot(
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
        handler.write_text(
            handler.read_text(encoding="utf-8") + "\n# task result\n", encoding="utf-8"
        )
        return CodexRunResult(
            returncode=0, final_message="Implemented from exact base."
        )

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
    assert "task result" not in (skill_root / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )
    task_workspace = Path(result["run_dir"]) / "workspace"
    assert "task result" in (
        task_workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
    ).read_text(encoding="utf-8")


def test_local_worker_recovers_committed_validated_result_without_rerunning_codex(
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
        handler.write_text(
            handler.read_text(encoding="utf-8") + "\n# recovered task result\n",
            encoding="utf-8",
        )
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
        executor=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Codex must not rerun")
        ),
    )

    recovered = recovery_worker.recover_validated_run(submitted["task"]["task_id"])

    assert recovered["ok"] is True
    assert codex_calls and len(codex_calls) == 1
    assert "recovered task result" in (skill_root / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == submitted["task"]["task_id"]
    )
    assert task["status"] == "completed"
    assert task["attempts"] == 1
    assert task["result_recovery_history"][-1]["failure_id"]


def test_local_worker_recovers_precommit_result_without_rerunning_codex(
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
        handler.write_text(
            handler.read_text(encoding="utf-8") + "\n# preserved result\n",
            encoding="utf-8",
        )
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
        executor=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Codex must not rerun")
        ),
    )

    recovered = recovery_worker.recover_validated_run(submitted["task"]["task_id"])

    assert recovered["ok"] is True
    assert len(codex_calls) == 1
    assert "preserved result" in (skill_root / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )
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
    runs_root = tmp_path / "runs"
    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=dev_scenarios,
        runs_root=runs_root,
        executor=lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Codex must not rerun")
        ),
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
    handler.write_text(
        handler.read_text(encoding="utf-8") + "\n# completed after parent restart\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "codex child result"], cwd=workspace, check=True
    )
    (output_dir / "last_message.md").write_text(
        "Implemented after parent restart.", encoding="utf-8"
    )
    (output_dir / "codex-live.jsonl").write_text(
        '{"type":"item.completed"}\n{"type":"turn.completed"}\n',
        encoding="utf-8",
    )

    recovered = worker.recover_orphaned_codex_run(task_id)

    assert recovered["ok"] is True
    assert "completed after parent restart" in (
        skill_root / "handlers" / "main.py"
    ).read_text(encoding="utf-8")
    task = next(
        item
        for item in factory.snapshot(include_tasks=True)["tasks"]
        if item["task_id"] == task_id
    )
    assert task["status"] == "completed"
    runtime_state = json.loads(
        (run_root / "runtime" / "state.json").read_text(encoding="utf-8")
    )
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
    calls = 0

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        nonlocal calls
        calls += 1
        handler = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        if calls == 1:
            handler.write_text(
                handler.read_text(encoding="utf-8") + "\nthis is invalid python\n",
                encoding="utf-8",
            )
            return CodexRunResult(returncode=0, final_message="Initial invalid result.")
        assert "Deterministic validation repair" in prompt
        handler.write_text(
            handler.read_text(encoding="utf-8").replace(
                "this is invalid python", "# repaired"
            ),
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
    assert "# repaired" in (skill_root / "handlers" / "main.py").read_text(
        encoding="utf-8"
    )


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
    worker.factory = SimpleNamespace(
        fail_task=lambda value: failures.append(dict(value))
    )
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


def test_return_to_prototype_uses_snapshot_but_cannot_modify_automation_skill(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    dev_scenarios = tmp_path / "dev" / "scenarios"
    dev_skills.mkdir(parents=True)
    _scenario(dev_scenarios, "recipe_book")
    _core_created_skill_fixture(repo_root, dev_skills, "recipe_book_skill")
    snapshot = (
        state_dir
        / "builder"
        / "workflow_snapshots"
        / "scenario"
        / "recipe_book"
        / "automation"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {}}}),
        encoding="utf-8",
    )
    (snapshot / "snapshot.json").write_text(
        json.dumps({"task_id": "task.previous"}), encoding="utf-8"
    )
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {
                "companion_skill_id": "recipe_book_skill",
                "workflow_transition": "return_to_prototype",
            },
            "repo": {
                "sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]
            },
        }
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        assert "returns the completed Automation result to Prototype" in prompt
        assert (
            workspace
            / "scenarios"
            / "recipe_book"
            / ".builder_previous_automation"
            / "webui.json"
        ).is_file()
        skill = workspace / "skills" / "recipe_book_skill" / "handlers" / "main.py"
        skill.write_text(
            skill.read_text(encoding="utf-8") + "\n# forbidden change\n",
            encoding="utf-8",
        )
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
    assert "forbidden change" not in (
        dev_skills / "recipe_book_skill" / "handlers" / "main.py"
    ).read_text(encoding="utf-8")


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


def test_surgical_repair_enforces_exact_files_and_file_count(tmp_path: Path) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    assignment = {
        "forge": {"sparse_paths": ["skills/demo_metrics_skill/"]},
        "constraints": {
            "repair_profile": "surgical_ui",
            "exact_changed_paths": [
                "skills/demo_metrics_skill/webui.json",
                "skills/demo_metrics_skill/tests/test_resource_workbench.py",
            ],
            "max_changed_files": 2,
        },
        "realize_request": {"artifacts": {}},
    }

    worker._validate_changed_paths(
        assignment,
        ["skills/demo_metrics_skill/webui.json"],
    )
    with pytest.raises(ValueError, match="outside the exact repair files"):
        worker._validate_changed_paths(
            assignment,
            ["skills/demo_metrics_skill/handlers/main.py"],
        )
    with pytest.raises(ValueError, match="more files"):
        worker._validate_changed_paths(
            assignment,
            [
                "skills/demo_metrics_skill/webui.json",
                "skills/demo_metrics_skill/tests/test_resource_workbench.py",
                "skills/demo_metrics_skill/README.md",
            ],
        )


@pytest.mark.parametrize(
    "repair_profile",
    ["surgical_ui", "resource_crud", "subnet_data_integration", "project_batch"],
)
def test_bounded_repair_prompt_requires_targeted_reads(
    tmp_path: Path,
    repair_profile: str,
) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
    )
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    (workspace / "skills" / "demo").mkdir(parents=True)
    assignment = {
        "task_id": "task.surgical-prompt",
        "target": {"type": "skill", "id": "demo"},
        "forge": {"sparse_paths": ["skills/demo/"]},
        "constraints": {
            "mode": "dev_ticket_repair",
            "repair_profile": repair_profile,
            "minimal_diff": True,
        },
        "mcp": {
            "root_mcp": {
                "url": "https://ru.api.inimatic.com/v1/root/mcp",
                "required": True,
                "bound_target_id": "hub:sn_demo",
                "enabled_tools": ["get_status"],
            }
        },
        "realize_request": {
            "artifacts": {
                "implementation_brief": "adaos.dev_ticket.autonomous_repair_brief.v1",
                "repair_hints": {
                    "target_files": ["skills/demo/webui.json"],
                    "target_refs": ["summary.buttons"],
                    "acceptance_checks": ["focused UI assertion passes"],
                },
            }
        },
    }

    worker._build_packet(assignment, workspace, input_dir)
    prompt = (input_dir / "task.md").read_text(encoding="utf-8")

    assert "not Codex skill authoring" in prompt
    assert "Do not load generic skill-creator instructions" in prompt
    assert "`rg -n --max-count 12`" in prompt
    assert "at most 120 lines and 8192 bytes" in prompt
    assert "Never use `rg -A`, `rg -B`, or `rg -C`" in prompt
    assert "at most 400 source lines before the first edit" in prompt
    assert "Do not run tests or validation commands in the Codex turn" in prompt
    assert "Task-scoped Root MCP route" in prompt
    assert "hub:sn_demo" in prompt
    assert "Never substitute a skill, scenario, project, or component ID" in prompt
    expected_title = (
        "AdaOS bounded surgical UI repair"
        if repair_profile == "surgical_ui"
        else "AdaOS bounded Dev Ticket repair"
    )
    assert expected_title in prompt


def test_bounded_repair_prompt_omits_completed_builder_history(tmp_path: Path) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
    )
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    (workspace / "skills" / "demo").mkdir(parents=True)
    brief = {
        "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
        "ticket_id": "dticket.demo",
        "repair_id": "repair.current",
        "summary": "Expose note CRUD in semantic views.",
        "target": {"object_type": "skill", "object_id": "demo"},
        "evidence_refs": [
            {
                "type": "builder_automation",
                "id": "automation.previous",
                "status": "completed",
                "huge_history": "x" * 50_000,
            },
            {
                "type": "screenshot",
                "id": "artifact.failed",
                "status": "failed_acceptance",
            },
            {
                "type": "runtime_guard",
                "id": "semantic_authority_mismatch",
                "status": "failed",
            },
        ],
    }
    assignment = {
        "task_id": "task.compact-repair",
        "target": {"type": "skill", "id": "demo"},
        "forge": {"sparse_paths": ["skills/demo/"]},
        "constraints": {
            "mode": "dev_ticket_repair",
            "repair_profile": "resource_crud",
            "minimal_diff": True,
        },
        "realize_request": {
            "artifacts": {
                "implementation_brief": json.dumps(brief),
                "repair_hints": {"target_files": ["skills/demo/webui.json"]},
            }
        },
    }

    worker._build_packet(assignment, workspace, input_dir)
    prompt = (input_dir / "task.md").read_text(encoding="utf-8")
    packet = json.loads((input_dir / "packet.json").read_text(encoding="utf-8"))

    assert "automation.previous" not in prompt
    assert "huge_history" not in prompt
    assert "artifact.failed" in prompt
    assert "semantic_authority_mismatch" in prompt
    assert len(prompt.encode("utf-8")) < 12_000
    assert "automation.previous" in packet["brief"]


def test_bounded_repair_prompt_includes_only_qualified_json_target_slices(
    tmp_path: Path,
) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
    )
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    skill = workspace / "skills" / "demo"
    skill.mkdir(parents=True)
    (skill / "webui.json").write_text(
        json.dumps(
            {
                "registry": {
                    "modals": {
                        "metrics": {
                            "schema": {
                                "semantic": {
                                    "views": [
                                        {"id": "grid", "kind": "collection_grid", "title": "Metrics"},
                                        {"id": "chart", "kind": "metric_chart", "secret": "not selected"},
                                    ]
                                }
                            }
                        }
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    (skill / "handlers").mkdir()
    (skill / "handlers" / "main.py").write_text(
        'GRID_ID = "grid"\n',
        encoding="utf-8",
    )
    (skill / "tests").mkdir()
    (skill / "tests" / "test_webui.py").write_text(
        'def test_grid_id():\n    assert "grid" == "grid"\n',
        encoding="utf-8",
    )
    target_ref = "registry.modals.metrics.schema.semantic.views[id=grid]"
    moved_target_ref = "semantic.workspaces[0].widgets[id=grid]"
    assignment = {
        "task_id": "task.qualified-context",
        "target": {"type": "skill", "id": "demo"},
        "forge": {"sparse_paths": ["skills/demo/"]},
        "constraints": {
            "mode": "dev_ticket_repair",
            "repair_profile": "resource_crud",
            "minimal_diff": True,
        },
        "realize_request": {
            "artifacts": {
                "implementation_brief": json.dumps(
                    {
                        "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                        "summary": "Add CRUD beside the grid.",
                    }
                ),
                "repair_hints": {
                    "target_files": [
                        "skills/demo/webui.json",
                        "skills/demo/handlers/main.py",
                        "skills/demo/tests/test_webui.py",
                    ],
                    "target_refs": [target_ref, moved_target_ref, "registry.modals.missing"],
                },
                "iteration_instruction": "Keep the chart sibling unchanged.",
            }
        },
    }

    worker._build_packet(assignment, workspace, input_dir)
    prompt = (input_dir / "task.md").read_text(encoding="utf-8")
    packet = json.loads((input_dir / "packet.json").read_text(encoding="utf-8"))

    context = packet["repair_target_context"]
    assert context["resolved"][0]["target_ref"] == target_ref
    assert context["resolved"][0]["value"]["title"] == "Metrics"
    assert context["resolved"][0]["neighbor_values"] == [
        {"id": "chart", "kind": "metric_chart", "secret": "not selected"}
    ]
    assert context["resolved"][1]["target_ref"] == moved_target_ref
    assert context["resolved"][1]["resolved_by"] == "unique_id"
    assert context["resolved"][1]["resolved_path"].endswith("views[id=grid]")
    assert context["missing"] == ["registry.modals.missing"]
    assert {item["file"] for item in context["source_slices"]} == {
        "skills/demo/handlers/main.py",
        "skills/demo/tests/test_webui.py",
    }
    assert context["coverage"]["complete"] is True
    assert "collection_grid" in prompt
    assert "not selected" in prompt
    assert "edit directly and do not rediscover" in prompt
    assert "Keep the chart sibling unchanged." in prompt


def test_bounded_repair_resolves_semantic_refs_in_json_and_yaml(
    tmp_path: Path,
) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
    )
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    scenario = workspace / "scenarios" / "demo"
    scenario.mkdir(parents=True)
    (scenario / "webui.json").write_text(
        json.dumps(
            {
                "widgets": [
                    {"id": "metrics-table", "title": "Metrics", "kind": "table"},
                    {"id": "metrics-chart", "title": "Trend", "kind": "chart"},
                ],
                "events": {"refresh": {"target": "demo_metrics.refresh"}},
            }
        ),
        encoding="utf-8",
    )
    (scenario / "scenario.yaml").write_text(
        "id: demo\ntitle: Demo Metrics\nversion: 0.1.0\n",
        encoding="utf-8",
    )
    (scenario / "handlers.py").write_text(
        'TARGET_WIDGET = "metrics-table"\n',
        encoding="utf-8",
    )
    assignment = {
        "task_id": "task.semantic-context",
        "target": {"type": "scenario", "id": "demo"},
        "forge": {"sparse_paths": ["scenarios/demo/"]},
        "constraints": {
            "mode": "dev_ticket_repair",
            "repair_profile": "surgical_ui",
            "minimal_diff": True,
        },
        "realize_request": {
            "artifacts": {
                "implementation_brief": "Rename only the qualified targets.",
                "repair_hints": {
                    "target_files": [
                        "scenarios/demo/webui.json",
                        "scenarios/demo/scenario.yaml",
                        "scenarios/demo/handlers.py",
                    ],
                    "target_refs": [
                        "widget:metrics-table.title",
                        "event:refresh.target",
                        "scenario:demo.title",
                    ],
                },
            }
        },
    }

    worker._build_packet(assignment, workspace, input_dir)
    packet = json.loads((input_dir / "packet.json").read_text(encoding="utf-8"))
    context = packet["repair_target_context"]

    assert [item["value"] for item in context["resolved"]] == [
        "Metrics",
        "demo_metrics.refresh",
        "Demo Metrics",
    ]
    assert [item["resolved_by"] for item in context["resolved"]] == [
        "semantic_id",
        "semantic_key",
        "semantic_id",
    ]
    assert context["missing"] == []
    assert context["coverage"]["complete"] is True
    assert context["source_slices"][0]["file"] == "scenarios/demo/scenario.yaml"
    assert any(
        item["file"] == "scenarios/demo/handlers.py"
        for item in context["source_slices"]
    )


def test_fully_qualified_surgical_ui_prompt_forbids_model_discovery(
    tmp_path: Path,
) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
    )
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    skill = workspace / "skills" / "demo"
    (skill / "handlers").mkdir(parents=True)
    (skill / "tests").mkdir()
    (skill / "webui.json").write_text(
        json.dumps({"buttons": [{"id": "open-workspace", "label": "Workspace"}]}),
        encoding="utf-8",
    )
    (skill / "handlers" / "main.py").write_text(
        'BUTTON = {"id": "open-workspace", "label": "Workspace"}\n',
        encoding="utf-8",
    )
    (skill / "tests" / "test_ui.py").write_text(
        'assert {"id": "open-workspace", "label": "Workspace"}\n',
        encoding="utf-8",
    )
    assignment = {
        "task_id": "task.fully-qualified-ui",
        "target": {"type": "skill", "id": "demo"},
        "forge": {"sparse_paths": ["skills/demo/"]},
        "constraints": {
            "mode": "dev_ticket_repair",
            "repair_profile": "surgical_ui",
            "minimal_diff": True,
        },
        "realize_request": {
            "artifacts": {
                "implementation_brief": json.dumps(
                    {
                        "schema": "adaos.dev_ticket.autonomous_repair_brief.v1",
                        "summary": "Rename Workspace to Data workspace.",
                    }
                ),
                "repair_hints": {
                    "target_files": [
                        "skills/demo/webui.json",
                        "skills/demo/handlers/main.py",
                        "skills/demo/tests/test_ui.py",
                    ],
                    "target_refs": ["buttons[id=open-workspace]"],
                },
            }
        },
    }

    worker._build_packet(assignment, workspace, input_dir)
    prompt = (input_dir / "task.md").read_text(encoding="utf-8")
    packet = json.loads((input_dir / "packet.json").read_text(encoding="utf-8"))

    assert packet["repair_target_context"]["coverage"]["complete"] is True
    assert "Apply the exact patch directly in one file-change operation" in prompt
    assert "Do not run discovery, source-read, diff, status, test, or validation commands" in prompt
    assert "Locate one exact target ID" not in prompt


def test_structured_edits_apply_with_exact_preconditions(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill = workspace / "skills" / "demo"
    (skill / "handlers").mkdir(parents=True)
    (skill / "webui.json").write_text(
        json.dumps(
            {
                "title": "Metrics",
                "actions": [
                    {"id": "refresh", "label": "Refresh"},
                    {"id": "create", "label": "Create"},
                ],
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    (skill / "handlers" / "main.py").write_text(
        'SUMMARY = "Technical status"\n',
        encoding="utf-8",
    )
    structured = {
        "schema": "adaos.builder.structured_edit_set.v1",
        "operations": [
            {
                "id": "rename",
                "op": "json_replace",
                "path": "skills/demo/webui.json",
                "pointer": "/title",
                "expected": "Metrics",
                "value": "Live metrics",
            },
            {
                "id": "move-create",
                "op": "json_move",
                "path": "skills/demo/webui.json",
                "from_pointer": "/actions/1",
                "pointer": "/actions/0",
                "expected": {"id": "create", "label": "Create"},
            },
            {
                "id": "plain-language",
                "op": "replace_text",
                "path": "skills/demo/handlers/main.py",
                "old": 'SUMMARY = "Technical status"',
                "new": 'SUMMARY = "Current usage"',
                "expected_count": 1,
            },
        ],
    }
    assignment = {
        "constraints": {
            "mode": "dev_ticket_repair",
            "exact_changed_paths": [
                "skills/demo/webui.json",
                "skills/demo/handlers/main.py",
            ],
            "max_changed_files": 2,
        },
        "realize_request": {
            "artifacts": {
                "repair_hints": {
                    "target_files": [
                        "skills/demo/webui.json",
                        "skills/demo/handlers/main.py",
                    ],
                    "structured_edits": structured,
                }
            }
        },
    }
    worker = object.__new__(LocalSkillFactoryWorker)

    receipt = worker._apply_structured_edits(assignment, workspace)

    webui = json.loads((skill / "webui.json").read_text(encoding="utf-8"))
    assert webui["title"] == "Live metrics"
    assert [item["id"] for item in webui["actions"]] == ["create", "refresh"]
    assert 'SUMMARY = "Current usage"' in (
        skill / "handlers" / "main.py"
    ).read_text(encoding="utf-8")
    assert receipt["strategy"] == "structured_edits"
    assert receipt["model_tokens"] == 0
    assert receipt["changed_files"] == [
        "skills/demo/handlers/main.py",
        "skills/demo/webui.json",
    ]

    with pytest.raises(ValueError, match="precondition failed"):
        worker._apply_structured_edits(assignment, workspace)


def test_structured_edit_worker_never_calls_codex(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    state_dir = tmp_path / "state"
    dev_skills = tmp_path / "dev" / "skills"
    skill = _core_created_skill_fixture(repo_root, dev_skills, "structured_demo")
    handler = skill / "handlers" / "main.py"
    old = "def lang_res() -> dict[str, str]:\n    return {}"
    assert old in handler.read_text(encoding="utf-8")
    factory = SkillFactoryService(state_dir=state_dir)
    submitted = factory.submit_realize_request(
        {
            "target": {"type": "skill", "id": "structured_demo"},
            "artifacts": {
                "implementation_brief": "Use plain language in the skill response.",
                "execution_budget": {
                    "source": "test.zero_model",
                    "max_tokens": 1000,
                    "max_wall_seconds": 300,
                },
                "repair_hints": {
                    "profile": "surgical_ui",
                    "target_files": ["skills/structured_demo/handlers/main.py"],
                    "structured_edits": {
                        "schema": "adaos.builder.structured_edit_set.v1",
                        "operations": [
                            {
                                "id": "plain-language",
                                "op": "replace_text",
                                "path": "skills/structured_demo/handlers/main.py",
                                "old": old,
                                "new": (
                                    "def lang_res() -> dict[str, str]:\n"
                                    '    return {"status": "Current usage"}'
                                ),
                                "expected_count": 1,
                            }
                        ],
                    },
                },
            },
            "repo": {"sparse_paths": ["skills/structured_demo/"]},
            "constraints": {
                "mode": "dev_ticket_repair",
                "repair_profile": "surgical_ui",
                "exact_changed_paths": ["skills/structured_demo/handlers/main.py"],
                "max_changed_files": 1,
            },
        }
    )

    def forbidden_codex(**kwargs):  # noqa: ARG001
        raise AssertionError("Codex must not run for admitted structured edits")

    worker = LocalSkillFactoryWorker(
        state_dir=state_dir,
        repo_root=repo_root,
        dev_skills_root=dev_skills,
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
        executor=forbidden_codex,
    )

    result = worker.run_once()

    assert result["ok"] is True, result
    assert result["result"]["execution_strategy"] == "structured_edits"
    assert result["result"]["provenance"]["structured_edit_receipt"][
        "model_tokens"
    ] == 0
    preflight = json.loads(
        (
            tmp_path
            / "runs"
            / submitted["task"]["task_id"]
            / "input"
            / "token_budget_preflight.json"
        ).read_text(encoding="utf-8")
    )
    assert preflight["status"] == "not_applicable"
    assert preflight["reason"] == "structured_edits_without_model"
    assert result["assignment"]["task_id"] == submitted["task"]["task_id"]
    assert "Current usage" in handler.read_text(encoding="utf-8")


def test_bounded_dev_ticket_rejects_large_manifest_collapse(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    scenario = workspace / "scenarios" / "demo"
    scenario.mkdir(parents=True)
    manifest = scenario / "scenario.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "adaos.scenario.v1",
                "id": "demo",
                "widgets": [
                    {
                        "id": f"widget_{index}",
                        "type": "demo.card",
                        "title": f"Metric card {index}",
                        "bindings": {"metric": "cpu", "slot": index},
                    }
                    for index in range(180)
                ],
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=workspace,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    manifest.write_text('{"schema":"adaos.scenario.v1","id":"demo"}\n', encoding="utf-8")
    worker = object.__new__(LocalSkillFactoryWorker)
    changed_paths = worker._changed_from_baseline(workspace)
    assignment = {
        "forge": {"sparse_paths": ["scenarios/demo/"]},
        "realize_request": {"artifacts": {"execution_budget": {"max_wall_seconds": 300}}},
    }

    with pytest.raises(ValueError, match="large declarative manifest rewrite"):
        worker._validate_changed_paths(assignment, changed_paths, workspace=workspace)

    admitted = copy.deepcopy(assignment)
    admitted["realize_request"]["artifacts"]["allow_large_manifest_rewrite"] = True
    worker._validate_changed_paths(admitted, changed_paths, workspace=workspace)


def test_return_to_prototype_skips_frozen_skill_tests_but_enforces_safe_ui(
    tmp_path: Path,
) -> None:
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
    skipped = next(
        check for check in safe["checks"] if check.get("status") == "skipped"
    )
    assert skipped["path"] == "skills/recipe_book_skill/tests"

    manifest = yaml.safe_load((scenario / "scenario.yaml").read_text(encoding="utf-8"))
    manifest["depends"] = ["recipe_book_skill"]
    (scenario / "scenario.yaml").write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
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
                                        "dataSource": {
                                            "kind": "skill",
                                            "name": "recipe_book_skill.list",
                                        },
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
    assert any(
        "left functional or external bindings" in error for error in unsafe["errors"]
    )


def test_return_to_prototype_ignores_preexisting_generated_skill_caches(
    tmp_path: Path,
) -> None:
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
    snapshot = (
        state_dir
        / "builder"
        / "workflow_snapshots"
        / "scenario"
        / "recipe_book"
        / "automation"
    )
    snapshot.mkdir(parents=True)
    (snapshot / "webui.json").write_text(
        json.dumps({"schema": "adaos.webui.v1", "ui": {"application": {}}}),
        encoding="utf-8",
    )
    (snapshot / "snapshot.json").write_text(
        json.dumps({"task_id": "task.previous"}), encoding="utf-8"
    )
    factory = SkillFactoryService(state_dir=state_dir)
    factory.submit_realize_request(
        {
            "target": {"type": "scenario", "id": "recipe_book"},
            "artifacts": {
                "companion_skill_id": "recipe_book_skill",
                "workflow_transition": "return_to_prototype",
            },
            "repo": {
                "sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]
            },
        }
    )

    def fake_codex(*, workspace: Path, prompt: str, output_dir: Path) -> CodexRunResult:  # noqa: ARG001
        manifest_path = workspace / "scenarios" / "recipe_book" / "scenario.yaml"
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
        manifest["description"] = "Safe local recipe prototype"
        manifest_path.write_text(
            yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
        )
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
    assert all(
        not path.startswith("skills/") for path in result["result"]["changed_paths"]
    )
    assert "Safe local recipe prototype" in (scenario / "scenario.yaml").read_text(
        encoding="utf-8"
    )


def test_codex_executor_environment_does_not_inherit_api_or_adaos_secrets(
    monkeypatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "secret")
    monkeypatch.setenv("ADAOS_TOKEN", "secret")
    monkeypatch.setenv("CODEX_HOME", "C:/codex-home")
    monkeypatch.setenv("PATH", "C:/bin")

    environment = SubprocessCodexExecutor._bounded_environment()

    assert environment["CODEX_HOME"] == "C:/codex-home"
    assert environment["PATH"] == "C:/bin"
    assert "OPENAI_API_KEY" not in environment
    assert "ADAOS_TOKEN" not in environment


def test_codex_executor_uses_current_sdk_and_utf8_python(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("PATH", "C:/global-bin")
    repo_root = tmp_path / "adaos"
    executor = SubprocessCodexExecutor(repo_root=repo_root)

    environment = executor._execution_environment()

    assert environment["ADAOS_REPO_ROOT"] == str(repo_root.resolve())
    assert environment["PYTHONPATH"] == str(repo_root.resolve() / "src")
    assert environment["ADAOS_PYTHON"] == str(Path(sys.executable).resolve())
    assert environment["PATH"].split(os.pathsep)[0] == str(
        Path(sys.executable).resolve().parent
    )
    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["PYTHONDONTWRITEBYTECODE"] == "1"
    assert "OPENAI_API_KEY" not in environment


def test_codex_executor_materializes_filtered_commit_bound_sdk(tmp_path: Path) -> None:
    repo_root = tmp_path / "adaos"
    (repo_root / "src" / "adaos").mkdir(parents=True)
    (repo_root / "docs" / "architecture").mkdir(parents=True)
    (repo_root / "src" / "adaos" / "sdk_marker.py").write_text(
        "SDK = True\n", encoding="utf-8"
    )
    (repo_root / "docs" / "skill_runtime.md").write_text(
        "runtime policy\n", encoding="utf-8"
    )
    (repo_root / "docs" / "architecture" / "domain-reference.md").write_text(
        "must stay hidden\n", encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "AdaOS Test"], cwd=repo_root, check=True
    )
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    executor = SubprocessCodexExecutor(repo_root=repo_root)

    snapshot = executor._materialize_sdk_snapshot(tmp_path / "task-runtime")

    assert snapshot is not None
    assert (snapshot / "src" / "adaos" / "sdk_marker.py").is_file()
    assert (snapshot / "docs" / "skill_runtime.md").is_file()
    assert not (snapshot / "docs" / "architecture").exists()
    receipt = json.loads((snapshot / "SDK_SNAPSHOT.json").read_text(encoding="utf-8"))
    expected_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert receipt["core_commit"] == expected_commit
    environment = executor._execution_environment(sdk_root=snapshot)
    assert environment["ADAOS_REPO_ROOT"] == str(snapshot.resolve())


def test_codex_executor_publishes_task_private_sdk_receipt_last(
    monkeypatch,
    tmp_path: Path,
) -> None:
    repo_root = tmp_path / "adaos"
    (repo_root / "src" / "adaos").mkdir(parents=True)
    (repo_root / "docs").mkdir(parents=True)
    (repo_root / "src" / "adaos" / "sdk_marker.py").write_text(
        "SDK = True\n", encoding="utf-8"
    )
    (repo_root / "docs" / "skill_runtime.md").write_text(
        "runtime policy\n", encoding="utf-8"
    )
    subprocess.run(["git", "init"], cwd=repo_root, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "AdaOS Test"],
        cwd=repo_root,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=repo_root, check=True)
    subprocess.run(
        ["git", "commit", "-m", "fixture"],
        cwd=repo_root,
        check=True,
        capture_output=True,
    )
    original_write_json = worker_module._write_json
    observed: dict[str, bool] = {}

    def inspect_receipt_write(path: Path, payload: Any) -> None:
        if path.name == "SDK_SNAPSHOT.json":
            observed["source_present_before_receipt"] = (
                path.parent / "src" / "adaos" / "sdk_marker.py"
            ).is_file()
        original_write_json(path, payload)

    monkeypatch.setattr(worker_module, "_write_json", inspect_receipt_write)

    snapshot = SubprocessCodexExecutor(repo_root=repo_root)._materialize_sdk_snapshot(
        tmp_path / "task-runtime"
    )

    assert snapshot == (tmp_path / "task-runtime" / "sdk-reference").resolve()
    assert observed == {"source_present_before_receipt": True}
    assert (snapshot / "SDK_SNAPSHOT.json").is_file()


def test_codex_executor_scopes_mutable_adaos_runtime_to_task(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAOS_BASE_DIR", "C:/host-adaos")
    executor = SubprocessCodexExecutor(repo_root=tmp_path / "repo")
    task_runtime = (
        tmp_path / "workspace" / ".adaos" / "tasks" / "task.example" / "adaos-runtime"
    )

    environment = executor._execution_environment(runtime_base_dir=task_runtime)

    assert environment["ADAOS_BASE_DIR"] == str(task_runtime.resolve())
    assert environment["ADAOS_TASK_RUNTIME_DIR"] == str(task_runtime.resolve())
    assert environment["ADAOS_DISABLE_ACTIVE_SLOT_PYTHON_REEXEC"] == "1"
    assert environment["ADAOS_DISABLE_ACTIVE_SLOT_ENV_APPLY"] == "1"
    assert environment["ADAOS_BASE_DIR"] != "C:/host-adaos"


def test_codex_executor_projects_root_mcp_config_without_prompt_secret(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAOS_ROOT_MCP_AUTH", "secret-token-value")
    profile = {
        "url": "https://ru.api.inimatic.com/v1/root/mcp",
        "server_name": "adaos-root",
        "bearer_token_env_var": "ADAOS_ROOT_MCP_AUTH",
        "enabled_tools": "get_status",
        "bound_target_id": "hub:sn_demo",
        "disabled_tools": ["unsafe_write", "unsafe_write"],
        "tool_timeout_sec": 45,
    }
    executor = SubprocessCodexExecutor(repo_root=tmp_path / "repo")

    config_args = executor._root_mcp_config_args(profile)
    environment = executor._execution_environment(root_mcp=profile)

    assert "mcp_servers.adaos_root.url" in " ".join(config_args)
    assert "mcp_servers.adaos_root.bearer_token_env_var" in " ".join(config_args)
    assert any(arg.endswith('enabled_tools=["get_status"]') for arg in config_args)
    assert any(arg.endswith('disabled_tools=["unsafe_write"]') for arg in config_args)
    assert "secret-token-value" not in " ".join(config_args)
    assert environment["ADAOS_ROOT_MCP_AUTH"] == "secret-token-value"

    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
    )
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    (workspace / "skills" / "demo").mkdir(parents=True)
    assignment = {
        "task_id": "task.mcp",
        "target": {"type": "skill", "id": "demo"},
        "mcp": {"root_mcp": profile},
        "forge": {"sparse_paths": ["skills/demo/"]},
        "realize_request": {
            "artifacts": {
                "implementation_brief": "Use MCP only for live context if needed."
            }
        },
    }

    worker._build_packet(assignment, workspace, input_dir)
    packet = json.loads((input_dir / "packet.json").read_text(encoding="utf-8"))
    prompt = (input_dir / "task.md").read_text(encoding="utf-8")

    assert packet["root_mcp"]["server_name"] == "adaos_root"
    assert packet["root_mcp"]["bound_target_id"] == "hub:sn_demo"
    assert packet["root_mcp"]["bearer_env_present"] is True
    assert "Task-scoped Root MCP route" in prompt
    assert "adaos_root" in prompt
    assert "Never substitute a skill, scenario, project, or component ID" in prompt
    assert "hub:sn_demo" in prompt
    assert "secret-token-value" not in prompt


def test_optional_root_mcp_uses_generic_runtime_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADAOS_ROOT_MCP_AUTH", "generic-secret")
    assignment = {
        "task_id": "task.generic-mcp",
        "mcp": {
            "root_mcp": {
                "enabled": True,
                "url": "https://ru.api.inimatic.com/v1/root/mcp",
                "required": False,
            }
        },
    }

    profile = _root_mcp_profile_from_assignment(assignment, include_private_token=True)

    assert profile is not None
    assert profile["bearer_token_env_var"] == "ADAOS_TASK_MCP_AUTH_TASK_GENERIC_MCP"
    assert profile["_bearer_token_value"] == "generic-secret"
    assert "generic-secret" not in json.dumps(_context_packet_prompt_projection({}))


def test_optional_root_mcp_is_omitted_without_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ADAOS_ROOT_MCP_AUTH", raising=False)
    assignment = {
        "task_id": "task.no-mcp",
        "mcp": {
            "root_mcp": {
                "enabled": True,
                "url": "https://ru.api.inimatic.com/v1/root/mcp",
                "required": False,
            }
        },
    }

    assert _root_mcp_profile_from_assignment(assignment, include_private_token=True) is None


def test_worker_projects_task_scoped_mcp_lease_without_prompt_secret(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ADAOS_HUB_URL", "http://127.0.0.1:8778")
    assignment = {
        "task_id": "task.lease",
        "target": {"type": "skill", "id": "demo"},
        "mcp": {
            "endpoint": "/v1/root/mcp/task/task.lease",
            "token_ref": "task_access_lease:lease.demo",
            "scope": ["read_capability_snapshot"],
            "lease_id": "lease.demo",
            "access_token": "lease-secret-value",
            "expires_at": "2026-08-31T12:00:00+00:00",
        },
        "forge": {"sparse_paths": ["skills/demo/"]},
        "realize_request": {
            "artifacts": {
                "implementation_brief": "Use Root MCP only when it reduces guessing."
            }
        },
    }

    private_profile = _root_mcp_profile_from_assignment(
        assignment,
        include_private_token=True,
    )
    assert private_profile is not None
    assert private_profile["server_name"] == "adaos_task_root"
    assert private_profile["url"] == "http://127.0.0.1:8778/v1/root/mcp/task/task.lease"
    assert private_profile["bearer_token_env_var"] == "ADAOS_TASK_MCP_AUTH_TASK_LEASE"
    assert private_profile["bearer_env_present"] is True
    assert private_profile["_bearer_token_value"] == "lease-secret-value"

    executor = SubprocessCodexExecutor(repo_root=tmp_path / "repo")
    config_args = executor._root_mcp_config_args(private_profile)
    environment = executor._execution_environment(root_mcp=private_profile)
    assert any(
        arg.endswith(
            "mcp_servers.adaos_task_root.url=\"http://127.0.0.1:8778/v1/root/mcp/task/task.lease\""
        )
        for arg in config_args
    )
    assert "lease-secret-value" not in " ".join(config_args)
    assert environment["ADAOS_TASK_MCP_AUTH_TASK_LEASE"] == "lease-secret-value"

    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
    )
    workspace = tmp_path / "workspace"
    input_dir = tmp_path / "input"
    (workspace / "skills" / "demo").mkdir(parents=True)

    worker._build_packet(assignment, workspace, input_dir)
    packet = json.loads((input_dir / "packet.json").read_text(encoding="utf-8"))
    prompt = (input_dir / "task.md").read_text(encoding="utf-8")

    assert packet["root_mcp"]["lease_id"] == "lease.demo"
    assert packet["root_mcp"]["bearer_env_present"] is True
    assert "_bearer_token_value" not in packet["root_mcp"]
    assert "lease-secret-value" not in prompt
    assert "Task-scoped Root MCP route" in prompt


def test_codex_prompt_budget_blocks_oversized_instruction_before_launch() -> None:
    assignment = {
        "realize_request": {
            "artifacts": {
                "execution_budget": {
                    "source": "test",
                    "max_tokens": 1600,
                    "max_wall_seconds": 300,
                }
            }
        }
    }

    check = _codex_prompt_budget_check(assignment, "x" * 12000)

    assert check["status"] == "blocked"
    assert check["declared"]["max_model_tokens"] == 1600
    assert check["prompt_token_estimate"] > check["prompt_token_limit"]


def test_codex_live_budget_estimate_counts_growing_tool_context(tmp_path: Path) -> None:
    journal = tmp_path / "codex-events.jsonl"
    events = [
        {
            "type": "item.completed",
            "item": {
                "type": "command_execution",
                "output": "x" * 4000,
            },
        }
        for _ in range(4)
    ]
    journal.write_text(
        "\n".join(json.dumps(item) for item in events) + "\n",
        encoding="utf-8",
    )

    usage = _codex_jsonl_live_budget_estimate(journal, prompt="p" * 4000)

    assert usage["accuracy"] == "estimated"
    assert usage["tool_rounds"] == 4
    assert usage["model_tokens"] > 12_000
    assert usage["cached_input_tokens"] > 0
    assert usage["estimated_fresh_input_tokens"] == (
        usage["input_tokens"] - usage["cached_input_tokens"]
    )
    assert _codex_budget_observed_tokens(
        usage,
        metric="fresh_plus_output",
    ) < usage["model_tokens"]


def test_codex_fresh_budget_excludes_cached_input_but_keeps_output() -> None:
    usage = {
        "model_tokens": 155_768,
        "input_tokens": 153_933,
        "cached_input_tokens": 133_888,
        "output_tokens": 1_835,
    }

    assert _codex_budget_observed_tokens(usage, metric="model_tokens") == 155_768
    assert _codex_budget_observed_tokens(usage, metric="fresh_plus_output") == 21_880


def test_context_packet_omits_intent_duplicated_by_implementation_brief() -> None:
    packet = {
        "change": {
            "change_id": "change.demo",
            "intent": "Fix the scoped projection.",
            "issues": [{"issue_id": "issue.demo"}],
        }
    }

    projected = _context_packet_prompt_projection(
        packet,
        implementation_brief="Fix the scoped projection.",
    )

    assert projected["change"]["change_id"] == "change.demo"
    assert "intent" not in projected["change"]


def test_codex_task_runtime_is_outside_candidate_worktree(tmp_path: Path) -> None:
    workspace = tmp_path / "run" / "workspace"
    output_dir = tmp_path / "run" / "output"

    task_runtime = SubprocessCodexExecutor._task_runtime_root(output_dir)

    assert workspace not in task_runtime.parents
    assert task_runtime.parent == output_dir.parent


def test_generated_tests_receive_task_owned_runtime_outside_candidate(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "run" / "workspace"
    tests_dir = workspace / "skills" / "candidate" / "tests"
    scenario_root = workspace / "scenarios" / "companion"
    scenario_tests = scenario_root / "tests"
    tests_dir.mkdir(parents=True)
    scenario_tests.mkdir(parents=True)
    (scenario_root / "scenario.yaml").write_text(
        "id: companion\nversion: 1.0.0\n",
        encoding="utf-8",
    )
    (tests_dir / "test_runtime_boundary.py").write_text(
        "import os\n"
        "from pathlib import Path\n\n"
        "def test_runtime_boundary():\n"
        "    runtime = Path(os.environ['ADAOS_BASE_DIR']).resolve()\n"
        "    internal = Path(os.environ['ADAOS_SKILL_INTERNAL_DATA_ROOT']).resolve()\n"
        "    workspace = Path.cwd().resolve()\n"
        "    assert runtime != workspace\n"
        "    assert workspace not in runtime.parents\n"
        "    assert os.environ['ADAOS_SKILL_NAME'] == 'candidate'\n"
        "    assert internal == runtime / 'skill-data' / 'candidate'\n"
        "    assert (workspace / 'scenarios' / 'companion' / 'scenario.yaml').is_file()\n"
        "    (internal / 'installed-context-marker.txt').parent.mkdir(parents=True, exist_ok=True)\n"
        "    (internal / 'installed-context-marker.txt').write_text('ok', encoding='utf-8')\n"
        "    (runtime / 'validation-marker.txt').parent.mkdir(parents=True, exist_ok=True)\n"
        "    (runtime / 'validation-marker.txt').write_text('ok', encoding='utf-8')\n",
        encoding="utf-8",
    )
    (scenario_tests / "test_scenario_package.py").write_text(
        "from pathlib import Path\n\n"
        "def test_scenario_is_validated_from_project_envelope():\n"
        "    assert (Path.cwd() / 'skills' / 'candidate' / 'tests').is_dir()\n",
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
    assert {(check["path"], check["ok"]) for check in checks} == {
        ("skills/candidate/tests", True),
        ("scenarios/companion/tests", True),
    }
    assert (
        workspace.parent / "adaos-runtime-packaged" / "validation-marker.txt"
    ).is_file()
    assert (
        workspace.parent
        / "adaos-runtime-packaged"
        / "skill-data"
        / "candidate"
        / "installed-context-marker.txt"
    ).is_file()
    assert not (workspace.parent / "package-validation").exists()
    assert not (workspace / "skills" / ".runtime").exists()


def test_generated_cleanup_removes_only_reserved_runtime_projection(
    tmp_path: Path,
) -> None:
    reserved = tmp_path / "skills" / ".runtime" / "candidate" / "state.json"
    arbitrary = tmp_path / ".adaos_validation_base" / "state.json"
    reserved.parent.mkdir(parents=True)
    arbitrary.parent.mkdir(parents=True)
    reserved.write_text("{}", encoding="utf-8")
    arbitrary.write_text("{}", encoding="utf-8")

    LocalSkillFactoryWorker._cleanup_generated_files(tmp_path)

    assert not (tmp_path / "skills" / ".runtime").exists()
    assert arbitrary.is_file()


def test_worker_applies_frozen_agent_profile_to_codex_executor(
    monkeypatch, tmp_path: Path
) -> None:
    captured: dict[str, int | str | None] = {}

    def fake_call(self, **_kwargs):
        captured["model"] = self.model
        captured["reasoning_effort"] = self.reasoning_effort
        captured["timeout_seconds"] = self.timeout_seconds
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
        assignment={
            "realize_request": {
                "artifacts": {
                    "execution_budget": {
                        "max_wall_seconds": 600,
                    }
                }
            }
        },
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

    assert captured == {
        "model": "gpt-5.4",
        "reasoning_effort": "high",
        "timeout_seconds": 600,
    }


def test_worker_prompt_requires_authoritative_sdk_and_utf8_transport(
    tmp_path: Path,
) -> None:
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
                "development_context": {
                    "execution_budget": {
                        "budget_view": "fixed_downstream",
                        "max_wall_seconds": 10800,
                        "max_model_tokens": 12000000,
                        "max_attempts": 2,
                        "max_human_interventions": 0,
                    }
                },
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
                                "adapter_catalog": [
                                    {"implementation": "irrelevant.full.catalog"}
                                ],
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
    assert "Exact executable provider contract bundle" in prompt
    assert "change.demo" in prompt
    assert "workflow.json validates" in prompt
    assert "complete TransitionDescriptor contract" in prompt
    assert "fabricated metrics" in prompt
    assert "Resolve skill-owned runtime storage through AdaOS SDK" in prompt
    assert "does not permit omitting the executable scientific path" in prompt
    assert "lifecycle allowance for this task is 180 seconds" in prompt
    assert (
        "Do not execute a scientific smoke or confirmatory workload from packaged tests"
        in prompt
    )
    assert "exact declared name" in prompt
    assert "skill_schema.json" in prompt
    assert "allow_heavy_dependencies" in prompt
    assert "experiment_plan.system" in prompt
    assert "must not substitute another model family" in prompt
    assert "install-strict" in prompt
    assert "trusted worker finalizer owns package" in prompt
    assert "ADAOS_TASK_RUNTIME_DIR" in prompt
    assert "bind `ADAOS_SKILL_INTERNAL_DATA_ROOT` to a dedicated child" in prompt
    assert "Never copy that binding into the returned ExecutionSpec" in prompt
    assert "`PYTHONHOME`, or `PYTHONPATH`" in prompt
    assert "Path(working_directory) / expected_outputs[i]" in prompt
    assert "collection through the returned `output_ref`" in prompt
    assert "never create repository-relative `.adaos*` runtime directories" in prompt
    assert "do not copy into or mutate the canonical workspace/runtime" in prompt
    assert "workflow.json" in prompt
    assert "irrelevant.full.catalog" not in prompt
    assert packet["context_packet_digest"] == "sha256:" + "a" * 64
    assert packet["validation_budget"] == {
        "schema": "adaos.builder.validation_budget.v1",
        "packaged_pytest_wall_seconds": 180,
        "source": "development_session.execution_budget",
        "execution_max_wall_seconds": 10800,
    }
    assert packet["context_packet"]["change"]["change_id"] == "change.demo"
    assert packet["context_packet"]["facets"]["workflow_definition"]["authoring"][
        "adapter_catalog"
    ]


def test_worker_compiles_manifest_bound_workflow_definition(tmp_path: Path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "workspace"
    skill = _core_created_skill_fixture(repo_root, workspace / "skills", "demo")
    manifest_path = skill / "skill.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    manifest["workflow"] = {"manifest": "workflow.json"}
    manifest_path.write_text(
        yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8"
    )
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
        (
            repo_root
            / "src"
            / "adaos"
            / "services"
            / "builder"
            / "builder_change.workflow.json"
        ).read_text(encoding="utf-8"),
        encoding="utf-8",
    )

    valid = worker._validate_workspace(assignment, workspace)

    assert valid["ok"] is True, valid["errors"]
    workflow_check = next(
        item for item in valid["checks"] if item.get("kind") == "workflow.definition.v1"
    )
    assert workflow_check["path"] == "skills/demo/workflow.json"
    assert workflow_check["definition_digest"].startswith("sha256:")


def test_worker_treats_browser_data_route_warnings_as_strict_errors(
    tmp_path: Path,
) -> None:
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


def test_worker_rejects_skill_manifest_that_runtime_dependency_policy_will_refuse(
    tmp_path: Path,
) -> None:
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

    LocalSkillFactoryWorker._validate_skill_dependency_isolation(
        workspace, checks, errors
    )

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
                "tools": [
                    {"name": "prepare_attempt", "input_schema": {"type": "object"}}
                ],
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

    LocalSkillFactoryWorker._validate_brief_contract_requirements(
        assignment, workspace, checks, errors
    )

    assert errors == [
        "implementation brief provider requirement runner is missing operations: collect_attempt"
    ]
    assert checks == []


def _document_contract_assignment(workspace: Path) -> dict:
    instruction = (
        workspace / ".adaos_context" / "dev-1" / "instructions" / "contract.json"
    )
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


def _operation_sequence_assignment(
    workspace: Path,
    *,
    omit_output: bool = False,
    mismatched_observation: bool = False,
    mismatched_cross_step: bool = False,
    returned_environment: dict[str, str] | None = None,
) -> dict:
    skill = workspace / "skills" / "sequence_provider"
    handlers = skill / "handlers"
    handlers.mkdir(parents=True)
    (skill / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "sequence_provider",
                "version": "0.1.0",
                "provider_contracts": [
                    {
                        "contract": "example.sequence.v1",
                        "capability": "example.sequence",
                        "operations": ["prepare", "collect", "verify"],
                    }
                ],
                "tools": [
                    {"name": "prepare", "entry": "handlers.main:prepare"},
                    {"name": "collect", "entry": "handlers.main:collect"},
                    {"name": "verify", "entry": "handlers.main:verify"},
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )
    (handlers / "main.py").write_text(
        "from __future__ import annotations\n"
        "import hashlib, json, os, sys\n"
        "from pathlib import Path\n"
        f"OMIT_OUTPUT = {omit_output!r}\n"
        "def prepare(request):\n"
        "    if request.get('provider') != 'sequence_provider':\n"
        "        raise ValueError('trusted candidate binding was not resolved')\n"
        "    root = Path(os.environ['ADAOS_SKILL_INTERNAL_DATA_ROOT']) / 'attempt'\n"
        "    root.mkdir(parents=True, exist_ok=True)\n"
        "    return {'command': [sys.executable, str(Path(__file__).resolve()), 'execute'], "
        "'working_directory': str(root), 'expected_outputs': ['result.json'], "
        f"'output_ref': str(root), 'environment': {dict(returned_environment or {})!r}}}\n"
        "def collect(output_ref):\n"
        "    path = Path(output_ref) / 'result.json'\n"
        "    raw = path.read_bytes()\n"
        "    digest = 'sha256:' + hashlib.sha256(raw).hexdigest()\n"
        f"    observed = {7 if mismatched_observation else 6}\n"
        "    return {'complete': True, 'artifacts': [{'uri': str(path), 'digest': digest}], "
        "'result': {'primary_metric': 6, 'evidence_class': 'workflow_smoke'}, "
        "'observations': [{'metric': {'name': 'primary_metric'}, 'value': observed, "
        "'evidence_role': 'workflow_smoke'}]}\n"
        "def verify(uri, digest):\n"
        "    path = Path(uri)\n"
        "    actual = 'sha256:' + hashlib.sha256(path.read_bytes()).hexdigest()\n"
        f"    return {{'ok': path.is_file() and actual == digest, "
        f"'metric': {7 if mismatched_cross_step else 6}}}\n"
        "if __name__ == '__main__' and len(sys.argv) > 1 and sys.argv[1] == 'execute':\n"
        "    if not OMIT_OUTPUT:\n"
        "        Path('result.json').write_text(json.dumps({'value': 6}) + '\\n', encoding='utf-8')\n",
        encoding="utf-8",
    )
    instruction = (
        workspace / ".adaos_context" / "dev-sequence" / "instructions" / "contract.json"
    )
    instruction.parent.mkdir(parents=True)
    object_schema = {"type": "object", "additionalProperties": True}
    contract = {
        "schema": "adaos.contract.operation_set.v1",
        "contract": "example.sequence.v1",
        "capability": "example.sequence",
        "candidate_role": "provider",
        "operations": {
            "prepare": {
                "input_schema": {
                    "type": "object",
                    "required": ["request"],
                    "properties": {
                        "request": {
                            "type": "object",
                            "required": ["value", "provider"],
                            "properties": {
                                "value": {"const": 3},
                                "provider": {"const": "sequence_provider"},
                            },
                            "additionalProperties": False,
                        }
                    },
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "required": [
                        "command",
                        "working_directory",
                        "expected_outputs",
                        "output_ref",
                    ],
                    "properties": {
                        "command": {"type": "array"},
                        "working_directory": {"type": "string"},
                        "expected_outputs": {"type": "array"},
                        "output_ref": {"type": "string"},
                        "environment": {
                            "type": "object",
                            "additionalProperties": {"type": "string"},
                        },
                    },
                    "additionalProperties": False,
                },
            },
            "collect": {
                "input_schema": {
                    "type": "object",
                    "required": ["output_ref"],
                    "properties": {"output_ref": {"type": "string"}},
                    "additionalProperties": False,
                },
                "output_schema": object_schema,
            },
            "verify": {
                "input_schema": {
                    "type": "object",
                    "required": ["uri", "digest"],
                    "properties": {
                        "uri": {"type": "string"},
                        "digest": {"type": "string"},
                    },
                    "additionalProperties": False,
                },
                "output_schema": {
                    "type": "object",
                    "required": ["ok", "metric"],
                    "properties": {
                        "ok": {"type": "boolean"},
                        "metric": {"type": "integer"},
                    },
                    "additionalProperties": False,
                },
            },
        },
        "conformance_fixtures": [
            {
                "id": "trusted-production-path",
                "kind": "operation_sequence",
                "required": True,
                "steps": [
                    {
                        "id": "prepare",
                        "kind": "operation",
                        "operation": "prepare",
                        "input": {
                            "request": {
                                "value": 3,
                                "provider": {"$candidate": "/skill_id"},
                            }
                        },
                    },
                    {
                        "id": "execute",
                        "kind": "execution_spec",
                        "source_step": "prepare",
                        "timeout_seconds": 10,
                    },
                    {
                        "id": "collect",
                        "kind": "operation",
                        "operation": "collect",
                        "input": {
                            "output_ref": {
                                "$bind": {"step": "prepare", "pointer": "/output_ref"}
                            }
                        },
                        "assert": [
                            {"pointer": "/complete", "equals": True},
                            {
                                "pointer": "/observations",
                                "contains": [
                                    {
                                        "pointer": "/metric/name",
                                        "equals": "primary_metric",
                                    },
                                    {
                                        "pointer": "/value",
                                        "equals_root_pointer": "/result/primary_metric",
                                    },
                                    {
                                        "pointer": "/evidence_role",
                                        "equals_root_pointer": "/result/evidence_class",
                                    },
                                ],
                            },
                        ],
                    },
                    {
                        "id": "verify",
                        "kind": "operation",
                        "operation": "verify",
                        "for_each": {
                            "$bind": {"step": "collect", "pointer": "/artifacts"}
                        },
                        "input": {
                            "uri": {"$item": "/uri"},
                            "digest": {"$item": "/digest"},
                        },
                        "assert": [
                            {"pointer": "/ok", "equals": True},
                            {
                                "pointer": "/metric",
                                "equals_step_pointer": {
                                    "step": "collect",
                                    "pointer": "/result/primary_metric",
                                },
                            },
                        ],
                    },
                ],
            }
        ],
    }
    instruction.write_text(json.dumps(contract, indent=2) + "\n", encoding="utf-8")
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


def _operation_contract_assignment(
    workspace: Path,
    *,
    candidate_input_schema: dict,
    declared_operations: list[str] | None = None,
    candidate_role: str | None = None,
    candidate_capability: str = "example.probe",
) -> dict:
    instruction = (
        workspace / ".adaos_context" / "dev-ops" / "instructions" / "contract.json"
    )
    instruction.parent.mkdir(parents=True)
    contract_input = {
        "type": "object",
        "required": ["request"],
        "properties": {
            "request": {
                "type": "object",
                # JSON Schema object-required order is not semantic and an
                # autonomous author need not reproduce source formatting.
                "required": ["value", "schema"],
                "properties": {
                    "schema": {"const": "example.probe.v1"},
                    "value": {"type": "number"},
                },
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
    }
    contract_output = {
        "type": "object",
        "required": ["schema", "value"],
        "properties": {
            "schema": {"const": "example.probe_result.v1"},
            "value": {"type": "number"},
        },
        "additionalProperties": False,
    }
    operation_set = {
        "schema": "adaos.contract.operation_set.v1",
        "contract": "example.probe_provider.v1",
        "capability": "example.probe",
        "operations": {
            "implementation_probe": {
                "input_schema": contract_input,
                "output_schema": contract_output,
            }
        },
    }
    if candidate_role is not None:
        operation_set["candidate_role"] = candidate_role
    instruction.write_text(
        json.dumps(
            operation_set,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    skill = workspace / "skills" / "example_skill"
    skill.mkdir(parents=True)
    candidate_output = dict(contract_output)
    candidate_output["description"] = "Annotation differences are not ABI differences."
    (skill / "skill.yaml").write_text(
        yaml.safe_dump(
            {
                "name": "example_skill",
                "version": "0.1.0",
                "provider_contracts": [
                    {
                        "contract": "example.probe_provider.v1",
                        "capability": candidate_capability,
                        "operations": declared_operations
                        if declared_operations is not None
                        else ["implementation_probe"],
                    }
                ],
                "tools": [
                    {
                        "name": "implementation_probe",
                        "entry": "handlers.main:implementation_probe",
                        "input_schema": candidate_input_schema,
                        "output_schema": candidate_output,
                    }
                ],
            },
            sort_keys=False,
        ),
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


def test_worker_binds_provider_tool_schema_to_admitted_operation_contract(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    exact_input = {
        "type": "object",
        "required": ["request"],
        "properties": {
            "request": {
                "type": "object",
                "required": ["schema", "value"],
                "properties": {
                    "schema": {"const": "example.probe.v1"},
                    "value": {"type": "number"},
                },
                "additionalProperties": False,
            }
        },
        "additionalProperties": False,
        "description": "This annotation may differ from the consumer contract.",
    }
    assignment = _operation_contract_assignment(
        workspace, candidate_input_schema=exact_input
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_operation_schemas(
        assignment, workspace, checks, errors
    )

    assert errors == []
    assert checks == [
        {
            "kind": "admitted_contract.operation_schema",
            "contract": "example.probe_provider.v1",
            "operation": "implementation_probe",
            "path": "skills/example_skill/skill.yaml",
            "ok": True,
        }
    ]


def test_worker_rejects_flat_tool_input_for_wrapped_consumer_operation(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_contract_assignment(
        workspace,
        candidate_input_schema={
            "type": "object",
            "required": ["schema", "value"],
            "properties": {
                "schema": {"const": "example.probe.v1"},
                "value": {"type": "number"},
            },
            "additionalProperties": False,
        },
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_operation_schemas(
        assignment, workspace, checks, errors
    )

    assert checks == []
    assert len(errors) == 1
    assert "implementation_probe input_schema differs" in errors[0]
    assert "/properties missing keys ['request']" in errors[0]


def test_worker_requires_every_admitted_operation_in_provider_declaration(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_contract_assignment(
        workspace,
        candidate_input_schema={"type": "object"},
        declared_operations=[],
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_operation_schemas(
        assignment, workspace, checks, errors
    )

    assert checks == []
    assert errors == [
        "skills/example_skill/skill.yaml: provider contract example.probe_provider.v1 "
        "does not declare admitted operation implementation_probe"
    ]


def test_worker_requires_provider_for_provider_role_operation_set(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_contract_assignment(
        workspace,
        candidate_input_schema={"type": "object"},
        candidate_role="provider",
        candidate_capability="example.other",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_operation_schemas(
        assignment, workspace, checks, errors
    )

    assert checks == []
    assert errors == [
        "admitted operation set requires the candidate to provide contract "
        "example.probe_provider.v1 with capability example.probe, but no matching "
        "skill provider_contracts declaration exists"
    ]


def test_worker_allows_context_operation_set_without_provider(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_contract_assignment(
        workspace,
        candidate_input_schema={"type": "object"},
        candidate_role="context",
        candidate_capability="example.other",
    )
    checks: list[dict] = []
    errors: list[str] = []

    LocalSkillFactoryWorker._validate_admitted_operation_schemas(
        assignment, workspace, checks, errors
    )

    assert errors == []
    assert checks == []


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


def test_worker_executes_consumer_owned_operation_sequence(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_sequence_assignment(workspace)
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    checks: list[dict] = []
    errors: list[str] = []

    worker._validate_admitted_contract_operation_sequences(
        assignment,
        workspace,
        runtime_dir=tmp_path / "runtime",
        checks=checks,
        errors=errors,
    )

    assert errors == []
    assert len(checks) == 1
    assert checks[0]["kind"] == "admitted_contract.operation_sequence"
    assert [item["id"] for item in checks[0]["steps"]] == [
        "prepare",
        "execute",
        "collect",
        "verify",
    ]
    assert (
        tmp_path / "runtime" / checks[0]["runtime_path"] / "attempt" / "result.json"
    ).is_file()


def test_worker_operation_sequence_detects_missing_execution_output(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_sequence_assignment(workspace, omit_output=True)
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    checks: list[dict] = []
    errors: list[str] = []

    worker._validate_admitted_contract_operation_sequences(
        assignment,
        workspace,
        runtime_dir=tmp_path / "runtime",
        checks=checks,
        errors=errors,
    )

    assert checks == []
    assert len(errors) == 1
    assert "execution_spec omitted exact expected outputs: result.json" in errors[0]


def test_worker_operation_sequence_reports_all_protected_environment_overrides(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_sequence_assignment(
        workspace,
        returned_environment={
            "PYTHONPATH": "candidate-path",
            "ADAOS_SKILL_INTERNAL_DATA_ROOT": "candidate-data",
        },
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

    worker._validate_admitted_contract_operation_sequences(
        assignment,
        workspace,
        runtime_dir=tmp_path / "runtime",
        checks=checks,
        errors=errors,
    )

    assert checks == []
    assert len(errors) == 1
    assert "protected environment keys" in errors[0]
    assert "ADAOS_SKILL_INTERNAL_DATA_ROOT" in errors[0]
    assert "PYTHONPATH" in errors[0]


def test_worker_operation_sequence_enforces_cross_field_array_invariant(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_sequence_assignment(workspace, mismatched_observation=True)
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    checks: list[dict] = []
    errors: list[str] = []

    worker._validate_admitted_contract_operation_sequences(
        assignment,
        workspace,
        runtime_dir=tmp_path / "runtime",
        checks=checks,
        errors=errors,
    )

    assert checks == []
    assert len(errors) == 1
    assert "contains no matching item" in errors[0]


def test_worker_operation_sequence_enforces_cross_step_invariant(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _operation_sequence_assignment(workspace, mismatched_cross_step=True)
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=Path(__file__).resolve().parents[1],
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    checks: list[dict] = []
    errors: list[str] = []

    worker._validate_admitted_contract_operation_sequences(
        assignment,
        workspace,
        runtime_dir=tmp_path / "runtime",
        checks=checks,
        errors=errors,
    )

    assert checks == []
    assert len(errors) == 1
    assert "expected 6, got 7" in errors[0]


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
    assert len(errors) == 1
    assert errors[0].startswith(
        "admitted contract fixture example.runner.v1:bounded-output produced no complete "
        "runtime document set; required: run_log.json, index.json; trusted task runtime root: "
    )
    assert str(runtime.resolve()) in errors[0]
    assert "incomplete sets found: none" in errors[0]
    assert "outside ADAOS_TASK_RUNTIME_DIR are not admissible" in errors[0]


def test_worker_reports_incomplete_contract_document_roots(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    assignment = _document_contract_assignment(workspace)
    runtime = tmp_path / "runtime"
    partial = runtime / "candidate-self-check" / "attempt-1"
    partial.mkdir(parents=True)
    (partial / "run_log.json").write_text(
        '{"network": {"mode": "offline", "accessed": false}}\n',
        encoding="utf-8",
    )
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
    assert len(errors) == 1
    assert (
        "incomplete sets found: candidate-self-check/attempt-1=run_log.json"
        in errors[0]
    )


def test_workspace_validates_contract_output_from_codex_task_runtime(
    tmp_path: Path,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    run_root = tmp_path / "run"
    workspace = run_root / "workspace"
    workspace.mkdir(parents=True)
    _core_created_skill_fixture(repo_root, workspace / "skills", "demo")
    assignment = _document_contract_assignment(workspace)
    assignment["target"] = {"type": "skill", "id": "demo"}
    attempt = run_root / "adaos-runtime" / "self-check" / "attempt-1"
    attempt.mkdir(parents=True)
    (attempt / "run_log.json").write_text(
        '{"network": {"mode": "offline", "accessed": false}}\n',
        encoding="utf-8",
    )
    (attempt / "index.json").write_text('{"files": []}\n', encoding="utf-8")
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )

    report = worker._validate_workspace(assignment, workspace)

    assert report["ok"] is True, report["errors"]
    contract_check = next(
        item
        for item in report["checks"]
        if item.get("kind") == "admitted_contract.document_set"
    )
    assert contract_check["runtime_path"] == "self-check/attempt-1"


def test_worker_rejects_tests_that_pin_checkpoint_owned_versions(
    tmp_path: Path,
) -> None:
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

    LocalSkillFactoryWorker._validate_tests_do_not_pin_checkpoint_metadata(
        workspace, checks, errors
    )

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

    LocalSkillFactoryWorker._validate_tests_do_not_pin_checkpoint_metadata(
        workspace, checks, errors
    )

    assert errors == []
    assert checks == [
        {
            "kind": "checkpoint_test_contract",
            "path": "scenarios/demo/tests/test_manifest.py",
            "ok": True,
        }
    ]


def test_worker_rejects_package_tests_bound_to_development_context(
    tmp_path: Path,
) -> None:
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


def test_worker_runs_generated_tests_from_package_shaped_projection(
    tmp_path: Path,
) -> None:
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


def test_worker_records_budgeted_package_test_timeout_for_autonomous_repair(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = tmp_path / "run" / "workspace"
    tests_dir = workspace / "skills" / "demo" / "tests"
    tests_dir.mkdir(parents=True)
    (tests_dir / "test_slow.py").write_text(
        "def test_slow():\n    assert True\n",
        encoding="utf-8",
    )
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=repo_root,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    assignment = {
        "realize_request": {
            "artifacts": {
                "development_context": {
                    "execution_budget": {"max_wall_seconds": 10800}
                }
            }
        }
    }
    observed: dict[str, float] = {}

    def timeout_run(*_args, **kwargs):
        observed["timeout"] = float(kwargs["timeout"])
        raise subprocess.TimeoutExpired(
            cmd=["python", "-m", "pytest"],
            timeout=kwargs["timeout"],
            output="partial output",
        )

    monkeypatch.setattr(worker_module, "_run", timeout_run)
    checks: list[dict] = []
    errors: list[str] = []

    worker._run_generated_tests(
        workspace,
        checks,
        errors,
        assignment=assignment,
    )

    assert observed["timeout"] == 180.0
    assert checks == [
        {
            "kind": "pytest.packaged",
            "path": "skills/demo/tests",
            "ok": False,
            "status": "timeout",
            "timeout_seconds": 180,
            "validation_budget": {
                "schema": "adaos.builder.validation_budget.v1",
                "packaged_pytest_wall_seconds": 180,
                "source": "development_session.execution_budget",
                "execution_max_wall_seconds": 10800,
            },
            "output": "partial output",
        }
    ]
    assert errors == [
        "skills/demo/tests: packaged pytest timed out after 180 seconds: partial output"
    ]
    assert worker_module._generated_test_budget({})[
        "packaged_pytest_wall_seconds"
    ] == 60


def test_worker_ignores_unchanged_baseline_version_pins(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    baseline_tests = workspace / "skills" / "dependency" / "tests"
    changed_tests = workspace / "skills" / "target" / "tests"
    baseline_tests.mkdir(parents=True)
    changed_tests.mkdir(parents=True)
    (baseline_tests / "test_manifest.py").write_text(
        "def test_version(manifest):\n    assert manifest['version'] == '0.1.0'\n",
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


def test_worker_changed_paths_supports_single_commit_dirty_workspace(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=workspace,
        check=True,
    )
    tracked = workspace / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    tracked.write_text("changed\n", encoding="utf-8")
    (workspace / "new.txt").write_text("new\n", encoding="utf-8")

    worker = object.__new__(LocalSkillFactoryWorker)

    assert worker._changed_from_baseline(workspace) == ["tracked.txt", "new.txt"]


def test_worker_changed_paths_differs_from_root_after_commit(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    subprocess.run(["git", "init"], cwd=workspace, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "test"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.invalid"],
        cwd=workspace,
        check=True,
    )
    tracked = workspace / "tracked.txt"
    tracked.write_text("baseline\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )
    tracked.write_text("changed\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=workspace, check=True)
    subprocess.run(
        ["git", "commit", "-m", "result"],
        cwd=workspace,
        check=True,
        capture_output=True,
    )

    worker = object.__new__(LocalSkillFactoryWorker)

    assert worker._changed_from_baseline(workspace) == ["tracked.txt"]


def test_worker_restores_budget_stopped_candidate_for_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    source_task_id = "task.budget-stopped"
    source_run = worker.runs_root / source_task_id
    previous_workspace = source_run / "workspace"
    previous_file = previous_workspace / "skills" / "demo" / "webui.json"
    previous_file.parent.mkdir(parents=True)
    previous_file.write_text('{"value":"baseline"}', encoding="utf-8")
    worker._init_git_workspace(previous_workspace, "realize/source")
    previous_file.write_text('{"value":"candidate"}', encoding="utf-8")
    previous_assignment = {
        "task_id": source_task_id,
        "target": {"type": "skill", "id": "demo"},
        "forge": {
            "sparse_paths": ["skills/demo/"],
            "source_snapshot": {"digest": "sha256:same"},
        },
    }
    (source_run / "input").mkdir(parents=True)
    (source_run / "input" / "assignment.json").write_text(
        json.dumps(previous_assignment),
        encoding="utf-8",
    )
    failure_id = "failure.task.budget-stopped.test"
    monkeypatch.setattr(
        SkillFactoryService,
        "read_task",
        lambda _self, task_id: {
            "task_id": task_id,
            "status": "failed",
            "failure_history": [
                {
                    "failure_id": failure_id,
                    "message": "Codex token budget exceeded: observed 10 of 5 model tokens.",
                }
            ],
        },
    )

    workspace = tmp_path / "current"
    current_file = workspace / "skills" / "demo" / "webui.json"
    current_file.parent.mkdir(parents=True)
    current_file.write_text('{"value":"baseline"}', encoding="utf-8")
    worker._init_git_workspace(workspace, "realize/current")
    assignment = {
        "task_id": "task.current",
        "target": {"type": "skill", "id": "demo"},
        "forge": {
            "sparse_paths": ["skills/demo/"],
            "source_snapshot": {"digest": "sha256:same"},
        },
        "realize_request": {
            "artifacts": {
                "continuation_checkpoint": {
                    "mode": "validate_preserved_candidate",
                    "source_task_id": source_task_id,
                    "failure_id": failure_id,
                }
            }
        },
    }

    restored = worker._restore_continuation_candidate(assignment, workspace)

    assert restored is not None
    assert restored["source_task_id"] == source_task_id
    assert restored["changed_paths"] == ["skills/demo/webui.json"]
    assert json.loads(current_file.read_text(encoding="utf-8"))["value"] == "candidate"


def test_worker_ignores_budget_stopped_continuation_without_source_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    worker = LocalSkillFactoryWorker(
        state_dir=tmp_path / "state",
        repo_root=tmp_path,
        dev_skills_root=tmp_path / "dev" / "skills",
        dev_scenarios_root=tmp_path / "dev" / "scenarios",
        runs_root=tmp_path / "runs",
    )
    source_task_id = "task.discovery-only"
    source_run = worker.runs_root / source_task_id
    previous_workspace = source_run / "workspace"
    previous_file = previous_workspace / "skills" / "demo" / "webui.json"
    previous_file.parent.mkdir(parents=True)
    previous_file.write_text('{"value":"baseline"}', encoding="utf-8")
    worker._init_git_workspace(previous_workspace, "realize/source")
    previous_assignment = {
        "task_id": source_task_id,
        "target": {"type": "skill", "id": "demo"},
        "forge": {
            "sparse_paths": ["skills/demo/"],
            "source_snapshot": {"digest": "sha256:same"},
        },
    }
    (source_run / "input").mkdir(parents=True)
    (source_run / "input" / "assignment.json").write_text(
        json.dumps(previous_assignment),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        SkillFactoryService,
        "read_task",
        lambda _self, task_id: {
            "task_id": task_id,
            "status": "failed",
            "failure_history": [
                {
                    "failure_id": "failure.discovery-only",
                    "message": "Codex token budget exceeded: observed 10 of 5 model tokens.",
                }
            ],
        },
    )
    workspace = tmp_path / "current"
    current_file = workspace / "skills" / "demo" / "webui.json"
    current_file.parent.mkdir(parents=True)
    current_file.write_text('{"value":"baseline"}', encoding="utf-8")
    worker._init_git_workspace(workspace, "realize/current")
    assignment = {
        "task_id": "task.current",
        "target": {"type": "skill", "id": "demo"},
        "forge": {
            "sparse_paths": ["skills/demo/"],
            "source_snapshot": {"digest": "sha256:same"},
        },
        "realize_request": {
            "artifacts": {
                "continuation_checkpoint": {
                    "mode": "validate_preserved_candidate",
                    "source_task_id": source_task_id,
                    "failure_id": "failure.discovery-only",
                }
            }
        },
    }

    restored = worker._restore_continuation_candidate(assignment, workspace)

    assert restored is None
    assert json.loads(current_file.read_text(encoding="utf-8"))["value"] == "baseline"


def test_codex_executor_discovers_vscode_bundled_cli(
    monkeypatch, tmp_path: Path
) -> None:
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


def test_codex_executor_reports_actionable_missing_cli(
    monkeypatch, tmp_path: Path
) -> None:
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
            "repo": {
                "sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]
            },
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


def test_worker_reasks_codex_to_repair_source_boundary_violation(
    tmp_path: Path,
) -> None:
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
            "repo": {
                "sparse_paths": ["scenarios/recipe_book/", "skills/recipe_book_skill/"]
            },
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


def test_worker_isolates_generated_test_side_effects_from_candidate_source(
    tmp_path: Path,
) -> None:
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
        test_file = (
            workspace / "skills" / "boundary_skill" / "tests" / "test_side_effect.py"
        )
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
            test_file.write_text(
                "def test_side_effect():\n    assert True\n", encoding="utf-8"
            )
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
    assert not (
        Path(result["result"]["local_run_dir"]) / "workspace" / "escaped-validation.txt"
    ).exists()
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
        progress_callback=lambda task_id, status, message: projected.append(
            (task_id, status, message)
        ),
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
