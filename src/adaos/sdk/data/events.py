"""High-level SDK helpers for publishing events."""

from __future__ import annotations

import asyncio
import inspect
import os
import time
from types import SimpleNamespace
from typing import Any, Mapping

from adaos.domain import enrich_event_payload
from adaos.sdk.core._ctx import require_ctx
from adaos.sdk.core._service_event_bridge import publish as publish_service_event
from adaos.sdk.core.errors import SdkRuntimeNotInitialized

from .bus import BusNotAvailable


def _ensure_bus(ctx: Any):
    bus = getattr(ctx, "bus", None)
    publish = getattr(bus, "publish", None)
    if publish is None:
        raise BusNotAvailable("Event bus is not available in current context")
    return publish


def publish(topic: str, payload: Mapping[str, Any] | None = None, **meta: Any) -> Any:
    """Publish an event via the runtime event bus."""
    data = enrich_event_payload(
        payload,
        event_id=meta.get("event_id"),
        generate_event_id=bool(meta.get("generate_event_id", False)),
        source_authority=meta.get("source_authority"),
        actor=meta.get("actor"),
        scope=meta.get("scope"),
        trace_id=meta.get("trace_id"),
        cause_event_id=meta.get("cause_event_id"),
        schema=meta.get("schema"),
        version=meta.get("version"),
        priority=meta.get("priority"),
    )
    extra_meta = {
        k: v
        for k, v in meta.items()
        if k
        not in {
            "source",
            "ts",
            "event_id",
            "generate_event_id",
            "source_authority",
            "actor",
            "scope",
            "trace_id",
            "cause_event_id",
            "schema",
            "version",
            "priority",
        }
    }
    if extra_meta:
        meta_container = dict(data.get("_meta", {}))
        meta_container.update(extra_meta)
        data["_meta"] = meta_container

    source = str(meta.get("source", ""))
    ts = float(meta.get("ts", time.time()))

    try:
        ctx = require_ctx("sdk.events.publish")
    except SdkRuntimeNotInitialized:
        if os.getenv("ADAOS_SERVICE_EVENT_BRIDGE_URL"):
            return publish_service_event(topic, data)
        raise
    publish_fn = _ensure_bus(ctx)

    try:
        sig = inspect.signature(publish_fn)
    except (TypeError, ValueError):  # pragma: no cover - exotic backends
        sig = None

    kwargs: dict[str, Any] = {}
    if sig:
        if "source" in sig.parameters:
            kwargs["source"] = source
        if "ts" in sig.parameters:
            kwargs["ts"] = ts

    try:
        result = publish_fn(topic, data, **kwargs)
    except TypeError:
        try:
            from adaos.domain.types import Event as DomainEvent

            event = DomainEvent(type=topic, payload=data, source=source, ts=ts)
        except Exception:  # pragma: no cover - fallback path
            event = SimpleNamespace(type=topic, payload=data, source=source, ts=ts)
        result = publish_fn(event)

    if inspect.isawaitable(result):
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return asyncio.run(result)
        return loop.create_task(result)
    return result


__all__ = ["publish"]
