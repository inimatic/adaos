"""Shared status-card primitives and registry."""

from __future__ import annotations

from .cards import StatusCard, make_status_card, normalize_status_card, status_card_fingerprint
from .guard_cards import guard_status_cards_from_runtime
from .hot_events import HotEventBudget, HotEventDecision
from .registry import StatusRegistry, register_status_registry

__all__ = [
    "HotEventBudget",
    "HotEventDecision",
    "StatusCard",
    "StatusRegistry",
    "guard_status_cards_from_runtime",
    "make_status_card",
    "normalize_status_card",
    "register_status_registry",
    "status_card_fingerprint",
]
