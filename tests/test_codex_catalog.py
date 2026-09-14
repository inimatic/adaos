import io
import json
from types import SimpleNamespace

import pytest

from adaos.services import codex_catalog


class Input(io.StringIO):
    def close(self):
        self.recorded = self.getvalue()
        super().close()


def fake_process(monkeypatch, responses):
    incoming = io.StringIO("".join(json.dumps(response) + "\n" for response in responses))
    process = SimpleNamespace(stdin=Input(), stdout=incoming, terminated=False,
        poll=lambda: None, wait=lambda **_: 0)
    process.terminate = lambda: setattr(process, "terminated", True)
    monkeypatch.setattr(codex_catalog.subprocess, "Popen", lambda *_, **__: process)
    return process


def test_discovery_initializes_then_pages_and_retains_only_model_metadata(monkeypatch):
    process = fake_process(monkeypatch, [
        {"id": 1, "result": {}}, {"method": "notification", "params": {}},
        {"id": 2, "result": {"data": [{"model": "one", "displayName": "One",
            "supportedReasoningEfforts": [{"reasoningEffort": "low"}], "private": "not exported"}], "nextCursor": "next"}},
        {"id": 3, "result": {"data": [{"model": "hidden", "hidden": True}, {"model": "two"}]}}
    ])
    result = codex_catalog._discover("codex", {}, timeout=1)
    assert result["status"] == "ready"
    assert [row["id"] for row in result["models"]] == ["one", "two"]
    assert "private" not in result["models"][0]
    assert result["models"][0]["supported_reasoning_efforts"] == ["low"]
    requests = [json.loads(line) for line in process.stdin.recorded.splitlines()]
    assert [request["method"] for request in requests] == ["initialize", "initialized", "model/list", "model/list"]
    assert requests[-1]["params"]["cursor"] == "next"
    assert process.terminated and process.stdout.closed


@pytest.mark.parametrize("response", [{"id": 1, "error": {"message": "private detail"}}, None])
def test_discovery_protocol_failure_closes_its_process(monkeypatch, response):
    process = fake_process(monkeypatch, [response] if response else [])
    with pytest.raises(RuntimeError):
        codex_catalog._discover("codex", {}, timeout=1)
    assert process.terminated and process.stdin.closed and process.stdout.closed
