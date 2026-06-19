from __future__ import annotations

from adaos.services.skill import manager


def test_skill_tool_yjs_read_only_honors_declared_mutating_payload_key() -> None:
    tool_spec = {
        "side_effects": "read",
        "yjs_governance": {
            "read_only": True,
            "mutating_payload_keys": ["project"],
        },
    }

    assert manager._skill_tool_yjs_read_only("get_snapshot", {"project": False}, tool_spec) is True
    assert manager._skill_tool_yjs_read_only("get_snapshot", {"project": True}, tool_spec) is False


def test_skill_tool_yjs_read_only_keeps_legacy_snapshot_tools_available() -> None:
    assert manager._skill_tool_yjs_read_only("get_snapshot", {"project": False}, {}) is True
    assert manager._skill_tool_yjs_read_only("get_snapshot", {"project": True}, {}) is False


def test_resolve_runtime_tool_name_maps_missing_get_snapshot_to_default_tool() -> None:
    tools = {"refresh_snapshot": {"module": "handlers.main", "callable": "refresh_snapshot"}}

    assert manager._resolve_runtime_tool_name("get_snapshot", "refresh_snapshot", tools) == "refresh_snapshot"
