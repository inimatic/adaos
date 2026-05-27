from __future__ import annotations

import logging
import os
import time
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.nlu.runtime_flags")

_TRUE_VALUES = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled", "none"}
_FLAG_KEYS = {
    "regex": "regex_enabled",
    "regexp": "regex_enabled",
    "regex_enabled": "regex_enabled",
    "regexp_enabled": "regex_enabled",
    "neural": "neural_enabled",
    "neure": "neural_enabled",
    "neural_enabled": "neural_enabled",
    "neuro": "neuro_lite_enabled",
    "neuro_lite": "neuro_lite_enabled",
    "neuro_light": "neuro_lite_enabled",
    "neurolite": "neuro_lite_enabled",
    "lite": "neuro_lite_enabled",
    "neuro_lite_enabled": "neuro_lite_enabled",
    "rasa": "rasa_enabled",
    "rasa_enabled": "rasa_enabled",
}
DEFAULT_FLAGS: dict[str, bool] = {
    "regex_enabled": True,
    "neuro_lite_enabled": True,
    "neural_enabled": True,
    "rasa_enabled": True,
}


def _payload(evt: Any) -> dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    data = getattr(evt, "payload", None)
    return data if isinstance(data, dict) else {}


def _resolve_webspace_id(payload: Mapping[str, Any] | None) -> str:
    payload = payload if isinstance(payload, Mapping) else {}
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    token = payload.get("webspace_id") or payload.get("workspace_id") or meta.get("webspace_id") or meta.get("workspace_id")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        token = value.strip().lower()
        if token in _TRUE_VALUES:
            return True
        if token in _FALSE_VALUES:
            return False
    return None


def _env_default(stage: str, fallback: bool) -> bool:
    env_name = {
        "regex_enabled": "ADAOS_NLU_REGEX",
        "neuro_lite_enabled": "ADAOS_NLU_NEURO_LITE_RUNTIME",
        "neural_enabled": "ADAOS_NLU_NEURAL_RUNTIME",
        "rasa_enabled": "ADAOS_NLU_RASA_RUNTIME",
    }.get(stage)
    if not env_name:
        return fallback
    raw = os.getenv(env_name)
    value = _coerce_bool(raw)
    return fallback if value is None else value


def default_flags() -> dict[str, bool]:
    return {
        key: _env_default(key, value)
        for key, value in DEFAULT_FLAGS.items()
    }


def normalize_flags(value: Any) -> dict[str, bool]:
    flags = default_flags()
    if not isinstance(value, Mapping):
        return flags
    raw_flags = value.get("flags") if isinstance(value.get("flags"), Mapping) else value
    for raw_key, raw_value in raw_flags.items():
        key = _FLAG_KEYS.get(str(raw_key or "").strip().lower())
        if not key:
            continue
        coerced = _coerce_bool(raw_value)
        if coerced is None:
            continue
        flags[key] = coerced
    return flags


def normalize_flag_updates(value: Any) -> dict[str, bool]:
    updates: dict[str, bool] = {}
    if not isinstance(value, Mapping):
        return updates
    raw_flags = value.get("flags") if isinstance(value.get("flags"), Mapping) else value
    for raw_key, raw_value in raw_flags.items():
        key = _FLAG_KEYS.get(str(raw_key or "").strip().lower())
        if not key:
            continue
        coerced = _coerce_bool(raw_value)
        if coerced is None:
            continue
        updates[key] = coerced
    return updates


def _runtime_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.runtime_flags",
        owner="core:nlu.runtime_flags",
        channel="core.nlu.runtime_flags.async",
    )


async def get_runtime_flags(webspace_id: str | None = None) -> dict[str, bool]:
    from adaos.services.yjs.doc import async_read_ydoc

    ws = str(webspace_id or "").strip() or default_webspace_id()
    try:
        async with async_read_ydoc(ws) as ydoc:
            data_map = ydoc.get_map("data")
            current = data_map.get("nlu_runtime")
    except Exception:
        _log.debug("failed to read NLU runtime flags webspace=%s", ws, exc_info=True)
        current = None
    return normalize_flags(current)


async def is_stage_enabled(webspace_id: str | None, stage: str) -> bool:
    key = _FLAG_KEYS.get(str(stage or "").strip().lower())
    if not key:
        return True
    flags = await get_runtime_flags(webspace_id)
    return bool(flags.get(key, True))


async def set_runtime_flags(
    webspace_id: str | None,
    flags_update: Mapping[str, Any],
    *,
    source: str = "nlu.runtime_flags",
) -> dict[str, Any]:
    from adaos.services.yjs.doc import async_get_ydoc, mutate_live_room

    ws = str(webspace_id or "").strip() or default_webspace_id()
    next_flags = normalize_flags({"flags": await get_runtime_flags(ws)})
    next_flags.update(normalize_flag_updates(flags_update))
    now = time.time()
    payload = {
        "flags": next_flags,
        "updated_at": now,
        "updated_by": source,
    }

    def _apply(ydoc: Any, txn: Any) -> None:
        data_map = ydoc.get_map("data")
        current = data_map.get("nlu_runtime")
        merged = dict(current) if isinstance(current, Mapping) else {}
        merged.update(payload)
        data_map.set(txn, "nlu_runtime", merged)

    if not mutate_live_room(
        ws,
        _apply,
        root_names=["data"],
        source=source,
        owner="core:nlu.runtime_flags",
        channel="core.nlu.runtime_flags.live_room",
    ):
        async with _runtime_write_meta():
            async with async_get_ydoc(
                ws,
                publish_live_room=False,
                load_mark_roots=["data"],
                write_source=source,
                write_owner="core:nlu.runtime_flags",
                write_channel="core.nlu.runtime_flags.async",
            ) as ydoc:
                with ydoc.begin_transaction() as txn:
                    _apply(ydoc, txn)

    return {"ok": True, "webspace_id": ws, **payload}


@subscribe("nlu.runtime.flags.set")
async def on_runtime_flags_set(evt: Any) -> None:
    payload = _payload(evt)
    raw_flags = payload.get("flags") if isinstance(payload.get("flags"), Mapping) else payload
    ws = _resolve_webspace_id(payload)
    try:
        result = await set_runtime_flags(ws, raw_flags, source="nlu.runtime_flags.event")
    except Exception:
        _log.warning("failed to update NLU runtime flags", exc_info=True)
        return

    try:
        from adaos.services.agent_context import get_ctx
        from adaos.services.eventbus import emit as bus_emit

        bus_emit(
            get_ctx().bus,
            "nlu.runtime.flags.changed",
            {
                "webspace_id": ws,
                "flags": dict(result.get("flags") or {}),
                "updated_at": result.get("updated_at"),
            },
            source="nlu.runtime_flags",
        )
    except Exception:
        pass
