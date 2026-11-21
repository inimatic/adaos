"""Core-owned test receipts for a restored, immutable whole Project.

Only source checks are reusable. Hub, data and runtime materialization admission
remain fresh checks of their effect owners; this receipt cannot replace them.
"""
from __future__ import annotations

from dataclasses import asdict
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import time

import yaml

from adaos.domain.artifact_release import canonical_payload_digest
from adaos.services.skill.tests_runner import run_tests
from .project_source_sync import capture_project
from .storage import atomic_write_json, mutation_lock


class SourceValidationError(RuntimeError):
    code = "project_source_validation_failed"


class SourceValidationTimeout(TimeoutError):
    code = "project_source_validation_timeout"


class SourceValidationUnavailable(RuntimeError):
    code = "project_source_validation_environment_unavailable"


def validation_environment() -> dict:
    """Bind reuse to interpreter, installed dependencies, Core code and config.

    Core can be an editable checkout: its package version alone is insufficient.
    Do not retain environment values or credentials in an evidence receipt.
    """
    source = Path(__file__).resolve().parents[2]
    core = hashlib.sha256()
    paths = []
    for parent, directories, names in os.walk(source):
        directories[:] = sorted(d for d in directories if d not in {"integrations", "__pycache__"})
        paths.extend(Path(parent) / name for name in names if name.endswith(".py"))
    for path in sorted(paths):
        core.update(path.relative_to(source).as_posix().encode())
        core.update(hashlib.sha256(path.read_bytes()).digest())
    config = dict(os.environ)
    return {"schema": "adaos.project.source_test_environment.v1",
            "python": sys.version, "interpreter": sys.executable,
            "platform": platform.platform(), "core_digest": "sha256:" + core.hexdigest(),
            "dependencies": sorted([d.metadata.get("Name", ""), d.version] for d in metadata.distributions()),
            "configuration_digest": canonical_payload_digest(config)}


def _cached_record(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding='utf-8'))
        return value if isinstance(value,dict) else {}
    except (OSError,ValueError):
        return {}


def _verified_package(row: dict, *, ref: str, identity_key: str, root: Path) -> bool:
    try:
        kind, name = ref.split(':')
        log = root / f'{kind}-{name}.log'
        return (row.get('ref') == ref and row.get('identity_digest') == identity_key
            and row.get('log') == log.name
            and log.resolve().is_relative_to(root.resolve())
            and row.get('receipt_digest') == canonical_payload_digest({
                k:v for k,v in row.items() if k not in {'receipt_digest','reused'}})
            and bool(row.get('results')) and all(result['status'] == 'passed' for result in row['results'])
            and hashlib.sha256(log.read_bytes()).hexdigest() == row.get('log_sha256'))
    except (OSError,ValueError,KeyError,TypeError,AttributeError):
        return False


def validate_restored_project(*, workspace: Path, project_id: str,
                              snapshot_digest: str, state_root: Path,
                              timeout_seconds: int = 600,
                              max_wall_seconds: int = 3600) -> dict:
    """Run every owned test package with an inherited, bounded test allowance.

    A successful, unchanged full closure may be reused after restart. Failed,
    interrupted, missing or changed log receipts never authorize reuse.
    """
    if type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 3600:
        raise ValueError("Source validation allowance must be 1..3600 seconds")
    if type(max_wall_seconds) is not int or not timeout_seconds <= max_wall_seconds <= 7200:
        raise ValueError("Whole-Project allowance must cover one test package and be at most 7200 seconds")
    workspace = Path(workspace)
    before = capture_project(workspace, project_id)
    if before.digest != snapshot_digest:
        raise SourceValidationError("Restored Project differs from validation basis")
    definition = yaml.safe_load(dict(before.entries)[f"projects/{project_id}/project.yaml"])
    members = [{"ref": "project:" + project_id}, *definition["components"]["owned"]]
    suites = [member for member in members if
              (workspace / (member["ref"].split(":")[0] + "s") /
               member["ref"].split(":")[1] / "tests").is_dir()]
    test_plan = {"schema": "adaos.project.test_plan.v1",
                 "suites": [member["ref"] for member in suites], "complete_suite": True}
    identity = {"schema": "adaos.project.source_validation.v1", "project_id": project_id,
                "source_snapshot_digest": snapshot_digest, "test_plan": test_plan,
                "environment": validation_environment()}
    key = canonical_payload_digest(identity).removeprefix("sha256:")
    root = Path(state_root) / "source-validation" / key
    receipt_path = root / "receipt.json"
    with mutation_lock(root.with_suffix(".lock"), timeout_s=3600):
        if receipt_path.is_file():
            receipt = _cached_record(receipt_path)
            unsigned = {k: v for k, v in receipt.items() if k != "receipt_digest"}
            if (receipt.get("receipt_digest") == canonical_payload_digest(unsigned)
                    and receipt.get("identity") == identity and receipt.get("status") == "passed"
                    and receipt.get('source_unchanged') is True
                    and isinstance(receipt.get('packages'),list)
                    and all(isinstance(row,dict) for row in receipt['packages'])
                    and sorted(str(row.get('ref','')) for row in receipt['packages'] if isinstance(row,dict))
                        == sorted(test_plan['suites'])
                    and receipt['packages']
                    and all(_verified_package(row,ref=row.get('ref',''),identity_key=key,root=root)
                            for row in receipt['packages'])):
                return {**receipt, "reused": True}
        rows = []
        deadline = time.monotonic() + max_wall_seconds
        for member in suites:
            kind, name = member["ref"].split(":")
            component = workspace / (kind + "s") / name
            if not (component / "tests").is_dir():
                continue
            log_name = f"{kind}-{name}.log"
            row_path = root / f"{kind}-{name}.json"
            if row_path.is_file():
                row = _cached_record(row_path)
                if _verified_package(row, ref=member['ref'], identity_key=key, root=root):
                    rows.append({**row, "reused": True})
                    continue
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                raise SourceValidationTimeout("Whole-Project test allowance exhausted; completed package receipts retained")
            try:
                results = run_tests(component, log_path=root / log_name,
                    interpreter=Path(sys.executable),
                    python_paths=[str(workspace), str(Path(__file__).resolve().parents[3])],
                    dev_mode=True, complete_suite=True, timeout_seconds=min(timeout_seconds, remaining),
                    extra_env={"ADAOS_DEV_DIR": str(workspace), "PYTHONDONTWRITEBYTECODE": "1"},
                    skill_name=name if kind == "skill" else None)
            except subprocess.TimeoutExpired:
                raise SourceValidationTimeout("Frozen test package timed out; source and completed package receipts retained") from None
            except OSError:
                raise SourceValidationUnavailable("Frozen test process unavailable; inspect retained private logs") from None
            if capture_project(workspace, project_id).digest != before.digest:
                raise SourceValidationError("Test package changed its frozen source; its result cannot be reused")
            if validation_environment() != identity['environment']:
                raise SourceValidationUnavailable("Core or test environment changed during validation; package result not reusable")
            row = {"ref": member["ref"], "identity_digest": key,
                   "results": [asdict(r) for r in results.values()], "log": log_name,
                   "log_sha256": hashlib.sha256((root / log_name).read_bytes()).hexdigest()}
            row["receipt_digest"] = canonical_payload_digest(row)
            atomic_write_json(row_path, row)
            rows.append(row)
        unchanged = capture_project(workspace, project_id).digest == before.digest
        passed = (unchanged and bool(rows) and all(row["results"] and
                  all(r["status"] == "passed" for r in row["results"]) for row in rows))
        receipt = {"identity": identity, "status": "passed" if passed else "failed",
                   "source_unchanged": unchanged, "packages": rows,
                   "timeout_seconds": timeout_seconds, "max_wall_seconds": max_wall_seconds}
        receipt["receipt_digest"] = canonical_payload_digest(receipt)
        atomic_write_json(receipt_path, receipt)
        if not passed:
            raise SourceValidationError("Frozen Project tests failed; retained source and test logs are available")
        return {**receipt, "reused": False}
