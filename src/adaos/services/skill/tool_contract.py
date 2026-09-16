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

    spec = _resolved_tool_spec(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    governance = spec.get("yjs_governance") if isinstance(spec.get("yjs_governance"), dict) else {}
    return str(
        governance.get("side_effects")
        or spec.get("side_effects")
        or spec.get("sideEffects")
        or spec.get("effects")
        or ""
    ).strip()


def declared_tool_approval_scope(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Return a trusted, manifest-declared reusable approval scope."""

    spec = _resolved_tool_spec(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    scope = spec.get("approval_scope")
    return dict(scope) if isinstance(scope, dict) else {}


def declared_tool_permissions(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> tuple[str, ...]:
    """Return normalized capability ids admitted by the resolved tool manifest."""

    spec = _resolved_tool_spec(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
    raw = spec.get("permissions")
    values: list[Any] = []
    if isinstance(raw, dict):
        for key in ("required", "optional"):
            candidate = raw.get(key)
            if isinstance(candidate, list):
                values.extend(candidate)
    elif isinstance(raw, list):
        values.extend(raw)
    return tuple(sorted({str(item).strip().lower() for item in values if str(item).strip()}))


def declared_tool_application_access(
    manager: Any,
    *,
    skill_name: str,
    public_tool: str,
    dev: bool,
) -> dict[str, Any]:
    """Return a bounded Application permission/capability mapping from the manifest."""

    spec = _resolved_tool_spec(
        manager,
        skill_name=skill_name,
        public_tool=public_tool,
        dev=dev,
    )
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


__all__ = [
    "READ_ONLY_SIDE_EFFECTS",
    "declared_tool_application_access",
    "declared_tool_approval_scope",
    "declared_tool_permissions",
    "declared_tool_side_effects",
    "normalize_side_effects",
    "side_effects_are_read_only",
]
