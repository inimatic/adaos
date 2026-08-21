"""Budget contract shared by autonomous builders and independent validators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


DEFAULT_PACKAGED_PYTEST_WALL_SECONDS = 60
MAX_PACKAGED_PYTEST_WALL_SECONDS = 300
EXECUTION_TO_PYTEST_BUDGET_DIVISOR = 60


def derive_validation_budget(
    execution_budget: Mapping[str, Any] | None,
    *,
    source: str = "platform_default",
) -> dict[str, Any]:
    """Derive one immutable package-test allowance from execution authority."""

    max_wall_seconds: int | None = None
    if isinstance(execution_budget, Mapping):
        try:
            observed = int(execution_budget.get("max_wall_seconds") or 0)
        except (TypeError, ValueError):
            observed = 0
        if observed > 0:
            max_wall_seconds = observed
    timeout_seconds = DEFAULT_PACKAGED_PYTEST_WALL_SECONDS
    if max_wall_seconds is not None:
        scaled = (
            max_wall_seconds + EXECUTION_TO_PYTEST_BUDGET_DIVISOR - 1
        ) // EXECUTION_TO_PYTEST_BUDGET_DIVISOR
        timeout_seconds = max(timeout_seconds, scaled)
    timeout_seconds = min(MAX_PACKAGED_PYTEST_WALL_SECONDS, timeout_seconds)
    return {
        "schema": "adaos.builder.validation_budget.v1",
        "packaged_pytest_wall_seconds": timeout_seconds,
        "source": source if max_wall_seconds is not None else "platform_default",
        "execution_max_wall_seconds": max_wall_seconds,
    }


def normalize_validation_budget(value: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the compact handoff form without accepting caller expansion."""

    if str(value.get("schema") or "") != "adaos.builder.validation_budget.v1":
        raise ValueError("validation budget schema is invalid")
    try:
        timeout_seconds = int(value.get("packaged_pytest_wall_seconds") or 0)
    except (TypeError, ValueError) as exc:
        raise ValueError("validation budget timeout is invalid") from exc
    if not 1 <= timeout_seconds <= MAX_PACKAGED_PYTEST_WALL_SECONDS:
        raise ValueError("validation budget timeout is outside platform bounds")
    max_wall = value.get("execution_max_wall_seconds")
    if max_wall is not None:
        try:
            max_wall = int(max_wall)
        except (TypeError, ValueError) as exc:
            raise ValueError("validation budget execution authority is invalid") from exc
        if max_wall <= 0:
            raise ValueError("validation budget execution authority is invalid")
    return {
        "schema": "adaos.builder.validation_budget.v1",
        "packaged_pytest_wall_seconds": timeout_seconds,
        "source": str(value.get("source") or "platform_default"),
        "execution_max_wall_seconds": max_wall,
    }


__all__ = [
    "DEFAULT_PACKAGED_PYTEST_WALL_SECONDS",
    "EXECUTION_TO_PYTEST_BUDGET_DIVISOR",
    "MAX_PACKAGED_PYTEST_WALL_SECONDS",
    "derive_validation_budget",
    "normalize_validation_budget",
]
