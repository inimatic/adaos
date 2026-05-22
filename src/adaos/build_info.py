"""Utilities for exposing AdaOS build metadata.

The project keeps the core base version in :mod:`pyproject.toml`.  CI may bump
that patch version, while local and slot runtimes append a Git-history build
suffix when the checkout still has VCS metadata.  Values can be overridden by
environment variables for packaged builds.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import importlib.metadata
import os
from pathlib import Path
import subprocess
import tomllib
from typing import Final


_DEFAULT_BASE_VERSION: Final[str] = "0.1.0"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _git(*args: str) -> str | None:
    try:
        completed = subprocess.run(
            ("git", *args),
            cwd=_repo_root(),
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        return completed.stdout.strip()
    except Exception:
        return None


def _pyproject_version(repo_root: Path) -> str | None:
    pyproject_path = repo_root / "pyproject.toml"
    try:
        payload = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    project = payload.get("project") if isinstance(payload, dict) else None
    if not isinstance(project, dict):
        return None
    version = str(project.get("version") or "").strip()
    return version or None


def _installed_distribution_version() -> str | None:
    try:
        return str(importlib.metadata.version("adaos") or "").strip() or None
    except importlib.metadata.PackageNotFoundError:
        return None
    except Exception:
        return None


def base_version(repo_root: Path | str | None = None) -> str:
    explicit = str(os.getenv("ADAOS_BASE_VERSION") or "").strip()
    if explicit:
        return explicit
    root = Path(repo_root).expanduser().resolve() if repo_root is not None else _repo_root()
    return _pyproject_version(root) or _installed_distribution_version() or _DEFAULT_BASE_VERSION


def _compute_version() -> str:
    explicit = os.getenv("ADAOS_BUILD_VERSION")
    if explicit:
        return explicit
    base = base_version()

    rev_count = _git("rev-list", "--count", "HEAD")
    short_sha = _git("rev-parse", "--short", "HEAD")
    if rev_count:
        suffix = f"+{rev_count}"
        if short_sha:
            suffix += f".{short_sha}"
        return f"{base}{suffix}"

    return base


def _compute_build_date() -> str:
    explicit = os.getenv("ADAOS_BUILD_DATE")
    if explicit:
        return explicit

    commit_ts = _git("show", "-s", "--format=%cI", "HEAD")
    if commit_ts:
        return commit_ts

    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True, slots=True)
class BuildInfo:
    version: str
    build_date: str


def _load_build_info() -> BuildInfo:
    return BuildInfo(version=_compute_version(), build_date=_compute_build_date())


BUILD_INFO: Final[BuildInfo] = _load_build_info()

