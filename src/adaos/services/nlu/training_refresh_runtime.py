from __future__ import annotations

import asyncio
import logging
import os
import time
from typing import Any, Mapping

from adaos.sdk.core.decorators import subscribe
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.interpreter.workspace import InterpreterWorkspace
from adaos.services.nlu.data_registry import sync_from_scenarios_and_skills
from adaos.services.nlu.teacher_events import append_event, make_event

from . import neural_service_bridge, neuro_lite_service_bridge
from .runtime_flags import is_stage_enabled

_log = logging.getLogger("adaos.nlu.training_refresh")
_REFRESH_LOCK = asyncio.Lock()


def _payload(evt: Any) -> dict[str, Any]:
    if isinstance(evt, dict):
        return evt
    data = getattr(evt, "payload", None)
    return data if isinstance(data, dict) else {}


def _meta(payload: Mapping[str, Any]) -> dict[str, Any]:
    value = payload.get("_meta")
    return dict(value) if isinstance(value, Mapping) else {}


def _resolve_webspace_id(payload: Mapping[str, Any]) -> str:
    meta = _meta(payload)
    for source in (payload, meta):
        token = source.get("webspace_id") or source.get("workspace_id")
        if isinstance(token, str) and token.strip():
            return token.strip()
    return "desktop"


def _dataset_item(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    value = payload.get("dataset_item")
    return value if isinstance(value, Mapping) else {}


def _request_id(payload: Mapping[str, Any]) -> str | None:
    item = _dataset_item(payload)
    token = item.get("request_id") or payload.get("request_id")
    return token.strip() if isinstance(token, str) and token.strip() else None


def _request_text(payload: Mapping[str, Any]) -> str | None:
    item = _dataset_item(payload)
    examples = item.get("examples")
    if isinstance(examples, list):
        for value in examples:
            if isinstance(value, str) and value.strip():
                return value.strip()
    result = item.get("result")
    if isinstance(result, Mapping):
        token = result.get("example")
        if isinstance(token, str) and token.strip():
            return token.strip()
    token = payload.get("text") or payload.get("example")
    return token.strip() if isinstance(token, str) and token.strip() else None


def _intent(payload: Mapping[str, Any]) -> str | None:
    item = _dataset_item(payload)
    token = item.get("intent") or payload.get("intent")
    if not isinstance(token, str) or not token.strip():
        result = item.get("result")
        if isinstance(result, Mapping):
            token = result.get("intent")
    return token.strip() if isinstance(token, str) and token.strip() else None


async def _append_refresh_event(webspace_id: str, payload: Mapping[str, Any], summary: Mapping[str, Any]) -> None:
    try:
        await append_event(
            webspace_id,
            make_event(
                webspace_id=webspace_id,
                request_id=_request_id(payload),
                request_text=_request_text(payload) or _intent(payload) or "nlu.training.refresh",
                kind="training.refresh.completed" if summary.get("ok") else "training.refresh.failed",
                title="NLU training refresh" if summary.get("ok") else "NLU training refresh failed",
                subtitle=_intent(payload) or str(summary.get("reason") or "curated_examples"),
                raw=dict(summary),
                meta=_meta(payload),
            ),
        )
    except Exception:
        _log.debug("failed to append NLU training refresh event webspace=%s", webspace_id, exc_info=True)


async def refresh_from_curated_examples(
    *,
    webspace_id: str,
    reason: str = "nlu.teacher.example.saved",
    payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Sync saved NLU examples into runtime-readable engine artifacts.

    Neuro-lite can safely reload new labels from the curated manifest. Neural
    reindex is attempted only when the active model already has every curated
    label; otherwise the summary records that a gated rebuild is required.
    """
    ctx = get_ctx()
    payload = payload if isinstance(payload, Mapping) else {}
    started_at = time.time()
    result: dict[str, Any] = {
        "ok": True,
        "reason": reason,
        "webspace_id": webspace_id,
        "started_at": started_at,
        "engines": {},
    }

    try:
        sync_summary = sync_from_scenarios_and_skills(ctx)
        ws = InterpreterWorkspace(ctx)
        export = ws.export_neural_training_data()
        result["sync"] = sync_summary
        result["export"] = export
    except Exception as exc:
        result.update({"ok": False, "reason": "curated_export_failed", "error": f"{type(exc).__name__}: {exc}"})
        return result

    if await is_stage_enabled(webspace_id, "neuro_lite"):
        neuro_result: dict[str, Any] = {"status": "skipped"}
        try:
            sync_result = neuro_lite_service_bridge.sync_curated_examples(str(export.get("examples_path") or ""))
            rebuild_result = None
            if sync_result.get("ok"):
                rebuild_result = await neuro_lite_service_bridge.rebuild_active_model(start_service=True, stop_after=False)
            neuro_result = {
                "status": "refreshed" if sync_result.get("ok") and rebuild_result and rebuild_result.get("ok") else "failed",
                "sync": sync_result,
                "rebuild": rebuild_result,
            }
            if neuro_result["status"] == "failed":
                result["ok"] = False
        except Exception as exc:
            neuro_result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            result["ok"] = False
        result["engines"]["neuro_lite"] = neuro_result
    else:
        result["engines"]["neuro_lite"] = {"status": "skipped", "reason": "runtime_disabled"}

    if await is_stage_enabled(webspace_id, "neural"):
        neural_result: dict[str, Any]
        try:
            plan = ws.plan_neural_curated_reindex(export=False)
            if plan.get("apply_allowed"):
                apply_result = ws.apply_neural_curated_reindex(plan=plan)
                reindex_result = None
                if apply_result.get("ok"):
                    reindex_result = await neural_service_bridge.reindex_active_model(
                        start_service=True,
                        stop_after=False,
                        purge_indexes=True,
                    )
                neural_result = {
                    "status": "reindexed" if apply_result.get("ok") and reindex_result and reindex_result.get("ok") else "failed",
                    "plan": plan,
                    "apply": apply_result,
                    "reindex": reindex_result,
                }
                if neural_result["status"] == "failed":
                    result["ok"] = False
            else:
                neural_result = {
                    "status": "rebuild_required",
                    "reason": "curated_labels_not_in_active_model"
                    if "curated_labels_not_in_active_model" in set(plan.get("warnings") or [])
                    else "curated_reindex_not_allowed",
                    "plan": plan,
                }
        except Exception as exc:
            neural_result = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            result["ok"] = False
        result["engines"]["neural"] = neural_result
    else:
        result["engines"]["neural"] = {"status": "skipped", "reason": "runtime_disabled"}

    if await is_stage_enabled(webspace_id, "rasa"):
        try:
            bus_emit(ctx.bus, "nlp.rasa.train", {"webspace_id": webspace_id, "_meta": _meta(payload)}, source="nlu.training_refresh")
            result["engines"]["rasa"] = {"status": "train_requested"}
        except Exception as exc:
            result["engines"]["rasa"] = {"status": "failed", "error": f"{type(exc).__name__}: {exc}"}
            result["ok"] = False
    else:
        result["engines"]["rasa"] = {"status": "skipped", "reason": "runtime_disabled"}

    result["finished_at"] = time.time()
    result["latency_ms"] = round((result["finished_at"] - started_at) * 1000.0, 3)
    return result


@subscribe("nlp.teacher.example.saved")
async def _on_teacher_example_saved(evt: Any) -> None:
    if str(os.getenv("ADAOS_NLU_TRAINING_REFRESH", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
        return

    payload = _payload(evt)
    webspace_id = _resolve_webspace_id(payload)
    async with _REFRESH_LOCK:
        summary = await refresh_from_curated_examples(
            webspace_id=webspace_id,
            reason="nlu.teacher.example.saved",
            payload=payload,
        )

    try:
        bus_emit(
            get_ctx().bus,
            "nlu.training.refresh.completed" if summary.get("ok") else "nlu.training.refresh.failed",
            {"webspace_id": webspace_id, "summary": summary, "_meta": _meta(payload)},
            source="nlu.training_refresh",
        )
    except Exception:
        _log.debug("failed to emit NLU training refresh summary webspace=%s", webspace_id, exc_info=True)

    await _append_refresh_event(webspace_id, payload, summary)
