from __future__ import annotations

import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW_ROOT = ROOT / ".github" / "workflows"
JOB_ENV_HEADER = re.compile(r"^ {4}env:\s*$")


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
