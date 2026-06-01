# src/adaos/apps/api/nlu_teacher_api.py
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit as bus_emit
from adaos.services.nlu_lookup_tables import collect_desktop_lookup_tables_async
from adaos.services.nlu.probe import probe_phrase
from adaos.services.nlu.teacher_read_model import (
    describe_scenario_nlu,
    describe_skill_nlu,
    get_nlu_dialog_context,
    get_nlu_recent_failures,
    get_nlu_trace,
    list_nlu_templates,
    list_training_targets,
    preview_interface_action,
    preview_template_patch,
)
from adaos.services.nlu.teacher_events import rebuild_events_by_candidate
from adaos.services.nlu.teacher_store import save_teacher_state
from adaos.services.yjs.doc import async_get_ydoc
from adaos.services.yjs.webspace import default_webspace_id

router = APIRouter(tags=["nlu-teacher"])


def _resolve_webspace_id(token: Optional[str]) -> str:
    if isinstance(token, str) and token.strip():
        return token.strip()
    return default_webspace_id()


def _teacher_obj(data_map: Any) -> dict:
    current = data_map.get("nlu_teacher")
    return dict(current) if isinstance(current, dict) else {}


def _request_id_from_teacher_row(row: Any) -> str:
    if not isinstance(row, Mapping):
        return ""
    rid = row.get("request_id")
    if isinstance(rid, str) and rid.strip():
        return rid.strip()
    raw = row.get("raw")
    if isinstance(raw, Mapping):
        rid = raw.get("request_id")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
    request = row.get("request")
    if isinstance(request, Mapping):
        rid = request.get("request_id")
        if isinstance(rid, str) and rid.strip():
            return rid.strip()
    return ""


def _prune_teacher_requests(
    teacher: dict,
    *,
    request_ids: set[str],
    request_id_prefixes: tuple[str, ...],
) -> tuple[dict, dict[str, int]]:
    next_teacher = dict(teacher)
    removed: dict[str, int] = {}

    def _matches(row: Any) -> bool:
        rid = _request_id_from_teacher_row(row)
        if not rid:
            return False
        if rid in request_ids:
            return True
        return any(rid.startswith(prefix) for prefix in request_id_prefixes)

    for key in ("items", "events", "revisions", "candidates", "dataset", "plan", "llm_logs"):
        raw = next_teacher.get(key)
        if not isinstance(raw, list):
            continue
        kept: list[Any] = []
        for item in raw:
            if isinstance(item, Mapping):
                if not _matches(item):
                    kept.append(dict(item))
            else:
                kept.append(item)
        removed[key] = len(raw) - len(kept)
        next_teacher[key] = kept

    rebuild_events_by_candidate(next_teacher)
    return next_teacher, removed


class ApplyRevisionRequest(BaseModel):
    revision_id: str = Field(..., min_length=1)
    intent: str = Field(..., min_length=1)
    examples: list[str] = Field(default_factory=list)
    slots: Dict[str, Any] = Field(default_factory=dict)


class SaveExampleTarget(BaseModel):
    type: str = Field(..., min_length=1)
    id: Optional[str] = None


class SaveExampleRequest(BaseModel):
    text: str = Field(..., min_length=1)
    intent: str = Field(..., min_length=1)
    target: SaveExampleTarget
    slots: Dict[str, Any] = Field(default_factory=dict)
    request_id: Optional[str] = None
    source: Optional[str] = None
    note: Optional[str] = None


class ApplyCandidateRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1)
    target: Optional[SaveExampleTarget] = None


class RollbackCandidateRequest(BaseModel):
    candidate_id: str = Field(..., min_length=1)
    rule_id: Optional[str] = None
    target: Optional[SaveExampleTarget] = None


class PruneTeacherRequestsRequest(BaseModel):
    request_ids: list[str] = Field(default_factory=list)
    request_id_prefixes: list[str] = Field(default_factory=list)
    dry_run: bool = False


class ProbePhraseRequest(BaseModel):
    text: str = Field(..., min_length=1)
    use_rasa: bool = True
    emit_trace: bool = True
    request_locale: Optional[str] = None
    preferred_locales: list[str] = Field(default_factory=list)


class PreviewTemplatePatchRequest(BaseModel):
    operation: str = Field(..., min_length=1)
    target: SaveExampleTarget
    intent: str = Field(..., min_length=1)
    text: Optional[str] = None
    pattern: Optional[str] = None
    slots: Dict[str, Any] = Field(default_factory=dict)
    base_fingerprint: Optional[str] = None


class PreviewInterfaceActionRequest(BaseModel):
    action_id: Optional[str] = None
    intent: Optional[str] = None
    host_action: Optional[str] = None
    params: Dict[str, Any] = Field(default_factory=dict)


@router.get("/nlu/teacher/{webspace_id}", dependencies=[Depends(require_token)])
async def get_teacher_state(webspace_id: str):
    ws = _resolve_webspace_id(webspace_id)
    async with async_get_ydoc(ws, read_only=True, prefer_live_room=True, load_mark_roots=["data"]) as ydoc:
        data_map = ydoc.get_map("data")
        return {"webspace_id": ws, "nlu_teacher": _teacher_obj(data_map)}


@router.get("/nlu/teacher/{webspace_id}/lookups", dependencies=[Depends(require_token)])
async def get_lookup_tables(webspace_id: str):
    ws = _resolve_webspace_id(webspace_id)
    try:
        return await collect_desktop_lookup_tables_async(webspace_id=ws, include_live=True)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to collect lookup tables: {exc}")


@router.get("/nlu/teacher/{webspace_id}/trace", dependencies=[Depends(require_token)])
async def get_trace(webspace_id: str, request_id: Optional[str] = None, candidate_id: Optional[str] = None, limit: int = 80):
    ws = _resolve_webspace_id(webspace_id)
    return get_nlu_trace(webspace_id=ws, request_id=request_id, candidate_id=candidate_id, limit=limit)


@router.get("/nlu/teacher/{webspace_id}/dialog-context", dependencies=[Depends(require_token)])
async def get_dialog_context(
    webspace_id: str,
    request_id: Optional[str] = None,
    candidate_id: Optional[str] = None,
    limit: int = 25,
):
    ws = _resolve_webspace_id(webspace_id)
    return get_nlu_dialog_context(webspace_id=ws, request_id=request_id, candidate_id=candidate_id, limit=limit)


@router.get("/nlu/teacher/{webspace_id}/failures", dependencies=[Depends(require_token)])
async def get_recent_failures(webspace_id: str, limit: int = 50):
    ws = _resolve_webspace_id(webspace_id)
    return get_nlu_recent_failures(webspace_id=ws, limit=limit)


@router.get("/nlu/teacher/{webspace_id}/templates", dependencies=[Depends(require_token)])
async def get_templates(
    webspace_id: str,
    owner_type: Optional[str] = None,
    owner_id: Optional[str] = None,
    include_system_actions: bool = True,
):
    ws = _resolve_webspace_id(webspace_id)
    return list_nlu_templates(
        webspace_id=ws,
        owner_type=owner_type,
        owner_id=owner_id,
        include_system_actions=include_system_actions,
    )


@router.get("/nlu/teacher/{webspace_id}/training-targets", dependencies=[Depends(require_token)])
async def get_training_targets(webspace_id: str, include_system_actions: bool = True):
    ws = _resolve_webspace_id(webspace_id)
    return list_training_targets(webspace_id=ws, include_system_actions=include_system_actions)


@router.post("/nlu/teacher/{webspace_id}/template-patch/preview", dependencies=[Depends(require_token)])
async def preview_template_patch_api(webspace_id: str, body: PreviewTemplatePatchRequest):
    ws = _resolve_webspace_id(webspace_id)
    return preview_template_patch(
        webspace_id=ws,
        operation=body.operation,
        target=body.target.model_dump(exclude_none=True),
        intent=body.intent,
        text=body.text,
        pattern=body.pattern,
        slots=body.slots,
        base_fingerprint=body.base_fingerprint,
    )


@router.post("/nlu/teacher/{webspace_id}/interface-action/preview", dependencies=[Depends(require_token)])
async def preview_interface_action_api(webspace_id: str, body: PreviewInterfaceActionRequest):
    ws = _resolve_webspace_id(webspace_id)
    return preview_interface_action(
        webspace_id=ws,
        action_id=body.action_id,
        intent=body.intent,
        host_action=body.host_action,
        params=body.params,
    )


@router.get("/nlu/teacher/{webspace_id}/skills/{skill_id}/nlu", dependencies=[Depends(require_token)])
async def get_skill_nlu(webspace_id: str, skill_id: str):
    _resolve_webspace_id(webspace_id)
    return describe_skill_nlu(skill_id)


@router.get("/nlu/teacher/{webspace_id}/scenarios/{scenario_id}/nlu", dependencies=[Depends(require_token)])
async def get_scenario_nlu(webspace_id: str, scenario_id: str):
    _resolve_webspace_id(webspace_id)
    return describe_scenario_nlu(scenario_id)


@router.post("/nlu/teacher/{webspace_id}/revision/apply", dependencies=[Depends(require_token)])
async def apply_revision(webspace_id: str, body: ApplyRevisionRequest):
    ws = _resolve_webspace_id(webspace_id)
    ctx = get_ctx()

    examples = [x.strip() for x in (body.examples or []) if isinstance(x, str) and x.strip()]
    payload = {
        "webspace_id": ws,
        "revision_id": body.revision_id.strip(),
        "intent": body.intent.strip(),
        "examples": examples,
        "slots": dict(body.slots or {}),
        "_meta": {"webspace_id": ws},
    }

    try:
        bus_emit(ctx.bus, "nlp.teacher.revision.apply", payload, source="api.nlu.teacher")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to emit apply event: {exc}")

    return {"ok": True, "webspace_id": ws, "revision_id": body.revision_id, "intent": body.intent}


@router.post("/nlu/teacher/{webspace_id}/example/save", dependencies=[Depends(require_token)])
async def save_example(webspace_id: str, body: SaveExampleRequest):
    ws = _resolve_webspace_id(webspace_id)
    ctx = get_ctx()
    payload = {
        "webspace_id": ws,
        "text": body.text.strip(),
        "intent": body.intent.strip(),
        "target": body.target.model_dump(exclude_none=True),
        "slots": dict(body.slots or {}),
        "request_id": body.request_id.strip() if isinstance(body.request_id, str) and body.request_id.strip() else None,
        "source": body.source.strip() if isinstance(body.source, str) and body.source.strip() else "api.nlu.teacher",
        "note": body.note.strip() if isinstance(body.note, str) and body.note.strip() else None,
        "_meta": {"webspace_id": ws},
    }

    try:
        bus_emit(ctx.bus, "nlp.teacher.example.save", payload, source="api.nlu.teacher")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to emit save example event: {exc}")

    return {"ok": True, "webspace_id": ws, "intent": payload["intent"], "target": payload["target"]}


@router.post("/nlu/teacher/{webspace_id}/candidate/apply", dependencies=[Depends(require_token)])
async def apply_candidate(webspace_id: str, body: ApplyCandidateRequest):
    ws = _resolve_webspace_id(webspace_id)
    ctx = get_ctx()
    payload = {
        "webspace_id": ws,
        "candidate_id": body.candidate_id.strip(),
        "_meta": {"webspace_id": ws, "source": "api.nlu.teacher"},
    }
    if body.target is not None:
        payload["target"] = body.target.model_dump(exclude_none=True)

    try:
        bus_emit(ctx.bus, "nlp.teacher.candidate.apply", payload, source="api.nlu.teacher")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to emit candidate apply event: {exc}")

    return {"ok": True, "webspace_id": ws, "candidate_id": payload["candidate_id"]}


@router.post("/nlu/teacher/{webspace_id}/candidate/rollback", dependencies=[Depends(require_token)])
async def rollback_candidate(webspace_id: str, body: RollbackCandidateRequest):
    ws = _resolve_webspace_id(webspace_id)
    ctx = get_ctx()
    payload = {
        "webspace_id": ws,
        "candidate_id": body.candidate_id.strip(),
        "_meta": {"webspace_id": ws, "source": "api.nlu.teacher"},
    }
    if body.rule_id:
        payload["rule_id"] = body.rule_id.strip()
    if body.target is not None:
        payload["target"] = body.target.model_dump(exclude_none=True)

    try:
        bus_emit(ctx.bus, "nlp.teacher.regex_rule.rollback", payload, source="api.nlu.teacher")
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to emit candidate rollback event: {exc}")

    return {"ok": True, "webspace_id": ws, "candidate_id": payload["candidate_id"]}


@router.post("/nlu/teacher/{webspace_id}/requests/prune", dependencies=[Depends(require_token)])
async def prune_requests(webspace_id: str, body: PruneTeacherRequestsRequest):
    ws = _resolve_webspace_id(webspace_id)
    request_ids = {str(item or "").strip() for item in body.request_ids if str(item or "").strip()}
    request_id_prefixes = tuple(
        str(item or "").strip() for item in body.request_id_prefixes if str(item or "").strip()
    )
    if not request_ids and not request_id_prefixes:
        raise HTTPException(status_code=400, detail="request_ids or request_id_prefixes required")

    try:
        async with async_get_ydoc(
            ws,
            prefer_live_room=True,
            load_mark_roots=["data"],
            write_source="api.nlu.teacher.prune",
            write_owner="core:nlu.teacher",
            write_channel="core.nlu.teacher.api",
            governed=True,
        ) as ydoc:
            data_map = ydoc.get_map("data")
            teacher = _teacher_obj(data_map)
            next_teacher, removed = _prune_teacher_requests(
                teacher,
                request_ids=request_ids,
                request_id_prefixes=request_id_prefixes,
            )
            if not body.dry_run:
                with ydoc.begin_transaction() as txn:
                    data_map.set(txn, "nlu_teacher", next_teacher)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to prune teacher requests: {exc}")

    if not body.dry_run:
        try:
            save_teacher_state(webspace_id=ws, teacher=next_teacher)
        except Exception:
            pass

    return {
        "ok": True,
        "webspace_id": ws,
        "dry_run": bool(body.dry_run),
        "removed": removed,
        "remaining": {
            "items": len(next_teacher.get("items") or []),
            "events": len(next_teacher.get("events") or []),
            "threads_by_request": len(next_teacher.get("threads_by_request") or []),
        },
    }


@router.post("/nlu/teacher/{webspace_id}/probe", dependencies=[Depends(require_token)])
async def probe(webspace_id: str, body: ProbePhraseRequest):
    ws = _resolve_webspace_id(webspace_id)
    try:
        result = await probe_phrase(
            body.text,
            webspace_id=ws,
            use_rasa=bool(body.use_rasa),
            emit_trace=bool(body.emit_trace),
            request_locale=body.request_locale,
            preferred_locales=body.preferred_locales,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"failed to probe phrase: {exc}")
    return result

