import json
from types import SimpleNamespace

import pytest

from adaos.services.companion import plane, observation
from adaos.services.companion.catalog import CapabilityIndex, ContextHandles
from adaos.services.root_mcp import companion_plane as legacy


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv("ADAOS_COMPANION_SAGE_ENABLED", "true")
    state = {"current_scenario": "management"}
    index = CapabilityIndex(lambda: [
        {"ref": "version", "operation": "runtime.version.read", "effect": "read_only", "admitted": True},
        {"ref": "denied", "effect": "write", "admitted": False},
        {"ref": "home", "operation": "ui.home.open", "effect": "ui_navigation", "admitted": True},
    ])
    handles = ContextHandles(index, lambda: dict(state))
    monkeypatch.setattr(plane, "runtime", lambda ws: (index, handles))
    monkeypatch.setattr(legacy, "get_ctx", lambda: SimpleNamespace(paths=SimpleNamespace(root_mcp_state_dir=lambda: tmp_path)))
    return state, index, handles


def preview(handles, capability="version"):
    return plane.dispatch("operations.preview", {"capability_ref": capability, "context_handle": handles.read()["context_handle"],
        "params_json": "{}", "webspace_id": "test", "session_id": "s", "turn_id": "t"})


def test_preview_execute_identity_replay_and_scope(runtime):
    _, _, handles = runtime
    prepared = preview(handles)
    args = {"preview_id": prepared["preview_id"], "webspace_id": "test", "session_id": "s", "turn_id": "t"}
    with pytest.raises(ValueError, match="preview_not_found"):
        plane.dispatch("operations.execute", {**args, "turn_id": "other"})
    first = plane.dispatch("operations.execute", args)
    assert first["receipt"]["status"] == "completed"
    assert first["receipt"]["result"]["version"]
    replay = plane.dispatch("operations.execute", args)
    assert replay["replayed"]
    assert replay["receipt"] == first["receipt"]


def test_stale_or_denied_preview_cannot_execute(runtime):
    state, _, handles = runtime
    assert preview(handles, "denied")["error"] == "capability_not_admitted"
    prepared = preview(handles)
    state["current_scenario"] = "other"
    with pytest.raises(ValueError, match="context_stale"):
        plane.dispatch("operations.execute", {"preview_id": prepared["preview_id"], "webspace_id": "test", "session_id": "s", "turn_id": "t"})


def test_browser_ack_correlates_existing_identity_only(runtime):
    legacy._write_receipt({"action_id": "actual", "webspace_id": "test", "operation": "ui.modal.open", "status": "dispatched"})
    def emit(request_id, webspace="test", topic="desktop.modal.opened"):
        observation.on_outcome(SimpleNamespace(type=topic, payload={"modal_id": "settings", "_meta": {"request_id": request_id, "webspace_id": webspace}}))
    emit("invented")
    emit("actual", "another")
    emit("actual", topic="desktop.modal.closed")
    assert legacy._recent_receipts(webspace_id="test", limit=100)[0]["status"] == "dispatched"
    emit("actual")
    rows = legacy._recent_receipts(webspace_id="test", limit=100)
    assert len(rows) == 1 and rows[0]["status"] == "completed"
    emit("actual")
    assert legacy._recent_receipts(webspace_id="test", limit=100) == rows


def test_no_declared_targets_does_not_mean_every_target_is_allowed():
    for operation, params in [("ui.modal.open", {"modal_id": "made-up"}), ("ui.scenario.open", {"scenario_id": "made-up"}), ("ui.widget.focus", {"widget_id": "made-up"})]:
        assert not legacy.validate_action_request({"operation": operation, "params": params, "context_digest": "x"}, frame={"context_digest": "x"})["ok"]


def test_sdk_preserves_responses_tool_conversation():
    from adaos.sdk.llm.llm_client import _message_list, _responses_payload
    items = [{"type": "function_call", "name": "capabilities_search", "arguments": "{}", "call_id": "c"},
             {"type": "function_call_output", "call_id": "c", "output": json.dumps({"items": []})},
             {"type": "reasoning", "id": "r", "summary": []}]
    payload = _responses_payload({"model": "test"}, _message_list(items))
    assert payload["input"] == items
