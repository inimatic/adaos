"""SDK facade for accessing the current AgentContext.

Skills and scenarios should import context helpers from the SDK instead of
reaching into ``adaos.services`` directly.
"""

from __future__ import annotations

from adaos.services.agent_context import AgentContext, clear_ctx, get_ctx, set_ctx
from adaos.sdk.core._ctx import require_ctx

__all__ = ["AgentContext", "clear_ctx", "get_ctx", "require_ctx", "set_ctx"]

