from __future__ import annotations

from collections.abc import Sequence


TASK_SCOPE_TOOL_ALLOWLIST: dict[str, tuple[str, ...]] = {
    "read_capability_snapshot": (
        "foundation",
        "get_builder_context",
    ),
    "read_requirements": (
        "get_builder_context",
        "get_architecture_catalog",
        "get_sdk_metadata",
        "get_template_catalog",
    ),
    "read_mock_data": ("get_builder_context",),
    "run_staging_validation": (
        "list_managed_targets",
        "get_managed_target",
    ),
}


def task_scope_enabled_tools(scopes: Sequence[str] | None) -> list[str]:
    """Compile Builder task scopes into the smallest Root MCP tool surface."""

    enabled: list[str] = []
    seen_scopes: set[str] = set()
    for raw_scope in scopes or ():
        scope = str(raw_scope or "").strip()
        if not scope or scope in seen_scopes:
            continue
        seen_scopes.add(scope)
        for tool in TASK_SCOPE_TOOL_ALLOWLIST.get(scope, ()):
            if tool not in enabled:
                enabled.append(tool)
    return enabled


__all__ = ["TASK_SCOPE_TOOL_ALLOWLIST", "task_scope_enabled_tools"]
