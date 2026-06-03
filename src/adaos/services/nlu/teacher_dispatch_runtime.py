from __future__ import annotations

import logging
import time
from collections.abc import Iterable, Mapping
from typing import Any

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.nlu.teacher_events import append_event, make_event, rebuild_events_by_candidate
from adaos.services.nlu.ycoerce import coerce_dict, iter_mappings
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.webspace import default_webspace_id

_log = logging.getLogger("adaos.nlu.teacher.dispatch")

_AUTO_DISPATCH_SIDE_EFFECTS = {"read_only", "ui_navigation"}
_DISPATCH_TERMINAL_STATUSES = {"requested", "emitted", "succeeded", "blocked"}


def _nlu_teacher_dispatch_write_meta():
    return ystore_write_metadata(
        root_names=["data"],
        source="nlu.teacher_dispatch_runtime",
        owner="core:nlu.teacher_dispatch",
        channel="core.nlu.teacher_dispatch.async",
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


def _candidate_intent(candidate: Mapping[str, Any]) -> str:
    rr = candidate.get("regex_rule") if isinstance(candidate.get("regex_rule"), Mapping) else {}
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    strategy = candidate.get("strategy_candidate") if isinstance(candidate.get("strategy_candidate"), Mapping) else {}
    for value in (rr.get("intent"), candidate.get("intent"), action.get("intent"), strategy.get("intent")):
        token = str(value or "").strip()
        if token:
            return token
    return ""


def _verification_probe(payload: Mapping[str, Any]) -> dict[str, Any]:
    verification = payload.get("verification") if isinstance(payload.get("verification"), Mapping) else {}
    probe = verification.get("probe") if isinstance(verification.get("probe"), Mapping) else {}
    return dict(probe)


def _slots_from_payload(payload: Mapping[str, Any], candidate: Mapping[str, Any]) -> dict[str, Any]:
    probe = _verification_probe(payload)
    probe_slots = probe.get("slots") if isinstance(probe.get("slots"), Mapping) else {}
    payload_slots = payload.get("slots") if isinstance(payload.get("slots"), Mapping) else {}
    preview = candidate.get("preview") if isinstance(candidate.get("preview"), Mapping) else {}
    preview_slots = preview.get("slots") if isinstance(preview.get("slots"), Mapping) else {}
    action = candidate.get("action_candidate") if isinstance(candidate.get("action_candidate"), Mapping) else {}
    action_slots = action.get("slots") if isinstance(action.get("slots"), Mapping) else {}
    return {**dict(action_slots), **dict(preview_slots), **dict(payload_slots), **dict(probe_slots)}


def _side_effect_policy(candidate: Mapping[str, Any]) -> tuple[bool, dict[str, Any]]:
    validation = candidate.get("validation") if isinstance(candidate.get("validation"), Mapping) else {}
    policy = validation.get("side_effect_policy") if isinstance(validation.get("side_effect_policy"), Mapping) else {}
    side_effect = str(policy.get("side_effect_class") or "").strip()
    approval = str(policy.get("approval") or "").strip()
    if validation and not validation.get("ok"):
        return False, {"reason": "validation_not_passed", "side_effect_class": side_effect, "approval": approval}
    if side_effect in _AUTO_DISPATCH_SIDE_EFFECTS and approval in {"operator_apply_allowed", ""}:
        return True, {"side_effect_class": side_effect, "approval": approval or "operator_apply_allowed"}
    return False, {
        "reason": "side_effect_policy",
        "side_effect_class": side_effect or "unknown",
        "approval": approval or "unknown",
    }


def _is_voice_confirmed(meta: Mapping[str, Any]) -> bool:
    route_id = str(meta.get("route_id") or meta.get("route") or "").strip()
    answer = str(meta.get("nlu_teacher_confirmation_answer") or "").strip().casefold()
    return route_id == "voice_chat" and answer == "yes"


async def _patch_candidate_dispatch(
    *,
    webspace_id: str,
    candidate_id: str,
    patch: Mapping[str, Any],
) -> dict[str, Any] | None:
    updated: dict[str, Any] | None = None
    async with _nlu_teacher_dispatch_write_meta():
        async with async_get_ydoc(webspace_id, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            next_candidates: list[dict[str, Any]] = []
            for item in _as_list(teacher.get("candidates")):
                candidate = dict(item)
                if candidate.get("id") == candidate_id:
                    candidate.update(dict(patch))
                    updated = dict(candidate)
                next_candidates.append(candidate)
            if updated is None:
                return None
            teacher["candidates"] = next_candidates
            rebuild_events_by_candidate(teacher)
            with ydoc.begin_transaction() as txn:
                data_map.set(txn, "nlu_teacher", teacher)
    return updated


async def _read_candidate(webspace_id: str, candidate_id: str) -> dict[str, Any] | None:
    async with async_get_ydoc(webspace_id, read_only=True, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
        teacher = _teacher_obj(ydoc.get_map("data"))
        for item in _as_list(teacher.get("candidates")):
            if item.get("id") == candidate_id:
                return dict(item)
    return None


@subscribe("nlp.teacher.understanding.acquired")
async def _on_understanding_acquired(evt: Any) -> None:
    payload = _payload(evt)
    meta = coerce_dict(payload.get("_meta"))
    if not _is_voice_confirmed(meta):
        return
    if bool(meta.get("nlu_teacher_dispatch_disabled")):
        return

    webspace_id = _resolve_webspace_id(payload)
    candidate_id = str(payload.get("candidate_id") or "").strip()
    if not candidate_id:
        return

    try:
        candidate = await _read_candidate(webspace_id, candidate_id)
    except Exception:
        _log.debug("failed to read teacher candidate for dispatch webspace=%s candidate=%s", webspace_id, candidate_id, exc_info=True)
        return
    if not candidate:
        return

    existing_status = str(candidate.get("dispatch_status") or "").strip()
    if existing_status in _DISPATCH_TERMINAL_STATUSES:
        return

    allowed, policy = _side_effect_policy(candidate)
    now = time.time()
    if not allowed:
        patch = {
            "dispatch_status": "blocked",
            "dispatch": {
                "status": "blocked",
                "blocked_at": now,
                "reason": policy.get("reason") or "policy",
                "side_effect_policy": dict(policy),
            },
            "updated_at": now,
        }
        try:
            await _patch_candidate_dispatch(webspace_id=webspace_id, candidate_id=candidate_id, patch=patch)
            await append_event(
                webspace_id,
                make_event(
                    webspace_id=webspace_id,
                    request_id=payload.get("request_id") if isinstance(payload.get("request_id"), str) else None,
                    request_text=payload.get("text") if isinstance(payload.get("text"), str) else "",
                    kind="dispatch.blocked",
                    title="Teacher dispatch blocked",
                    subtitle=str(policy.get("reason") or "policy"),
                    raw={"candidate_id": candidate_id, "policy": dict(policy)},
                    meta=meta,
                ),
            )
        except Exception:
            _log.debug("failed to record blocked teacher dispatch webspace=%s candidate=%s", webspace_id, candidate_id, exc_info=True)
        return

    intent = str(payload.get("intent") or "").strip() or _candidate_intent(candidate)
    if not intent:
        return
    request_id = str(payload.get("request_id") or candidate.get("request_id") or "").strip()
    text = str(payload.get("text") or candidate.get("text") or "").strip()
    dispatch_id = f"tdispatch.{int(now * 1000)}"
    detected_payload = {
        "intent": intent,
        "confidence": 1.0,
        "slots": _slots_from_payload(payload, candidate),
        "text": text,
        "webspace_id": webspace_id,
        "request_id": f"{request_id or candidate_id}.teacher_dispatch",
        "via": "nlu_teacher.verified",
        "_meta": {
            **dict(meta),
            "webspace_id": webspace_id,
            "route_id": "voice_chat",
            "nlu_teacher_candidate_id": candidate_id,
            "nlu_teacher_dispatch_id": dispatch_id,
            "nlu_teacher_dispatch": True,
            "nlu_teacher_original_request_id": request_id,
        },
    }
    patch = {
        "dispatch_status": "requested",
        "dispatched_at": now,
        "dispatch": {
            "id": dispatch_id,
            "status": "requested",
            "requested_at": now,
            "path": "nlp.intent.detected",
            "intent": intent,
            "slots": dict(detected_payload["slots"]),
            "side_effect_policy": dict(policy),
            "request_id": detected_payload["request_id"],
        },
        "updated_at": now,
    }
    try:
        await _patch_candidate_dispatch(webspace_id=webspace_id, candidate_id=candidate_id, patch=patch)
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=request_id,
                request_text=text,
                kind="dispatch.requested",
                title="Teacher dispatch requested",
                subtitle=intent,
                raw={"candidate_id": candidate_id, "detected": detected_payload, "dispatch": patch["dispatch"]},
                meta=meta,
            ),
        )
        bus_emit(get_ctx().bus, "nlp.intent.detected", detected_payload, source="nlu.teacher.dispatch")
    except Exception:
        _log.warning("failed to dispatch acquired teacher understanding webspace=%s candidate=%s", webspace_id, candidate_id, exc_info=True)
