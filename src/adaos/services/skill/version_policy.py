from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Literal


BumpKind = Literal["major", "minor", "patch"]

_BUMP_INDEX: dict[BumpKind, int] = {"major": 0, "minor": 1, "patch": 2}


def bump_index(kind: BumpKind) -> int:
    return _BUMP_INDEX[kind]


def skill_manifest_has_data_migration(manifest: Mapping[str, Any]) -> bool:
    data_migration = manifest.get("data_migration")
    if isinstance(data_migration, Mapping):
        for key in ("tool", "script", "entry"):
            value = data_migration.get(key)
            if isinstance(value, str) and value.strip():
                return True
    for key in ("data_migration_tool", "data_migration_script"):
        value = manifest.get(key)
        if isinstance(value, str) and value.strip():
            return True
    return False


def effective_skill_bump(
    manifest: Mapping[str, Any],
    requested: BumpKind = "patch",
) -> BumpKind:
    if requested == "patch" and skill_manifest_has_data_migration(manifest):
        return "minor"
    return requested


__all__ = [
    "BumpKind",
    "bump_index",
    "effective_skill_bump",
    "skill_manifest_has_data_migration",
]
