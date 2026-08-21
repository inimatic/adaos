"""Canonical resolution of skill-owned runtime data paths."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.domain.relational_storage import RelationalStorageIsolationError
from adaos.services.skill.runtime_env import SkillRuntimeEnvironment


def _path_from_provider(paths: Any, name: str) -> Path | None:
    getter = getattr(paths, name, None)
    if getter is None:
        return None
    try:
        return Path(getter() if callable(getter) else getter).expanduser().resolve()
    except Exception:
        return None


def _under(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _runtime_bucket_from_path(path: Path, skill_name: str) -> Path | None:
    parts = path.parts
    indexes = [index for index, part in enumerate(parts) if part == ".runtime"]
    for index in reversed(indexes):
        if len(parts) <= index + 2:
            continue
        path_skill = str(parts[index + 1])
        bucket = str(parts[index + 2])
        if path_skill != skill_name:
            raise RelationalStorageIsolationError(
                f"active skill {skill_name!r} cannot use runtime data owned by {path_skill!r}"
            )
        if bucket.startswith("v") and "." in bucket:
            return Path(*parts[: index + 3]).resolve()
    return None


def resolve_skill_data_root(ctx: Any, current_skill: Any) -> Path:
    """Return the active compatibility-bucket data root for ``current_skill``.

    The caller supplies the context-owned ``CurrentSkill`` object. A skill
    never supplies another skill id or a physical target path through the SDK.
    """

    skill_name = str(getattr(current_skill, "name", "") or "").strip()
    raw_path = getattr(current_skill, "path", None)
    if not skill_name or raw_path is None:
        raise RuntimeError("current skill identity and path are required")
    skill_path = Path(raw_path).expanduser().resolve()
    direct_bucket = _runtime_bucket_from_path(skill_path, skill_name)
    if direct_bucket is not None:
        data_root = direct_bucket / "data"
        data_root.mkdir(parents=True, exist_ok=True)
        return data_root

    workspace_root = _path_from_provider(ctx.paths, "skills_dir")
    dev_root = _path_from_provider(ctx.paths, "dev_skills_dir")
    source_root = dev_root if _under(skill_path, dev_root) else workspace_root
    if source_root is None:
        raise RuntimeError("skill runtime root is unavailable")

    env = SkillRuntimeEnvironment(skills_root=source_root, skill_name=skill_name)
    version = env.resolve_active_version() or "0.0.0"
    env.ensure_data_dirs(version)
    return env.data_root(version)


def resolve_installed_skill_data_root(ctx: Any, skill_name: str) -> Path:
    """Resolve another installed skill's data root for trusted core brokering.

    Unlike :func:`resolve_skill_data_root`, this helper is not exposed through
    the skill SDK.  A service must first admit a typed owner delegation before
    using the returned physical path.
    """

    name = str(skill_name or "").strip()
    if not name or any(character not in "abcdefghijklmnopqrstuvwxyz0123456789_.-" for character in name.lower()):
        raise RuntimeError("installed skill identity is invalid")
    skills_root = _path_from_provider(ctx.paths, "skills_dir")
    if skills_root is None:
        raise RuntimeError("installed skill runtime root is unavailable")
    env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=name)
    version = env.resolve_active_version()
    if not version:
        raise RuntimeError(f"skill {name!r} has no active installed runtime")
    env.ensure_data_dirs(version)
    return env.data_root(version)


__all__ = ["resolve_installed_skill_data_root", "resolve_skill_data_root"]
