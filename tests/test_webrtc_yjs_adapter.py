from __future__ import annotations

import asyncio
import importlib
import struct
import sys
from types import ModuleType
from types import SimpleNamespace

import pytest


_CHUNK_HEADER = struct.Struct("!BBIII")


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


def _load_yjs_adapter(
    monkeypatch,
    *,
    enabled: str = "1",
    drain_timeout_ms: str = "1",
    drain_poll_ms: str = "1",
):
    called = {
        "start": 0,
        "acquire": 0,
        "room_serve": 0,
        "server_serve": 0,
        "last_webspace_id": "",
        "last_dev_id": "",
        "last_attempt_id": "",
    }

    async def _start_y_server() -> None:
        called["start"] += 1

    async def _server_serve(_adapter) -> None:
        called["server_serve"] += 1

    class _FakeRoom:
        async def serve(self, _adapter) -> None:
            called["room_serve"] += 1

    async def _acquire_yws_room(webspace_id: str, dev_id: str, *, yws_attempt_id: str | None = None):
        called["acquire"] += 1
        called["last_webspace_id"] = webspace_id
        called["last_dev_id"] = dev_id
        called["last_attempt_id"] = str(yws_attempt_id or "")
        return _FakeRoom()

    fake_gateway = SimpleNamespace(
        _acquire_yws_room=_acquire_yws_room,
        start_y_server=_start_y_server,
        y_server=SimpleNamespace(serve=_server_serve),
    )
    fake_yjs = ModuleType("adaos.services.yjs")
    fake_yjs.gateway_ws = fake_gateway
    monkeypatch.setitem(sys.modules, "adaos.services.yjs", fake_yjs)
    monkeypatch.setitem(sys.modules, "adaos.services.yjs.gateway_ws", fake_gateway)
    monkeypatch.delitem(sys.modules, "adaos.services.webrtc.yjs_adapter", raising=False)
    try:
        import adaos.services.webrtc as webrtc_pkg

        monkeypatch.delattr(webrtc_pkg, "yjs_adapter", raising=False)
    except Exception:
        pass
    monkeypatch.setenv("ADAOS_WEBRTC_YJS_CHANNEL_ENABLED", enabled)
    monkeypatch.setenv("ADAOS_WEBRTC_YJS_OUTBOUND_DRAIN_TIMEOUT_MS", drain_timeout_ms)
    monkeypatch.setenv("ADAOS_WEBRTC_YJS_OUTBOUND_DRAIN_POLL_MS", drain_poll_ms)
    return importlib.import_module("adaos.services.webrtc.yjs_adapter"), called


async def _expect_recv_closed(adapter) -> None:
    with pytest.raises(RuntimeError):
        await adapter.recv()


def _chunk_frames(payload: bytes, *, chunk_size: int = 4, chunk_id: int = 123) -> list[bytes]:
    chunks = [payload[index : index + chunk_size] for index in range(0, len(payload), chunk_size)]
    total = len(chunks)
    return [
        _CHUNK_HEADER.pack(0xFF, 1, chunk_id, index, total) + chunk
        for index, chunk in enumerate(chunks)
    ]


def _reassemble_chunk_frames(frames: list[bytes]) -> bytes:
    chunks: list[tuple[int, bytes]] = []
    total = 0
    for frame in frames:
        magic, frame_type, _chunk_id, index, total = _CHUNK_HEADER.unpack_from(frame, 0)
        assert magic == 0xFF
        assert frame_type == 1
        chunks.append((index, frame[_CHUNK_HEADER.size :]))
    assert len(chunks) == total
    return b"".join(chunk for _index, chunk in sorted(chunks))


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

    dc.emit("message", b"x" * (20 * 1024 * 1024))

    assert dc.close_called == 1
    asyncio.run(_expect_recv_closed(adapter))


def test_datachannel_yjs_adapter_closes_oversized_outbound_messages(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    with pytest.raises(RuntimeError):
        asyncio.run(adapter.send(b"x" * (20 * 1024 * 1024)))

    assert dc.close_called == 1
    assert dc.sent == []


def test_datachannel_yjs_adapter_chunks_large_outbound_messages(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")
    payload = b"x" * (600 * 1024)

    asyncio.run(adapter.send(payload))

    assert dc.close_called == 0
    assert len(dc.sent) > 1
    assert _reassemble_chunk_frames(dc.sent) == payload


def test_datachannel_yjs_adapter_reassembles_inbound_chunked_messages(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")
    payload = b"initial-yjs-update"
    frames = _chunk_frames(payload, chunk_size=5)

    for frame in frames[:-1]:
        dc.emit("message", frame)
        assert adapter._recv_queue.qsize() == 0
    dc.emit("message", frames[-1])

    assert asyncio.run(adapter.recv()) == payload


def test_datachannel_yjs_adapter_closes_high_outbound_buffer(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    dc.bufferedAmount = 9 * 1024 * 1024
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    with pytest.raises(RuntimeError):
        asyncio.run(adapter.send(b"hello"))

    assert dc.close_called == 1
    assert dc.sent == []


def test_datachannel_yjs_adapter_waits_for_outbound_drain(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch, drain_timeout_ms="50", drain_poll_ms="1")
    dc = _DummyDataChannel()
    dc.bufferedAmount = 9 * 1024 * 1024
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    async def _send_after_drain() -> None:
        task = asyncio.create_task(adapter.send(b"hello"))
        await asyncio.sleep(0.01)
        dc.bufferedAmount = 0
        await task

    asyncio.run(_send_after_drain())

    assert dc.close_called == 0
    assert dc.sent == [b"hello"]


def test_datachannel_yjs_adapter_rechecks_buffer_after_drain_timeout(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    dc.bufferedAmount = 9 * 1024 * 1024
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")

    async def _timeout_but_buffer_did_drain() -> bool:
        dc.bufferedAmount = 0
        return False

    adapter._wait_for_outbound_drain = _timeout_but_buffer_did_drain  # type: ignore[method-assign]

    asyncio.run(adapter.send(b"hello"))

    assert dc.close_called == 0
    assert dc.sent == [b"hello"]


def test_datachannel_yjs_adapter_coalesces_concurrent_outbound_drains(monkeypatch) -> None:
    yjs_adapter, _called = _load_yjs_adapter(monkeypatch)
    dc = _DummyDataChannel()
    dc.bufferedAmount = 9 * 1024 * 1024
    adapter = yjs_adapter.DataChannelYjsAdapter(dc, "desktop")
    drain_calls = 0

    async def _drain_once() -> bool:
        nonlocal drain_calls
        drain_calls += 1
        await asyncio.sleep(0.01)
        dc.bufferedAmount = 0
        return True

    adapter._wait_for_outbound_drain_once = _drain_once  # type: ignore[method-assign]

    async def _send_many() -> None:
        await asyncio.gather(*(adapter.send(f"msg-{idx}".encode("utf-8")) for idx in range(5)))

    asyncio.run(_send_many())

    assert drain_calls == 1
    assert dc.close_called == 0
    assert dc.sent == [f"msg-{idx}".encode("utf-8") for idx in range(5)]


def test_datachannel_yjs_adapter_respects_disabled_env(monkeypatch) -> None:
    yjs_adapter, called = _load_yjs_adapter(monkeypatch, enabled="0")

    asyncio.run(yjs_adapter.DataChannelYjsAdapter(_DummyDataChannel(), "desktop").serve())

    assert called["start"] == 0
    assert called["acquire"] == 0
    assert called["room_serve"] == 0
    assert called["server_serve"] == 0


def test_datachannel_yjs_adapter_serves_acquired_yws_room_when_enabled(monkeypatch) -> None:
    yjs_adapter, called = _load_yjs_adapter(monkeypatch, enabled="1")

    asyncio.run(yjs_adapter.DataChannelYjsAdapter(_DummyDataChannel(), "desktop", device_id="dev-1").serve())

    assert called["start"] == 1
    assert called["acquire"] == 1
    assert called["room_serve"] == 1
    assert called["server_serve"] == 0
    assert called["last_webspace_id"] == "desktop"
    assert called["last_dev_id"] == "dev-1"
    assert called["last_attempt_id"] == "webrtc-yjs:dev-1"
