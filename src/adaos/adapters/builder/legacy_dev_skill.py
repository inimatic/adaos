"""Compatibility execution port for the transitional DEV Builder skill."""

from __future__ import annotations

from collections.abc import Mapping
from threading import RLock
from typing import Any


class LegacyDevSkillPrototypeExecution:
    adapter_id = "legacy_dev_skill.v1"

    def __init__(self) -> None:
        self._skill_manager: Any = None
        self._manager_context_key: tuple[str, str] | None = None
        self._manager_lock = RLock()

    def _manager(self) -> Any:
        from adaos.services.agent_context import get_ctx
        ctx = get_ctx()
        key = (str(ctx.paths.skills_dir()), str(ctx.paths.state_dir()))
        with self._manager_lock:
            return self._manager_for_context(ctx, key)

    def _manager_for_context(self, ctx: Any, key: tuple[str, str]) -> Any:
        if self._skill_manager is not None and self._manager_context_key is None:
            # Preserve explicit test/embedding injection, binding it to only
            # its first context instead of reusing it across Trial/Stable.
            self._manager_context_key = key
        if self._skill_manager is None or self._manager_context_key != key:
            from adaos.adapters.db import SqliteSkillRegistry
            from adaos.services.skill.manager import SkillManager

            self._skill_manager = SkillManager(
                repo=ctx.skills_repo,
                registry=SqliteSkillRegistry(ctx.sql),
                git=ctx.git,
                paths=ctx.paths,
                bus=getattr(ctx, "bus", None),
                caps=ctx.caps,
                settings=ctx.settings,
            )
            self._manager_context_key = key
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


class WorkspaceSkillPrototypeExecution(LegacyDevSkillPrototypeExecution):
    """Execute the admitted opt-in route in the active Workspace package.

    The separate legacy DEV slot is not an attestation of the installed Builder.
    No fallback to another package is permitted after an installed-route failure.
    """
    adapter_id = "workspace_skill.v1"

    def submit_turn(self, payload: Mapping[str, Any], *, timeout_seconds: float) -> Mapping[str, Any]:
        return self._manager().run_tool("builder_skill", "chat", dict(payload), timeout=timeout_seconds)

    def get_session(self, session_id: str, *, webspace_id: str, timeout_seconds: float) -> Mapping[str, Any]:
        return self._manager().run_tool("builder_skill", "get_session",
            {"session_id": session_id, "webspace_id": webspace_id}, timeout=timeout_seconds)


class PersonalChatGPTPrototypeExecution(WorkspaceSkillPrototypeExecution):
    """Exact opt-in endpoint; an older Builder must fail, never ignore selection."""
    adapter_id = 'personal_chatgpt_prototype.v1'

    def submit_turn(self, payload: Mapping[str, Any], *, timeout_seconds: float) -> Mapping[str, Any]:
        return self._manager().run_tool('builder_skill', 'prototype_chatgpt_edit',
            dict(payload), timeout=timeout_seconds)


__all__ = ['LegacyDevSkillPrototypeExecution', 'WorkspaceSkillPrototypeExecution',
           'PersonalChatGPTPrototypeExecution']
