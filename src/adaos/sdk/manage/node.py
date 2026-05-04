"""Node-level management tools exposed via the AdaOS SDK."""

from __future__ import annotations

from typing import Any, Mapping

from adaos.services.node_config import normalize_node_names, set_node_names as _save_node_names
from adaos.sdk.core.decorators import tool

__all__ = ["set_node_names"]


_SET_NODE_NAMES_INPUT = {
    "type": "object",
    "properties": {
        "node_names": {
            "oneOf": [
                {"type": "string"},
                {"type": "array", "items": {"type": "string"}},
            ]
        }
    },
    "required": ["node_names"],
    "additionalProperties": True,
}

_SET_NODE_NAMES_OUTPUT = {
    "type": "object",
    "properties": {
        "node_id": {"type": "string"},
        "node_names": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["node_id", "node_names"],
    "additionalProperties": True,
}


def _apply_node_names(node_names: Any) -> Any:
    """Persist normalized node names through the node configuration service."""

    normalized = normalize_node_names(node_names)
    return _save_node_names(normalized)


@tool(
    "manage.node.names.set",
    summary="Set node display names in local node configuration.",
    stability="experimental",
    idempotent=True,
    examples=["manage.node.names.set(node_names=['Node 1', 'Node 2'])"],
    input_schema=_SET_NODE_NAMES_INPUT,
    output_schema=_SET_NODE_NAMES_OUTPUT,
)
def set_node_names(node_names: Any) -> Mapping[str, Any]:
    conf = _apply_node_names(node_names)
    return {
        "node_id": str(getattr(conf, "node_id", "") or ""),
        "node_names": list(getattr(getattr(conf, "node_settings", None), "node_names", []) or []),
    }
