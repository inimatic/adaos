from __future__ import annotations

from adaos.adapters.db import sqlite as skill_db


class SkillService:
    """
    Minimal skill service facade used by ScenarioService.

    Stage A2 / Part 1 only needs an `install(name)` hook; real behaviour
    (download, compile, etc.) can be wired later. For now we mark the skill
    as installed in the sqlite registry on a best-effort basis.
    """

    async def install(self, skill_name: str) -> None:
        try:
            # Mark skill as installed in the registry.
            skill_db.add_or_update_entity("skills", name=skill_name)
        except Exception:
            # Dev-only path; ignore failures for now.
            pass


__all__ = ["SkillService"]

