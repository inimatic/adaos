from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from adaos.apps.api.auth import require_token
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.applications.access_management import (
    ROLE_TEMPLATES,
    ApplicationAccessManagementService,
)
from adaos.services.applications.runtime import get_application_service
from adaos.services.pending_actions import list_pending_actions_async, publish_pending_action_async
from adaos.services.personalization_runtime import current_user_id, personalization_access_service


router = APIRouter(tags=["application-access"], dependencies=[Depends(require_token)])


def _management(ctx: AgentContext) -> ApplicationAccessManagementService:
    state = Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir())
    return ApplicationAccessManagementService(get_application_service(state))


def _issuer(ctx: AgentContext) -> str:
    return f"user:{current_user_id(ctx)}"


def _raise_access_error(exc: Exception) -> None:
    name = type(exc).__name__
    status = 409 if name == "ApplicationRevisionConflict" else 400
    if isinstance(exc, FileNotFoundError):
        status = 404
    elif isinstance(exc, PermissionError):
        status = 403
    raise HTTPException(status_code=status, detail={"error": name, "message": str(exc)}) from exc


class GrantRequest(BaseModel):
    release_digest: str
    subject_ref: str
    application_roles: list[str]
    idempotency_key: str
    permission_ceiling: list[str] | None = None
    explicit_denies: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    expires_at: str | None = None


class ChangeGrantRequest(BaseModel):
    release_digest: str
    expected_revision: int = Field(ge=1)
    application_roles: list[str]
    permission_ceiling: list[str] | None = None
    explicit_denies: list[str] | None = None
    constraints: dict[str, Any] | None = None
    expires_at: str | None = None


class RevokeRequest(BaseModel):
    expected_revision: int = Field(ge=1)


class SimulationRequest(BaseModel):
    release_digest: str
    subject_ref: str
    permission_id: str
    app_capability: str
    application_roles: list[str]
    permission_ceiling: list[str]
    explicit_denies: list[str] = Field(default_factory=list)
    constraints: dict[str, Any] = Field(default_factory=dict)
    actor_chain: dict[str, Any] = Field(default_factory=dict)
    component_capabilities: list[str] = Field(default_factory=list)


class ConnectedAccountRequest(BaseModel):
    release_digest: str
    account_id: str
    provider_id: str
    subject_ref: str
    mode: Literal["delegated_user", "app_service"] = "delegated_user"
    scopes: list[str] = Field(default_factory=list)
    status: Literal["missing", "connected", "expired", "revoked", "denied"]
    token_expires_at: str | None = None
    scope_changed_at: str | None = None


class SnapshotImportRequest(BaseModel):
    snapshot: dict[str, Any]
    apply: bool = False


class VerificationRequest(BaseModel):
    release_digest: str
    source_commit: str
    observed_capabilities: list[str] = Field(default_factory=list)
    inferred_capabilities: list[str] = Field(default_factory=list)
    regression_evidence: list[str] = Field(default_factory=list)
    access_matrix_evidence: list[str] = Field(default_factory=list)
    pending_action_evidence: list[str] = Field(default_factory=list)
    audit_evidence: list[str] = Field(default_factory=list)
    disclosure_evidence: list[str] = Field(default_factory=list)
    redaction_evidence: list[str] = Field(default_factory=list)
    release_scope: Literal["dev", "candidate", "trial", "publication"] = "candidate"


class PermissionProfilerRequest(BaseModel):
    release_digest: str
    observed_capabilities: list[str] = Field(default_factory=list)
    inferred_capabilities: list[str] = Field(default_factory=list)
    previous_release_digest: str | None = None


class ConversationRequest(BaseModel):
    intent: Literal["list", "explain", "revoke"]
    application_id: str | None = None
    release_digest: str | None = None
    subject_ref: str | None = None
    permission_id: str | None = None
    app_capability: str = "application.use"
    component_capabilities: list[str] = Field(default_factory=list)
    grant_id: str | None = None
    expected_revision: int | None = None
    pending_action_id: str | None = None


@router.get("/application-access/surface-contract")
def surface_contract() -> dict[str, Any]:
    return ApplicationAccessManagementService.surface_contract()


@router.get("/application-access/role-templates")
def role_templates() -> dict[str, Any]:
    return {"templates": {key: [dict(item) for item in value] for key, value in ROLE_TEMPLATES.items()}}


@router.get("/application-access/applications/{application_id}")
def application_detail(
    application_id: str,
    release_digest: str | None = None,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return _management(ctx).application_detail(application_id, release_digest=release_digest)
    except Exception as exc:
        _raise_access_error(exc)


@router.get("/application-access/users")
def users_access(ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        access = personalization_access_service(ctx)
        summary = access.admin_summary(actor=access.owner)
        return _management(ctx).users_access(summary)
    except Exception as exc:
        _raise_access_error(exc)


@router.post("/application-access/applications/{application_id}/grants")
def create_grant(
    application_id: str,
    body: GrantRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        grant = _management(ctx).access.grant_access(
            application_id,
            release_digest=body.release_digest,
            subject_ref=body.subject_ref,
            application_roles=tuple(body.application_roles),
            issuer_ref=_issuer(ctx),
            idempotency_key=body.idempotency_key,
            permission_ceiling=tuple(body.permission_ceiling) if body.permission_ceiling is not None else None,
            explicit_denies=tuple(body.explicit_denies),
            constraints=body.constraints,
            expires_at=body.expires_at,
        )
        return {"grant": grant.to_dict()}
    except Exception as exc:
        _raise_access_error(exc)


@router.patch("/application-access/grants/{grant_id}")
def change_grant(
    grant_id: str,
    body: ChangeGrantRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        grant = _management(ctx).access.change_access(
            grant_id,
            release_digest=body.release_digest,
            application_roles=tuple(body.application_roles),
            issuer_ref=_issuer(ctx),
            expected_revision=body.expected_revision,
            permission_ceiling=tuple(body.permission_ceiling) if body.permission_ceiling is not None else None,
            explicit_denies=tuple(body.explicit_denies) if body.explicit_denies is not None else None,
            constraints=body.constraints,
            expires_at=body.expires_at,
        )
        return {"grant": grant.to_dict()}
    except Exception as exc:
        _raise_access_error(exc)


@router.post("/application-access/grants/{grant_id}/revoke")
def revoke_grant(
    grant_id: str,
    body: RevokeRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        grant = _management(ctx).access.revoke_access(
            grant_id,
            issuer_ref=_issuer(ctx),
            expected_revision=body.expected_revision,
        )
        return {"grant": grant.to_dict()}
    except Exception as exc:
        _raise_access_error(exc)


@router.post("/application-access/applications/{application_id}/simulate")
def simulate(
    application_id: str,
    body: SimulationRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return _management(ctx).simulate(
            application_id,
            release_digest=body.release_digest,
            subject_ref=body.subject_ref,
            permission_id=body.permission_id,
            app_capability=body.app_capability,
            application_roles=body.application_roles,
            permission_ceiling=body.permission_ceiling,
            explicit_denies=body.explicit_denies,
            constraints=body.constraints,
            actor_chain=body.actor_chain,
            component_capabilities=body.component_capabilities,
        )
    except Exception as exc:
        _raise_access_error(exc)


@router.put("/application-access/applications/{application_id}/connected-accounts")
def put_connected_account(
    application_id: str,
    body: ConnectedAccountRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return {"connected_account": _management(ctx).put_connected_account(application_id, body.model_dump())}
    except Exception as exc:
        _raise_access_error(exc)


@router.get("/application-access/applications/{application_id}/privacy")
def privacy_report(
    application_id: str,
    release_digest: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        service = _management(ctx)
        return {
            "privacy_report": service.privacy_report(application_id, release_digest=release_digest),
            "anomalies": service.anomalies(application_id, release_digest=release_digest),
        }
    except Exception as exc:
        _raise_access_error(exc)


@router.get("/application-access/reviews")
def access_reviews(
    application_id: str | None = None,
    stale_days: int = 90,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    return {"findings": _management(ctx).access_reviews(application_id=application_id, stale_days=stale_days)}


@router.get("/application-access/applications/{application_id}/update-review")
def update_review(
    application_id: str,
    old_release_digest: str,
    new_release_digest: str,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return _management(ctx).update_review(
            application_id,
            old_release_digest=old_release_digest,
            new_release_digest=new_release_digest,
        )
    except Exception as exc:
        _raise_access_error(exc)


@router.get("/application-access/applications/{application_id}/snapshot")
def export_snapshot(application_id: str, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        return _management(ctx).export_snapshot(application_id)
    except Exception as exc:
        _raise_access_error(exc)


@router.post("/application-access/snapshots/import")
def import_snapshot(body: SnapshotImportRequest, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    try:
        return _management(ctx).import_snapshot(body.snapshot, issuer_ref=_issuer(ctx), apply=body.apply)
    except Exception as exc:
        _raise_access_error(exc)


@router.post("/application-access/applications/{application_id}/final-verification")
def final_verification(
    application_id: str,
    body: VerificationRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return _management(ctx).final_verification(
            application_id,
            **body.model_dump(),
            actor_ref=_issuer(ctx),
        )
    except Exception as exc:
        _raise_access_error(exc)


@router.post("/application-access/applications/{application_id}/permission-profile")
def permission_profiler(
    application_id: str,
    body: PermissionProfilerRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    try:
        return _management(ctx).permission_profiler(application_id, **body.model_dump())
    except Exception as exc:
        _raise_access_error(exc)


@router.get("/application-access/applications/{application_id}/verification-reports")
def verification_reports(application_id: str, ctx: AgentContext = Depends(get_ctx)) -> dict[str, Any]:
    return {"reports": _management(ctx).list_verification_reports(application_id)}


@router.post("/application-access/conversation")
async def conversation(
    body: ConversationRequest,
    ctx: AgentContext = Depends(get_ctx),
) -> dict[str, Any]:
    service = _management(ctx)
    try:
        if body.intent == "list":
            if body.application_id:
                return {"intent": "list", "result": service.application_detail(body.application_id, release_digest=body.release_digest)}
            return {"intent": "list", "result": service.users_access()}
        if body.intent == "explain":
            if not all((body.application_id, body.release_digest, body.subject_ref, body.permission_id)):
                raise ValueError("explain requires application, release, subject and permission")
            decision = service.access.decide(
                body.application_id,
                release_digest=body.release_digest,
                subject_ref=body.subject_ref,
                permission_id=body.permission_id,
                app_capability=body.app_capability,
                actor_chain={"user_ref": _issuer(ctx), "subject_ref": body.subject_ref},
                component_capabilities=tuple(body.component_capabilities),
            )
            return {"intent": "explain", "decision": decision.to_dict()}
        if not body.grant_id or body.expected_revision is None:
            raise ValueError("revoke requires grant_id and expected_revision")
        grant = service.store.get_application_access_grant(body.grant_id)
        if body.pending_action_id:
            snapshot = await list_pending_actions_async(include_terminal=True)
            action = dict((snapshot.get("by_id") or {}).get(body.pending_action_id) or {})
            response = dict(action.get("response") or {})
            if (
                action.get("kind") != "application.access.revoke"
                or (action.get("domain_ref") or {}).get("grant_id") != body.grant_id
                or action.get("status") != "responded"
                or response.get("response_action_id") != "approve"
            ):
                raise PermissionError("pending action does not approve this Application access revocation")
            revoked = service.access.revoke_access(
                body.grant_id,
                issuer_ref=_issuer(ctx),
                expected_revision=body.expected_revision,
            )
            return {"intent": "revoke", "grant": revoked.to_dict(), "approval_id": body.pending_action_id}
        action = await publish_pending_action_async(
            ctx=ctx,
            kind="application.access.revoke",
            title="Revoke Application access",
            summary=f"Revoke {grant.subject_ref} access to {grant.application_id}?",
            producer={"type": "application", "application_id": grant.application_id},
            owner_scope={"application_id": grant.application_id, "subject_ref": grant.subject_ref},
            domain_ref={
                "application_id": grant.application_id,
                "subject_ref": grant.subject_ref,
                "grant_id": grant.grant_id,
                "expected_revision": body.expected_revision,
                "reviewed_permission_profile_digest": grant.reviewed_permission_profile_digest,
            },
            allowed_actions=["approve", "refuse", "postpone"],
            response_route={"type": "event", "topic": "application.access.conversation"},
            metadata={
                "application_access": True,
                "routes": {
                    "application": f"applications://{grant.application_id}/access",
                    "user": f"applications://users-access/{grant.subject_ref}",
                    "trusted_device": "pending-actions://trusted-device",
                },
                "channel_affordances": {
                    "chat": "keyboard",
                    "telegram": "inline_keyboard",
                    "voice": "trusted_device_handoff",
                    "voice_text": "Confirm this access change on a trusted device.",
                    "voice_text_i18n_key": "runtime.application_approval.voice_handoff",
                },
            },
        )
        return {"intent": "revoke", "status": "approval_required", "pending_action": action}
    except Exception as exc:
        _raise_access_error(exc)


__all__ = ["router"]
