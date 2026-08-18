"""Opaque runtime identity for reproducible skill operations."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any

import yaml

from adaos.build_info import BUILD_INFO
from adaos.sdk.core._ctx import require_ctx


def _source_tree_identity() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[4]
    if not (root / ".git").exists():
        return {"kind": "installed", "clean": None, "tracked_diff_digest": None}
    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=no"],
            cwd=root,
            check=True,
            capture_output=True,
            text=False,
            timeout=10,
        ).stdout
        diff = subprocess.run(
            ["git", "diff", "--no-ext-diff", "--binary", "HEAD"],
            cwd=root,
            check=True,
            capture_output=True,
            text=False,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return {"kind": "git", "clean": None, "tracked_diff_digest": None}
    return {
        "kind": "git",
        "clean": not bool(status.strip()),
        "tracked_diff_digest": "sha256:" + hashlib.sha256(diff).hexdigest(),
    }


def _current_skill_identity(ctx: Any) -> dict[str, str] | None:
    current = ctx.skill_ctx.get()
    if current is None:
        return None
    manifest_path = Path(current.path) / "skill.yaml"
    version = ""
    digest = ""
    if manifest_path.is_file():
        content = manifest_path.read_bytes()
        digest = "sha256:" + hashlib.sha256(content).hexdigest()
        value = yaml.safe_load(content.decode("utf-8-sig")) or {}
        if isinstance(value, dict):
            version = str(value.get("version") or "").strip()
    return {
        "name": str(current.name),
        "version": version,
        "manifest_digest": digest,
    }


def runtime_identity() -> dict[str, Any]:
    """Return stable runtime facts without exposing host filesystem locations."""

    ctx = require_ctx("sdk.core.environment.runtime_identity")
    return {
        "schema": "adaos.runtime.identity.v1",
        "core": {
            "version": str(BUILD_INFO.version or ""),
            "git_commit": str(BUILD_INFO.git_commit or ""),
            "build_date": str(BUILD_INFO.build_date or ""),
            "source_tree": _source_tree_identity(),
        },
        "python_version": platform.python_version(),
        "platform": platform.platform(),
        "implementation": sys.implementation.name,
        "current_skill": _current_skill_identity(ctx),
    }


__all__ = ["runtime_identity"]
