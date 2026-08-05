from __future__ import annotations

import math
import os
from typing import Any

TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t"})
FALSE_VALUES = frozenset({"", "0", "false", "no", "off", "n", "f", "none", "null"})


def truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    token = str(value).strip().lower()
    if token in TRUE_VALUES:
        return True
    if token in FALSE_VALUES:
        return False
    return bool(default)


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
