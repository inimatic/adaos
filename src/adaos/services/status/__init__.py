"""Shared status-card primitives and registry."""

from __future__ import annotations

from .cards import StatusCard, make_status_card, normalize_status_card, status_card_fingerprint
from .registry import StatusRegistry, register_status_registry

__all__ = [
    "StatusCard",
    "StatusRegistry",
    "make_status_card",
    "normalize_status_card",
    "register_status_registry",
    "status_card_fingerprint",
]
