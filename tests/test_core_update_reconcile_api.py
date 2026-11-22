from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("fields,manual", [({}, False), ({"manual": True}, True)])
def test_reconcile_distinguishes_background_checks_from_manual_update(monkeypatch, fields, manual):
    from adaos.apps.api import server
    from adaos.services.root import core_update_sync

    conf = SimpleNamespace(core_update_enabled=False)
    calls = []

    def reconcile(config, **kwargs):
        assert config is conf
        calls.append(kwargs)
        return {"ok": True, "needs_update": False}

    async def status():
        return {"state": "succeeded"}

    monkeypatch.setattr(server, "_ensure_runtime_admin_mutation_allowed", lambda _action: None)
    monkeypatch.setattr(server, "core_update_reactions_disabled_reason", lambda: None)
    monkeypatch.setattr(server, "get_ctx", lambda: SimpleNamespace(config=conf))
    monkeypatch.setattr(server, "_read_core_update_status_async", status)
    monkeypatch.setattr(server.app.state, "core_update_task", None, raising=False)
    monkeypatch.setattr(server.app.state, "shutdown_requested", False, raising=False)
    monkeypatch.setattr(core_update_sync, "reconcile_hub_core_update", reconcile)

    result = asyncio.run(server.admin_update_reconcile(server.CoreUpdateReconcileRequest(**fields)))

    assert calls == [{"countdown_sec": 60.0, "manual": manual}]
    assert result["ok"] is True
    assert result["accepted"] is False
