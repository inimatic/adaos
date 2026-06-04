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
_RECONFIRMABLE_CANDIDATE_STATUSES = {"pending", "validation_failed", "apply_requested"}
_YES_RE = re.compile(r"^\s*(да|ага|угу|ок|okay|yes|y|верно|подтверждаю|применяй|открой)\b", re.I | re.U)
_NO_RE = re.compile(r"^\s*(нет|неа|no|n|не\s+то|неверно|ошибка)\b", re.I | re.U)
_FIRST_ANSWERS = {
    "1",
    "one",
    "first",
    "option 1",
    "первый",
    "первая",
    "первое",
    "вариант 1",
    "вариант один",
}
_SECOND_ANSWERS = {
    "2",
    "two",
    "second",
    "option 2",
    "второй",
    "вторая",
    "второе",
    "вариант 2",
    "вариант два",
}


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


def _match_text_key(text: Any) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip().casefold())
    return value.strip(" \t\r\n.,!?;:()[]{}\"'")


def _candidate_matches_request_text(candidate: Mapping[str, Any], text: str) -> bool:
    expected = _match_text_key(text)
    if not expected:
        return False
    values = [
        candidate.get("text"),
        candidate.get("request_text"),
    ]
    for key in ("matched_text", "input_text"):
        preview = candidate.get("preview") if isinstance(candidate.get("preview"), Mapping) else {}
        values.append(preview.get(key))
    for value in values:
        if _match_text_key(value) == expected:
            return True
    return False


def _is_reconfirmable_voice_regex_candidate(candidate: Mapping[str, Any], text: str) -> bool:
    status = str(candidate.get("status") or "").strip()
    return (
        candidate.get("kind") == "regex_rule"
        and status in _RECONFIRMABLE_CANDIDATE_STATUSES
        and bool(_candidate_id(candidate))
        and _candidate_matches_request_text(candidate, text)
    )


def _clarification_from_confirmation(confirmation: Mapping[str, Any]) -> dict[str, Any]:
    confirmation_id = str(confirmation.get("id") or "").strip()
    status = str(confirmation.get("status") or "").strip() or "awaiting_user"
    answer = confirmation.get("answer") if isinstance(confirmation.get("answer"), str) else None
    rejected_candidates: list[str] = []
    if status in {"rejected", "needs_clarification"}:
        candidate_id = str(confirmation.get("candidate_id") or "").strip()
        if candidate_id:
            rejected_candidates.append(candidate_id)
    return {
        "id": confirmation_id.replace("confirm.", "clarify.", 1) if confirmation_id else f"clarify.{int(time.time() * 1000)}",
        "confirmation_id": confirmation_id or None,
        "ts": confirmation.get("ts") or time.time(),
        "status": status,
        "kind": "voice_confirmation",
        "uncertainty_kind": "candidate_confirmation",
        "candidate_id": confirmation.get("candidate_id"),
        "request_id": confirmation.get("request_id"),
        "request_text": confirmation.get("request_text"),
        "question": confirmation.get("question"),
        "allowed_answers": [
            {"id": "yes", "label": "yes", "effect": "apply_candidate"},
            {"id": "no", "label": "no", "effect": "reject_or_retry"},
        ],
        "answer": answer,
        "answered_at": confirmation.get("answered_at"),
        "attempt": confirmation.get("attempt") or 0,
        "target": dict(confirmation.get("target") or {}) if isinstance(confirmation.get("target"), Mapping) else None,
        "rejected_candidates": rejected_candidates,
        "_meta": dict(confirmation.get("_meta") or {}) if isinstance(confirmation.get("_meta"), Mapping) else {},
    }


def _upsert_clarification_session(teacher: dict[str, Any], confirmation: Mapping[str, Any]) -> None:
    session = _clarification_from_confirmation(confirmation)
    session_id = str(session.get("id") or "").strip()
    existing = _as_list(teacher.get("clarification_sessions"))
    next_items: list[dict[str, Any]] = []
    replaced = False
    for item in existing:
        if session_id and item.get("id") == session_id:
            next_items.append(session)
            replaced = True
        else:
            next_items.append(item)
    if not replaced:
        next_items.append(session)
    teacher["clarification_sessions"] = next_items[-_MAX_CONFIRMATIONS:]


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


def _negative_feedback_evidence(
    *,
    answer_text: str,
    answer_kind: str,
    reason: str,
    candidate_ids: Iterable[str] = (),
    selected_answer: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    rejected_candidates: list[str] = []
    for candidate_id in candidate_ids:
        token = str(candidate_id or "").strip()
        if token and token not in rejected_candidates:
            rejected_candidates.append(token)
    return {
        "kind": "negative_feedback",
        "reason": str(reason or "").strip() or "rejected",
        "answer": str(answer_text or "").strip(),
        "answer_kind": str(answer_kind or "").strip(),
        "selected_answer": dict(selected_answer or {}) if isinstance(selected_answer, Mapping) else None,
        "rejected_candidates": rejected_candidates,
        "recorded_at": time.time(),
    }


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


def _latest_active_clarification(teacher: Mapping[str, Any]) -> dict[str, Any] | None:
    items = [
        item
        for item in _as_list(teacher.get("clarification_sessions"))
        if item.get("status") == "awaiting_user"
        and item.get("kind") != "voice_confirmation"
        and not _is_expired(item)
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
        _upsert_clarification_session(teacher, confirmation)

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
                _upsert_clarification_session(teacher, next_item)
            items.append(next_item)
        teacher["pending_confirmations"] = items[-_MAX_CONFIRMATIONS:]

        if candidate_status and candidate_id:
            candidates: list[dict[str, Any]] = []
            feedback = next(
                (
                    item.get("negative_feedback")
                    for item in items
                    if item.get("id") == confirmation_id and isinstance(item.get("negative_feedback"), Mapping)
                ),
                None,
            )
            for item in _as_list(teacher.get("candidates")):
                next_item = dict(item)
                if next_item.get("id") == candidate_id:
                    next_item["status"] = candidate_status
                    next_item["status_reason"] = "voice_confirmation"
                    if isinstance(feedback, Mapping):
                        next_item["feedback_status"] = "rejected"
                        next_item["feedback_evidence"] = dict(feedback)
                        rejected = _as_list(next_item.get("rejected_alternatives"))
                        rejected.append(
                            {
                                "candidate_id": candidate_id,
                                "reason": feedback.get("reason"),
                                "answer": feedback.get("answer"),
                                "recorded_at": feedback.get("recorded_at"),
                            }
                        )
                        next_item["rejected_alternatives"] = rejected[-10:]
                    next_item["updated_at"] = time.time()
                candidates.append(next_item)
            teacher["candidates"] = candidates

    await _write_teacher(webspace_id, _mutate)


async def _append_clarification_session(webspace_id: str, session: Mapping[str, Any]) -> dict[str, Any]:
    now = time.time()
    session_id = str(session.get("id") or "").strip() or f"clarify.{int(now * 1000)}"
    normalized = {
        "id": session_id,
        "ts": session.get("ts") or now,
        "status": str(session.get("status") or "awaiting_user").strip() or "awaiting_user",
        "kind": str(session.get("kind") or "llm_clarification").strip() or "llm_clarification",
        "uncertainty_kind": str(session.get("uncertainty_kind") or "llm_uncertainty").strip() or "llm_uncertainty",
        "request_id": session.get("request_id"),
        "request_text": session.get("request_text"),
        "question": session.get("question"),
        "allowed_answers": _as_list(session.get("allowed_answers")),
        "attempt": session.get("attempt") or 0,
        "llm": dict(session.get("llm") or {}) if isinstance(session.get("llm"), Mapping) else None,
        "action_candidate": dict(session.get("action_candidate") or {})
        if isinstance(session.get("action_candidate"), Mapping)
        else None,
        "training_strategy": dict(session.get("training_strategy") or {})
        if isinstance(session.get("training_strategy"), Mapping)
        else None,
        "risk_notes": session.get("risk_notes") if isinstance(session.get("risk_notes"), str) else None,
        "_meta": dict(session.get("_meta") or {}) if isinstance(session.get("_meta"), Mapping) else {},
    }

    def _mutate(teacher: dict[str, Any]) -> None:
        items = _as_list(teacher.get("clarification_sessions"))
        request_id = str(normalized.get("request_id") or "").strip()
        next_items: list[dict[str, Any]] = []
        for item in items:
            same_session = item.get("id") == session_id
            same_active_request = (
                bool(request_id)
                and item.get("request_id") == request_id
                and item.get("status") == "awaiting_user"
            )
            if same_session or same_active_request:
                continue
            next_items.append(item)
        items.append(normalized)
        teacher["clarification_sessions"] = next_items[-(_MAX_CONFIRMATIONS - 1) :] + [normalized]

    await _write_teacher(webspace_id, _mutate)
    return normalized


async def _patch_clarification_session(
    webspace_id: str,
    session_id: str,
    patch: Mapping[str, Any],
) -> dict[str, Any] | None:
    updated: dict[str, Any] | None = None

    def _mutate(teacher: dict[str, Any]) -> None:
        nonlocal updated
        items: list[dict[str, Any]] = []
        for item in _as_list(teacher.get("clarification_sessions")):
            next_item = dict(item)
            if next_item.get("id") == session_id:
                next_item.update(dict(patch))
                updated = dict(next_item)
            items.append(next_item)
        teacher["clarification_sessions"] = items[-_MAX_CONFIRMATIONS:]

    await _write_teacher(webspace_id, _mutate)
    return updated


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


def _clarification_instruction(question: str, allowed_answers: Iterable[Mapping[str, Any]]) -> str:
    labels = [
        str(item.get("label") or item.get("title") or item.get("id") or "").strip()
        for item in allowed_answers
        if isinstance(item, Mapping)
    ]
    labels = [label for label in labels if label]
    if len(labels) < 2:
        return question
    lines = [question]
    for idx, label in enumerate(labels[:4], start=1):
        lines.append(f"{idx}. {label}")
    return "\n".join(lines)


async def request_clarification(
    webspace_id: str,
    session: Mapping[str, Any],
    *,
    meta: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    merged_meta = {
        **coerce_dict(session.get("_meta")),
        **dict(meta or {}),
        "webspace_id": webspace_id,
    }
    normalized = dict(session)
    normalized["_meta"] = merged_meta
    normalized["status"] = "awaiting_user"
    normalized.setdefault("kind", "llm_clarification")
    normalized.setdefault("uncertainty_kind", "llm_uncertainty")
    requested = await _append_clarification_session(webspace_id, normalized)
    request_id = str(requested.get("request_id") or "").strip()
    request_text = str(requested.get("request_text") or "").strip()
    question = str(requested.get("question") or "").strip()
    await append_event(
        webspace_id,
        make_event(
            webspace_id=webspace_id,
            request_id=request_id,
            request_text=request_text,
            kind="clarification.requested",
            title="Clarification requested",
            subtitle=question,
            raw=requested,
            meta=merged_meta,
        ),
    )
    bus_emit(
        get_ctx().bus,
        "nlp.teacher.clarification.requested",
        {"webspace_id": webspace_id, "session": requested, "_meta": merged_meta},
        source="nlu.teacher.confirmation",
    )
    if question and _route_id(merged_meta) == "voice_chat":
        await _emit_chat(
            webspace_id,
            _clarification_instruction(question, _as_list(requested.get("allowed_answers"))),
            merged_meta,
        )
    return requested


def _normalize_answer_text(text: str) -> str:
    value = re.sub(r"\s+", " ", str(text or "").strip().casefold())
    return value.strip(" \t\r\n.,!?;:()[]{}\"'")


def _classify_answer(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    if _NO_RE.search(raw):
        return "no"
    if _YES_RE.search(raw):
        return "yes"
    normalized = _normalize_answer_text(raw)
    if normalized in _FIRST_ANSWERS:
        return "first"
    if normalized in _SECOND_ANSWERS:
        return "second"
    return ""


def is_confirmation_answer(text: str) -> bool:
    return bool(_classify_answer(text))


def _looks_like_correction_text(text: str) -> bool:
    raw = str(text or "").strip()
    if not raw:
        return False
    lower = raw.casefold()
    markers = (
        "покажи",
        "открой",
        "показать",
        "открыть",
        "запусти",
        "нужно",
        "надо",
        "имел",
        "имела",
        "open",
        "show",
        "launch",
        "instead",
        "i meant",
    )
    return any(marker in lower for marker in markers)


async def should_suppress_voice_text_for_confirmation(
    webspace_id: str,
    text: str,
    *,
    within_s: float = 20.0,
) -> bool:
    if is_confirmation_answer(text):
        return False
    try:
        teacher = await _read_teacher(webspace_id)
    except Exception:
        return False
    active = _latest_active_confirmation(teacher) or _latest_active_clarification(teacher)
    if not active:
        return False
    try:
        ts = float(active.get("ts") or 0.0)
    except Exception:
        ts = 0.0
    if ts <= 0 or time.time() - ts > max(1.0, float(within_s)):
        return False
    raw = str(text or "").strip()
    if _looks_like_correction_text(raw):
        return False
    # Browser STT can emit a short tail of the original utterance after the
    # confirmation prompt. Ignore that fragment instead of teaching on it.
    return len(raw) <= 24


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


def _select_clarification_answer(session: Mapping[str, Any], answer: str, text: str) -> dict[str, Any]:
    allowed = _as_list(session.get("allowed_answers"))
    if answer == "first" and allowed:
        return dict(allowed[0])
    if answer == "second" and len(allowed) > 1:
        return dict(allowed[1])

    normalized_text = _normalize_answer_text(text)
    for item in allowed:
        item_id = _normalize_answer_text(str(item.get("id") or ""))
        label = _normalize_answer_text(str(item.get("label") or item.get("title") or ""))
        if answer and item_id == answer:
            return dict(item)
        if normalized_text and normalized_text in {item_id, label}:
            return dict(item)

    if answer in {"yes", "no"}:
        return {
            "id": answer,
            "label": answer,
            "effect": "accept_hypothesis" if answer == "yes" else "reject_hypothesis",
        }
    return {"id": answer or normalized_text, "label": str(text or "").strip(), "effect": "answer"}


async def _answer_clarification(
    webspace_id: str,
    session: Mapping[str, Any],
    *,
    answer: str,
    answer_text: str,
    meta: Mapping[str, Any],
) -> None:
    session_id = str(session.get("id") or "").strip()
    if not session_id:
        return
    selected = _select_clarification_answer(session, answer, answer_text)
    effect = str(selected.get("effect") or "").strip()
    status = "rejected" if answer == "no" or effect.startswith("reject") else "answered"
    request_id = str(session.get("request_id") or "").strip()
    request_text = str(session.get("request_text") or "").strip()
    session_meta = coerce_dict(session.get("_meta"))
    merged_meta = {**session_meta, **dict(meta), "route_id": "voice_chat"}
    rejected_candidate_ids: list[str] = []
    for source in (selected, session):
        candidate_id = str(source.get("candidate_id") or "").strip()
        if candidate_id and candidate_id not in rejected_candidate_ids:
            rejected_candidate_ids.append(candidate_id)
        action_candidate = source.get("action_candidate") if isinstance(source.get("action_candidate"), Mapping) else {}
        for key in ("candidate_id", "id"):
            candidate_id = str(action_candidate.get(key) or "").strip()
            if candidate_id and candidate_id not in rejected_candidate_ids:
                rejected_candidate_ids.append(candidate_id)
    negative_feedback = None
    if status == "rejected":
        negative_feedback = _negative_feedback_evidence(
            answer_text=answer_text,
            answer_kind=answer,
            reason=effect or "clarification_rejected",
            candidate_ids=rejected_candidate_ids,
            selected_answer=selected,
        )
    updated = await _patch_clarification_session(
        webspace_id,
        session_id,
        {
            "status": status,
            "answer": str(answer_text or "").strip(),
            "answer_kind": answer,
            "selected_answer": selected,
            "answered_at": time.time(),
            **({"negative_feedback": negative_feedback, "rejected_candidates": rejected_candidate_ids} if negative_feedback else {}),
        },
    )
    raw = {
        "session": updated if isinstance(updated, Mapping) else dict(session),
        "answer": str(answer_text or "").strip(),
        "answer_kind": answer,
        "selected_answer": selected,
        **({"negative_feedback": negative_feedback} if negative_feedback else {}),
    }
    await append_event(
        webspace_id,
        make_event(
            webspace_id=webspace_id,
            request_id=request_id,
            request_text=request_text,
            kind="clarification.answered",
            title="Clarification answered",
            subtitle=str(selected.get("label") or selected.get("id") or answer),
            raw=raw,
            meta=merged_meta,
        ),
    )
    bus_emit(
        get_ctx().bus,
        "nlp.teacher.clarification.answered",
        {
            "webspace_id": webspace_id,
            "session": updated if isinstance(updated, Mapping) else dict(session),
            "answer": str(answer_text or "").strip(),
            "answer_kind": answer,
            "selected_answer": selected,
            "_meta": merged_meta,
        },
        source="nlu.teacher.confirmation",
    )
    if effect == "apply_candidate" and selected.get("candidate_id"):
        bus_emit(
            get_ctx().bus,
            "nlp.teacher.candidate.apply",
            {
                "webspace_id": webspace_id,
                "candidate_id": str(selected.get("candidate_id")),
                "target": selected.get("target") if isinstance(selected.get("target"), Mapping) else None,
                "_meta": {**merged_meta, "nlu_teacher_clarification_id": session_id},
            },
            source="nlu.teacher.confirmation",
        )


async def has_recent_voice_confirmation(webspace_id: str, *, within_s: float = 15.0) -> bool:
    try:
        teacher = await _read_teacher(webspace_id)
    except Exception:
        return False
    now = time.time()
    items = _as_list(teacher.get("pending_confirmations")) + _as_list(teacher.get("clarification_sessions"))
    for item in reversed(items):
        status = str(item.get("status") or "").strip()
        if status not in {"awaiting_user", "accepted", "answered", "rejected", "needs_clarification"}:
            continue
        try:
            marker = float(item.get("answered_at") or item.get("ts") or 0.0)
        except Exception:
            marker = 0.0
        if marker > 0 and now - marker <= max(1.0, float(within_s)):
            return True
    return False


async def request_existing_candidate_confirmation(
    webspace_id: str,
    text: str,
    *,
    request_id: str = "",
    meta: Mapping[str, Any] | None = None,
) -> bool:
    """Re-open voice confirmation for a previously proposed regex candidate."""

    request_text = str(text or "").strip()
    if not request_text:
        return False
    merged_meta = {**dict(meta or {}), "route_id": "voice_chat", "webspace_id": webspace_id}
    try:
        teacher = await _read_teacher(webspace_id)
    except Exception:
        _log.debug("failed to read teacher state for existing confirmation webspace=%s", webspace_id, exc_info=True)
        return False

    candidates = [
        candidate
        for candidate in _as_list(teacher.get("candidates"))
        if _is_reconfirmable_voice_regex_candidate(candidate, request_text)
    ]
    if not candidates:
        return False
    candidates.sort(key=lambda item: float(item.get("updated_at") or item.get("ts") or 0.0), reverse=True)
    candidate = candidates[0]
    try:
        attempt = int(merged_meta.get("nlu_teacher_confirmation_attempt") or 0)
    except Exception:
        attempt = 0
    candidate_request_id = _request_id(candidate)
    effective_request_id = str(request_id or candidate_request_id or "").strip()
    confirmation = {
        "id": f"confirm.{int(time.time() * 1000)}",
        "ts": time.time(),
        "status": "awaiting_user",
        "attempt": max(0, attempt),
        "candidate_id": _candidate_id(candidate),
        "request_id": effective_request_id,
        "candidate_request_id": candidate_request_id,
        "request_text": request_text,
        "question": _confirmation_question(candidate),
        "target": dict(candidate.get("target") or {}) if isinstance(candidate.get("target"), Mapping) else None,
        "reused_candidate": True,
        "_meta": dict(merged_meta),
    }
    try:
        await _append_confirmation(webspace_id, confirmation)
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=effective_request_id,
                request_text=request_text,
                kind="confirmation.requested",
                title="Voice confirmation requested",
                subtitle=confirmation["question"],
                raw=confirmation,
                meta=merged_meta,
            ),
        )
        await _emit_chat(
            webspace_id,
            (
                "Для такого обращения уже есть ожидающий шаблон NLU.\n"
                + _confirmation_instruction(str(confirmation["question"]), attempt=attempt)
            ),
            merged_meta,
        )
        return True
    except Exception:
        _log.warning("failed to request existing NLU Teacher confirmation webspace=%s", webspace_id, exc_info=True)
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
        clarification = _latest_active_clarification(teacher)
    except Exception:
        _log.debug("failed to read teacher confirmation state webspace=%s", webspace_id, exc_info=True)
        return
    if not confirmation:
        if clarification:
            try:
                await _answer_clarification(
                    webspace_id,
                    clarification,
                    answer=answer,
                    answer_text=text,
                    meta=meta,
                )
            except Exception:
                _log.warning("failed to answer NLU Teacher clarification webspace=%s", webspace_id, exc_info=True)
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
            candidate_status="apply_requested",
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
        await _emit_chat(
            webspace_id,
            "Принял. Применяю новое правило NLU; после этого повторите запрос для проверки.",
            merged_meta,
        )
        try:
            from adaos.services.nlu.candidates_runtime import _on_candidate_apply  # local import to avoid cycles

            await _on_candidate_apply(
                {
                    "webspace_id": webspace_id,
                    "candidate_id": candidate_id,
                    "target": confirmation.get("target") if isinstance(confirmation.get("target"), Mapping) else None,
                    "_meta": {
                        **merged_meta,
                        "nlu_teacher_confirmation_id": confirmation_id,
                        "nlu_teacher_confirmation_answer": "yes",
                    },
                }
            )
        except Exception:
            _log.warning("failed to apply confirmed NLU Teacher candidate webspace=%s candidate_id=%s", webspace_id, candidate_id, exc_info=True)
        return

    if attempt < 1:
        negative_feedback = _negative_feedback_evidence(
            answer_text=text,
            answer_kind=answer,
            reason="voice_confirmation_rejected",
            candidate_ids=[candidate_id],
        )
        await _patch_confirmation(
            webspace_id,
            confirmation_id,
            {
                "status": "rejected",
                "answer": text.strip(),
                "answered_at": time.time(),
                "retry_requested_at": time.time(),
                "negative_feedback": negative_feedback,
                "rejected_candidates": [candidate_id] if candidate_id else [],
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
                raw={"confirmation": confirmation, "answer": text.strip(), "retry": True, "negative_feedback": negative_feedback},
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

    negative_feedback = _negative_feedback_evidence(
        answer_text=text,
        answer_kind=answer,
        reason="voice_confirmation_needs_clarification",
        candidate_ids=[candidate_id],
    )
    await _patch_confirmation(
        webspace_id,
        confirmation_id,
        {
            "status": "needs_clarification",
            "answer": text.strip(),
            "answered_at": time.time(),
            "negative_feedback": negative_feedback,
            "rejected_candidates": [candidate_id] if candidate_id else [],
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
            raw={"confirmation": confirmation, "answer": text.strip(), "negative_feedback": negative_feedback},
            meta=merged_meta,
        ),
    )
    await _emit_chat(webspace_id, "Тогда уточните, что именно нужно сделать.", merged_meta)
