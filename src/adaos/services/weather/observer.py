from __future__ import annotations

import logging
import time
from typing import Dict

import y_py as Y

from adaos.domain import Event
from adaos.services.agent_context import get_ctx

_log = logging.getLogger("adaos.weather.observer")
_OBSERVERS: Dict[str, int] = {}
_LAST_CITY: Dict[str, str | None] = {}


def _current_city(ydoc: Y.YDoc) -> str | None:
    data = ydoc.get_map("data")
    weather = data.get("weather")
    if isinstance(weather, dict):
        current = weather.get("current") or {}
        if isinstance(current, dict):
            city = current.get("city")
            return str(city) if city else None
    return None


def ensure_weather_observer(workspace_id: str, ydoc: Y.YDoc) -> None:
    if workspace_id in _OBSERVERS:
        return

    def _maybe_emit(event: Y.YDocEvent | None = None) -> None:  # noqa: ARG001 - event unused
        city = _current_city(ydoc)
        if not city or _LAST_CITY.get(workspace_id) == city:
            return
        _LAST_CITY[workspace_id] = city
        try:
            ctx = get_ctx()
            ev = Event(
                type="weather.city_changed",
                payload={"workspace_id": workspace_id, "city": city},
                source="weather.observer",
                ts=time.time(),
            )
            ctx.bus.publish(ev)
        except Exception as exc:
            _log.warning("failed to publish weather.city_changed: %s", exc)

    sub_id = ydoc.observe(_maybe_emit)
    _OBSERVERS[workspace_id] = sub_id
    _maybe_emit()
