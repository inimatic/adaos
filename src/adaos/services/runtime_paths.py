from __future__ import annotations

import os
import sys
from pathlib import Path


def _ctx_path(attr: str) -> Path | None:
    try:
        from adaos.services.agent_context import get_ctx

        ctx = get_ctx()
        getter = getattr(ctx.paths, attr)
        raw = getter() if callable(getter) else getter
        return Path(raw).expanduser().resolve()
    except Exception:
        return None


def _resolve_path(raw: str | os.PathLike[str] | None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        return Path(text).expanduser().resolve()
    except Exception:
        return None


def _absolute_path(raw: str | os.PathLike[str] | None) -> Path | None:
    text = str(raw or "").strip()
    if not text:
        return None
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        return path.absolute()
    except Exception:
        return None


def _looks_like_project_root(path: Path | None) -> bool:
    if path is None:
        return False
    try:
        return bool((path / ".git").exists() or (path / "pyproject.toml").exists() or (path / "src" / "adaos").exists())
    except Exception:
        return False


def is_core_slot_path(path: Path | str | None, *, base_dir: Path | None = None) -> bool:
    candidate = _absolute_path(path)
    if candidate is None:
        return False
    try:
        resolved_base = (base_dir or current_base_dir()).expanduser().absolute()
        slots_root = (resolved_base / "state" / "core_slots" / "slots").absolute()
    except Exception:
        return False
    return candidate == slots_root or slots_root in candidate.parents


def shared_dotenv_project_root(dotenv_path: Path | str | None = None) -> Path | None:
    dotenv = _resolve_path(dotenv_path or os.getenv("ADAOS_SHARED_DOTENV_PATH"))
    if dotenv is None or not dotenv.exists():
        return None
    candidate = dotenv.parent
    return candidate if _looks_like_project_root(candidate) else None


def current_control_repo_root(
    *,
    shared_dotenv_path: Path | str | None = None,
    context_repo_root: Path | str | None = None,
    context_package_path: Path | str | None = None,
) -> Path | None:
    explicit = _resolve_path(os.getenv("ADAOS_CONTROL_REPO_ROOT"))
    if explicit is not None:
        return explicit

    env_root = _resolve_path(os.getenv("ADAOS_ROOT_REPO_ROOT") or os.getenv("ADAOS_REPO_ROOT"))
    if env_root is not None and not is_core_slot_path(env_root):
        return env_root

    shared_root = shared_dotenv_project_root(shared_dotenv_path)
    if shared_root is not None:
        return shared_root

    repo_root = _resolve_path(context_repo_root) if context_repo_root is not None else _ctx_path("repo_root")
    if repo_root is not None and not is_core_slot_path(repo_root):
        return repo_root

    package_dir = (
        _resolve_path(context_package_path) if context_package_path is not None else _ctx_path("package_path")
    )
    if package_dir is not None:
        try:
            package_root = package_dir.parents[1].resolve()
        except Exception:
            package_root = None
        if package_root is not None and not is_core_slot_path(package_root):
            return package_root

    return env_root or repo_root


def current_control_python(repo_root: Path | str | None = None) -> Path:
    explicit = _resolve_path(os.getenv("ADAOS_CONTROL_PYTHON") or os.getenv("ADAOS_ROOT_PYTHON"))
    if explicit is not None:
        return explicit
    root = _resolve_path(repo_root) or current_control_repo_root()
    if root is not None:
        candidates = [
            root / ".venv" / "Scripts" / "python.exe",
            root / ".venv" / "bin" / "python",
            root / ".venv" / "bin" / "python3",
        ]
        for candidate in candidates:
            try:
                if candidate.exists():
                    return candidate.absolute()
            except Exception:
                continue
    return Path(sys.executable).expanduser().resolve()


def current_base_dir() -> Path:
    base_dir = _ctx_path("base_dir")
    if base_dir is not None:
        return base_dir
    raw = str(os.getenv("ADAOS_BASE_DIR") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    try:
        from adaos.services.settings import Settings

        return Path(Settings.from_sources().base_dir).expanduser().resolve()
    except Exception:
        return (Path.home() / ".adaos").resolve()


def current_state_dir() -> Path:
    state_dir = _ctx_path("state_dir")
    if state_dir is not None:
        return state_dir
    return (current_base_dir() / "state").resolve()


def current_logs_dir() -> Path:
    logs_dir = _ctx_path("logs_dir")
    if logs_dir is not None:
        return logs_dir
    return (current_base_dir() / "logs").resolve()


def current_repo_root() -> Path | None:
    return current_control_repo_root()
