"""
Adapter bridging an aiortc RTCDataChannel to the ypy-websocket protocol.

Mirrors ``FastAPIWebsocketAdapter`` from ``gateway_ws.py`` but operates on a
WebRTC DataChannel instead of a FastAPI WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from aiortc import RTCDataChannel

from adaos.services.yjs import gateway_ws as yjs_gateway

_log = logging.getLogger("adaos.webrtc.yjs")


def _env_int(name: str, default: int) -> int:
    try:
        value = int(str(os.getenv(name, "") or "").strip())
    except Exception:
        return default
    return value if value > 0 else default


_MAX_MESSAGE_BYTES = _env_int("ADAOS_WEBRTC_YJS_MAX_MESSAGE_BYTES", 512 * 1024)
_MAX_QUEUE_BYTES = _env_int("ADAOS_WEBRTC_YJS_MAX_QUEUE_BYTES", 2 * 1024 * 1024)
_MAX_QUEUE_MESSAGES = _env_int("ADAOS_WEBRTC_YJS_MAX_QUEUE_MESSAGES", 256)


class DataChannelYjsAdapter:
    """ypy-websocket ``Websocket`` interface backed by a WebRTC DataChannel."""

    def __init__(self, dc: RTCDataChannel, webspace_id: str) -> None:
        self._dc = dc
        self._path = webspace_id
        self._recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._queued_bytes = 0
        self._closed = False

        @dc.on("message")
        def on_message(data: bytes | str) -> None:
            if isinstance(data, str):
                self._enqueue(data.encode("utf-8"))
            elif isinstance(data, (bytes, bytearray)):
                self._enqueue(bytes(data))

        @dc.on("close")
        def on_close() -> None:
            self._closed = True
            self._wake_receiver()

    # -- ypy-websocket Websocket interface ------------------------------------

    @property
    def path(self) -> str:
        return self._path

    def __aiter__(self):  # noqa: ANN204
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except Exception:
            raise StopAsyncIteration()

    async def send(self, message: bytes) -> None:
        payload = bytes(message or b"")
        if len(payload) > _MAX_MESSAGE_BYTES:
            self._close_for_pressure(
                "outbound_message_too_large",
                bytes_len=len(payload),
                limit=_MAX_MESSAGE_BYTES,
            )
            raise RuntimeError("webrtc_yjs_outbound_message_too_large")
        buffered = self._buffered_amount()
        if buffered is not None and buffered > _MAX_QUEUE_BYTES:
            self._close_for_pressure(
                "outbound_buffered_amount_high",
                buffered_amount=buffered,
                limit=_MAX_QUEUE_BYTES,
            )
            raise RuntimeError("webrtc_yjs_outbound_buffered_amount_high")
        try:
            self._dc.send(payload)
        except Exception:
            return

    async def recv(self) -> bytes:
        if self._closed:
            raise RuntimeError("datachannel closed")
        data = await self._recv_queue.get()
        if data:
            self._queued_bytes = max(0, self._queued_bytes - len(data))
        if not data and self._closed:
            raise RuntimeError("datachannel closed")
        return data

    # -- lifecycle ------------------------------------------------------------

    def close(self) -> None:
        self._closed = True
        self._wake_receiver()

    def _wake_receiver(self) -> None:
        try:
            self._recv_queue.put_nowait(b"")
        except Exception:
            return

    def _buffered_amount(self) -> int | None:
        try:
            value = getattr(self._dc, "bufferedAmount", None)
            return int(value) if value is not None else None
        except Exception:
            return None

    def _enqueue(self, payload: bytes) -> None:
        if self._closed:
            return
        if len(payload) > _MAX_MESSAGE_BYTES:
            self._close_for_pressure(
                "inbound_message_too_large",
                bytes_len=len(payload),
                limit=_MAX_MESSAGE_BYTES,
            )
            return
        queued_messages = self._recv_queue.qsize()
        if queued_messages + 1 > _MAX_QUEUE_MESSAGES:
            self._close_for_pressure(
                "inbound_queue_messages_high",
                queued_messages=queued_messages,
                limit=_MAX_QUEUE_MESSAGES,
            )
            return
        if self._queued_bytes + len(payload) > _MAX_QUEUE_BYTES:
            self._close_for_pressure(
                "inbound_queue_bytes_high",
                queued_bytes=self._queued_bytes + len(payload),
                limit=_MAX_QUEUE_BYTES,
            )
            return
        self._queued_bytes += len(payload)
        self._recv_queue.put_nowait(payload)

    def _close_for_pressure(self, reason: str, **details: object) -> None:
        if self._closed:
            return
        _log.warning(
            "closing yjs datachannel for pressure webspace=%s reason=%s details=%s",
            self._path,
            reason,
            details,
        )
        self._closed = True
        try:
            close = getattr(self._dc, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
        self._wake_receiver()

    async def serve(self) -> None:
        """Start serving Yjs sync on this DataChannel."""
        enabled_token = str(os.getenv("ADAOS_WEBRTC_YJS_CHANNEL_ENABLED", "1") or "1").strip().lower()
        if enabled_token in {"0", "false", "no", "off"}:
            _log.info("yjs datachannel disabled by ADAOS_WEBRTC_YJS_CHANNEL_ENABLED webspace=%s", self._path)
            return
        await yjs_gateway.start_y_server()
        try:
            await yjs_gateway.y_server.serve(self)  # type: ignore[arg-type]
        except RuntimeError:
            pass
        except Exception:
            _log.debug("yjs datachannel serve ended with error webspace=%s", self._path, exc_info=True)
        finally:
            _log.info("yjs datachannel closed webspace=%s", self._path)
