"""Compatibility execution port for the transitional DEV Builder skill."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


class LegacyDevSkillPrototypeExecution:
    adapter_id = "legacy_dev_skill.v1"

    def __init__(self) -> None:
        self._skill_manager: Any = None

    def _manager(self) -> Any:
        if self._skill_manager is None:
            from adaos.adapters.db import SqliteSkillRegistry
            from adaos.services.agent_context import get_ctx
            from adaos.services.skill.manager import SkillManager

            ctx = get_ctx()
            self._skill_manager = SkillManager(
                repo=ctx.skills_repo,
                registry=SqliteSkillRegistry(ctx.sql),
                git=ctx.git,
                paths=ctx.paths,
                bus=getattr(ctx, "bus", None),
                caps=ctx.caps,
                settings=ctx.settings,
            )
        return self._skill_manager

    def submit_turn(
        self, payload: Mapping[str, Any], *, timeout_seconds: float
    ) -> Mapping[str, Any]:
        return self._manager().run_dev_tool(
            "builder_skill",
            "chat",
            dict(payload),
            timeout=timeout_seconds,
        )

    def get_session(
        self,
        session_id: str,
        *,
        webspace_id: str,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        return self._manager().run_dev_tool(
            "builder_skill",
            "get_session",
            {"session_id": session_id, "webspace_id": webspace_id},
            timeout=timeout_seconds,
        )


__all__ = ["LegacyDevSkillPrototypeExecution"]
