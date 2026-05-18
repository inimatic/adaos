from __future__ import annotations

from pathlib import Path
from typing import Iterable


class MissingLocalDependencyError(RuntimeError):
    """Raised when a manifest points at a local module that is not checked out."""


_EDITABLE_OPTIONS = {"-e", "--editable"}


def resolve_skill_dependency_args(
    dependencies: Iterable[str],
    *,
    skill_dir: Path,
    repo_root: Path | None = None,
) -> list[str]:
    """Return pip-installable args after validating local module dependencies.

    Skill manifests historically use ``dependencies`` for pip arguments, but a
    skill can also need a repo-local integration module such as
    ``src/adaos/integrations/adaos-backend``. Those local module paths should be
    checked for presence without asking pip to install a non-Python submodule.
    """

    resolved: list[str] = []
    for raw in dependencies:
        value = str(raw or "").strip()
        if not value:
            continue
        local = _resolve_local_dependency(value, skill_dir=skill_dir, repo_root=repo_root)
        if local is None:
            resolved.append(value)
            continue
        path, suffix = local
        if _is_python_installable(path):
            resolved.append(f"{path}{suffix}")
        elif resolved and resolved[-1] in _EDITABLE_OPTIONS:
            resolved.pop()
    return resolved


def _resolve_local_dependency(
    value: str,
    *,
    skill_dir: Path,
    repo_root: Path | None,
) -> tuple[Path, str] | None:
    path_part, suffix = _split_path_suffix(value)
    if not _looks_like_local_path(path_part):
        return None

    candidates = _local_path_candidates(path_part, skill_dir=skill_dir, repo_root=repo_root)
    for candidate in candidates:
        if _is_materialized(candidate):
            return candidate, suffix

    raise MissingLocalDependencyError(_missing_dependency_message(value, candidates=candidates, repo_root=repo_root))


def _split_path_suffix(value: str) -> tuple[str, str]:
    if value.endswith("]"):
        marker = value.rfind("[")
        if marker > 0 and "://" not in value[:marker]:
            return value[:marker], value[marker:]
    return value, ""


def _looks_like_local_path(value: str) -> bool:
    raw = str(value or "").strip()
    if not raw or raw.startswith("-"):
        return False
    lowered = raw.lower()
    if " @ " in raw or "://" in raw:
        return False
    if lowered.startswith(("git+", "http:", "https:", "ssh:", "file:")):
        return False
    if raw in {".", ".."} or raw.startswith((".", "~")):
        return True
    if Path(raw).is_absolute():
        return True
    return "/" in raw or "\\" in raw


def _local_path_candidates(value: str, *, skill_dir: Path, repo_root: Path | None) -> list[Path]:
    raw = Path(value).expanduser()
    if raw.is_absolute():
        return [raw.resolve()]

    candidates = [(skill_dir / raw).resolve()]
    if repo_root is not None:
        repo_candidate = (repo_root / raw).resolve()
        if repo_candidate not in candidates:
            candidates.append(repo_candidate)
    return candidates


def _is_materialized(path: Path) -> bool:
    if not path.exists():
        return False
    if path.is_file():
        return True
    try:
        next(path.iterdir())
    except StopIteration:
        return False
    except Exception:
        return True
    return True


def _is_python_installable(path: Path) -> bool:
    if path.is_file():
        name = path.name.lower()
        return name.endswith((".whl", ".zip", ".tar.gz", ".tgz"))
    return any((path / marker).exists() for marker in ("pyproject.toml", "setup.py", "setup.cfg"))


def _missing_dependency_message(value: str, *, candidates: list[Path], repo_root: Path | None) -> str:
    normalized = _normalize_relpath(value)
    submodule = _matching_submodule(repo_root, normalized) if repo_root is not None else None
    searched = ", ".join(str(path) for path in candidates)
    if submodule:
        return (
            f"required local dependency is missing: {value}. "
            f"It is declared as git submodule '{submodule}'. "
            f"Run: git submodule update --init --recursive {submodule}. "
            f"Searched: {searched}"
        )
    return f"required local dependency is missing: {value}. Searched: {searched}"


def _matching_submodule(repo_root: Path | None, dependency: str) -> str | None:
    if repo_root is None:
        return None
    gitmodules = repo_root / ".gitmodules"
    try:
        text = gitmodules.read_text(encoding="utf-8")
    except Exception:
        return None

    for line in text.splitlines():
        key, sep, raw_value = line.partition("=")
        if not sep or key.strip().lower() != "path":
            continue
        submodule = _normalize_relpath(raw_value)
        if dependency == submodule or dependency.startswith(f"{submodule}/"):
            return submodule
    return None


def _normalize_relpath(value: str) -> str:
    raw, _suffix = _split_path_suffix(str(value or "").strip())
    return raw.replace("\\", "/").strip().strip("/")
