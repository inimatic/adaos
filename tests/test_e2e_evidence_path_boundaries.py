"""Exercise actual CLI containment expressions without running research jobs."""
import ast
import os
from pathlib import Path
import subprocess

import pytest


REPOSITORY = Path(__file__).resolve().parents[1]
SCRIPTS = ("check-declared-migrations.py", "check-workbench-trial.py", "analyze-prototype-oracle-cost.py")


def directory_link(link, target):
    if os.name == "nt":
        # Junction creation needs no Developer Mode/admin and works across drives.
        subprocess.run(["cmd.exe", "/d", "/c", "mklink", "/J", str(link), str(target)],
                       check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def admitted(script, repository, candidate):
    tree = ast.parse((REPOSITORY / "e2e/stand" / script).read_text(encoding="utf-8"))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
             and isinstance(node.func, ast.Attribute) and node.func.attr == "is_relative_to"
             and any(isinstance(part, ast.Constant) and part.value == "e2e/artifacts/builder"
                     for part in ast.walk(node.args[0]))]
    assert len(calls) == 1
    expression = ast.Expression(body=calls[0])
    return eval(compile(expression, script, "eval"), {"Path": Path, "root": repository,
                "output": candidate.resolve(), "run": candidate.resolve()})


@pytest.mark.parametrize("script", SCRIPTS)
@pytest.mark.parametrize("linked", (False, True))
def test_evidence_root_can_be_relocated_without_loosening_boundaries(tmp_path, monkeypatch, script, linked):
    repository = tmp_path / "repository"
    (repository / "e2e").mkdir(parents=True)
    external = tmp_path / "external"
    external.mkdir()
    artifacts = repository / "e2e/artifacts"
    if linked:
        directory_link(artifacts, external)
    else:
        artifacts.mkdir()
    evidence_root = artifacts / "builder"
    evidence_root.mkdir()
    monkeypatch.chdir(repository)
    assert admitted(script, repository, evidence_root / "new-run/result.json")
    assert not admitted(script, repository, artifacts / "builder-sibling/result.json")
    assert not admitted(script, repository, tmp_path / "outside/result.json")
    escaped = tmp_path / "escape-target"
    escaped.mkdir()
    directory_link(evidence_root / "escape", escaped)
    assert not admitted(script, repository, evidence_root / "escape/result.json")
