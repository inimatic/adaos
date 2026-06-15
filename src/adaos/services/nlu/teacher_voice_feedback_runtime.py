from __future__ import annotations

import time
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.nlu.ycoerce import coerce_dict
from adaos.services.yjs.webspace import default_webspace_id


def _payload(evt: Any) -> dict[str, Any]:
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


def _route_id(meta: Mapping[str, Any]) -> str:
    return str(meta.get("route_id") or meta.get("route") or "").strip()


def _voice_meta(payload: Mapping[str, Any]) -> dict[str, Any] | None:
    meta = coerce_dict(payload.get("_meta"))
    if _route_id(meta) != "voice_chat":
        return None
    return meta


def _emit_chat(webspace_id: str, text: str, meta: Mapping[str, Any]) -> None:
    bus_emit(
        get_ctx().bus,
        "io.out.chat.append",
        {
            "id": "",
            "from": "hub",
            "text": text,
            "ts": time.time(),
            "_meta": {"webspace_id": webspace_id, **dict(meta), "route_id": "voice_chat"},
        },
        source="nlu.teacher.voice_feedback",
    )


@subscribe("nlp.teacher.candidate.duplicate_suppressed")
async def _on_candidate_duplicate_suppressed(evt: Any) -> None:
    payload = _payload(evt)
    meta = _voice_meta(payload)
    if meta is None:
        return
    webspace_id = _resolve_webspace_id(payload)
    suppressed = coerce_dict(payload.get("suppressed"))
    preview = coerce_dict(suppressed.get("preview"))
    reason = str(preview.get("status") or "duplicate").strip()
    if preview and not bool(preview.get("ok")):
        text = (
            "Я проверил гипотезу обучения, но предложенный шаблон не совпал с вашей фразой. "
            f"Я не применяю его автоматически ({reason}). Детали записаны в NLU Teacher."
        )
    else:
        text = (
            "Я проверил гипотезу обучения, но такой шаблон уже есть или уже ожидает проверки. "
            "Я не создал дубликат. Детали записаны в NLU Teacher."
        )
    _emit_chat(webspace_id, text, meta)


@subscribe("nlp.teacher.candidate.proposed")
async def _on_candidate_proposed(evt: Any) -> None:
    payload = _payload(evt)
    meta = _voice_meta(payload)
    if meta is None:
        return
    candidate = coerce_dict(payload.get("candidate"))
    candidate_payload: Mapping[str, Any] = candidate if candidate else payload
    if str(candidate_payload.get("status") or "").strip() != "quarantined":
        return
    preview = coerce_dict(candidate_payload.get("preview"))
    if not preview:
        action_candidate = coerce_dict(candidate_payload.get("action_candidate"))
        preview = coerce_dict(action_candidate.get("phrase_preview"))
    reason = str(preview.get("status") or "quarantined").strip()
    webspace_id = _resolve_webspace_id(payload)
    _emit_chat(
        webspace_id,
        (
            "Я не смог надежно распознать команду. "
            f"Предложенное правило NLU не прошло проверку ({reason}) и отправлено в карантин. "
            "Повторите команду, пожалуйста. Детали записаны в NLU Teacher."
        ),
        meta,
    )


@subscribe("nlp.teacher.llm.deferred")
async def _on_llm_deferred(evt: Any) -> None:
    payload = _payload(evt)
    meta = _voice_meta(payload)
    if meta is None:
        return
    webspace_id = _resolve_webspace_id(payload)
    reason = str(payload.get("reason") or "llm_unavailable").strip()
    _emit_chat(
        webspace_id,
        (
            "Я записал запрос, но сейчас не смог завершить анализ через LLM/MCP. "
            f"Причина: {reason}. Вернусь к нему позже; подробности в NLU Teacher."
        ),
        meta,
    )


@subscribe("nlp.teacher.llm.retrying")
async def _on_llm_retrying(evt: Any) -> None:
    payload = _payload(evt)
    meta = _voice_meta(payload)
    if meta is None:
        return
    webspace_id = _resolve_webspace_id(payload)
    reason = str(payload.get("reason") or "llm_unavailable").strip()
    attempt = int(payload.get("attempt") or 0)
    max_attempts = int(payload.get("max_attempts") or 0)
    suffix = f" ({attempt}/{max_attempts})" if attempt and max_attempts else ""
    _emit_chat(
        webspace_id,
        (
            "\u0410\u043d\u0430\u043b\u0438\u0437 \u0447\u0435\u0440\u0435\u0437 LLM/MCP \u0432\u0440\u0435\u043c\u0435\u043d\u043d\u043e \u0437\u0430\u0434\u0435\u0440\u0436\u0438\u0432\u0430\u0435\u0442\u0441\u044f"
            f"{suffix}. \u042f \u0437\u0430\u043f\u0438\u0441\u0430\u043b \u0437\u0430\u043f\u0440\u043e\u0441 \u0438 \u043f\u0440\u043e\u0431\u0443\u044e \u0435\u0449\u0435 \u0440\u0430\u0437. "
            f"\u041f\u0440\u0438\u0447\u0438\u043d\u0430: {reason}."
        ),
        meta,
    )


@subscribe("nlp.teacher.ignored")
async def _on_llm_ignored(evt: Any) -> None:
    payload = _payload(evt)
    meta = _voice_meta(payload)
    if meta is None:
        return
    webspace_id = _resolve_webspace_id(payload)
    suggestion = coerce_dict(payload.get("suggestion"))
    notes = str(suggestion.get("notes") or "").strip()
    suffix = f" Причина: {notes}" if notes else ""
    _emit_chat(
        webspace_id,
        "Я разобрал запрос, но не нашел безопасного шаблона или действия для обучения." + suffix,
        meta,
    )


@subscribe("nlp.teacher.candidate.verified")
async def _on_candidate_verified(evt: Any) -> None:
    payload = _payload(evt)
    meta = _voice_meta(payload)
    if meta is None:
        return
    verification = coerce_dict(payload.get("verification"))
    if str(verification.get("status") or "").strip() == "intent_matched":
        return
    webspace_id = _resolve_webspace_id(payload)
    probe = coerce_dict(verification.get("probe"))
    expected = str(verification.get("expected_intent") or "").strip()
    actual = str(probe.get("intent") or probe.get("reason") or "unknown").strip()
    _emit_chat(
        webspace_id,
        (
            "Я применил шаблон, но проверка не подтвердила новое понимание. "
            f"Ожидал intent {expected or '-'}, получил {actual or '-'}. Детали в NLU Teacher."
        ),
        meta,
    )


@subscribe("nlp.teacher.understanding.acquired")
async def _on_understanding_acquired(evt: Any) -> None:
    payload = _payload(evt)
    meta = _voice_meta(payload)
    if meta is None:
        return
    webspace_id = _resolve_webspace_id(payload)
    intent = str(payload.get("intent") or "").strip()
    _emit_chat(
        webspace_id,
        (
            "Готово. Новое понимание установлено и проверено"
            + (f": {intent}." if intent else ".")
            + " Сейчас выполняю запрос."
        ),
        meta,
    )
