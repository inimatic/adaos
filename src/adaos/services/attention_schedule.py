"""Deterministic local-calendar quiet windows, shared by conversation and notifications."""
from __future__ import annotations

import math
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

UTC = timezone.utc
_TIME = re.compile(r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")


def instant(value: datetime | str | float | None = None) -> datetime:
    """Normalize an aware instant; never interpret a caller's naive local clock."""
    if value is None:
        return datetime.now(UTC)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(value):
            raise ValueError("invalid attention clock")
        return datetime.fromtimestamp(value, UTC)
    current = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(current, datetime) or current.utcoffset() is None:
        raise ValueError("attention clock must be timezone-aware")
    return current.astimezone(UTC)


def validate_schedule(quiet: Mapping[str, Any]) -> None:
    for key in ("start", "end"):
        if not _TIME.fullmatch(str(quiet.get(key, ""))):
            raise ValueError(f"invalid quiet-hours {key}")
    try:
        ZoneInfo(str(quiet.get("timezone", "")))
    except (ValueError, ZoneInfoNotFoundError) as exc:
        raise ValueError("invalid quiet-hours IANA timezone") from exc
    days = quiet.get("weekdays", list(range(7)))
    if not isinstance(days, list) or not days or len(set(days)) != len(days):
        raise ValueError("quiet-hours weekdays must be unique integers 0..6")
    if any(type(day) is not int or not 0 <= day <= 6 for day in days):
        raise ValueError("quiet-hours weekdays must be unique integers 0..6")


def _wall(naive: datetime, zone: ZoneInfo) -> datetime:
    """Choose the first fold; advance a missing wall time to the next valid minute."""
    for _ in range(2881):  # Also bounds skipped-day changes in IANA history.
        choices = []
        for fold in (0, 1):
            candidate = naive.replace(tzinfo=zone, fold=fold).astimezone(UTC)
            if candidate.astimezone(zone).replace(tzinfo=None) == naive:
                choices.append(candidate)
        if choices:
            return min(choices)
        naive += timedelta(minutes=1)
    raise ValueError("quiet-hours wall time cannot be resolved")


def quiet_period(
    quiet: Mapping[str, Any], *, now: datetime | str | float | None = None,
) -> tuple[bool, datetime | None]:
    """Return (currently quiet, next allowed UTC instant); None can mean all-week quiet."""
    validate_schedule(quiet)
    current = instant(now)
    if not quiet.get("enabled", False):
        return False, None
    zone = ZoneInfo(str(quiet["timezone"]))
    day = current.astimezone(zone).date()
    start = tuple(map(int, str(quiet["start"]).split(":")))
    end = tuple(map(int, str(quiet["end"]).split(":")))
    weekdays = set(quiet.get("weekdays", range(7)))
    windows = []
    for offset in range(-2, 9):
        date = day + timedelta(days=offset)
        if date.weekday() not in weekdays:
            continue
        begin = datetime(date.year, date.month, date.day, *start)
        finish = datetime(date.year, date.month, date.day, *end)
        if end <= start:
            finish += timedelta(days=1)
        windows.append((_wall(begin, zone), _wall(finish, zone)))
    until = next((end for start, end in windows if start <= current < end), None)
    if until is None:
        return False, None
    if start == end and len(weekdays) == 7:
        return True, None
    for begin, finish in windows:
        if begin <= until < finish:
            until = finish
    return True, until
