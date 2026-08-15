from __future__ import annotations

from typing import Any


_NODE_IDENTITY_KINDS = frozenset({"hub", "member", "node", "redevice"})


def node_identity_token(value: Any) -> str:
    """Return the transport node id from a canonical node identity."""

    raw = str(value or "").strip()
    kind, separator, token = raw.partition(":")
    if separator and kind.strip().lower() in _NODE_IDENTITY_KINDS:
        return token.strip()
    return raw


def node_identities_match(left: Any, right: Any) -> bool:
    left_token = node_identity_token(left)
    right_token = node_identity_token(right)
    return bool(left_token and right_token and left_token == right_token)
