"""Deterministic, source-bound validation for one DEV project.

This service is deliberately independent from Builder and from candidate-owned
test assertions.  It provides a stable evidence receipt while reusing the same
native skill validator and isolated test runner as the AdaOS CLI.
"""

from __future__ import annotations

import hashlib
import json
import re
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Any

import yaml

from adaos.services.skill.tests_runner import run_tests
from adaos.services.skill.validation import SkillValidationService


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


__all__ = ["activate_dev_skill", "invoke_dev_skill", "validate_dev_skill"]
