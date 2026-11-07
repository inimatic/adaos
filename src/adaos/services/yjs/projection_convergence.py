"""Core-owned, bounded receipts for delivered application projections."""

from __future__ import annotations

import copy
import json
import threading
import time
from collections import OrderedDict
from typing import Any, Mapping

_lock = threading.RLock()
_projections: OrderedDict[str, dict[str, Any]] = OrderedDict()
_repairs: OrderedDict[tuple[str, str, str], float] = OrderedDict()
_navigations: OrderedDict[str, str] = OrderedDict()
_navigation_requests: OrderedDict[tuple[str, str], None] = OrderedDict()
_MAX_WEBSPACES = 256
_MAX_CLIENTS = 32


def _identity_snapshot(value: Any) -> dict[str, Any]:
    """Detach the small identity receipt while still on its YDoc owner.

    Persisted room bootstrap supplies shallow dicts containing native nested
    YMap/YArray values. Never retain those thread-bound objects in this cache.
    Unknown non-JSON values refuse rather than becoming string identities.
    """
    def native_json(item: Any) -> Any:
        from y_py import YArray, YMap

        if isinstance(item, (YMap, YArray)):
            return json.loads(item.to_json())
        raise TypeError(f"Unsupported projection identity value: {type(item).__name__}")

    return json.loads(json.dumps(dict(value or {}), default=native_json, allow_nan=False))


def claim_navigation(webspace: str, request_id: str) -> bool:
    """Fence at admission, before asynchronous identity or source resolution."""
    with _lock:
        key = (webspace, request_id)
        if key in _navigation_requests and _navigations.get(webspace) != request_id:
            return False
        _navigation_requests[key] = None
        _navigation_requests.move_to_end(key)
        while len(_navigation_requests) > 1024:
            _navigation_requests.popitem(last=False)
        _navigations[webspace] = request_id
        _navigations.move_to_end(webspace)
        while len(_navigations) > _MAX_WEBSPACES:
            _navigations.popitem(last=False)
        return True


def navigation_is_current(webspace: str, request_id: str) -> bool:
    with _lock:
        return _navigations.get(webspace, request_id) == request_id


def current_navigation_fence(webspace: str) -> str | None:
    """Return the navigation identity a background projection must preserve.

    A live navigation claim wins over the last published projection. The
    published fallback also fences refreshes that began before the first
    navigation command handled after a Core restart.
    """
    with _lock:
        claimed = str(_navigations.get(webspace) or "").strip()
        if claimed:
            return claimed
        published = _projections.get(webspace) or {}
        return str(published.get("navigation_id") or "").strip() or None


def publish(webspace: str, contract: Mapping[str, Any]) -> None:
    generation = str(contract.get("generation") or "")
    if not generation:
        return
    identity = _identity_snapshot(contract.get("identity"))
    with _lock:
        previous = _projections.get(webspace, {})
        clients = previous.get("clients", {}) if previous.get("generation") == generation else {}
        _projections[webspace] = {
            "generation": generation,
            "scenario_id": contract.get("scenario_id"),
            "payload_hash": contract.get("payload_hash"),
            "navigation_id": contract.get("navigation_id"),
            "identity": identity,
            "published_at": previous.get("published_at", time.time()) if previous.get("generation") == generation else time.time(),
            "clients": clients,
        }
        _projections.move_to_end(webspace)
        while len(_projections) > _MAX_WEBSPACES:
            _projections.popitem(last=False)


def acknowledge(webspace: str, client: str, receipt: Mapping[str, Any]) -> dict[str, Any]:
    with _lock:
        current = _projections.get(webspace)
        if not current:
            return {"ok": False, "error": "projection_not_published"}
        for key in ("generation", "scenario_id", "payload_hash"):
            if not current.get(key) or receipt.get(key) != current[key]:
                return {"ok": False, "error": "projection_identity_mismatch", "generation": current["generation"]}
        if receipt.get("complete") is not True:
            return {"ok": False, "error": "projection_incomplete"}
        clients = current["clients"]
        clients[client[:256]] = {"state": "applied", "applied_at": time.time(), "generation": current["generation"]}
        while len(clients) > _MAX_CLIENTS:
            clients.pop(next(iter(clients)))
        return {"ok": True, "state": "applied", "generation": current["generation"]}


def snapshot(webspace: str) -> dict[str, Any]:
    with _lock:
        value = copy.deepcopy(_projections.get(webspace, {}))
    value["state"] = "applied" if value.get("clients") else "awaiting_client" if value else "unpublished"
    return value


def admit_repair(webspace: str, client: str, generation: str) -> bool:
    """Permit one recovery per client/generation in a bounded failure window."""
    key = (webspace, client[:256], generation[:256])
    now = time.monotonic()
    with _lock:
        previous = _repairs.get(key)
        if previous is not None and now - previous < 120.0:
            return False
        _repairs[key] = now
        _repairs.move_to_end(key)
        while len(_repairs) > 1024:
            _repairs.popitem(last=False)
    return True
