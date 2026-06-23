from __future__ import annotations

import asyncio
import json
import logging
import os
import shutil
import time
from pathlib import Path
from typing import Any, Dict, Mapping
from urllib.request import Request, urlopen

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.skill.service_supervisor import get_service_supervisor

from .runtime_flags import is_stage_enabled

_log = logging.getLogger("adaos.nlu.neuro_lite")

_SERVICE_NAME = "neuro_nlu_lite_skill"
_SEMAPHORE = asyncio.Semaphore(2)
_START_LOCK = asyncio.Lock()
_PARSE_TIMEOUT_S = float(os.getenv("ADAOS_NLU_NEURO_LITE_TIMEOUT_S", "3.0") or "3.0")
_ACCEPT_CONFIDENCE = float(os.getenv("ADAOS_NLU_NEURO_LITE_ACCEPT_CONFIDENCE", "0.0") or "0.0")


def _payload(evt: Any) -> Dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    data = getattr(evt, "payload", None)
    return data if isinstance(data, dict) else {}


def _resolve_webspace_id(payload: Mapping[str, Any]) -> str | None:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    for source in (payload, meta):
        token = source.get("webspace_id") or source.get("workspace_id")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return None


def _request_locale(payload: Mapping[str, Any]) -> str | None:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    for source in (payload, meta):
        token = source.get("request_locale") or source.get("locale")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return None


def _preferred_locales(payload: Mapping[str, Any]) -> list[str]:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    raw = payload.get("preferred_locales") or meta.get("preferred_locales")
    if isinstance(raw, str):
        items: list[Any] = raw.split(",")
    elif isinstance(raw, (list, tuple, set)):
        items = list(raw)
    else:
        items = []
    out: list[str] = []
    for item in items:
        token = str(item or "").strip()
        if token and token not in out:
            out.append(token)
    return out


def _http_post_json(url: str, payload: dict, *, timeout_ms: int) -> dict:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url, data=body, method="POST")
    req.add_header("Content-Type", "application/json")
    with urlopen(req, timeout=timeout_ms / 1000.0) as resp:
        raw = resp.read().decode("utf-8", errors="ignore")
        return json.loads(raw)


def _neuro_lite_artifact_root() -> Path:
    explicit = os.getenv("ADAOS_NEURO_LITE_ARTIFACT_ROOT", "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    try:
        ctx = get_ctx()
        try:
            from adaos.services.skill.runtime_env import SkillRuntimeEnvironment

            skills_root = Path(ctx.paths.skills_dir()).expanduser().resolve()
            env = SkillRuntimeEnvironment(skills_root=skills_root, skill_name=_SERVICE_NAME)
            version = env.resolve_active_version()
            if version:
                return (env.files_dir(version) / "nlu" / "neuro_lite").resolve()
        except Exception:
            pass
        state_dir = Path(ctx.paths.state_dir()).expanduser().resolve()
        return state_dir / "nlu" / "neuro_lite"
    except Exception:
        base_dir = os.getenv("ADAOS_BASE_DIR", "").strip()
        if base_dir:
            return Path(base_dir).expanduser().resolve() / "state" / "nlu" / "neuro_lite"
        return Path.home() / ".adaos" / "state" / "nlu" / "neuro_lite"


def sync_curated_examples(examples_path: str | Path) -> dict[str, Any]:
    source = Path(examples_path).expanduser().resolve()
    root = _neuro_lite_artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    target = root / "examples_manifest.jsonl"
    if not source.exists():
        return {
            "ok": False,
            "reason": "curated_examples_missing",
            "source_examples_path": str(source),
            "artifact_root": str(root),
        }

    backup_path: Path | None = None
    if target.exists():
        rollback_dir = root / "rollback"
        rollback_dir.mkdir(parents=True, exist_ok=True)
        backup_path = rollback_dir / f"examples_manifest.{int(time.time() * 1000)}.jsonl"
        shutil.copy2(target, backup_path)

    tmp = target.with_suffix(".jsonl.tmp")
    shutil.copy2(source, tmp)
    os.replace(tmp, target)
    return {
        "ok": True,
        "source_examples_path": str(source),
        "active_examples_path": str(target),
        "backup_examples_path": str(backup_path) if backup_path else None,
        "artifact_root": str(root),
    }


async def rebuild_active_model(
    *,
    start_service: bool = True,
    stop_after: bool = False,
) -> Dict[str, Any]:
    supervisor = get_service_supervisor()
    service: dict[str, Any] = {
        "name": _SERVICE_NAME,
        "installed": False,
        "base_url": None,
        "started": False,
    }
    warnings: list[str] = []
    result: dict[str, Any] | None = None
    try:
        await supervisor.refresh_discovered(force=True)
        base_url = supervisor.resolve_base_url(_SERVICE_NAME)
    except Exception as exc:
        base_url = None
        warnings.append(f"service_discovery_failed:{type(exc).__name__}")

    if base_url:
        service["installed"] = True
        service["base_url"] = base_url
        if start_service:
            try:
                await supervisor.start(_SERVICE_NAME)
                service["started"] = True
                service["base_url"] = supervisor.resolve_base_url(_SERVICE_NAME) or base_url
            except Exception as exc:
                warnings.append(f"service_start_failed:{type(exc).__name__}")
        try:
            async with _SEMAPHORE:
                future = asyncio.to_thread(
                    _http_post_json,
                    f"{service['base_url']}/rebuild",
                    {},
                    timeout_ms=30_000,
                )
                result = await asyncio.wait_for(future, timeout=30.0)
        except Exception as exc:
            warnings.append(f"service_rebuild_failed:{type(exc).__name__}")
    else:
        warnings.append("service_base_url_unresolved")

    if stop_after and start_service:
        try:
            await supervisor.stop(_SERVICE_NAME)
        except Exception as exc:
            warnings.append(f"service_stop_failed:{type(exc).__name__}")

    return {
        "ok": bool(service.get("installed")) and bool(result and result.get("ok")),
        "service": service,
        "rebuild": result,
        "warnings": warnings,
    }


async def _ensure_service_base_url(supervisor: Any) -> str | None:
    await supervisor.refresh_discovered(force=True)
    base_url = supervisor.resolve_base_url(_SERVICE_NAME)
    if not base_url:
        return None
    async with _START_LOCK:
        base_url = supervisor.resolve_base_url(_SERVICE_NAME)
        if not base_url:
            return None
        await supervisor.start(_SERVICE_NAME)
        return supervisor.resolve_base_url(_SERVICE_NAME)


def _emit_stage(
    *,
    ctx: Any,
    text: str,
    webspace_id: str | None,
    request_id: str | None,
    meta: Mapping[str, Any],
    status: str,
    reason: str | None = None,
    intent: str | None = None,
    confidence: float | None = None,
    slots: Mapping[str, Any] | None = None,
    raw: Mapping[str, Any] | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "stage": "neuro_lite",
        "status": status,
        "text": text,
        "via": "neuro_lite",
    }
    if webspace_id:
        payload["webspace_id"] = webspace_id
    if request_id:
        payload["request_id"] = request_id
    if reason:
        payload["reason"] = reason
    if intent:
        payload["intent"] = intent
    if confidence is not None:
        payload["confidence"] = float(confidence)
    if slots:
        payload["slots"] = dict(slots)
    if raw:
        payload["raw"] = dict(raw)
    if isinstance(meta, Mapping) and meta:
        payload["_meta"] = dict(meta)
    try:
        bus_emit(ctx.bus, "nlu.trace.stage", payload, source="nlu.neuro_lite")
    except Exception:
        pass


def _emit_pipeline_stage(
    *,
    ctx: Any,
    stage: str,
    status: str,
    text: str,
    webspace_id: str | None,
    request_id: str | None,
    meta: Mapping[str, Any],
    via: str,
    reason: str | None = None,
    raw: Mapping[str, Any] | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "stage": stage,
        "status": status,
        "text": text,
        "via": via,
    }
    if webspace_id:
        payload["webspace_id"] = webspace_id
    if request_id:
        payload["request_id"] = request_id
    if reason:
        payload["reason"] = reason
    if raw:
        payload["raw"] = dict(raw)
    if isinstance(meta, Mapping) and meta:
        payload["_meta"] = dict(meta)
    try:
        bus_emit(ctx.bus, "nlu.trace.stage", payload, source="nlu.neuro_lite")
    except Exception:
        pass


def _emit_not_obtained(
    *,
    ctx: Any,
    text: str,
    webspace_id: str | None,
    request_id: str | None,
    meta: Mapping[str, Any],
    reason: str,
) -> None:
    payload: Dict[str, Any] = {"reason": reason, "text": text, "via": "neuro_lite"}
    if webspace_id:
        payload["webspace_id"] = webspace_id
    if request_id:
        payload["request_id"] = request_id
    if isinstance(meta, Mapping) and meta:
        payload["_meta"] = dict(meta)
    bus_emit(ctx.bus, "nlp.intent.not_obtained", payload, source="nlu.neuro_lite")


def _emit_next_stage(
    *,
    ctx: Any,
    text: str,
    webspace_id: str | None,
    request_id: str | None,
    meta: Mapping[str, Any],
    event_type: str,
    reason: str,
    locale: str | None,
    preferred_locales: list[str],
) -> None:
    payload: Dict[str, Any] = {"text": text}
    if webspace_id:
        payload["webspace_id"] = webspace_id
    if request_id:
        payload["request_id"] = request_id
    if locale:
        payload["locale"] = locale
        payload["request_locale"] = locale
    if preferred_locales:
        payload["preferred_locales"] = list(preferred_locales)
    next_meta = dict(meta) if isinstance(meta, Mapping) else {}
    next_meta["neuro_lite_fallback"] = True
    next_meta["neuro_lite_fallback_reason"] = reason
    payload["_meta"] = next_meta
    bus_emit(ctx.bus, event_type, payload, source="nlu.neuro_lite")


async def parse_text(
    text: str,
    *,
    webspace_id: str | None = None,
    request_id: str | None = None,
    locale: str | None = None,
    preferred_locales: list[str] | tuple[str, ...] | None = None,
) -> Dict[str, Any]:
    clean_text = str(text or "").strip()
    if not clean_text:
        return {"ok": False, "accepted": False, "reason": "empty_text", "via": "neuro_lite"}

    supervisor = get_service_supervisor()
    try:
        base_url = await _ensure_service_base_url(supervisor)
    except Exception:
        _log.warning("failed to start %s", _SERVICE_NAME, exc_info=True)
        return {"ok": False, "accepted": False, "reason": "neuro_lite_start_failed", "via": "neuro_lite"}
    if not base_url:
        return {"ok": False, "accepted": False, "reason": "neuro_lite_base_url_unresolved", "via": "neuro_lite"}

    payload: Dict[str, Any] = {"text": clean_text}
    if webspace_id:
        payload["webspace_id"] = webspace_id
    if request_id:
        payload["request_id"] = request_id
    if locale:
        payload["locale"] = locale
        payload["request_locale"] = locale
    preferred = [str(item).strip() for item in list(preferred_locales or []) if str(item).strip()]
    if preferred:
        payload["preferred_locales"] = preferred

    try:
        async with _SEMAPHORE:
            future = asyncio.to_thread(
                _http_post_json,
                f"{base_url}/parse",
                payload,
                timeout_ms=int(_PARSE_TIMEOUT_S * 1000),
            )
            data = await asyncio.wait_for(future, timeout=_PARSE_TIMEOUT_S)
    except asyncio.TimeoutError:
        return {"ok": False, "accepted": False, "reason": "neuro_lite_timeout", "via": "neuro_lite"}
    except Exception:
        _log.debug("neuro lite parse failed", exc_info=True)
        return {"ok": False, "accepted": False, "reason": "neuro_lite_parse_failed", "via": "neuro_lite"}

    result = data.get("result") if isinstance(data, Mapping) else None
    if not isinstance(result, Mapping):
        result = data if isinstance(data, Mapping) else {}

    top_intent = result.get("top_intent") or result.get("intent")
    intent = str(top_intent or "").strip()
    confidence = result.get("confidence")
    confidence_val = float(confidence) if isinstance(confidence, (int, float)) else 0.0
    slots = dict(result.get("slots")) if isinstance(result.get("slots"), Mapping) else {}
    evidence = result.get("evidence") if isinstance(result.get("evidence"), Mapping) else {}
    model_id = result.get("model_id") if isinstance(result.get("model_id"), str) else None
    accepted = bool(result.get("accepted", data.get("ok") if isinstance(data, Mapping) else False))

    raw = dict(result) if isinstance(result, Mapping) else {}
    base: Dict[str, Any] = {
        "via": "neuro_lite",
        "raw": raw,
        "slots": slots,
        "confidence": confidence_val,
        "model_id": model_id,
    }
    if not accepted or not intent:
        reason = str(evidence.get("reason") or result.get("reason") or "neuro_lite_abstained")
        return {"ok": False, "accepted": False, "reason": reason, "intent": intent or None, **base}
    if confidence_val < _ACCEPT_CONFIDENCE:
        return {
            "ok": False,
            "accepted": False,
            "reason": "neuro_lite_low_confidence",
            "intent": intent,
            **base,
        }
    return {"ok": True, "accepted": True, "intent": intent, **base}


@subscribe("nlp.intent.detect.neuro_lite")
async def _on_nlp_intent_detect_neuro_lite(evt: Any) -> None:
    payload = _payload(evt)
    text = payload.get("text") or payload.get("utterance")
    if not isinstance(text, str) or not text.strip():
        return
    text = text.strip()

    webspace_id = _resolve_webspace_id(payload)
    request_id = payload.get("request_id") if isinstance(payload.get("request_id"), str) else None
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), Mapping) else {}
    locale = _request_locale(payload)
    preferred_locales = _preferred_locales(payload)
    ctx = get_ctx()

    if not await is_stage_enabled(webspace_id, "neuro_lite"):
        _emit_stage(
            ctx=ctx,
            text=text,
            webspace_id=webspace_id,
            request_id=request_id,
            meta=meta,
            status="skipped",
            reason="runtime_disabled",
        )
        result_reason = "neuro_lite_runtime_disabled"
    else:
        result = await parse_text(
            text,
            webspace_id=webspace_id,
            request_id=request_id,
            locale=locale,
            preferred_locales=preferred_locales,
        )
        raw = result.get("raw") if isinstance(result.get("raw"), Mapping) else {}
        slots = dict(result.get("slots")) if isinstance(result.get("slots"), Mapping) else {}
        confidence = result.get("confidence")
        confidence_val = float(confidence) if isinstance(confidence, (int, float)) else 0.0
        intent = str(result.get("intent") or "").strip()

        if result.get("ok"):
            out: Dict[str, Any] = {
                "intent": intent,
                "confidence": confidence_val,
                "slots": slots,
                "text": text,
                "via": "neuro_lite",
                "_raw": raw,
            }
            if webspace_id:
                out["webspace_id"] = webspace_id
            if request_id:
                out["request_id"] = request_id
            if isinstance(meta, Mapping) and meta:
                out["_meta"] = dict(meta)
            _emit_stage(
                ctx=ctx,
                text=text,
                webspace_id=webspace_id,
                request_id=request_id,
                meta=meta,
                status="hit",
                intent=intent,
                confidence=confidence_val,
                slots=slots,
                raw=raw,
            )
            bus_emit(ctx.bus, "nlp.intent.detected", out, source="nlu.neuro_lite")
            return

        result_reason = str(result.get("reason") or "neuro_lite_failed")
        _emit_stage(
            ctx=ctx,
            text=text,
            webspace_id=webspace_id,
            request_id=request_id,
            meta=meta,
            status="miss",
            reason=result_reason,
            intent=intent or None,
            confidence=confidence_val if isinstance(confidence, (int, float)) else None,
            slots=slots,
            raw=raw,
        )

    if await is_stage_enabled(webspace_id, "neural"):
        _emit_next_stage(
            ctx=ctx,
            text=text,
            webspace_id=webspace_id,
            request_id=request_id,
            meta=meta,
            event_type="nlp.intent.detect.neural",
            reason=result_reason,
            locale=locale,
            preferred_locales=preferred_locales,
        )
        return

    if await is_stage_enabled(webspace_id, "rasa"):
        _emit_next_stage(
            ctx=ctx,
            text=text,
            webspace_id=webspace_id,
            request_id=request_id,
            meta=meta,
            event_type="nlp.intent.detect.rasa",
            reason=result_reason,
            locale=locale,
            preferred_locales=preferred_locales,
        )
        return

    _emit_pipeline_stage(
        ctx=ctx,
        stage="neural",
        status="skipped",
        text=text,
        webspace_id=webspace_id,
        request_id=request_id,
        meta=meta,
        via="neural",
        reason="runtime_disabled",
    )
    _emit_pipeline_stage(
        ctx=ctx,
        stage="rasa",
        status="skipped",
        text=text,
        webspace_id=webspace_id,
        request_id=request_id,
        meta=meta,
        via="rasa",
        reason="runtime_disabled",
    )
    _emit_not_obtained(
        ctx=ctx,
        text=text,
        webspace_id=webspace_id,
        request_id=request_id,
        meta=meta,
        reason=result_reason,
    )
