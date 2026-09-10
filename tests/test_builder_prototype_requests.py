from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from adaos.sdk.builder import prototype
from adaos.services.builder.prototype_requests import candidate_status, submit_request


class FakePrototypeExecution:
    adapter_id = "fake.prototype.v1"

    def __init__(self) -> None:
        self.turns: list[tuple[dict[str, Any], float]] = []
        self.sessions: list[tuple[str, str, float]] = []

    def submit_turn(
        self, payload: Mapping[str, Any], *, timeout_seconds: float
    ) -> Mapping[str, Any]:
        self.turns.append((dict(payload), timeout_seconds))
        return {"ok": True, "status": "accepted", "session_id": "session-1"}

    def get_session(
        self,
        session_id: str,
        *,
        webspace_id: str,
        timeout_seconds: float,
    ) -> Mapping[str, Any]:
        self.sessions.append((session_id, webspace_id, timeout_seconds))
        return {"ok": True, "session": {"id": session_id}}


def test_submit_request_admits_intent_and_brief_before_execution() -> None:
    port = FakePrototypeExecution()

    result = submit_request(
        port,
        "List requests and let a coordinator assign them.",
        webspace_id="e2e-sdk",
        locale="en",
        metadata={"message_id": "message-1"},
        source_kind="e2e",
        timeout_seconds=12,
    )

    payload, timeout = port.turns[0]
    assert timeout == 12
    assert payload["_meta"]["prototype_intent_id"].startswith("intent:")
    assert payload["_meta"]["prototype_brief_digest"].startswith("sha256:")
    assert result["sdk"]["intent"]["source"]["kind"] == "e2e"
    assert result["sdk"]["execution_adapter"] == "fake.prototype.v1"
    assert [item["kind"] for item in result["sdk"]["brief"]["operations"]] == [
        "list",
        "assign",
    ]


def test_candidate_status_keeps_backend_addressing_below_service() -> None:
    port = FakePrototypeExecution()

    result = candidate_status(
        port,
        "session-1",
        webspace_id="e2e-sdk",
        timeout_seconds=4,
    )

    assert port.sessions == [("session-1", "e2e-sdk", 4)]
    assert result["session"]["id"] == "session-1"
    assert result["sdk"]["schema"] == "adaos.builder.prototype_candidate_status.v1"


def test_public_prototype_sdk_uses_named_execution_port(monkeypatch) -> None:
    port = FakePrototypeExecution()
    monkeypatch.setattr(prototype, "_compatibility_execution_port", lambda: port)

    result = prototype.submit_request(
        "Show entries.",
        webspace_id="sdk-public",
        locale="en",
    )

    assert result["ok"] is True
    assert result["sdk"]["execution_adapter"] == "fake.prototype.v1"
