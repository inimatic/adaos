from __future__ import annotations

from adaos.services.skill.manager import _skill_quarantine_event, _skill_tool_yjs_denied_result


def test_skill_quarantine_payload_includes_scenario_context() -> None:
    payload = {
        "webspace_id": "desktop",
        "scenario_id": "web_desktop",
        "target_node_id": "node-1",
    }
    admission = {
        "allowed": False,
        "webspace_id": "desktop",
        "owner": "skill:browsers_skill",
        "reason": "write_amplification_blocked",
        "retry_after_s": 12.0,
        "quarantine": {"owner": "skill:browsers_skill"},
    }

    denied = _skill_tool_yjs_denied_result(
        name="browsers_skill",
        tool="refresh_snapshot",
        payload=payload,
        admission=admission,
    )
    event = _skill_quarantine_event(
        name="browsers_skill",
        tool="refresh_snapshot",
        payload=payload,
        admission=admission,
    )

    assert denied["scenario_id"] == "web_desktop"
    assert denied["webspace_id"] == "desktop"
    assert denied["target_node_id"] == "node-1"
    assert event["scenario_id"] == "web_desktop"
    assert event["webspace_id"] == "desktop"
    assert event["target_node_id"] == "node-1"


def test_skill_quarantine_payload_uses_meta_scenario_context() -> None:
    denied = _skill_tool_yjs_denied_result(
        name="browsers_skill",
        tool="refresh_snapshot",
        payload={
            "webspace_id": "desktop",
            "_meta": {
                "scenario_id": "web_desktop",
            },
        },
        admission={"allowed": False, "reason": "write_amplification_blocked"},
    )

    assert denied["scenario_id"] == "web_desktop"
