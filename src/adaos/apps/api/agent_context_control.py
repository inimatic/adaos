from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status

from adaos.apps.api.auth import require_token
from adaos.services.context_control import (
    ContextAccessDenied,
    ContextConflict,
    ContextControlService,
)


router = APIRouter(tags=["agent-context"], dependencies=[Depends(require_token)])


def _get_service() -> ContextControlService:
    return ContextControlService()


def _raise(exc: Exception) -> None:
    if isinstance(exc, ContextAccessDenied):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    if isinstance(exc, ContextConflict):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    if isinstance(exc, KeyError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/capsules")
def list_capsules(
    subject_ref: str | None = None,
    kind: str | None = None,
    trust_class: str | None = None,
    search: str | None = None,
    include_revoked: bool = False,
    limit: int = Query(default=200, ge=1, le=2000),
    service: ContextControlService = Depends(_get_service),
) -> dict[str, Any]:
    items = service.list_capsules(
        subject_ref=subject_ref,
        kind=kind,
        trust_class=trust_class,
        search=search,
        include_revoked=include_revoked,
        limit=limit,
    )
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/capsules")
def register_capsule(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "capsule": service.register_capsule(body, bind=bool(body.get("bind")))}
    except (ValueError, KeyError, ContextConflict, ContextAccessDenied) as exc:
        _raise(exc)


@router.get("/capsules/{capsule_id}")
def get_capsule(
    capsule_id: str,
    include_content: bool = False,
    service: ContextControlService = Depends(_get_service),
) -> dict[str, Any]:
    try:
        return {"ok": True, "capsule": service.get_capsule(capsule_id, include_content=include_content)}
    except KeyError as exc:
        _raise(exc)


@router.post("/capsules/{capsule_id}/revoke")
def revoke_capsule(capsule_id: str, body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "capsule": service.revoke_capsule(capsule_id, actor_ref=str(body.get("actor_ref") or "api"), reason=str(body.get("reason") or ""))}
    except (ValueError, KeyError) as exc:
        _raise(exc)


@router.post("/relationships")
def add_relationship(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "relationship": service.add_relationship(body)}
    except (ValueError, KeyError) as exc:
        _raise(exc)


@router.post("/bindings")
def bind_subject(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            "binding": service.bind_subject(
                subject_ref=str(body.get("subject_ref") or ""),
                capsule_id=str(body.get("capsule_id") or ""),
                purpose=str(body.get("purpose") or "*"),
                audience=str(body.get("audience") or "*"),
                branch=str(body.get("branch") or "main"),
                expected_revision=body.get("expected_revision"),
                actor_ref=str(body.get("actor_ref") or "api"),
                reason=str(body.get("reason") or "updated"),
                valid_from=str(body.get("valid_from") or "") or None,
            ),
        }
    except (ValueError, KeyError, ContextConflict) as exc:
        _raise(exc)


@router.get("/bindings/compare")
def compare_bindings(
    subject_ref: str,
    purpose: str = "*",
    audience: str = "*",
    left_branch: str = "main",
    right_branch: str = "main",
    service: ContextControlService = Depends(_get_service),
) -> dict[str, Any]:
    return {
        "ok": True,
        "comparison": service.compare_bindings(
            subject_ref=subject_ref,
            purpose=purpose,
            audience=audience,
            left_branch=left_branch,
            right_branch=right_branch,
        ),
    }


@router.post("/bindings/merge")
def merge_binding(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {
            "ok": True,
            "merge": service.merge_binding(
                subject_ref=str(body.get("subject_ref") or ""),
                source_branch=str(body.get("source_branch") or ""),
                target_branch=str(body.get("target_branch") or "main"),
                purpose=str(body.get("purpose") or "*"),
                audience=str(body.get("audience") or "*"),
                base_capsule_id=str(body.get("base_capsule_id") or "") or None,
                expected_target_revision=body.get("expected_target_revision"),
                actor_ref=str(body.get("actor_ref") or "api"),
                reason=str(body.get("reason") or "branch_merge"),
            ),
        }
    except (ValueError, KeyError, ContextConflict) as exc:
        _raise(exc)


@router.post("/resolve")
def resolve_context(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "resolution": service.resolve(body)}
    except (ValueError, KeyError) as exc:
        _raise(exc)


@router.post("/plans")
def create_plan(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "plan": service.plan(body)}
    except (ValueError, KeyError) as exc:
        _raise(exc)


@router.get("/plans/{plan_id}")
def get_plan(plan_id: str, service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "plan": service.get_plan(plan_id)}
    except KeyError as exc:
        _raise(exc)


@router.post("/compile")
def compile_context(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "compilation": service.compile(body)}
    except (ValueError, KeyError, ContextAccessDenied) as exc:
        _raise(exc)


@router.post("/receipts")
def record_receipt(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "receipt": service.record_receipt(body)}
    except ValueError as exc:
        _raise(exc)


@router.get("/receipts")
def list_receipts(
    run_ref: str | None = None,
    limit: int = Query(default=200, ge=1, le=2000),
    service: ContextControlService = Depends(_get_service),
) -> dict[str, Any]:
    items = service.list_receipts(run_ref=run_ref, limit=limit)
    return {"ok": True, "items": items, "count": len(items)}


@router.get("/inspect/{run_ref:path}")
def inspect_run(run_ref: str, service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    return {"ok": True, "inspection": service.inspect(run_ref)}


@router.get("/memory-candidates")
def list_memory_candidates(
    candidate_status: str | None = Query(default=None, alias="status"),
    limit: int = Query(default=200, ge=1, le=2000),
    service: ContextControlService = Depends(_get_service),
) -> dict[str, Any]:
    items = service.list_memory_candidates(status=candidate_status, limit=limit)
    return {"ok": True, "items": items, "count": len(items)}


@router.post("/memory-candidates")
def propose_memory(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "candidate": service.propose_memory(body)}
    except ValueError as exc:
        _raise(exc)


@router.post("/memory-candidates/{candidate_id}/qualify")
def qualify_memory(candidate_id: str, body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "candidate": service.qualify_memory(
            candidate_id,
            validation_refs=body.get("validation_refs") or [],
            qualified_by=str(body.get("qualified_by") or ""),
            expected_revision=body.get("expected_revision"),
        )}
    except (ValueError, KeyError, ContextConflict, ContextAccessDenied) as exc:
        _raise(exc)


@router.post("/memory-candidates/{candidate_id}/promote")
def promote_memory(candidate_id: str, body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, **service.promote_memory(
            candidate_id,
            actor_ref=str(body.get("actor_ref") or ""),
            subject_refs=body.get("subject_refs") or [],
            bind=bool(body.get("bind", True)),
            expected_revision=body.get("expected_revision"),
        )}
    except (ValueError, KeyError, ContextConflict, ContextAccessDenied) as exc:
        _raise(exc)


@router.post("/memory-candidates/{candidate_id}/rollback")
def rollback_memory(candidate_id: str, body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, **service.rollback_memory(
            candidate_id,
            actor_ref=str(body.get("actor_ref") or ""),
            reason=str(body.get("reason") or ""),
            restore_capsule_id=str(body.get("restore_capsule_id") or "") or None,
        )}
    except (ValueError, KeyError, ContextConflict) as exc:
        _raise(exc)


@router.post("/invalidate")
def invalidate_context(body: dict[str, Any], service: ContextControlService = Depends(_get_service)) -> dict[str, Any]:
    try:
        return {"ok": True, "invalidation": service.invalidate(
            subject_ref=str(body.get("subject_ref") or ""),
            reason=str(body.get("reason") or ""),
            event_ref=str(body.get("event_ref") or ""),
            source_digest=str(body.get("source_digest") or "") or None,
            edge_type=str(body.get("edge_type") or "") or None,
        )}
    except ValueError as exc:
        _raise(exc)


@router.get("/invalidations")
def list_invalidations(
    subject_ref: str | None = None,
    event_ref: str | None = None,
    limit: int = Query(default=500, ge=1, le=2000),
    service: ContextControlService = Depends(_get_service),
) -> dict[str, Any]:
    items = service.list_invalidations(
        subject_ref=subject_ref,
        event_ref=event_ref,
        limit=limit,
    )
    return {"ok": True, "items": items, "count": len(items)}
