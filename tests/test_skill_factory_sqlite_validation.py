from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml

from adaos.services.skill_factory_worker import LocalSkillFactoryWorker


def validate(tmp_path, statements):
    workspace = tmp_path / "candidate"
    skill = workspace / "skills/sample"
    skill.mkdir(parents=True)
    (skill / "skill.yaml").write_text(yaml.safe_dump({"data_lifecycle": {
        "schema": "adaos.skill.data_lifecycle.v1", "execution": "native_tools",
        "databases": [{"path": "nested/sample.db", "migrations": [
            {"version": 1, "name": "base", "statements": statements}]}]}}), encoding="utf-8")
    (skill / "main.py").write_text("raise RuntimeError('must not execute candidate code')", encoding="utf-8")
    before = {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()}
    checks, errors = [], []
    worker = SimpleNamespace(state_dir=tmp_path / "state")
    LocalSkillFactoryWorker._validate_declared_sqlite_initialization(worker, workspace, checks, errors)
    assert {p.relative_to(workspace): p.read_bytes() for p in workspace.rglob("*") if p.is_file()} == before
    assert not list((worker.state_dir / "skill_factory/validation").glob("sqlite-*"))
    return checks, errors


def test_worker_qualifies_actual_core_sql_and_repeat_without_candidate_code(tmp_path):
    checks, errors = validate(tmp_path, ["CREATE TABLE samples (id INTEGER PRIMARY KEY)",
        "ALTER TABLE samples ADD COLUMN score INTEGER CHECK(score BETWEEN 1 AND 5 OR score IS NULL)"])
    assert not errors
    assert checks[0]["ok"] and checks[0]["databases"] == 1
    assert checks[0]["scope"] == "empty-synthetic-store-and-reopen"
    assert checks[0]["elapsed_ms"] >= 0


@pytest.mark.parametrize("statement", ["THIS IS NOT SQL", "SELECT * FROM absent", "COMMIT",
    "ATTACH DATABASE 'outside.db' AS external", "DELETE FROM adaos_schema_migrations"])
def test_worker_rejects_broken_or_escaping_sql_even_when_candidate_tests_could_pass(tmp_path, statement):
    checks, errors = validate(tmp_path, [statement])
    assert errors and "Core SQLite initialization" in errors[0]
    assert not checks[0]["ok"]
    assert not Path("outside.db").exists()


def test_workspace_validation_always_calls_trusted_sql_check(tmp_path, monkeypatch):
    worker = LocalSkillFactoryWorker(state_dir=tmp_path / "state", repo_root=tmp_path,
        dev_skills_root=tmp_path / "dev/skills", dev_scenarios_root=tmp_path / "dev/scenarios")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(worker, "_changed_from_baseline", lambda _: [])
    monkeypatch.setattr(worker, "_run_generated_tests", lambda *args, **kwargs: None)
    visited = []
    monkeypatch.setattr(worker, "_validate_declared_sqlite_initialization", lambda path, checks, errors: visited.append(path))
    worker._validate_workspace({}, workspace)
    assert visited == [workspace]
