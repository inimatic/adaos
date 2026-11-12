from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adaos.services.yjs import gateway_ws


@pytest.mark.asyncio
@pytest.mark.parametrize("remaining", [300.0, 24.4, 0.0])
async def test_yws_guard_reports_remaining_retry_time_without_renewing_quarantine(monkeypatch, remaining):
    now = 1000.0
    monkeypatch.setattr(gateway_ws.time, "time", lambda: now)
    monkeypatch.setattr(gateway_ws, "_yws_guard_should_notify", lambda **kwargs: False)
    monkeypatch.setattr(gateway_ws, "_remember_yws_attempt", lambda *args, **kwargs: None)
    monkeypatch.setattr(gateway_ws, "_hold_yws_guard_reject", AsyncMock(return_value=True))
    socket = SimpleNamespace(close=AsyncMock())
    diagnostic = {"quarantine_until": now + remaining}

    await gateway_ws._reject_yws_guard_connection(
        socket, webspace_id="desktop", dev_id="browser", browser_metadata={},
        attempt_id="attempt", client_attempt_id="client-attempt",
        guard_reason="client_reconnect_backoff", guard_diag=diagnostic,
    )

    socket.close.assert_awaited_once()
    close = socket.close.await_args.kwargs
    assert close["code"] == 1013
    token, retry = close["reason"].split(";")
    assert token == "yws_guard_client_reconnect_backoff"
    retry_ms = int(retry.removeprefix("retry_after_ms="))
    assert round(remaining * 1000) <= retry_ms <= round(remaining * 1000) + 1
    assert len(close["reason"].encode("utf-8")) <= 123
    assert diagnostic["quarantine_until"] == now + remaining
