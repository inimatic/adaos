from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Dict

import y_py as Y

from adaos.domain import Event
from adaos.services.agent_context import get_ctx

_log = logging.getLogger("adaos.weather.observer")
_OBSERVERS: Dict[str, int] = {}
_LAST_CITY: Dict[str, str | None] = {}


def _coerce_dict(candidate) -> dict:
    """
    Safely turn a Yjs/Ypy value into a plain dict for inspection.
    """
    if isinstance(candidate, dict):
        return dict(candidate)
    if candidate is None:
        return {}
    try:
        to_json = getattr(candidate, "to_json", None)
        if callable(to_json):
            maybe = to_json()
            if isinstance(maybe, dict):
                return dict(maybe)
    except Exception:
        pass
    try:
        return dict(candidate)
    except Exception:
        return {}


def _current_city(ydoc: Y.YDoc) -> tuple[str | None, dict]:
    """
    Extract the current city from the webspace YDoc, returning debug metadata
    alongside the resolved value for richer logging.
    """
    data = ydoc.get_map("data")
    weather_node = data.get("weather")
    weather = _coerce_dict(weather_node)
    current_node = weather.get("current")
    current = _coerce_dict(current_node)
    city = current.get("city")
    debug = {
        "weather_type": type(weather_node).__name__,
        "weather_keys": list(weather.keys()) if isinstance(weather, dict) else None,
        "current_type": type(current_node).__name__ if current_node is not None else None,
        "current_keys": list(current.keys()) if isinstance(current, dict) else None,
    }

    if city:
        return str(city), debug

    # Soft fallback: if the stored value is a live YMap, try direct access.
    try:
        direct_current = weather_node.get("current") if hasattr(weather_node, "get") else None  # type: ignore[attr-defined]
        direct_dict = _coerce_dict(direct_current)
        debug["current_type"] = type(direct_current).__name__ if direct_current is not None else debug["current_type"]
        debug["current_keys"] = list(direct_dict.keys()) if isinstance(direct_dict, dict) else debug["current_keys"]
        city = direct_dict.get("city")
    except Exception:
        city = None

    return (str(city) if city else None), debug


def ensure_weather_observer(webspace_id: str, ydoc: Y.YDoc) -> None:
    if webspace_id in _OBSERVERS:
        return

    def _emit_current() -> None:
        city, meta = _current_city(ydoc)
        if not city or _LAST_CITY.get(webspace_id) == city:
            if not city:
                _log.debug("weather observer skip: no city webspace=%s meta=%s", webspace_id, meta)
            return
        _log.info("weather observer city detected webspace=%s city=%s meta=%s", webspace_id, city, meta)
        _LAST_CITY[webspace_id] = city
        try:
            ctx = get_ctx()
            ev = Event(
                type="weather.city_changed",
                payload={"webspace_id": webspace_id, "city": city},
                source="weather.observer",
                ts=time.time(),
            )
            ctx.bus.publish(ev)
        except Exception as exc:
            _log.warning("failed to publish weather.city_changed: %s", exc)

    def _maybe_emit(event: Y.YDocEvent | None = None) -> None:  # noqa: ARG001 - event unused
        def _run_safe() -> None:
            try:
                _emit_current()
            except Exception:
                pass

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            threading.Thread(target=_run_safe, name="weather-observer", daemon=True).start()
        else:
            loop.call_soon(_run_safe)

    sub_id = ydoc.observe_after_transaction(_maybe_emit)
    _OBSERVERS[webspace_id] = sub_id
    _emit_current()
