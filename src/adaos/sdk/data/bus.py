"""Async event-bus helpers that stay import-safe until runtime."""

from __future__ import annotations

import inspect
import json
import os
import time
from types import SimpleNamespace
from typing import Any, Awaitable, Callable

from adaos.sdk.core._ctx import require_ctx

__all__ = ["emit", "on", "get_meta", "BusNotAvailable"]


class BusNotAvailable(RuntimeError):
    """Raised when the runtime context does not provide an event bus."""


def _bus() -> Any:
    ctx = require_ctx("sdk.data.bus")
    bus = getattr(ctx, "bus", None)
    if bus is None:
        raise BusNotAvailable("AgentContext.bus is not initialized")
    return bus


def _positional_params(fn: Callable[..., Any]) -> int:
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return 0
    params = list(sig.parameters.values())
    return sum(1 for i, p in enumerate(params) if i > 0 and p.kind in (p.POSITIONAL_ONLY, p.POSITIONAL_OR_KEYWORD))


def get_meta(payload: dict) -> dict:
    return payload.get("_meta", {}) if isinstance(payload, dict) else {}


def _payload_with_event_meta(ev: Any, data: Any) -> Any:
    """Preserve the bus envelope while keeping the payload-dict SDK contract.

    Skill handlers historically receive the payload rather than the Event
    instance. Copy envelope identity into reserved metadata so multi-topic
    handlers can distinguish lifecycle events without changing existing
    handler signatures or mutating the publisher's payload.
    """

    if not isinstance(data, dict):
        return data
    if hasattr(ev, "type"):
        event_type = str(getattr(ev, "type", "") or "").strip()
        event_source = str(getattr(ev, "source", "") or "").strip()
        event_ts = getattr(ev, "ts", None)
    elif isinstance(ev, dict):
        event_type = str(ev.get("type") or "").strip()
        event_source = str(ev.get("source") or "").strip()
        event_ts = ev.get("ts")
    else:
        event_type = ""
        event_source = ""
        event_ts = None
    if not event_type and not event_source and event_ts is None:
        return data

    payload = dict(data)
    raw_meta = payload.get("_meta")
    meta = dict(raw_meta) if isinstance(raw_meta, dict) else {}
    if event_type:
        meta["event_type"] = event_type
    if event_source:
        meta["event_source"] = event_source
    if event_ts is not None:
        meta["event_ts"] = event_ts
    payload["_meta"] = meta
    return payload


def _topic_matches_any(topic: str, patterns: str) -> bool:
    topic0 = str(topic or "")
    for raw in str(patterns or "").split(","):
        pat = raw.strip()
        if not pat:
            continue
        if pat == "*" or topic0 == pat:
            return True
        if pat.endswith("*") and topic0.startswith(pat[:-1]):
            return True
    return False


def _run_sync_handler_in_thread(topic: str) -> bool:
    try:
        raw = str(os.getenv("ADAOS_SYNC_SUBSCRIPTION_TO_THREAD", "1") or "1").strip().lower()
        if raw in {"0", "false", "no", "off"}:
            return False
        loop_patterns = os.getenv("ADAOS_SYNC_SUBSCRIPTION_LOOP_TOPICS", "")
        if loop_patterns and _topic_matches_any(topic, loop_patterns):
            return False
        patterns = os.getenv(
            "ADAOS_SYNC_SUBSCRIPTION_THREAD_TOPICS",
            "*",
        )
        return _topic_matches_any(topic, patterns)
    except Exception:
        return False


def _record_yjs_plain_copy_fault(exc: BaseException, *, operation: str) -> None:
    try:
        from adaos.services.incident_registry import (
            is_yjs_thread_affinity_fault,
            record_yjs_thread_affinity_fault,
        )

        if is_yjs_thread_affinity_fault(exc):
            record_yjs_thread_affinity_fault(
                source="sdk.data.bus",
                component="eventbus_plain_payload",
                operation=operation,
                exc=exc,
            )
    except Exception:
        pass


def _thread_safe_plain(value: Any, *, _depth: int = 0) -> Any:
    """Return a plain payload copy that can be dropped on a worker thread.

    y_py values are thread-affine on Windows. A synchronous subscription handler
    running on the isolated skill executor must not receive live YMap/YArray objects,
    otherwise Python may drop the final reference on the worker thread.
    """

    if _depth > 40:
        return repr(value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _thread_safe_plain(item, _depth=_depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_thread_safe_plain(item, _depth=_depth + 1) for item in value]
    to_json = getattr(value, "to_json", None)
    if callable(to_json):
        try:
            raw = to_json()
            if isinstance(raw, str):
                raw = json.loads(raw)
            return _thread_safe_plain(raw, _depth=_depth + 1)
        except Exception as exc:
            _record_yjs_plain_copy_fault(exc, operation="to_json")
            pass
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return {str(key): _thread_safe_plain(item, _depth=_depth + 1) for key, item in items()}
        except Exception as exc:
            _record_yjs_plain_copy_fault(exc, operation="items")
            return {}
    if not isinstance(value, (str, bytes, bytearray)):
        try:
            return [_thread_safe_plain(item, _depth=_depth + 1) for item in value]
        except Exception as exc:
            _record_yjs_plain_copy_fault(exc, operation="iter")
            pass
    return repr(value)


async def emit(topic: str, payload: dict, **kw: Any):
    bus = _bus()
    publish = getattr(bus, "publish")

    source = kw.pop("source", "")
    ts = float(kw.pop("ts", time.time()))
    extra_meta = dict(kw)

    pp = dict(payload) if isinstance(payload, dict) else {"value": payload}
    if extra_meta:
        pp["_meta"] = {**pp.get("_meta", {}), **extra_meta}

    npos = _positional_params(publish)
    try:
        sig = inspect.signature(publish)
    except (TypeError, ValueError):
        sig = None

    if npos >= 2:
        if sig and any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            res = publish(topic, pp, source=source, ts=ts, **extra_meta)
        else:
            allowed = {}
            if sig:
                for name in ("source", "ts"):
                    if name in sig.parameters:
                        allowed[name] = locals()[name]
            try:
                res = publish(topic, pp, **allowed)
            except TypeError:
                res = publish(topic, pp)
        if inspect.iscoroutine(res):
            return await res
        return res

    try:
        from adaos.domain.types import Event as DomainEvent

        event = DomainEvent(type=topic, payload=pp, source=source, ts=ts)
    except Exception:
        event = SimpleNamespace(type=topic, payload=pp, source=source, ts=ts)

    try:
        res = publish(event)
    except TypeError:
        res = publish(topic, pp)

    if inspect.iscoroutine(res):
        return await res
    return res


async def on(topic: str, handler: Callable[[dict], Awaitable[Any]]):
    bus = _bus()
    subscribe = getattr(bus, "subscribe")

    async def _adapt(ev):
        if hasattr(ev, "payload"):
            data = getattr(ev, "payload")
        elif isinstance(ev, dict) and "payload" in ev and "type" in ev:
            data = ev.get("payload")
        else:
            data = ev
        data = _payload_with_event_meta(ev, data)
        if inspect.iscoroutinefunction(handler):
            return await handler(data)
        if _run_sync_handler_in_thread(topic):
            from adaos.services.skill.subscription_execution import run_sync_subscription

            safe_data = _thread_safe_plain(data)
            return await run_sync_subscription(
                lambda: handler(safe_data),
                skill=str(getattr(handler, "_adaos_skill", None) or "<sdk>"),
                topic=str(topic or "<unknown>"),
                handler=str(
                    getattr(
                        handler,
                        "_adaos_handler",
                        f"{getattr(handler, '__module__', '<unknown>')}.{getattr(handler, '__name__', '<unknown>')}",
                    )
                ),
            )
        return handler(data)

    try:
        setattr(_adapt, "_adaos_topic", str(topic))
        setattr(_adapt, "_adaos_skill", getattr(handler, "_adaos_skill", None))
        setattr(
            _adapt,
            "_adaos_handler",
            getattr(
                handler,
                "_adaos_handler",
                f"{getattr(handler, '__module__', '<unknown>')}.{getattr(handler, '__name__', repr(handler))}",
            ),
        )
        event_filter = getattr(handler, "_adaos_event_filter", None)
        if callable(event_filter):
            setattr(_adapt, "_adaos_event_filter", event_filter)
    except Exception:
        pass

    try:
        sig = inspect.signature(subscribe)
    except (TypeError, ValueError):
        sig = None

    if sig and len(sig.parameters) >= 3:
        res = subscribe(topic, _adapt)
    else:
        try:
            res = subscribe(_adapt)
        except TypeError:
            try:
                res = subscribe(topic=topic, handler=_adapt)
            except TypeError:
                res = subscribe(topic, _adapt)

    if inspect.iscoroutine(res):
        await res
    return _adapt
