from __future__ import annotations

import copy
import os
from pathlib import Path
import threading
import time
from typing import Any

from adaos.domain.personalization_access import SubjectRef
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.personalization_access import PersonalizationAccessService, PersonalizationAccessStore
from adaos.services.user.profile import UserProfileService


_HEADER_CACHE_LOCK = threading.Lock()
_HEADER_CACHE: dict[tuple[str, str, int], dict[str, Any]] = {}
_HEADER_CACHE_INFLIGHT: dict[tuple[str, str, int], dict[str, Any]] = {}
_HEADER_CACHE_GENERATIONS: dict[tuple[str, str, int], int] = {}
_HEADER_CACHE_STATS: dict[str, Any] = {
    "request_total": 0,
    "hit_total": 0,
    "miss_total": 0,
    "invalidation_total": 0,
    "wait_total": 0,
    "coalesced_total": 0,
    "wait_timeout_total": 0,
    "max_wait_ms": 0.0,
    "compute_total": 0,
    "last_compute_ms": None,
    "max_compute_ms": 0.0,
    "last_error": None,
}


def _ctx(ctx: AgentContext | None = None) -> AgentContext:
    return ctx or get_ctx()


def _state_dir(ctx: AgentContext) -> Path:
    raw = ctx.paths.state_dir()
    return Path(raw() if callable(raw) else raw)


def current_user_id(ctx: AgentContext | None = None) -> str:
    resolved = _ctx(ctx)
    owner = getattr(resolved.settings, "owner_id", None) or "local-owner"
    return str(owner).strip() or "local-owner"


def current_subnet_id(ctx: AgentContext | None = None) -> str:
    resolved = _ctx(ctx)
    for source in (getattr(resolved, "settings", None), getattr(resolved, "config", None)):
        value = getattr(source, "subnet_id", None)
        if value:
            token = str(value).strip()
            if token:
                return token
    return "local-subnet"


def personalization_access_store(ctx: AgentContext | None = None) -> PersonalizationAccessStore:
    resolved = _ctx(ctx)
    return PersonalizationAccessStore(_state_dir(resolved) / "personalization" / "access.v0.json")


def deny_browser_session(session_id: str) -> dict[str, Any] | None:
    token = str(session_id or "").strip()
    if not token:
        return None
    from adaos.services import access_links

    result = access_links.deny_link("browser", token)
    for entry in access_links.list_links("browser"):
        entry_id = str(entry.get("id") or "").strip()
        if not entry_id or entry_id == token:
            continue
        if str(entry.get("admission_session_id") or "").strip() == token:
            access_links.deny_link("browser", entry_id)
    return result


def personalization_access_service(ctx: AgentContext | None = None) -> PersonalizationAccessService:
    resolved = _ctx(ctx)
    owner = SubjectRef("user", current_user_id(resolved))
    return PersonalizationAccessService(
        personalization_access_store(resolved),
        owner=owner,
        access_link_denier=deny_browser_session,
    )


def current_user_profile_service(ctx: AgentContext | None = None) -> UserProfileService:
    resolved = _ctx(ctx)
    return UserProfileService(resolved, access=personalization_access_service(resolved))


def _header_cache_ttl_s() -> float:
    try:
        value = float(os.getenv("ADAOS_PERSONALIZATION_HEADER_CACHE_TTL_S", "2.0") or 2.0)
    except (TypeError, ValueError):
        value = 2.0
    return max(0.0, min(value, 30.0))


def _header_cache_key(ctx: AgentContext) -> tuple[str, str, int]:
    return (str(_state_dir(ctx)), current_user_id(ctx), id(ctx.kv))


def _header_cache_wait_timeout_s() -> float:
    try:
        value = float(os.getenv("ADAOS_PERSONALIZATION_HEADER_CACHE_WAIT_TIMEOUT_S", "5.0") or 5.0)
    except (TypeError, ValueError):
        value = 5.0
    return max(0.1, min(value, 30.0))


def current_user_header_settings(ctx: AgentContext | None = None) -> dict[str, object]:
    """Return a bounded, single-flight snapshot for the frequently polled header."""
    resolved = _ctx(ctx)
    key = _header_cache_key(resolved)
    ttl_s = _header_cache_ttl_s()
    with _HEADER_CACHE_LOCK:
        _HEADER_CACHE_STATS["request_total"] = int(_HEADER_CACHE_STATS["request_total"]) + 1
        now = time.monotonic()
        cached = _HEADER_CACHE.get(key)
        if cached and now < float(cached.get("expires_at") or 0.0):
            _HEADER_CACHE_STATS["hit_total"] = int(_HEADER_CACHE_STATS["hit_total"]) + 1
            return copy.deepcopy(cached["value"])
        _HEADER_CACHE_STATS["miss_total"] = int(_HEADER_CACHE_STATS["miss_total"]) + 1
        inflight = _HEADER_CACHE_INFLIGHT.get(key)
        compute = inflight is None
        if compute:
            inflight = {
                "event": threading.Event(),
                "generation": int(_HEADER_CACHE_GENERATIONS.get(key) or 0),
                "value": None,
                "error": None,
            }
            _HEADER_CACHE_INFLIGHT[key] = inflight
        else:
            _HEADER_CACHE_STATS["wait_total"] = int(_HEADER_CACHE_STATS["wait_total"]) + 1
            _HEADER_CACHE_STATS["coalesced_total"] = (
                int(_HEADER_CACHE_STATS["coalesced_total"]) + 1
            )

    if not compute:
        wait_started = time.perf_counter()
        completed = inflight["event"].wait(timeout=_header_cache_wait_timeout_s())
        wait_ms = (time.perf_counter() - wait_started) * 1000.0
        with _HEADER_CACHE_LOCK:
            _HEADER_CACHE_STATS["max_wait_ms"] = round(
                max(float(_HEADER_CACHE_STATS["max_wait_ms"]), wait_ms),
                3,
            )
            if not completed:
                _HEADER_CACHE_STATS["wait_timeout_total"] = (
                    int(_HEADER_CACHE_STATS["wait_timeout_total"]) + 1
                )
                raise TimeoutError("personalization header cache computation did not finish in time")
            error = inflight.get("error")
            value = inflight.get("value")
        if error is not None:
            raise RuntimeError(f"coalesced header computation failed: {error}") from error
        return copy.deepcopy(value)

    compute_started = time.perf_counter()
    try:
        value = current_user_profile_service(resolved).header_settings()
    except Exception as exc:
        with _HEADER_CACHE_LOCK:
            _HEADER_CACHE_STATS["last_error"] = f"{type(exc).__name__}: {exc}"
            inflight["error"] = exc
            _HEADER_CACHE_INFLIGHT.pop(key, None)
            inflight["event"].set()
        raise
    else:
        compute_ms = (time.perf_counter() - compute_started) * 1000.0
        with _HEADER_CACHE_LOCK:
            _HEADER_CACHE_STATS["compute_total"] = int(_HEADER_CACHE_STATS["compute_total"]) + 1
            _HEADER_CACHE_STATS["last_compute_ms"] = round(compute_ms, 3)
            _HEADER_CACHE_STATS["max_compute_ms"] = round(
                max(float(_HEADER_CACHE_STATS["max_compute_ms"]), compute_ms),
                3,
            )
            _HEADER_CACHE_STATS["last_error"] = None
            inflight["value"] = copy.deepcopy(value)
            if int(_HEADER_CACHE_GENERATIONS.get(key) or 0) == int(inflight["generation"]):
                _HEADER_CACHE[key] = {
                    "value": copy.deepcopy(value),
                    "expires_at": time.monotonic() + ttl_s,
                    "state_path": str(_state_dir(resolved) / "personalization" / "access.v0.json"),
                }
            _HEADER_CACHE_INFLIGHT.pop(key, None)
            inflight["event"].set()
        return copy.deepcopy(value)


def invalidate_current_user_header_settings(ctx: AgentContext | None = None) -> None:
    resolved = _ctx(ctx)
    key = _header_cache_key(resolved)
    with _HEADER_CACHE_LOCK:
        _HEADER_CACHE.pop(key, None)
        _HEADER_CACHE_GENERATIONS[key] = int(_HEADER_CACHE_GENERATIONS.get(key) or 0) + 1
        _HEADER_CACHE_STATS["invalidation_total"] = int(_HEADER_CACHE_STATS["invalidation_total"]) + 1


def personalization_header_cache_snapshot() -> dict[str, Any]:
    from adaos.services.personalization_access import personalization_access_diagnostics

    with _HEADER_CACHE_LOCK:
        state_bytes = 0
        for item in _HEADER_CACHE.values():
            path = Path(str(item.get("state_path") or ""))
            try:
                state_bytes = max(state_bytes, path.stat().st_size)
            except OSError:
                continue
        return {
            "schema": "adaos.personalization.header_cache.v1",
            "ttl_s": _header_cache_ttl_s(),
            "entry_total": len(_HEADER_CACHE),
            "inflight_total": len(_HEADER_CACHE_INFLIGHT),
            "access_state_bytes": state_bytes,
            **dict(_HEADER_CACHE_STATS),
            "audit": personalization_access_diagnostics(),
        }


def _reset_personalization_header_cache_for_tests() -> None:
    with _HEADER_CACHE_LOCK:
        _HEADER_CACHE.clear()
        _HEADER_CACHE_GENERATIONS.clear()
        _HEADER_CACHE_STATS.update(
            {
                "request_total": 0,
                "hit_total": 0,
                "miss_total": 0,
                "invalidation_total": 0,
                "wait_total": 0,
                "coalesced_total": 0,
                "wait_timeout_total": 0,
                "max_wait_ms": 0.0,
                "compute_total": 0,
                "last_compute_ms": None,
                "max_compute_ms": 0.0,
                "last_error": None,
            }
        )


__all__ = [
    "current_subnet_id",
    "current_user_header_settings",
    "current_user_id",
    "current_user_profile_service",
    "deny_browser_session",
    "invalidate_current_user_header_settings",
    "personalization_access_service",
    "personalization_access_store",
    "personalization_header_cache_snapshot",
]
