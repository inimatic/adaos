import json

import pytest

from adaos.services.companion.agent import run_turn
from adaos.services.companion.ledger import Ledger


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    monkeypatch.setenv("ADAOS_COMPANION_SAGE_ENABLED", "true")
    return Ledger(tmp_path / "evidence.sqlite3")


def tool(name, args):
    return {"catalog_digest": "catalog-v1", "items": [], "total": 0}


def function(name, arguments, call_id="call-1"):
    return {"id": "response-1", "output": [{"type": "function_call", "name": name, "call_id": call_id, "arguments": json.dumps(arguments)}],
            "usage": {"input_tokens": 30, "output_tokens": 10, "total_tokens": 40}}


def test_loop_preserves_tool_results_and_separates_input_once(ledger):
    requests = []
    def model(messages, **kwargs):
        requests.append(list(messages))
        assert all(item["strict"] for item in kwargs["tools"])
        assert kwargs["prefer_global"] is True
        if len(requests) == 1:
            return function("capabilities_search", {"query": "слайд-шоу", "kind": "ui", "offset": 0, "limit": 5})
        assert any(item.get("type") == "function_call_output" for item in messages)
        return {"output": [], "output_text": "Уточните, какой экран нужен?"}
    result = run_turn("слайд-шоу", webspace="test", history=[{"role": "user", "text": "слайд-шоу"}], model_call=model, call_tool=tool, ledger=ledger)
    assert sum(m.get("content") == "слайд-шоу" for m in requests[0]) == 1
    assert result["tool_calls"] == 1
    records = ledger.records(result["session_id"])
    assert len([r for r in records if r["kind"] == "tool_result"]) == 1
    bundle = ledger.seal(result["session_id"])
    assert bundle["usage_totals"]["input_tokens"] == 30


def test_fabricated_receipt_is_not_delivered(ledger):
    result = run_turn("открой Media Center", webspace="test", ledger=ledger, call_tool=tool,
        model_call=lambda *a, **k: {"output_text": "Открыл. receipt_id=companion-action:fake"})
    assert "fake" not in result["message"]
    assert "нет подтверждения" in result["message"]
    assert any(r["kind"] == "anomaly" for r in ledger.records(result["session_id"]))


def test_receipt_ends_loop_without_another_model_call(ledger):
    calls = []
    receipt = {"action_id": "actual", "operation": "ui.home.open", "status": "dispatched"}
    def mcp(name, args):
        return {"receipt": receipt} if name == "operations.execute" else tool(name, args)
    def model(*a, **k):
        calls.append(1)
        return function("operations_execute", {"preview_id": "approved-preview"})
    result = run_turn("домой", webspace="test", ledger=ledger, call_tool=mcp, model_call=model)
    assert len(calls) == 1
    assert "ещё не получено" in result["message"]
    assert result["action_receipt"] == receipt


def test_exhausted_loop_keeps_failed_attempts(ledger):
    result = run_turn("неизвестно", webspace="test", ledger=ledger, call_tool=tool,
                      model_call=lambda *a, **k: function("not_a_tool", {}), max_calls=2)
    assert result["error"] == "loop_exhausted"
    attempts = [r for r in ledger.records(result["session_id"]) if r["kind"] == "tool_result"]
    assert len(attempts) == 2
    assert all(r["payload"]["result"]["ok"] is False for r in attempts)


def test_disabled_agent_makes_no_provider_or_mcp_call(monkeypatch):
    monkeypatch.delenv("ADAOS_COMPANION_SAGE_ENABLED", raising=False)
    with pytest.raises(PermissionError):
        run_turn("привет", webspace="test", call_tool=lambda *a: pytest.fail("MCP called"), model_call=lambda *a: pytest.fail("LLM called"))
