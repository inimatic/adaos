from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType
from types import SimpleNamespace


class _DummyDataChannel:
    def on(self, _event):
        def decorator(fn):
            return fn

        return decorator

    def send(self, _message: bytes) -> None:
        return None


def test_datachannel_yjs_adapter_respects_disabled_env(monkeypatch) -> None:
    called = {"start": 0, "serve": 0}

    async def _start_y_server() -> None:
        called["start"] += 1

    async def _serve(_adapter) -> None:
        called["serve"] += 1

    fake_gateway = SimpleNamespace(start_y_server=_start_y_server, y_server=SimpleNamespace(serve=_serve))
    fake_yjs = ModuleType("adaos.services.yjs")
    fake_yjs.gateway_ws = fake_gateway
    monkeypatch.setitem(sys.modules, "adaos.services.yjs", fake_yjs)
    monkeypatch.delitem(sys.modules, "adaos.services.webrtc.yjs_adapter", raising=False)
    monkeypatch.setenv("ADAOS_WEBRTC_YJS_CHANNEL_ENABLED", "0")
    yjs_adapter = importlib.import_module("adaos.services.webrtc.yjs_adapter")

    asyncio.run(yjs_adapter.DataChannelYjsAdapter(_DummyDataChannel(), "desktop").serve())

    assert called == {"start": 0, "serve": 0}
