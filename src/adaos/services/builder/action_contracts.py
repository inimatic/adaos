"""Versioned command-risk contracts shared by Builder projections and channels."""

from __future__ import annotations

from typing import Any


BUILDER_ACTION_RISK_SCHEMA = "adaos.builder.action_risk.v1"
BUILDER_ACTION_RISKS = (
    "read",
    "local_reversible",
    "isolated_write",
    "trial_activation",
    "workspace_activation",
    "publication",
    "destructive",
)

_RISK_POLICIES: dict[str, dict[str, Any]] = {
    "read": {
        "side_effect_scope": "none",
        "confirmation_required": False,
        "approval_required": False,
        "isolation_required": False,
        "rollback_required": False,
        "inline_callback": "allowed",
    },
    "local_reversible": {
        "side_effect_scope": "dev_local",
        "confirmation_required": False,
        "approval_required": False,
        "isolation_required": False,
        "rollback_required": True,
        "inline_callback": "allowed_with_precondition",
    },
    "isolated_write": {
        "side_effect_scope": "dev_isolated",
        "confirmation_required": True,
        "approval_required": False,
        "isolation_required": True,
        "rollback_required": True,
        "inline_callback": "confirm",
    },
    "trial_activation": {
        "side_effect_scope": "trial",
        "confirmation_required": True,
        "approval_required": False,
        "isolation_required": True,
        "rollback_required": True,
        "inline_callback": "confirm",
    },
    "workspace_activation": {
        "side_effect_scope": "workspace",
        "confirmation_required": True,
        "approval_required": True,
        "isolation_required": False,
        "rollback_required": True,
        "inline_callback": "rich_review_required",
    },
    "publication": {
        "side_effect_scope": "registry",
        "confirmation_required": True,
        "approval_required": True,
        "isolation_required": False,
        "rollback_required": True,
        "inline_callback": "rich_review_required",
    },
    "destructive": {
        "side_effect_scope": "destructive",
        "confirmation_required": True,
        "approval_required": True,
        "isolation_required": False,
        "rollback_required": True,
        "inline_callback": "rich_review_required",
    },
}

_RISK_ALIASES = {
    "none": "read",
    "safe": "read",
    "readonly": "read",
    "read_only": "read",
    "local": "local_reversible",
    "local_write": "local_reversible",
    "dev_write": "isolated_write",
    "automation": "isolated_write",
    "trial": "trial_activation",
    "workspace": "workspace_activation",
    "publish": "publication",
    "delete": "destructive",
}


class BuilderActionContractError(ValueError):
    """Raised when a projected Builder action does not have a safe contract."""


def normalize_builder_action_risk(value: Any) -> str:
    token = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    token = _RISK_ALIASES.get(token, token)
    if token not in _RISK_POLICIES:
        raise BuilderActionContractError(f"unsupported Builder action risk: {value}")
    return token


def builder_action_risk_policy(value: Any) -> dict[str, Any]:
    risk = normalize_builder_action_risk(value)
    return {
        "schema": BUILDER_ACTION_RISK_SCHEMA,
        "risk_class": risk,
        **dict(_RISK_POLICIES[risk]),
    }


def build_builder_action(
    command: Any,
    label: Any,
    risk: Any,
    *,
    expected_generation: Any,
    target_ref: Any = None,
    presentation: Any = "button",
    fallback: Any = "compact_action",
) -> dict[str, Any]:
    command_token = str(command or "").strip()
    label_token = str(label or "").strip()
    if not command_token.startswith("builder.") or len(command_token) > 160:
        raise BuilderActionContractError("Builder action command must use the builder.* namespace")
    if not label_token or len(label_token) > 160:
        raise BuilderActionContractError("Builder action label is required and must be at most 160 characters")
    try:
        generation = int(expected_generation)
    except (TypeError, ValueError) as exc:
        raise BuilderActionContractError("Builder action expected_generation must be an integer") from exc
    if generation < 0:
        raise BuilderActionContractError("Builder action expected_generation must be non-negative")
    target_token = str(target_ref or "").strip() or None
    if target_token and len(target_token) > 300:
        raise BuilderActionContractError("Builder action target_ref must be at most 300 characters")
    presentation_token = str(presentation or "").strip() or None
    fallback_token = str(fallback or "").strip() or None
    policy = builder_action_risk_policy(risk)
    return {
        "command": command_token,
        "label": label_token,
        "risk": policy["risk_class"],
        "risk_policy": policy,
        "expected_generation": generation,
        "target_ref": target_token,
        "presentation": presentation_token,
        "fallback": fallback_token,
    }


__all__ = [
    "BUILDER_ACTION_RISK_SCHEMA",
    "BUILDER_ACTION_RISKS",
    "BuilderActionContractError",
    "build_builder_action",
    "builder_action_risk_policy",
    "normalize_builder_action_risk",
]
