"""Compare source and native-package test environments without touching live slots."""

import argparse
import json
from pathlib import Path
import shutil
import tempfile
import time

from adaos.services import skill_factory_worker as worker_module
from adaos.e2e.builder import _write_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--skill", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--wall-seconds", type=int, default=60)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[2]
    args.output.parent.mkdir(parents=True, exist_ok=True)
    timings = []
    run = worker_module._run

    def measured(command, **kwargs):
        started = time.monotonic()
        try:
            kwargs["timeout"] = args.wall_seconds
            result = run([*(item for item in command if item != "-q"), "-o", "addopts=",
                          "-o", "faulthandler_timeout=10", "--durations=20", "-vv"], **kwargs)
            timings.append({"seconds": time.monotonic() - started,
                            "returncode": result.returncode,
                            "stdout": result.stdout, "stderr": result.stderr})
            return result
        except Exception as exc:
            def readable(value):
                return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value
            timings.append({"seconds": time.monotonic() - started,
                            "error": str(exc), "stdout": readable(getattr(exc, "stdout", None)),
                            "stderr": readable(getattr(exc, "stderr", None))})
            raise

    worker_module._run = measured
    with tempfile.TemporaryDirectory(prefix="packaged-diagnostic-", dir=args.output.parent) as directory:
        workspace = Path(directory).resolve() / "workspace"
        skill = args.skill.resolve(strict=True)
        shutil.copytree(skill, workspace / "skills" / skill.name,
                        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache", ".runtime"))
        worker = worker_module.LocalSkillFactoryWorker.__new__(worker_module.LocalSkillFactoryWorker)
        worker.repo_root = repo
        checks, errors = [], []
        worker._run_generated_tests(workspace, checks, errors)
        report = {"skill": skill.name, "ok": not errors, "checks": checks,
                  "errors": errors, "timings": timings}
        _write_json(args.output, report)
        print(json.dumps({"ok": not errors, "seconds": [x["seconds"] for x in timings],
                          "output": str(args.output)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
