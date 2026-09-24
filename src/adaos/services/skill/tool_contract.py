from __future__ import annotations

import json
from pathlib import Path
from typing import Any


READ_ONLY_SIDE_EFFECTS = frozenset({"safe", "none", "read", "read_only", "readonly"})


def normalize_side_effects(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def side_effects_are_read_only(value: Any) -> bool:
    return normalize_side_effects(value) in READ_ONLY_SIDE_EFFECTS


def _resolved_tool_spec(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    try:
        status = manager.dev_runtime_status(skill_name) if dev else manager.runtime_status(skill_name)
        manifest_path = Path(str(status.get("resolved_manifest") or ""))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tools = manifest.get("tools") if isinstance(manifest, dict) else {}
        spec = tools.get(public_tool) if isinstance(tools, dict) else {}
        return dict(spec) if isinstance(spec, dict) else {}
    except Exception:
        return {}


def _application_access(spec: dict[str, Any]) -> dict[str, str]:
    value = spec.get("application_access")
    if not isinstance(value, dict):
        return {}
    allowed = {"permission", "capability", "resource_argument", "provider_argument"}
    if set(value) - allowed:
        return {}
    return {
        key: str(value.get(key) or "").strip().lower()
        for key in sorted(allowed)
        if str(value.get(key) or "").strip()
    }


def declared_tool_contract(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Read all trusted execution fields from one resolved-manifest snapshot.

    Authorization must not assemble one decision from several independently
    selected runtime revisions.  Reading the manifest once is both cheaper and
    gives the caller a coherent contract when an A/B slot changes concurrently.
    """

    spec = _resolved_tool_spec(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    governance = spec.get("yjs_governance") if isinstance(spec.get("yjs_governance"), dict) else {}
    raw_permissions = spec.get("permissions")
    permission_values: list[Any] = []
    if isinstance(raw_permissions, dict):
        for key in ("required", "optional"):
            candidate = raw_permissions.get(key)
            if isinstance(candidate, list):
                permission_values.extend(candidate)
    elif isinstance(raw_permissions, list):
        permission_values.extend(raw_permissions)
    approval_scope = spec.get("approval_scope")
    return {
        "side_effects": str(
            governance.get("side_effects")
            or spec.get("side_effects")
            or spec.get("sideEffects")
            or spec.get("effects")
            or ""
        ).strip(),
        "approval_scope": dict(approval_scope) if isinstance(approval_scope, dict) else {},
        "permissions": tuple(
            sorted(
                {
                    str(item).strip().lower()
                    for item in permission_values
                    if str(item).strip()
                }
            )
        ),
        "application_access": _application_access(spec),
    }


def declared_tool_side_effects(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> str:
    """Read a tool's effects from the resolved manifest selected for execution.

    Callers must treat an empty result as undeclared, never as read-only.  The
    resolved manifest is the runtime authority; request payload metadata and
    method-name heuristics are deliberately excluded from this contract.
    """

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    return str(contract.get("side_effects") or "")


def declared_tool_approval_scope(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Return a trusted, manifest-declared reusable approval scope."""

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    scope = contract.get("approval_scope")
    return dict(scope) if isinstance(scope, dict) else {}


def declared_tool_permissions(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> tuple[str, ...]:
    """Return normalized capability ids admitted by the resolved tool manifest."""

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    raw = contract.get("permissions")
    return tuple(raw) if isinstance(raw, tuple) else ()


def declared_tool_application_access(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Return a bounded Application permission/capability mapping from the manifest."""

    contract = declared_tool_contract(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    value = contract.get("application_access")
    return dict(value) if isinstance(value, dict) else {}


__all__ = [
    "READ_ONLY_SIDE_EFFECTS",
    "declared_tool_contract",
    "declared_tool_application_access",
    "declared_tool_approval_scope",
    "declared_tool_permissions",
    "declared_tool_side_effects",
    "normalize_side_effects",
    "side_effects_are_read_only",
]
