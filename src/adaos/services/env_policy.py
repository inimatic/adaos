from __future__ import annotations

import math
import os
from collections.abc import Iterable
from typing import Any, Final

TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on", "y", "t"})
FALSE_VALUES: Final[frozenset[str]] = frozenset({"", "0", "false", "no", "off", "n", "f", "none", "null"})
ENABLE_VALUES: Final[frozenset[str]] = TRUE_VALUES | frozenset({"enable", "enabled"})
DISABLE_VALUES: Final[frozenset[str]] = FALSE_VALUES | frozenset({"disable", "disabled"})


def truthy(value: Any, *, default: bool = False) -> bool:
    coerced = coerce_bool(value, true_values=TRUE_VALUES, false_values=FALSE_VALUES)
    return bool(default) if coerced is None else coerced


def coerce_bool(
    value: Any,
    *,
    true_values: Iterable[str] = TRUE_VALUES,
    false_values: Iterable[str] = FALSE_VALUES,
) -> bool | None:
    if value is None:
        return None
    token = str(value).strip().lower()
    if token in true_values:
        return True
    if token in false_values:
        return False
    return None


def policy_bool(
    value: Any,
    *,
    default: bool,
    true_values: Iterable[str] = TRUE_VALUES,
    false_values: Iterable[str] = FALSE_VALUES,
) -> bool:
    coerced = coerce_bool(value, true_values=true_values, false_values=false_values)
    return bool(default) if coerced is None else coerced


def env_text(name: str, default: str | None = None) -> str:
    value = os.getenv(str(name))
    if value is None:
        return "" if default is None else str(default)
    return str(value).strip()


def env_bool(name: str, *, default: bool = False) -> bool:
    return truthy(os.getenv(str(name)), default=default)


def env_int(
    name: str,
    default: int,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    try:
        value = int(str(os.getenv(str(name)) or str(default)).strip() or str(default))
    except Exception:
        value = int(default)
    if minimum is not None:
        value = max(int(minimum), value)
    if maximum is not None:
        value = min(int(maximum), value)
    return value


def env_float(
    name: str,
    default: float,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
) -> float:
    try:
        value = float(str(os.getenv(str(name)) or str(default)).strip() or str(default))
    except Exception:
        value = float(default)
    if not math.isfinite(value):
        value = float(default)
    if minimum is not None:
        value = max(float(minimum), value)
    if maximum is not None:
        value = min(float(maximum), value)
    return float(value)


def env_csv(name: str) -> list[str]:
    raw = os.getenv(str(name))
    if raw is None:
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in str(raw).split(","):
        token = item.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        result.append(token)
    return result
