from __future__ import annotations

import time
from typing import Any, Mapping

from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.yjs.webspace import default_webspace_id

from .entity_resolver_runtime import build_entity_trace_stage
from . import neural_service_bridge, neuro_lite_service_bridge, rasa_service_bridge
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
    use_neuro_lite: bool = False,
    use_neural: bool = False,
    collect_all: bool = False,
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

    def compact_engine_result(result: Mapping[str, Any]) -> dict[str, Any]:
        out = {
            "ok": bool(result.get("ok")),
            "accepted": bool(result.get("accepted", result.get("ok"))),
            "via": result.get("via"),
            "intent": result.get("intent"),
            "confidence": result.get("confidence"),
            "slots": dict(result.get("slots") or {}) if isinstance(result.get("slots"), Mapping) else {},
            "reason": result.get("reason"),
            "model_id": result.get("model_id"),
        }
        return {key: value for key, value in out.items() if value not in (None, {}, "")}

    def result_payload(result: Mapping[str, Any], *, fallback_reason: str | None = None) -> dict[str, Any]:
        payload = {
            **dict(result),
            "accepted": bool(result.get("accepted", result.get("ok"))),
            "text": clean_text,
            "webspace_id": ws,
            "request_id": request_id,
            "entity_resolution": entity_resolution,
            "stages": stages,
            "engine_results": dict(engine_results),
        }
        if fallback_reason and not payload.get("reason"):
            payload["reason"] = fallback_reason
        return payload

    engine_results: dict[str, dict[str, Any]] = {}
    best_result: dict[str, Any] | None = None

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
        regex_result = {
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
        engine_results["regex"] = compact_engine_result(regex_result)
        best_result = regex_result
        if not collect_all:
            return result_payload(regex_result)

    else:
        regex_result = {"ok": False, "accepted": False, "reason": "no_match", "via": via or "regex", "slots": {}}
        engine_results["regex"] = compact_engine_result(regex_result)
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

    async def run_model_stage(stage_name: str, parser, *, parser_kwargs: Mapping[str, Any] | None = None) -> dict[str, Any]:
        result = await parser(
            clean_text,
            webspace_id=ws,
            request_id=request_id,
            locale=request_locale,
            preferred_locales=preferred_locales or [],
            **dict(parser_kwargs or {}),
        )
        status = "hit" if result.get("ok") else "miss"
        add_stage(
            _stage(
                request_id=request_id,
                webspace_id=ws,
                text=clean_text,
                stage=stage_name,
                status=status,
                via=str(result.get("via") or stage_name),
                intent=result.get("intent") if isinstance(result.get("intent"), str) else None,
                confidence=result.get("confidence") if isinstance(result.get("confidence"), (int, float)) else None,
                slots=result.get("slots") if isinstance(result.get("slots"), Mapping) else None,
                reason=result.get("reason") if isinstance(result.get("reason"), str) else None,
                raw=result.get("raw") if isinstance(result.get("raw"), Mapping) else None,
            )
        )
        engine_results[stage_name] = compact_engine_result(result)
        return dict(result)

    if use_neuro_lite:
        neuro_lite_result = await run_model_stage("neuro_lite", neuro_lite_service_bridge.parse_text)
        if neuro_lite_result.get("ok") and best_result is None:
            best_result = neuro_lite_result
        if neuro_lite_result.get("ok") and not collect_all:
            return result_payload(neuro_lite_result)
    elif collect_all:
        add_stage(
            _stage(
                request_id=request_id,
                webspace_id=ws,
                text=clean_text,
                stage="neuro_lite",
                status="skipped",
                reason="disabled_for_probe",
            )
        )
        engine_results["neuro_lite"] = {"ok": False, "accepted": False, "via": "neuro_lite", "reason": "skipped"}

    if use_neural:
        neural_result = await run_model_stage(
            "neural",
            neural_service_bridge.parse_text,
            parser_kwargs={
                "meta": {"webspace_id": ws, "probe": "nlu_teacher"},
                "entity_resolution": entity_resolution,
                "record_usage_stats": False,
            },
        )
        if neural_result.get("ok") and best_result is None:
            best_result = neural_result
        if neural_result.get("ok") and not collect_all:
            return result_payload(neural_result)
    elif collect_all:
        add_stage(
            _stage(
                request_id=request_id,
                webspace_id=ws,
                text=clean_text,
                stage="neural",
                status="skipped",
                reason="disabled_for_probe",
            )
        )
        engine_results["neural"] = {"ok": False, "accepted": False, "via": "neural", "reason": "skipped"}

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
        engine_results["rasa"] = {"ok": False, "accepted": False, "via": "rasa", "reason": "skipped"}
        if best_result is not None:
            return result_payload(best_result)
        return {
            "ok": False,
            "accepted": False,
            "reason": "rasa_skipped",
            "text": clean_text,
            "webspace_id": ws,
            "request_id": request_id,
            "entity_resolution": entity_resolution,
            "engine_results": dict(engine_results),
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
    engine_results["rasa"] = compact_engine_result(rasa_result)
    if rasa_result.get("ok") and best_result is None:
        best_result = dict(rasa_result)

    if best_result is not None:
        return result_payload(best_result)

    return {
        **rasa_result,
        "accepted": bool(rasa_result.get("ok")),
        "text": clean_text,
        "webspace_id": ws,
        "request_id": request_id,
        "via": rasa_result.get("via") or "rasa",
        "entity_resolution": entity_resolution,
        "engine_results": dict(engine_results),
        "stages": stages,
    }
