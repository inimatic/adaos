"""Check the verified caller's capabilities in the current skill's scope."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from adaos.domain.personalization_access import ScopeRef
from adaos.sdk.core._ctx import require_ctx
from adaos.services.personalization_runtime import personalization_access_service
from adaos.services.policy.caller import CallerAccessDenied, current_caller, current_caller_scope


def caller() -> dict[str, str] | None:
    """Return the authenticated invocation subject, or None outside trusted ingress.

    This is not the executing skill, the profile selected in the UI, or an
    ``actor``/``role`` supplied in arguments. An absent caller must not be treated
    as owner. Use ``require`` for authorization, not comparisons of display names.
    Core binds owner or purpose-scoped local session credentials at /api/tools/call.
    Personal DEV preview uses this same ingress: the Client presents its existing
    node-owner credential, Core verifies it and propagates the owner identity to
    the selected DEV skill handler. No installed release, app-side authentication
    setup or application-issued grant is required for this owner path.
    A scoped session admits only its named installed skill; administrative and
    legacy resource endpoints and personal DEV do not accept it. DEV-owner tests
    do not qualify delegated reader/writer access. Application code must not issue
    credentials or implement its own user/role authentication.
    """
    actor = current_caller()
    return {"kind": actor.kind, "id": actor.id} if actor is not None else None


def actor() -> dict[str, str] | None:
    """Alias used by Application-aware SDK contexts."""

    return caller()


def current_user() -> dict[str, str] | None:
    """Return the user subject behind the verified user/session invocation."""

    subject = current_caller()
    if subject is None:
        return None
    if subject.kind == "user":
        return {"kind": subject.kind, "id": subject.id}
    if subject.kind != "session":
        return None
    ctx = require_ctx("sdk.access.current_user")
    session = personalization_access_service(ctx).store.get_session(subject.id)
    value = dict((session or {}).get("subject") or {})
    if value.get("kind") != "user" or not value.get("id"):
        return None
    return {"kind": "user", "id": str(value["id"])}


def application() -> dict[str, Any] | None:
    """Return server-verified Application identity for the active tool call."""

    from adaos.services.policy.application import current_application

    return current_application()


def _application_grant() -> tuple[dict[str, Any], Any, Any]:
    from adaos.services.applications import get_application_service

    context = application()
    if context is None:
        raise CallerAccessDenied("application_context_missing")
    ctx = require_ctx("sdk.access.application")
    state = Path(getattr(ctx, "authority_state_dir", None) or ctx.paths.state_dir())
    service = get_application_service(state)
    grants = service.store.list_application_access_grants(
        str(context["application_id"]),
        subject_ref=str(context["subject_ref"]),
    )
    grant = next(
        (
            item
            for item in grants
            if item.status == "active"
            and item.reviewed_permission_profile_digest == context["permission_profile_digest"]
        ),
        None,
    )
    if grant is None:
        raise CallerAccessDenied("application_grant_missing")
    release = service.store.get_release(
        str(context["application_id"]),
        str(context["release_digest"]),
    )
    return context, grant, release


def require_app_role(role_id: str) -> dict[str, Any]:
    context, grant, _release = _application_grant()
    token = str(role_id or "").strip().lower()
    if token not in grant.application_roles:
        raise CallerAccessDenied(f"application_role_missing:{token}")
    return {"application": context, "grant_id": grant.grant_id, "role_id": token}


def require_app_capability(capability: str) -> dict[str, Any]:
    context, grant, release = _application_grant()
    token = str(capability or "").strip().lower()
    role_map = {item.role_id: item for item in release.application_roles}
    roles = [role_map[item] for item in grant.application_roles if item in role_map]
    if not any(token in role.grants for role in roles):
        raise CallerAccessDenied(f"application_capability_missing:{token}")
    return {"application": context, "grant_id": grant.grant_id, "capability": token}


def explain() -> dict[str, Any]:
    """Return the bounded policy facts used for the active Application invocation."""

    context, grant, release = _application_grant()
    return {
        "application": context,
        "grant_id": grant.grant_id,
        "roles": list(grant.application_roles),
        "permission_ceiling": list(grant.permission_ceiling),
        "explicit_denies": list(grant.explicit_denies),
        "permission_profile_digest": release.permission_profile.digest,
    }


class _PolicyContext:
    @staticmethod
    def explain() -> dict[str, Any]:
        return explain()


policy = _PolicyContext()


def require(capability: str) -> dict[str, Any]:
    """Require a caller grant in the executing skill's scope; otherwise deny.

    Use ``workspace.read`` for owned application records and ``workspace.write``
    for mutations. These are caller rights, independent of the skill's own SDK
    capabilities. Call before accessing data, including in direct operation
    handlers. Session/device expiry and revocation use the existing access kernel.
    This facade neither accepts caller identity from arguments nor creates grants.
    """
    ctx = require_ctx("sdk.access.require")
    actor = current_caller()
    if actor is None:
        raise CallerAccessDenied("caller_not_authenticated")
    skill = ctx.skill_ctx.get()
    if skill is None:
        raise CallerAccessDenied("caller_skill_scope_missing")
    scope = current_caller_scope()
    if scope is not None and scope != ScopeRef("skill", skill.name):
        raise CallerAccessDenied("caller_credential_scope_mismatch")
    verified_application = application()
    if (
        isinstance(verified_application, dict)
        and verified_application.get("_ingress_authorized_permission_id") == capability
    ):
        # Trusted ingress already admitted and audited this exact Application
        # permission for the bound release, subject and tool. Re-evaluating it
        # here used to append a second generic policy audit and rewrite the
        # multi-megabyte access store on every provider read.
        return {
            "decision": "allow",
            "actor": actor.to_dict(),
            "action": capability,
            "scope": ScopeRef("skill", skill.name).to_dict(),
            "resource": f"skill:{skill.name}",
            "reason_code": "application_ingress_authorized",
        }
    decision = personalization_access_service(ctx).evaluate(
        actor=actor,
        action=capability,
        scope=ScopeRef("skill", skill.name),
        resource=f"skill:{skill.name}",
        audit_success=capability != "workspace.read",
    )
    if decision.decision != "allow":
        raise CallerAccessDenied(f"caller_access_denied:{decision.reason_code}")
    return decision.to_dict()


__all__ = [
    "actor",
    "application",
    "caller",
    "current_user",
    "explain",
    "policy",
    "require",
    "require_app_capability",
    "require_app_role",
]
