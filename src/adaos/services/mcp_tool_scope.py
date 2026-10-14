"""One effective HTTP MCP scope for executor configuration and model facts.

An absent allowlist is unbounded, not empty. Never infer callable tools from
that configuration without discovery; an explicit empty allowlist denies all.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping


def _names(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


@dataclass(frozen=True)
class EffectiveMcpToolScope:
    enabled: bool
    tools: tuple[str, ...] | None
    disabled_tools: tuple[str, ...]


def effective_mcp_tool_scope(profile: Mapping[str, Any] | None) -> EffectiveMcpToolScope:
    value = profile or {}
    enabled = bool(value) and value.get('enabled') is not False and bool(str(value.get('url') or '').strip())
    enabled = enabled and str(value.get('transport') or 'streamable_http').strip() in {'http', 'streamable_http'}
    denied = _names(value.get('disabled_tools'))
    if not enabled:
        return EffectiveMcpToolScope(False, (), denied)
    if 'enabled_tools' not in value:
        return EffectiveMcpToolScope(True, None, denied)
    if not isinstance(value['enabled_tools'], (list, tuple, str)):
        return EffectiveMcpToolScope(True, (), denied)
    blocked = set(denied)
    return EffectiveMcpToolScope(True, tuple(name for name in _names(value['enabled_tools']) if name not in blocked), denied)
