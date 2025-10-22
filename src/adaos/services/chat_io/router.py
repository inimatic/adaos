from __future__ import annotations

"""Routing helpers for chat IO (placeholder).

- Resolve hub_id by binding or route rules (by locale), with a default.
"""

from typing import Optional


def resolve_hub_id(*, platform: str, user_id: str, bot_id: str, locale: Optional[str]) -> Optional[str]:
    # TODO: lookup binding in chat_bindings; else route_rules.yaml by locale; else default_hub
    return None
