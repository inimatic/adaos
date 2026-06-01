from __future__ import annotations

import asyncio
from types import SimpleNamespace

from adaos.sdk.data import ctx as ctx_module


def test_sync_ctx_set_inside_event_loop_schedules_projection(monkeypatch) -> None:
    calls: list[tuple[str, str, object, str | None]] = []

    class _ProjectionService:
        async def apply(self, scope, slot, value, *, user_id=None, webspace_id=None):
            calls.append((scope, slot, value, webspace_id))

    monkeypatch.setattr(ctx_module, "require_ctx", lambda _feature=None: SimpleNamespace())
    monkeypatch.setattr(
        ctx_module.ProjectionService,
        "from_ctx",
        staticmethod(lambda _ctx=None: _ProjectionService()),
    )

    async def _run() -> None:
        ctx_module.subnet.set("infra.status", {"value": "OK"}, webspace_id="desktop")
        await asyncio.sleep(0)

    asyncio.run(_run())

    assert calls == [("subnet", "infra.status", {"value": "OK"}, "desktop")]
