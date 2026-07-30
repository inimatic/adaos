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
import json
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


def _read_json_object(path: Path) -> dict | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _active_slot_manifest() -> dict | None:
    candidates: list[Path] = []
    slot_dir = str(os.getenv("ADAOS_ACTIVE_CORE_SLOT_DIR") or "").strip()
    if slot_dir:
        candidates.append(Path(slot_dir).expanduser() / "manifest.json")
    slot_repo = str(os.getenv("ADAOS_SLOT_REPO_ROOT") or "").strip()
    if slot_repo:
        repo_path = Path(slot_repo).expanduser()
        if repo_path.name == "repo":
            candidates.append(repo_path.parent / "manifest.json")
    base_dir = str(os.getenv("ADAOS_BASE_DIR") or "").strip()
    active_slot = str(os.getenv("ADAOS_ACTIVE_CORE_SLOT") or "").strip().upper()
    if base_dir and active_slot in {"A", "B"}:
        candidates.append(Path(base_dir).expanduser() / "state" / "core_slots" / "slots" / active_slot / "manifest.json")

    for candidate in candidates:
        payload = _read_json_object(candidate)
        if payload:
            return payload
    return None


def base_version(repo_root: Path | str | None = None) -> str:
    root = Path(repo_root).expanduser().resolve() if repo_root is not None else _repo_root()
    pyproject_version = _pyproject_version(root)
    if pyproject_version:
        return pyproject_version
    # A checkout or core slot with pyproject.toml owns its build identity.
    # ADAOS_BASE_VERSION may survive in a shared operational dotenv for years;
    # only use it for packaged layouts that do not carry source metadata.
    explicit = str(os.getenv("ADAOS_BASE_VERSION") or "").strip()
    if explicit:
        return explicit
    if repo_root is None:
        manifest = _active_slot_manifest()
        if manifest:
            manifest_version = str(manifest.get("base_version") or "").strip()
            if manifest_version:
                return manifest_version
    return _installed_distribution_version() or _DEFAULT_BASE_VERSION


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

    manifest = _active_slot_manifest()
    if manifest:
        manifest_version = str(manifest.get("build_version") or "").strip()
        if manifest_version:
            return manifest_version

    return base


def _compute_build_date() -> str:
    explicit = os.getenv("ADAOS_BUILD_DATE")
    if explicit:
        return explicit

    commit_ts = _git("show", "-s", "--format=%cI", "HEAD")
    if commit_ts:
        return commit_ts

    manifest = _active_slot_manifest()
    if manifest:
        manifest_date = str(manifest.get("build_date") or "").strip()
        if manifest_date:
            return manifest_date

    return datetime.now(tz=timezone.utc).isoformat()


def _compute_git_commit() -> str:
    explicit = str(os.getenv("ADAOS_GIT_COMMIT") or "").strip()
    if explicit:
        return explicit

    commit = str(_git("rev-parse", "HEAD") or "").strip()
    if commit:
        return commit

    manifest = _active_slot_manifest()
    if manifest:
        manifest_commit = str(manifest.get("git_commit") or "").strip()
        if manifest_commit:
            return manifest_commit
    return ""


@dataclass(frozen=True, slots=True)
class BuildInfo:
    version: str
    build_date: str
    git_commit: str = ""


def _load_build_info() -> BuildInfo:
    return BuildInfo(
        version=_compute_version(),
        build_date=_compute_build_date(),
        git_commit=_compute_git_commit(),
    )


BUILD_INFO: Final[BuildInfo] = _load_build_info()

