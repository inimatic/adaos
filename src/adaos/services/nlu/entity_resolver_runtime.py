from __future__ import annotations

import hashlib
import logging
import time
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services import named_entities
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.nlu.entity_resolver")


def _payload(evt: Any) -> dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    if hasattr(evt, "payload"):
        data = getattr(evt, "payload")
        return data if isinstance(data, dict) else {}
    return {}


def _resolve_webspace_id(payload: Mapping[str, Any]) -> str:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    token = payload.get("webspace_id") or payload.get("workspace_id") or meta.get("webspace_id")
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _request_id(payload: Mapping[str, Any], *, text: str, webspace_id: str) -> str:
    rid = payload.get("request_id") or payload.get("id")
    if isinstance(rid, str) and rid.strip():
        return rid.strip()
    seed = f"{webspace_id}:{text}:{payload.get('ts') or ''}"
    return "auto." + hashlib.sha1(seed.encode("utf-8", errors="ignore")).hexdigest()[:12]


def build_entity_trace_stage(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    text = payload.get("text") or payload.get("utterance")
    if not isinstance(text, str) or not text.strip():
        return None
    text = text.strip()
    webspace_id = _resolve_webspace_id(payload)
    request_id = _request_id(payload, text=text, webspace_id=webspace_id)
    result = named_entities.get_named_entity_service().resolve_text(text, webspace_id=webspace_id)
    if not result.resolved_entities and not result.ambiguities:
        return None
    status = "ambiguous" if result.ambiguities else "resolved"
    confidence = None
    if result.resolved_entities:
        confidence = max(item.confidence for item in result.resolved_entities)
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    raw = result.to_dict()
    return {
        "stage": "named_entity",
        "status": status,
        "text": text,
        "webspace_id": webspace_id,
        "request_id": request_id,
        "via": "named_entity.dry_run",
        "confidence": confidence,
        "raw": {
            "named_entities": raw,
            "resolved_entities": raw.get("resolved_entities") or [],
            "ambiguities": raw.get("ambiguities") or [],
            "normalized_text": raw.get("normalized_text"),
        },
        "_meta": dict(meta),
    }


@subscribe("nlp.intent.detect.request")
async def on_detect_request(evt: Any) -> None:
    payload = _payload(evt)
    try:
        stage = build_entity_trace_stage(payload)
    except Exception:
        _log.debug("failed to build named entity dry-run trace", exc_info=True)
        return
    if not stage:
        return
    try:
        ctx = get_ctx()
        bus_emit(ctx.bus, "nlu.trace.stage", stage, source="nlu.entity_resolver")
    except Exception:
        _log.debug("failed to emit named entity dry-run trace", exc_info=True)


__all__ = ["build_entity_trace_stage", "on_detect_request"]
