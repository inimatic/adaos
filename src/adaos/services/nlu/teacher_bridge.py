from __future__ import annotations

import logging
import os
import time
from collections.abc import Iterable
from typing import Any, Dict, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.webspace import default_webspace_id
from adaos.services.nlu.teacher_events import append_event, make_event, rebuild_events_by_candidate
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings

_log = logging.getLogger("adaos.nlu.teacher")

_MAX_ITEMS = int(os.getenv("ADAOS_NLU_TEACHER_MAX", "200") or "200")
_TRUE_VALUES = {"1", "true", "yes", "on", "enable", "enabled"}
_FALSE_VALUES = {"0", "false", "no", "off", "disable", "disabled"}


def _env_enabled(value: str | None) -> bool | None:
    token = str(value or "").strip().lower()
    if not token:
        return None
    if token in _TRUE_VALUES:
        return True
    if token in _FALSE_VALUES:
        return False
    return None


_ENABLED: bool | None = _env_enabled(os.getenv("ADAOS_NLU_TEACHER"))
_HARD_PROVIDER_ISSUE_REASONS = {
    "no_active_nlu_stages",
    "rasa_runtime_disabled",
    "rasa_disabled",
    "rasa_start_failed",
    "rasa_base_url_unresolved",
    "neural_runtime_disabled",
    "neural_disabled",
    "neural_service_unavailable",
    "neuro_lite_runtime_disabled",
    "neuro_lite_disabled",
    "runtime_disabled",
    "not_installed_or_policy_disabled",
}
_TRANSIENT_PROVIDER_ISSUE_REASONS = {
    "rasa_timeout",
    "rasa_failed",
    "rasa_invalid_result",
    "neural_timeout",
    "neural_failed",
    "neuro_lite_timeout",
    "neuro_lite_failed",
}


def _nlu_teacher_bridge_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.teacher_bridge",
        owner="core:nlu.teacher_bridge",
        channel="core.nlu.teacher_bridge.async",
    )


def _payload(evt: Any) -> Dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    if hasattr(evt, "payload"):
        data = getattr(evt, "payload")
        return data if isinstance(data, dict) else {}
    return {}


def _resolve_webspace_id(payload: Mapping[str, Any]) -> str:
    meta = coerce_dict(payload.get("_meta"))
    token = payload.get("webspace_id") or payload.get("workspace_id") or meta.get("webspace_id") or meta.get("workspace_id")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _list_of_dicts(value: Any) -> list[dict]:
    if isinstance(value, (str, bytes, bytearray)) or isinstance(value, Mapping) or not isinstance(value, Iterable):
        return []
    return [dict(x) for x in iter_mappings(value)]


def _has_multi_engine_miss_evidence(meta: Mapping[str, Any]) -> bool:
    if not isinstance(meta, Mapping):
        return False
    for key in (
        "neuro_lite_fallback",
        "neural_fallback",
        "rasa_fallback",
    ):
        if meta.get(key) is True:
            return True
    pipeline = meta.get("nlu_pipeline")
    if isinstance(pipeline, Mapping):
        active = pipeline.get("active_stages")
        if isinstance(active, Mapping):
            enabled = [name for name, value in active.items() if bool(value)]
            if len(enabled) > 1:
                return True
    return False


def _provider_issue_evidence(*, reason: str, via: str | None, meta: Mapping[str, Any]) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "reason": str(reason or "").strip() or "unknown",
        "via": str(via or "").strip() or None,
        "severity": "warning",
    }
    pipeline = meta.get("nlu_pipeline") if isinstance(meta, Mapping) else None
    if isinstance(pipeline, Mapping):
        evidence["pipeline"] = dict(pipeline)
    fallbacks = {
        key: meta.get(key)
        for key in (
            "neuro_lite_fallback",
            "neuro_lite_fallback_reason",
            "neural_fallback",
            "neural_fallback_reason",
            "rasa_fallback",
            "rasa_fallback_reason",
        )
        if isinstance(meta, Mapping) and key in meta
    }
    if fallbacks:
        evidence["fallbacks"] = fallbacks
    return evidence


def _classify_not_obtained_for_teacher(*, reason: str, via: str | None, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
    token = str(reason or "").strip()
    via_token = str(via or "").strip()
    meta_map = meta if isinstance(meta, Mapping) else {}
    if token in _HARD_PROVIDER_ISSUE_REASONS:
        return {
            "teachable": False,
            "class": "provider_state",
            "reason": token,
            "via": via_token or None,
            "skip_reason": "provider_or_stage_unavailable",
        }
    if token in _TRANSIENT_PROVIDER_ISSUE_REASONS:
        if _has_multi_engine_miss_evidence(meta_map):
            return {
                "teachable": True,
                "class": "nlu_gap",
                "reason": token,
                "via": via_token or None,
                "provider_issue": _provider_issue_evidence(reason=token, via=via_token, meta=meta_map),
            }
        return {
            "teachable": False,
            "class": "provider_state",
            "reason": token,
            "via": via_token or None,
            "skip_reason": "provider_or_stage_unavailable",
        }
    if token.startswith("low_confidence<"):
        return {"teachable": True, "class": "nlu_gap", "reason": token, "via": via_token or None}
    if token in {
        "no_match",
        "rasa_low_confidence",
        "rasa_no_intent",
        "rasa_not_ok",
        "neural_low_confidence",
        "neural_rejected",
        "neuro_lite_low_confidence",
        "fallback",
        "not_obtained",
        "unknown",
    }:
        return {"teachable": True, "class": "nlu_gap", "reason": token, "via": via_token or None}
    if any(marker in token for marker in ("disabled", "unresolved", "unavailable")):
        return {
            "teachable": False,
            "class": "provider_state",
            "reason": token,
            "via": via_token or None,
            "skip_reason": "provider_or_stage_unavailable",
        }
    if any(marker in token for marker in ("timeout", "failed")):
        if _has_multi_engine_miss_evidence(meta_map):
            return {
                "teachable": True,
                "class": "nlu_gap",
                "reason": token,
                "via": via_token or None,
                "provider_issue": _provider_issue_evidence(reason=token, via=via_token, meta=meta_map),
            }
        return {
            "teachable": False,
            "class": "provider_state",
            "reason": token,
            "via": via_token or None,
            "skip_reason": "provider_or_stage_unavailable",
        }
    return {"teachable": True, "class": "nlu_gap", "reason": token or "unknown", "via": via_token or None}


def _teacher_enabled(ctx: Any) -> bool:
    if _ENABLED is not None:
        return bool(_ENABLED)
    try:
        return bool(getattr(getattr(ctx.config, "root_settings", None), "llm", None).allow_nlu_teacher)  # type: ignore[attr-defined]
    except Exception:
        return True


async def _append_teacher_item(webspace_id: str, item: dict) -> None:
    from adaos.services.yjs.doc import async_get_ydoc

    async with _nlu_teacher_bridge_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            current = data_map.get("nlu_teacher")
            teacher: dict = coerce_dict(current)
            items = _list_of_dicts(teacher.get("items"))
            items.append(item)
            if _MAX_ITEMS > 0 and len(items) > _MAX_ITEMS:
                items = items[-_MAX_ITEMS:]
            teacher["items"] = items
            rebuild_events_by_candidate(teacher)
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)


@subscribe("nlp.intent.not_obtained")
async def _on_not_obtained(evt: Any) -> None:
    ctx = get_ctx()
    if not _teacher_enabled(ctx):
        return

    payload = _payload(evt)
    text = payload.get("text") or payload.get("utterance")
    if not isinstance(text, str) or not text.strip():
        return
    text = text.strip()

    webspace_id = _resolve_webspace_id(payload)
    request_id = payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
    reason = payload.get("reason") if isinstance(payload.get("reason"), str) else "unknown"
    via = payload.get("via") if isinstance(payload.get("via"), str) else None
    meta = coerce_dict(payload.get("_meta"))
    classification = _classify_not_obtained_for_teacher(reason=reason, via=via, meta=meta)

    item = {
        "id": f"teach.{int(time.time()*1000)}",
        "ts": time.time(),
        "text": text,
        "reason": reason,
        "via": via,
        "request_id": request_id,
        "classification": dict(classification),
        "status": "pending" if classification.get("teachable") else "skipped",
        "_meta": dict(meta),
    }

    try:
        await _append_teacher_item(webspace_id, item)
    except Exception:
        _log.debug("failed to append nlu_teacher item webspace=%s", webspace_id, exc_info=True)

    try:
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=text,
                kind="not_obtained" if classification.get("teachable") else "not_obtained.skipped",
                title="Intent not obtained" if classification.get("teachable") else "Teacher skipped",
                subtitle=f"{reason} via={via}" if via else reason,
                raw=item,
                meta=meta,
            ),
        )
    except Exception:
        _log.debug("failed to append nlu_teacher event webspace=%s", webspace_id, exc_info=True)

    if not classification.get("teachable"):
        bus_emit(
            ctx.bus,
            "nlp.teacher.skipped",
            {"webspace_id": webspace_id, "request": item, "classification": dict(classification)},
            source="nlu.teacher",
        )
        return

    # Emit a single, generic event to be consumed by an external teacher (LLM).
    bus_emit(
        ctx.bus,
        "nlp.teacher.request",
        {"webspace_id": webspace_id, "request": item},
        source="nlu.teacher",
    )
