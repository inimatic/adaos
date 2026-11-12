from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adaos.apps.api import node_api


@pytest.mark.asyncio
@pytest.mark.parametrize("ok,accepted", [(False, False), (True, False), (True, True)])
async def test_sidecar_restart_acknowledges_actual_result_before_root_reconnect(monkeypatch, ok, accepted):
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(realtime_sidecar_proc=None)))
    restart = AsyncMock(return_value=(None, {"ok": ok, "accepted": accepted}))
    reconnect = AsyncMock(return_value={"ok": True})
    monkeypatch.setattr(node_api, "_supervisor_enabled", lambda: False)
    monkeypatch.setattr(node_api, "load_config", lambda: SimpleNamespace(role="hub"))
    monkeypatch.setattr(node_api, "restart_realtime_sidecar_subprocess", restart)
    monkeypatch.setattr(node_api, "request_hub_root_reconnect", reconnect)
    monkeypatch.setattr(node_api, "_current_reliability_payload_async", AsyncMock(return_value={"runtime": {}}))
    monkeypatch.setattr(node_api, "realtime_sidecar_listener_snapshot", lambda *args, **kwargs: {})

    result = await node_api.sidecar_restart(request, node_api.SidecarRestartRequest(
        reconnect_hub_root=True, allow_active_channel_disruption=True,
    ))

    assert result["ok"] is ok
    assert result["accepted"] is accepted
    if ok and accepted:
        reconnect.assert_awaited_once()
    else:
        reconnect.assert_not_awaited()
