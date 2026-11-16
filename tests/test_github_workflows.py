from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
JOB_ENV_HEADER = re.compile(r"^ {4}env:\s*$")


@pytest.mark.parametrize("workflow", sorted(WORKFLOW_ROOT.glob("*.y*ml")), ids=lambda p: p.name)
def test_workflow_yaml_and_job_steps_are_valid(workflow: Path) -> None:
    document = yaml.safe_load(workflow.read_text(encoding="utf-8"))
    assert isinstance(document, dict), workflow.name
    assert isinstance(document.get("jobs"), dict), workflow.name
    for name, job in document["jobs"].items():
        assert isinstance(job, dict), (workflow.name, name)
        if "uses" in job:
            continue  # Reusable workflow job.
        assert isinstance(job.get("steps"), list), (workflow.name, name)
        for step in job["steps"]:
            assert isinstance(step, dict), (workflow.name, name)
            assert ("uses" in step) != ("run" in step), (workflow.name, name, step.get("name"))
            assert isinstance(step.get("run", step.get("uses")), str), (workflow.name, name)


def _job_env_blocks(workflow: Path) -> list[str]:
    lines = workflow.read_text(encoding="utf-8").splitlines()
    blocks: list[str] = []
    index = 0
    while index < len(lines):
        if not JOB_ENV_HEADER.match(lines[index]):
            index += 1
            continue
        block: list[str] = []
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.strip() and len(line) - len(line.lstrip()) <= 4:
                break
            block.append(line)
            index += 1
        blocks.append("\n".join(block))
    return blocks


def test_job_environment_does_not_use_runner_context() -> None:
    for workflow in sorted(WORKFLOW_ROOT.glob("*.y*ml")):
        for block in _job_env_blocks(workflow):
            assert "${{ runner." not in block, (
                f"{workflow.relative_to(ROOT)} uses the runner context in a job-level env; "
                "GitHub evaluates that scope before the runner context exists"
            )
