from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.nlu.teacher_events import append_event, make_event
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.nlu.teacher.confirmation")

_MAX_CONFIRMATIONS = 50
_CONFIRMATION_TTL_S = 15 * 60
_YES_RE = re.compile(r"^\s*(да|ага|угу|ок|okay|yes|y|верно|подтверждаю|применяй|открой)\b", re.I | re.U)
_NO_RE = re.compile(r"^\s*(нет|неа|no|n|не\s+то|неверно|ошибка)\b", re.I | re.U)


def _nlu_confirmation_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.teacher_confirmation_runtime",
        owner="core:nlu.teacher_confirmation",
        channel="core.nlu.teacher_confirmation.async",
    )


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


def _teacher_obj(data_map: Any) -> dict[str, Any]:
    return coerce_dict(getattr(data_map, "get", lambda _k: None)("nlu_teacher"))


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, (str, bytes, bytearray)) or isinstance(value, Mapping) or not isinstance(value, Iterable):
        return []
    return [dict(x) for x in iter_mappings(value)]


def _route_id(meta: Mapping[str, Any]) -> str:
    return str(meta.get("route_id") or meta.get("route") or "").strip()


def _candidate_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("id") or "").strip()


def _request_id(candidate: Mapping[str, Any]) -> str:
    return str(candidate.get("request_id") or "").strip()


def _is_voice_regex_candidate(candidate: Mapping[str, Any], meta: Mapping[str, Any]) -> bool:
    return (
        _route_id(meta) == "voice_chat"
        and candidate.get("kind") == "regex_rule"
        and candidate.get("status") == "pending"
        and bool(_candidate_id(candidate))
    )


def _slot_value(candidate: Mapping[str, Any], *keys: str) -> str:
    preview = candidate.get("preview") if isinstance(candidate.get("preview"), Mapping) else {}
    slots = preview.get("slots") if isinstance(preview.get("slots"), Mapping) else {}
    for key in keys:
        value = slots.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _confirmation_question(candidate: Mapping[str, Any]) -> str:
    rr = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
    intent = str(rr.get("intent") or "").strip()
    if intent in {"desktop.open_scenario", "desktop.switch_scenario"}:
        label = _slot_value(candidate, "scenario_id", "scenario")
        if label:
            return f"Открыть {label}?"
        return "Открыть этот сценарий?"
    if intent == "desktop.open_modal":
        label = _slot_value(candidate, "modal_id", "modal", "app_id", "app")
        if label:
            return f"Открыть {label}?"
        return "Открыть это окно?"
    if intent in {"desktop.open_weather", "weather.current"} or "weather" in intent:
        city = _slot_value(candidate, "city", "location")
        if city:
            return f"Показать погоду в {city}?"
        return "Показать погоду?"
    name = coerce_dict(candidate.get("candidate")).get("name")
    if isinstance(name, str) and name.strip():
        return f"Я правильно понял: {name.strip()}?"
    return "Я правильно понял намерение?"


def _confirmation_instruction(question: str, *, attempt: int) -> str:
    suffix = "Скажите «да», чтобы применить обучение, или «нет», чтобы я попробовал другой вариант."
    if attempt >= 1:
        suffix = "Скажите «да», чтобы применить обучение, или «нет», чтобы я попросил уточнение."
    return f"{question}\n{suffix}"


def _is_expired(item: Mapping[str, Any]) -> bool:
    try:
        ts = float(item.get("ts") or 0.0)
    except Exception:
        ts = 0.0
    return ts > 0 and (time.time() - ts) > _CONFIRMATION_TTL_S


def _latest_active_confirmation(teacher: Mapping[str, Any]) -> dict[str, Any] | None:
    items = [
        item
        for item in _as_list(teacher.get("pending_confirmations"))
        if item.get("status") == "awaiting_user" and not _is_expired(item)
    ]
    if not items:
        return None
    items.sort(key=lambda x: float(x.get("ts") or 0.0), reverse=True)
    return items[0]


async def _read_teacher(webspace_id: str) -> dict[str, Any]:
    async with async_get_ydoc(webspace_id, read_only=True, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
        return _teacher_obj(ydoc.get_map("data"))


async def _write_teacher(webspace_id: str, mutator) -> dict[str, Any]:
    async with _nlu_confirmation_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            mutator(teacher)
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)
            return dict(teacher)


async def _append_confirmation(webspace_id: str, confirmation: dict[str, Any]) -> None:
    candidate_id = str(confirmation.get("candidate_id") or "").strip()

    def _mutate(teacher: dict[str, Any]) -> None:
        items = _as_list(teacher.get("pending_confirmations"))
        if candidate_id:
            items = [
                item
                for item in items
                if not (item.get("candidate_id") == candidate_id and item.get("status") == "awaiting_user")
            ]
        items.append(dict(confirmation))
        teacher["pending_confirmations"] = items[-_MAX_CONFIRMATIONS:]

    await _write_teacher(webspace_id, _mutate)


async def _patch_confirmation(
    webspace_id: str,
    confirmation_id: str,
    patch: Mapping[str, Any],
    *,
    candidate_status: str | None = None,
) -> None:
    def _mutate(teacher: dict[str, Any]) -> None:
        items: list[dict[str, Any]] = []
        candidate_id = ""
        for item in _as_list(teacher.get("pending_confirmations")):
            next_item = dict(item)
            if next_item.get("id") == confirmation_id:
                next_item.update(dict(patch))
                candidate_id = str(next_item.get("candidate_id") or "").strip()
            items.append(next_item)
        teacher["pending_confirmations"] = items[-_MAX_CONFIRMATIONS:]

        if candidate_status and candidate_id:
            candidates: list[dict[str, Any]] = []
            for item in _as_list(teacher.get("candidates")):
                next_item = dict(item)
                if next_item.get("id") == candidate_id:
                    next_item["status"] = candidate_status
                    next_item["status_reason"] = "voice_confirmation"
                    next_item["updated_at"] = time.time()
                candidates.append(next_item)
            teacher["candidates"] = candidates

    await _write_teacher(webspace_id, _mutate)


async def _emit_chat(webspace_id: str, text: str, meta: Mapping[str, Any]) -> None:
    ctx = get_ctx()
    bus_emit(
        ctx.bus,
        "io.out.chat.append",
        {
            "id": "",
            "from": "hub",
            "text": text,
            "ts": time.time(),
            "_meta": {"webspace_id": webspace_id, **dict(meta), "route_id": "voice_chat"},
        },
        source="nlu.teacher.confirmation",
    )


def _classify_answer(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _NO_RE.search(raw):
        return "no"
    if _YES_RE.search(raw):
        return "yes"
    return ""


def is_confirmation_answer(text: str) -> bool:
    return bool(_classify_answer(text))


def _retry_text_from_rejection(answer_text: str, original_text: str) -> str:
    raw = str(answer_text or "").strip()
    original = str(original_text or "").strip()
    if len(raw) < 8:
        return original
    lower = raw.lower()
    markers = ("нужно", "надо", "имел", "имела", "открой", "покажи", "запусти", "instead", "i meant")
    if any(marker in lower for marker in markers):
        return raw
    return original


async def has_recent_voice_confirmation(webspace_id: str, *, within_s: float = 15.0) -> bool:
    try:
        teacher = await _read_teacher(webspace_id)
    except Exception:
        return False
    now = time.time()
    for item in reversed(_as_list(teacher.get("pending_confirmations"))):
        status = str(item.get("status") or "").strip()
        if status not in {"awaiting_user", "accepted", "rejected", "needs_clarification"}:
            continue
        try:
            marker = float(item.get("answered_at") or item.get("ts") or 0.0)
        except Exception:
            marker = 0.0
        if marker > 0 and now - marker <= max(1.0, float(within_s)):
            return True
    return False


@subscribe("nlp.teacher.candidate.proposed")
async def _on_candidate_proposed(evt: Any) -> None:
    payload = _payload(evt)
    meta = coerce_dict(payload.get("_meta"))
    candidate = payload.get("candidate") if isinstance(payload.get("candidate"), Mapping) else {}
    if not _is_voice_regex_candidate(candidate, meta):
        return

    webspace_id = _resolve_webspace_id(payload)
    request_id = _request_id(candidate)
    request_text = str(candidate.get("text") or "").strip()
    try:
        attempt = int(meta.get("nlu_teacher_confirmation_attempt") or 0)
    except Exception:
        attempt = 0
    confirmation = {
        "id": f"confirm.{int(time.time() * 1000)}",
        "ts": time.time(),
        "status": "awaiting_user",
        "attempt": max(0, attempt),
        "candidate_id": _candidate_id(candidate),
        "request_id": request_id,
        "request_text": request_text,
        "question": _confirmation_question(candidate),
        "target": dict(candidate.get("target") or {}) if isinstance(candidate.get("target"), Mapping) else None,
        "_meta": dict(meta),
    }
    try:
        await _append_confirmation(webspace_id, confirmation)
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="confirmation.requested",
                title="Voice confirmation requested",
                subtitle=confirmation["question"],
                raw=confirmation,
                meta=meta,
            ),
        )
        await _emit_chat(
            webspace_id,
            _confirmation_instruction(str(confirmation["question"]), attempt=attempt),
            meta,
        )
    except Exception:
        _log.warning("failed to request NLU Teacher confirmation webspace=%s", webspace_id, exc_info=True)


@subscribe("voice.chat.user")
async def _on_voice_chat_user(evt: Any) -> None:
    payload = _payload(evt)
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        return
    answer = _classify_answer(text)
    if not answer:
        return

    webspace_id = _resolve_webspace_id(payload)
    meta = coerce_dict(payload.get("_meta"))
    try:
        teacher = await _read_teacher(webspace_id)
        confirmation = _latest_active_confirmation(teacher)
    except Exception:
        _log.debug("failed to read teacher confirmation state webspace=%s", webspace_id, exc_info=True)
        return
    if not confirmation:
        return

    confirmation_id = str(confirmation.get("id") or "").strip()
    candidate_id = str(confirmation.get("candidate_id") or "").strip()
    request_id = str(confirmation.get("request_id") or "").strip()
    request_text = str(confirmation.get("request_text") or "").strip()
    try:
        attempt = int(confirmation.get("attempt") or 0)
    except Exception:
        attempt = 0
    confirmation_meta = coerce_dict(confirmation.get("_meta"))
    merged_meta = {**confirmation_meta, **dict(meta), "route_id": "voice_chat"}

    if answer == "yes":
        await _patch_confirmation(
            webspace_id,
            confirmation_id,
            {
                "status": "accepted",
                "answer": text.strip(),
                "answered_at": time.time(),
            },
        )
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="confirmation.accepted",
                title="Voice confirmation accepted",
                subtitle=candidate_id,
                raw={"confirmation": confirmation, "answer": text.strip()},
                meta=merged_meta,
            ),
        )
        await _emit_chat(webspace_id, "Применяю новое правило NLU. После этого повторите запрос для проверки.", merged_meta)
        bus_emit(
            get_ctx().bus,
            "nlp.teacher.candidate.apply",
            {
                "webspace_id": webspace_id,
                "candidate_id": candidate_id,
                "target": confirmation.get("target") if isinstance(confirmation.get("target"), Mapping) else None,
                "_meta": {
                    **merged_meta,
                    "nlu_teacher_confirmation_id": confirmation_id,
                    "nlu_teacher_confirmation_answer": "yes",
                },
            },
            source="nlu.teacher.confirmation",
        )
        return

    if attempt < 1:
        await _patch_confirmation(
            webspace_id,
            confirmation_id,
            {
                "status": "rejected",
                "answer": text.strip(),
                "answered_at": time.time(),
                "retry_requested_at": time.time(),
            },
            candidate_status="rejected",
        )
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=request_text,
                kind="confirmation.rejected",
                title="Voice confirmation rejected",
                subtitle="retrying",
                raw={"confirmation": confirmation, "answer": text.strip(), "retry": True},
                meta=merged_meta,
            ),
        )
        await _emit_chat(webspace_id, "Хорошо, пробую ещё один вариант.", merged_meta)
        retry_text = _retry_text_from_rejection(text, request_text)
        retry_id = f"{request_id or 'nlu'}.retry1"
        bus_emit(
            get_ctx().bus,
            "nlp.teacher.request",
            {
                "webspace_id": webspace_id,
                "request": {
                    "id": f"teach.retry.{int(time.time() * 1000)}",
                    "ts": time.time(),
                    "text": retry_text,
                    "reason": "voice_confirmation_rejected",
                    "via": "voice_confirmation",
                    "request_id": retry_id,
                    "classification": {
                        "teachable": True,
                        "class": "nlu_gap_retry",
                        "reason": "voice_confirmation_rejected",
                    },
                    "status": "pending",
                    "_meta": {
                        **merged_meta,
                        "nlu_teacher_confirmation_retry": True,
                        "nlu_teacher_confirmation_attempt": 1,
                        "rejected_candidate_id": candidate_id,
                        "previous_request_id": request_id,
                        "original_request_text": request_text,
                    },
                },
            },
            source="nlu.teacher.confirmation",
        )
        return

    await _patch_confirmation(
        webspace_id,
        confirmation_id,
        {
            "status": "needs_clarification",
            "answer": text.strip(),
            "answered_at": time.time(),
        },
        candidate_status="rejected",
    )
    await append_event(
        webspace_id,
        make_event(
            webspace_id=webspace_id,
            request_id=request_id,
            request_text=request_text,
            kind="confirmation.needs_clarification",
            title="Voice confirmation needs clarification",
            subtitle=candidate_id,
            raw={"confirmation": confirmation, "answer": text.strip()},
            meta=merged_meta,
        ),
    )
    await _emit_chat(webspace_id, "Тогда уточните, что именно нужно сделать.", merged_meta)
