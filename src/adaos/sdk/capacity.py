"""SDK facade for capacity inspection helpers."""

from __future__ import annotations

from typing import Any, Mapping

from adaos.sdk.core.decorators import tool
from adaos.services.capacity import get_local_capacity as _get_local_capacity

__all__ = ["get_local_capacity"]


@tool(
    "capacity.local.get",
    summary="Return local node capacity snapshot (skills/scenarios/devices).",
    stability="experimental",
    examples=["capacity.local.get()"],
)
def get_local_capacity() -> Mapping[str, Any]:
    return _get_local_capacity() or {}

