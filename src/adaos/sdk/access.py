"""Check the verified caller's capabilities in the current skill's scope."""

from __future__ import annotations

from typing import Any

from adaos.domain.personalization_access import ScopeRef
from adaos.sdk.core._ctx import require_ctx
from adaos.services.personalization_runtime import personalization_access_service
from adaos.services.policy.caller import CallerAccessDenied, current_caller


def caller() -> dict[str, str] | None:
    """Return the authenticated invocation subject, or None outside trusted ingress.

    This is not the executing skill, the profile selected in the UI, or an
    ``actor``/``role`` supplied in arguments. An absent caller must not be treated
    as owner. Use ``require`` for authorization, not comparisons of display names.
    """
    actor = current_caller()
    return {"kind": actor.kind, "id": actor.id} if actor is not None else None


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
    decision = personalization_access_service(ctx).evaluate(
        actor=actor,
        action=capability,
        scope=ScopeRef("skill", skill.name),
        resource=f"skill:{skill.name}",
    )
    if decision.decision != "allow":
        raise CallerAccessDenied(f"caller_access_denied:{decision.reason_code}")
    return decision.to_dict()


__all__ = ["caller", "require"]
