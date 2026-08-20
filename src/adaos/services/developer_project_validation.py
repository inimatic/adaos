"""Deterministic, source-bound validation for one DEV project.

This service is deliberately independent from Builder and from candidate-owned
test assertions.  It provides a stable evidence receipt while reusing the same
native skill validator and isolated test runner as the AdaOS CLI.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any

import psutil
import yaml

from adaos.services.skill.tests_runner import run_tests
from adaos.services.skill.validation import SkillValidationService
from adaos.domain.execution import ExecutionSpec


_PROJECT_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")
_IGNORED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".runtime",
    "__pycache__",
}
_IGNORED_SUFFIXES = {".pyc", ".pyo"}
_INSPECTABLE_TEXT_SUFFIXES = {
    ".json",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}
_MAX_INSPECTION_FILE_BYTES = 512 * 1024
_MAX_INSPECTION_TOTAL_BYTES = 4 * 1024 * 1024


def _canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def _digest(value: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(value)).hexdigest()


def _source_inventory(root: Path) -> tuple[list[dict[str, Any]], str]:
    rows = []
    for path in sorted((item for item in root.rglob("*") if item.is_file())):
        relative = path.relative_to(root)
        if set(relative.parts) & _IGNORED_PARTS or path.suffix.lower() in _IGNORED_SUFFIXES:
            continue
        raw = path.read_bytes()
        rows.append(
            {
                "path": relative.as_posix(),
                "size_bytes": len(raw),
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
            }
        )
    return rows, _digest(rows)


def _root(ctx: Any, project_id: str) -> Path:
    token = str(project_id or "").strip()
    if not _PROJECT_ID.fullmatch(token):
        raise ValueError("project_id contains unsupported characters")
    parent = Path(ctx.paths.dev_skills_dir()).resolve()
    candidate = (parent / token).resolve()
    if candidate.parent != parent:
        raise PermissionError("project path escapes the DEV skill root")
    if not candidate.is_dir():
        raise FileNotFoundError(f"DEV skill {token!r} is unavailable")
    return candidate


def validate_dev_skill(
    ctx: Any,
    project_id: str,
    *,
    strict: bool = True,
    probe_tools: bool = True,
    run_packaged_tests: bool = True,
) -> dict[str, Any]:
    root = _root(ctx, project_id)
    inventory, source_digest = _source_inventory(root)
    validation = SkillValidationService(ctx).validate_path(
        root,
        name=str(project_id),
        strict=bool(strict),
        probe_tools=bool(probe_tools),
    )
    test_rows: list[dict[str, Any]] = []
    log_digest = None
    log_ref = None
    if run_packaged_tests:
        manifest = yaml.safe_load((root / "skill.yaml").read_text(encoding="utf-8-sig")) or {}
        evidence_root = (
            Path(ctx.paths.state_dir()).resolve()
            / "developer_validation"
            / str(project_id)
            / source_digest.removeprefix("sha256:")
        )
        log_path = evidence_root / "tests.log"
        package_path = Path(ctx.paths.package_path()).resolve()
        results = run_tests(
            root,
            log_path=log_path,
            interpreter=Path(sys.executable),
            python_paths=[str(root), str(root.parent.parent), str(package_path)],
            skill_name=str(project_id),
            skill_version=str(manifest.get("version") or "dev"),
            slot_current_dir=root,
            dev_mode=True,
            extra_env={
                "ADAOS_DEV_DIR": str(root.parent.parent),
                "ADAOS_DEV_SKILL_DIR": str(root),
            },
        )
        test_rows = [asdict(item) for _, item in sorted(results.items())]
        if log_path.is_file():
            log_digest = "sha256:" + hashlib.sha256(log_path.read_bytes()).hexdigest()
            log_ref = f"developer-validation://skill/{project_id}/{source_digest.removeprefix('sha256:')}/tests"
    validation_issues = [asdict(item) for item in validation.issues]
    tests_ok = bool(test_rows) and all(item["status"] == "passed" for item in test_rows)
    identity = {
        "schema": "adaos.developer.project_validation.v1",
        "project_ref": f"skill:{project_id}",
        "source_digest": source_digest,
        "source_inventory": inventory,
        "validation": {"ok": bool(validation.ok), "issues": validation_issues},
        "tests": {
            "requested": bool(run_packaged_tests),
            "ok": tests_ok if run_packaged_tests else None,
            "results": test_rows,
            "log_ref": log_ref,
            "log_digest": log_digest,
        },
    }
    return {**identity, "ok": bool(validation.ok) and (tests_ok or not run_packaged_tests), "digest": _digest(identity)}


def inspect_dev_skill_source(ctx: Any, project_id: str) -> dict[str, Any]:
    """Return a bounded, content-addressed source snapshot for a trusted judge.

    The developer capability already authorizes deterministic validation of a
    disposable candidate.  Correctness judges also need to distinguish a real
    implementation from schema-shaped or self-reported evidence.  This API
    exposes only inspectable source text, never runtime data, parent folders,
    caches, or host paths, and binds every returned file to the same inventory
    digest used by native validation.
    """

    root = _root(ctx, project_id)
    inventory, source_digest = _source_inventory(root)
    by_path = {str(item["path"]): dict(item) for item in inventory}
    files: list[dict[str, Any]] = []
    total_bytes = 0
    omitted: list[dict[str, Any]] = []
    for relative, item in by_path.items():
        path = (root / relative).resolve()
        suffix = path.suffix.lower()
        size_bytes = int(item["size_bytes"])
        reason = None
        if suffix not in _INSPECTABLE_TEXT_SUFFIXES:
            reason = "non_text"
        elif size_bytes > _MAX_INSPECTION_FILE_BYTES:
            reason = "file_limit"
        elif total_bytes + size_bytes > _MAX_INSPECTION_TOTAL_BYTES:
            reason = "snapshot_limit"
        if reason:
            omitted.append({**item, "reason": reason})
            continue
        raw = path.read_bytes()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            omitted.append({**item, "reason": "invalid_utf8"})
            continue
        total_bytes += len(raw)
        files.append({**item, "text": text})
    identity = {
        "schema": "adaos.developer.source_snapshot.v1",
        "project_ref": f"skill:{project_id}",
        "source_digest": source_digest,
        "files": files,
        "omitted": omitted,
        "limits": {
            "max_file_bytes": _MAX_INSPECTION_FILE_BYTES,
            "max_total_bytes": _MAX_INSPECTION_TOTAL_BYTES,
            "observed_text_bytes": total_bytes,
        },
    }
    return {**identity, "digest": _digest(identity)}


def _manager(ctx: Any):
    from adaos.adapters.db import SqliteSkillRegistry
    from adaos.services.skill.manager import SkillManager

    return SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
    )


def activate_dev_skill(ctx: Any, project_id: str) -> dict[str, Any]:
    """Prepare and activate a validated DEV skill in its disposable DEV runtime."""

    root = _root(ctx, project_id)
    manifest = yaml.safe_load((root / "skill.yaml").read_text(encoding="utf-8-sig")) or {}
    version = str(manifest.get("version") or "dev")
    slot = _manager(ctx).activate_for_space(
        str(project_id),
        space="dev",
        version=version,
        webspace_id="developer-validation",
        defer_webspace_rebuild=True,
    )
    return {
        "ok": True,
        "project_ref": f"skill:{project_id}",
        "version": version,
        "slot": slot,
    }


def invoke_dev_skill(
    ctx: Any,
    project_id: str,
    operation_id: str,
    arguments: dict[str, Any],
    *,
    timeout: float | None = None,
) -> Any:
    """Invoke one exported DEV operation after explicit activation."""

    _root(ctx, project_id)
    return _manager(ctx).run_dev_tool(
        str(project_id),
        str(operation_id),
        dict(arguments),
        timeout=timeout,
    )


def execute_dev_spec(
    ctx: Any,
    project_id: str,
    value: dict[str, Any],
    *,
    idempotency_key: str,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Execute one candidate-produced smoke spec as non-hostile DEV trial evidence."""

    source_root = _root(ctx, project_id)
    key = str(idempotency_key or "").strip()
    if not _PROJECT_ID.fullmatch(key):
        raise ValueError("idempotency_key contains unsupported characters")
    spec = ExecutionSpec.from_dict(dict(value))
    if spec.owner_ref != f"skill:{project_id}":
        raise PermissionError("execution spec owner differs from the evaluated project")
    if spec.network.mode != "offline":
        raise ValueError("developer trial execution requires an offline-intent spec")
    if spec.resources.gpu_count:
        raise ValueError("developer trial execution does not admit GPU allocation")
    command = [str(item) for item in spec.command]
    if len(command) < 2 or Path(command[0]).resolve() != Path(sys.executable).resolve():
        raise ValueError("developer trial execution requires the active AdaOS Python interpreter")
    script = Path(command[1]).resolve()
    manager = _manager(ctx)
    status = manager.dev_runtime_status(str(project_id))
    manifest_path = Path(str(status.get("resolved_manifest") or "")).resolve()
    runtime_root = manifest_path.parent if manifest_path.is_file() else source_root
    runtime_bucket = str(status.get("runtime_bucket") or "").strip()
    if not runtime_bucket:
        raise RuntimeError("developer runtime has no compatibility bucket")
    data_root = (
        Path(ctx.paths.dev_skills_dir()).resolve()
        / ".runtime"
        / str(project_id)
        / runtime_bucket
        / "data"
    ).resolve()
    internal_data_root = (data_root / "internal").resolve()
    if not any(_under(script, root) for root in (source_root, runtime_root)):
        raise PermissionError("developer trial command is outside evaluated skill sources")
    if not script.is_file() or script.suffix.lower() != ".py":
        raise ValueError("developer trial command must reference a Python source file")
    identity = {"project_ref": f"skill:{project_id}", "spec_digest": spec.digest, "key": key}
    receipt_digest = _digest(identity)
    output_root = (
        Path(ctx.paths.state_dir()).resolve()
        / "developer_validation"
        / str(project_id)
        / "executions"
        / receipt_digest.removeprefix("sha256:")
    )
    receipt_path = output_root / "receipt.json"
    if receipt_path.is_file():
        restored = json.loads(receipt_path.read_text(encoding="utf-8-sig"))
        if restored.get("spec_digest") != spec.digest:
            raise ValueError("trial receipt is already bound to another execution spec")
        return restored
    output_root.mkdir(parents=True, exist_ok=False)
    stdout_path = output_root / "stdout.log"
    stderr_path = output_root / "stderr.log"
    declared_working_directory = Path(spec.working_directory).expanduser()
    if not declared_working_directory.is_absolute():
        declared_working_directory = runtime_root / declared_working_directory
    declared_working_directory = declared_working_directory.resolve()
    if not any(
        _under(declared_working_directory, root)
        for root in (source_root, runtime_root, data_root)
    ):
        raise PermissionError("developer trial working directory is outside evaluated skill scope")
    if not declared_working_directory.is_dir():
        raise FileNotFoundError("developer trial working directory is unavailable")
    maximum = min(
        float(timeout or spec.resources.wall_time_s or 300),
        float(spec.resources.wall_time_s or timeout or 300),
        3600.0,
    )
    environment = {
        key_name: value_text
        for key_name, value_text in os.environ.items()
        if key_name.upper()
        in {
            "PATH",
            "SYSTEMROOT",
            "WINDIR",
            "TEMP",
            "TMP",
            "USERPROFILE",
            "HOME",
            "LANG",
            "LC_ALL",
        }
    }
    environment.update(
        {
            str(key_name): str(value_text)
            for key_name, value_text in spec.environment.items()
        }
    )
    environment.update(
        {
            "PYTHONUTF8": "1",
            "PYTHONIOENCODING": "utf-8",
            "ADAOS_SKILL_NAME": str(project_id),
            "ADAOS_SKILL_ROOT": str(runtime_root),
            "ADAOS_SKILL_INTERNAL_DATA_ROOT": str(internal_data_root),
            "ADAOS_SKILL_INTERNAL_ACTIVE_PATH": str(internal_data_root),
            "ADAOS_SKILL_INTERNAL_TARGET_PATH": str(internal_data_root),
            "ADAOS_SKILL_ENV_PATH": str(data_root / "db" / "skill_env.json"),
            "PYTHONPATH": os.pathsep.join((str(runtime_root), str(ctx.paths.package_path()))),
        }
    )
    environment["ADAOS_EXECUTION_NETWORK_MODE"] = spec.network.mode
    environment["ADAOS_EXECUTION_SPEC_DIGEST"] = spec.digest
    started_monotonic = time.monotonic()
    process: subprocess.Popen[Any] | None = None
    timed_out = False
    try:
        with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
            process = subprocess.Popen(  # noqa: S603 - immutable candidate command is contract-checked above
                command,
                cwd=str(declared_working_directory),
                env=environment,
                stdout=stdout,
                stderr=stderr,
                creationflags=(
                    int(getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))
                    if os.name == "nt"
                    else 0
                ),
                start_new_session=(os.name != "nt"),
            )
            try:
                exit_code = int(process.wait(timeout=maximum))
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(process)
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    _terminate_process_tree(process)
                exit_code = None
        status_name = "failed" if timed_out or exit_code != 0 else "succeeded"
        failure = (
            "wall_time_exceeded"
            if timed_out
            else None
            if exit_code == 0
            else "process_exit_nonzero"
        )
    except Exception:
        if process is not None and process.poll() is None:
            _terminate_process_tree(process)
        raise
    elapsed_seconds = round(max(0.0, time.monotonic() - started_monotonic), 6)
    outputs = []
    documents: dict[str, Any] = {}
    output_paths: list[Path] = []
    for expected in spec.expected_outputs:
        path = (declared_working_directory / Path(expected)).resolve()
        if not _under(path, declared_working_directory):
            raise PermissionError("developer trial expected output escapes its working directory")
        if path.is_file():
            output_paths.append(path)
    for path in sorted(output_paths):
        relative = path.relative_to(declared_working_directory).as_posix()
        raw = path.read_bytes()
        outputs.append(
            {
                "path": relative,
                "size_bytes": len(raw),
                "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
                "ref": f"developer-validation://skill/{project_id}/execution/{receipt_digest.removeprefix('sha256:')}/{relative}",
            }
        )
        if len(raw) <= 1_048_576 and path.suffix.lower() == ".json":
            try:
                documents[relative] = json.loads(raw.decode("utf-8-sig"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                pass
        elif len(raw) <= 131_072 and path.suffix.lower() in {".md", ".txt", ".jsonl"}:
            documents[relative] = raw.decode("utf-8", errors="replace")
    missing = sorted(set(spec.expected_outputs) - {item["path"] for item in outputs})
    receipt_identity = {
        "schema": "adaos.developer.trial_execution.v1",
        "project_ref": f"skill:{project_id}",
        "spec_digest": spec.digest,
        "status": status_name,
        "exit_code": exit_code,
        "failure": failure,
        "provider": {
            "id": "developer.local_process",
            "hostile_isolation": False,
            "network_intent": spec.network.mode,
            "network_enforced": False,
            "process_tree_isolated": True,
            "process_tree_terminated": bool(timed_out),
        },
        "limits": {
            "wall_time_seconds": maximum,
            "elapsed_seconds": elapsed_seconds,
            "wall_time_exceeded": bool(timed_out),
        },
        "expected_outputs": list(spec.expected_outputs),
        "missing_outputs": missing,
        "outputs": outputs,
        "documents": documents,
    }
    receipt = {**receipt_identity, "ok": status_name == "succeeded" and not missing}
    receipt["digest"] = _digest(receipt_identity)
    receipt_path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return receipt


def _terminate_process_tree(process: subprocess.Popen[Any]) -> None:
    """Terminate only the process family created for one DEV execution.

    ``subprocess.run(..., timeout=...)`` kills only the immediate process on
    Windows.  Scientific runners commonly spawn Python/DataLoader children, so
    that behaviour can leave an unowned workload consuming the node after the
    evaluator has returned.  Creation-time scoped psutil handles avoid killing
    an unrelated process after PID reuse.
    """

    try:
        root = psutil.Process(process.pid)
        expected_create_time = root.create_time()
        children = root.children(recursive=True)
    except (psutil.Error, OSError):
        root = None
        expected_create_time = None
        children = []
    for child in reversed(children):
        try:
            child.kill()
        except (psutil.Error, OSError):
            pass
    if root is not None:
        try:
            if expected_create_time is None or abs(root.create_time() - expected_create_time) < 0.01:
                root.kill()
        except (psutil.Error, OSError):
            pass
    elif process.poll() is None:
        try:
            process.kill()
        except OSError:
            pass


def _under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


__all__ = [
    "activate_dev_skill",
    "execute_dev_spec",
    "inspect_dev_skill_source",
    "invoke_dev_skill",
    "validate_dev_skill",
]
