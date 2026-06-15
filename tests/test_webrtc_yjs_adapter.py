from __future__ import annotations

import asyncio
import importlib
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest


class _DummyDataChannel:
    def __init__(self) -> None:
        self.handlers = {}
        self.sent: list[bytes] = []
        self.close_called = 0
        self.bufferedAmount = 0

    def on(self, _event):
        def decorator(fn):
            self.handlers[_event] = fn
            return fn

        return decorator

    def send(self, message: bytes) -> None:
        self.sent.append(bytes(message))

    def close(self) -> None:
        self.close_called += 1
        handler = self.handlers.get("close")
        if handler:
            handler()

    def emit(self, event: str, data=None) -> None:
        handler = self.handlers.get(event)
        if handler:
            if data is None:
                handler()
            else:
                handler(data)


def _load_yjs_adapter(monkeypatch, *, enabled: str = "1"):
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
    monkeypatch.setenv("ADAOS_WEBRTC_YJS_CHANNEL_ENABLED", enabled)
    return importlib.import_module("adaos.services.webrtc.yjs_adapter"), called


async def _expect_recv_closed(adapter) -> None:
    with pytest.raises(RuntimeError):
        await adapter.recv()


def test_datachannel_yjs_adapter_queues_inbound_messages(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    dc.emit("message", b"hello")

    assert asyncio.run(adapter.recv()) == b"hello"


def test_datachannel_yjs_adapter_closes_oversized_inbound_messages(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    dc.emit("message", b"x" * (513 * 1024))

    assert dc.close_called == 1
    asyncio.run(_expect_recv_closed(adapter))


def test_datachannel_yjs_adapter_closes_oversized_outbound_messages(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    with pytest.raises(RuntimeError):
        asyncio.run(adapter.send(b"x" * (513 * 1024)))

    assert dc.close_called == 1
    assert dc.sent == []


def test_datachannel_yjs_adapter_closes_high_outbound_buffer(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    dc.bufferedAmount = 3 * 1024 * 1024
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    with pytest.raises(RuntimeError):
        asyncio.run(adapter.send(b"hello"))

    assert dc.close_called == 1
    assert dc.sent == []


def test_datachannel_yjs_adapter_respects_disabled_env(monkeypatch) -> None:
    yjs_adapter, called = _load_yjs_adapter(monkeypatch, enabled="0")

    asyncio.run(yjs_adapter.DataChannelYjsAdapter(_DummyDataChannel(), "desktop").serve())

    assert called == {"start": 0, "serve": 0}


def test_datachannel_yjs_adapter_serves_when_enabled(monkeypatch) -> None:
    yjs_adapter, called = _load_yjs_adapter(monkeypatch, enabled="1")

    asyncio.run(yjs_adapter.DataChannelYjsAdapter(_DummyDataChannel(), "desktop").serve())

    assert called == {"start": 1, "serve": 1}
