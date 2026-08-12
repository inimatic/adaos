from __future__ import annotations

import json
from pathlib import Path
from typing import Any


READ_ONLY_SIDE_EFFECTS = frozenset({"safe", "none", "read", "read_only", "readonly"})


def normalize_side_effects(value: Any) -> str:
    return str(value or "").strip().lower().replace("-", "_")


def side_effects_are_read_only(value: Any) -> bool:
    return normalize_side_effects(value) in READ_ONLY_SIDE_EFFECTS


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

    try:
        status = manager.dev_runtime_status(skill_name) if dev else manager.runtime_status(skill_name)
        manifest_path = Path(str(status.get("resolved_manifest") or ""))
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        tools = manifest.get("tools") if isinstance(manifest, dict) else {}
        spec = tools.get(public_tool) if isinstance(tools, dict) else {}
        if not isinstance(spec, dict):
            return ""
        governance = spec.get("yjs_governance") if isinstance(spec.get("yjs_governance"), dict) else {}
        return str(
            governance.get("side_effects")
            or spec.get("side_effects")
            or spec.get("sideEffects")
            or spec.get("effects")
            or ""
        ).strip()
    except Exception:
        return ""
