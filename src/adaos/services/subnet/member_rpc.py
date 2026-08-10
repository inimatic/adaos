from __future__ import annotations

from typing import Any, Mapping

from adaos.adapters.db import SqliteSkillRegistry
from adaos.services.agent_context import get_ctx
from adaos.services.skill.manager import SkillManager


MEMBER_RPC_ALLOWED_TOOLS = frozenset(
    {
        "conversation_companions:list_characters",
        "conversation_companions:start",
        "conversation_companions:switch_character",
        "conversation_companions:talk",
        "conversation_companions:update_profile",
        "adaos_connect:prepare",
    }
)


def run_member_tool(
    *,
    node_id: str,
    tool: str,
    arguments: Mapping[str, Any] | None,
    timeout: float | None,
) -> Any:
    """Run a narrowly allowlisted Hub skill for an authenticated member node."""

    normalized_tool = str(tool or "").strip()
    if normalized_tool not in MEMBER_RPC_ALLOWED_TOOLS:
        raise PermissionError("member_rpc_tool_not_allowed")
    skill_name, public_tool = normalized_tool.split(":", 1)
    payload = dict(arguments or {})
    payload.setdefault("webspace_id", "desktop")
    meta = dict(payload.get("_meta") or {})
    meta.update(
        {
            "subnet_origin_node_id": str(node_id),
            "node_id": str(node_id),
            "runtime_profile": "android",
            "member_rpc": True,
        }
    )
    payload["_meta"] = meta
    bounded_timeout = min(50.0, max(5.0, float(timeout or 40.0)))
    ctx = get_ctx()
    manager = SkillManager(
        repo=ctx.skills_repo,
        registry=SqliteSkillRegistry(ctx.sql),
        git=ctx.git,
        paths=ctx.paths,
        bus=getattr(ctx, "bus", None),
        caps=ctx.caps,
        settings=ctx.settings,
    )
    return manager.run_tool(
        skill_name,
        public_tool,
        payload,
        timeout=bounded_timeout,
    )


__all__ = ["MEMBER_RPC_ALLOWED_TOOLS", "run_member_tool"]
