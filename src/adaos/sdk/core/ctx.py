"""SDK facade for accessing the current AgentContext.

Skills and scenarios should import context helpers from the SDK instead of
reaching into ``adaos.services`` directly.
"""

from __future__ import annotations

from adaos.services.agent_context import AgentContext, clear_ctx, get_ctx, set_ctx

__all__ = ["AgentContext", "get_ctx", "set_ctx", "clear_ctx"]

