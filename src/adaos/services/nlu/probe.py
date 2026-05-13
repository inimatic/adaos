from __future__ import annotations

import time
from typing import Any, Mapping

from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.yjs.webspace import default_webspace_id

from .entity_resolver_runtime import build_entity_trace_stage
from . import rasa_service_bridge
from .pipeline import _try_regex_intent


def _resolve_webspace_id(token: str | None) -> str:
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _stage(
    *,
    request_id: str,
    webspace_id: str,
    text: str,
    stage: str,
    status: str,
    via: str | None = None,
    intent: str | None = None,
    confidence: float | None = None,
    slots: Mapping[str, Any] | None = None,
    reason: str | None = None,
    raw: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "ts": time.time(),
        "stage": stage,
        "status": status,
        "text": text,
        "webspace_id": webspace_id,
        "request_id": request_id,
    }
    if via:
        item["via"] = via
    if intent:
        item["intent"] = intent
    if confidence is not None:
        item["confidence"] = float(confidence)
    if slots:
        item["slots"] = dict(slots)
    if reason:
        item["reason"] = reason
    if raw:
        item["raw"] = dict(raw)
    return item


def _emit_stage(item: Mapping[str, Any]) -> None:
    try:
        bus_emit(get_ctx().bus, "nlu.trace.stage", dict(item), source="nlu.probe")
    except Exception:
        pass


async def probe_phrase(
    text: str,
    *,
    webspace_id: str | None = None,
    use_rasa: bool = True,
    emit_trace: bool = True,
    request_locale: str | None = None,
    preferred_locales: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    clean_text = str(text or "").strip()
    ws = _resolve_webspace_id(webspace_id)
    request_id = f"probe.{int(time.time() * 1000)}"
    stages: list[dict[str, Any]] = []

    def add_stage(item: dict[str, Any]) -> None:
        stages.append(item)
        if emit_trace:
            _emit_stage(item)

    add_stage(
        _stage(
            request_id=request_id,
            webspace_id=ws,
            text=clean_text,
            stage="request",
            status="received",
        )
    )

    if not clean_text:
        add_stage(
            _stage(
                request_id=request_id,
                webspace_id=ws,
                text=clean_text,
                stage="probe",
                status="miss",
                reason="empty_text",
            )
        )
        return {
            "ok": False,
            "accepted": False,
            "reason": "empty_text",
            "text": clean_text,
            "webspace_id": ws,
            "request_id": request_id,
            "stages": stages,
        }

    entity_stage = build_entity_trace_stage(
        {
            "text": clean_text,
            "webspace_id": ws,
            "request_id": request_id,
            "request_locale": request_locale,
            "preferred_locales": list(preferred_locales or []),
            "_meta": {"webspace_id": ws, "probe": "nlu_teacher"},
        },
        include_miss=True,
    )
    entity_resolution = dict(entity_stage.get("raw") or {}) if isinstance(entity_stage, Mapping) else {}
    if entity_stage:
        add_stage(dict(entity_stage))

    intent, slots, via, raw = await _try_regex_intent(clean_text, webspace_id=ws)
    if intent:
        add_stage(
            _stage(
                request_id=request_id,
                webspace_id=ws,
                text=clean_text,
                stage="regex",
                status="hit",
                via=via,
                intent=intent,
                confidence=1.0,
                slots=slots,
                raw=raw,
            )
        )
        return {
            "ok": True,
            "accepted": True,
            "text": clean_text,
            "webspace_id": ws,
            "request_id": request_id,
            "via": via,
            "intent": intent,
            "confidence": 1.0,
            "slots": slots,
            "entities": [{"entity": key, "value": value} for key, value in slots.items()],
            "entity_resolution": entity_resolution,
            "intent_ranking": [{"name": intent, "confidence": 1.0}],
            "raw": raw,
            "stages": stages,
        }

    add_stage(
        _stage(
            request_id=request_id,
            webspace_id=ws,
            text=clean_text,
            stage="regex",
            status="miss",
            via=via or "regex",
            reason="no_match",
            raw=raw,
        )
    )

    if not use_rasa:
        add_stage(
            _stage(
                request_id=request_id,
                webspace_id=ws,
                text=clean_text,
                stage="rasa",
                status="skipped",
                reason="disabled_for_probe",
            )
        )
        return {
            "ok": False,
            "accepted": False,
            "reason": "rasa_skipped",
            "text": clean_text,
            "webspace_id": ws,
            "request_id": request_id,
            "entity_resolution": entity_resolution,
            "stages": stages,
        }

    rasa_result = await rasa_service_bridge.parse_text(
        clean_text,
        webspace_id=ws,
        request_id=request_id,
        meta={"webspace_id": ws, "probe": "nlu_teacher"},
    )
    status = "hit" if rasa_result.get("ok") else "miss"
    add_stage(
        _stage(
            request_id=request_id,
            webspace_id=ws,
            text=clean_text,
            stage="rasa",
            status=status,
            via="rasa",
            intent=rasa_result.get("intent") if isinstance(rasa_result.get("intent"), str) else None,
            confidence=rasa_result.get("confidence") if isinstance(rasa_result.get("confidence"), (int, float)) else None,
            slots=rasa_result.get("slots") if isinstance(rasa_result.get("slots"), Mapping) else None,
            reason=rasa_result.get("reason") if isinstance(rasa_result.get("reason"), str) else None,
            raw=rasa_result.get("raw") if isinstance(rasa_result.get("raw"), Mapping) else None,
        )
    )

    return {
        **rasa_result,
        "accepted": bool(rasa_result.get("ok")),
        "text": clean_text,
        "webspace_id": ws,
        "request_id": request_id,
        "via": rasa_result.get("via") or "rasa",
        "entity_resolution": entity_resolution,
        "stages": stages,
    }
