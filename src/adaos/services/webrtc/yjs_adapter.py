"""
Adapter bridging an aiortc RTCDataChannel to the ypy-websocket protocol.

Mirrors ``FastAPIWebsocketAdapter`` from ``gateway_ws.py`` but operates on a
WebRTC DataChannel instead of a FastAPI WebSocket.
"""

from __future__ import annotations

import asyncio
import logging
import os
import struct
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


_MAX_MESSAGE_BYTES = _env_int("ADAOS_WEBRTC_YJS_MAX_MESSAGE_BYTES", 16 * 1024 * 1024)
_MAX_QUEUE_BYTES = _env_int("ADAOS_WEBRTC_YJS_MAX_QUEUE_BYTES", 8 * 1024 * 1024)
_MAX_QUEUE_MESSAGES = _env_int("ADAOS_WEBRTC_YJS_MAX_QUEUE_MESSAGES", 512)
_OUTBOUND_DRAIN_TARGET_BYTES = _env_int("ADAOS_WEBRTC_YJS_OUTBOUND_DRAIN_TARGET_BYTES", 2 * 1024 * 1024)
_OUTBOUND_DRAIN_TIMEOUT_MS = _env_int("ADAOS_WEBRTC_YJS_OUTBOUND_DRAIN_TIMEOUT_MS", 8000)
_OUTBOUND_DRAIN_POLL_MS = _env_int("ADAOS_WEBRTC_YJS_OUTBOUND_DRAIN_POLL_MS", 50)
_CHUNK_PAYLOAD_BYTES = _env_int("ADAOS_WEBRTC_YJS_CHUNK_BYTES", 256 * 1024)
_CHUNK_MAGIC = 0xFF
_CHUNK_FRAME_TYPE = 1
_CHUNK_HEADER = struct.Struct("!BBIII")
_MAX_CHUNKS_PER_MESSAGE = (_MAX_MESSAGE_BYTES + max(1, _CHUNK_PAYLOAD_BYTES) - 1) // max(1, _CHUNK_PAYLOAD_BYTES) + 1


class DataChannelYjsAdapter:
    """ypy-websocket ``Websocket`` interface backed by a WebRTC DataChannel."""

    def __init__(
        self,
        dc: RTCDataChannel,
        webspace_id: str,
        *,
        device_id: str | None = None,
        peer_id: str | None = None,
    ) -> None:
        self._dc = dc
        self._path = webspace_id
        self._device_id = str(device_id or "").strip() or "webrtc"
        # One device may have several browser tabs.  The peer identity is the
        # lifecycle key for a single RTCPeerConnection/Yjs adapter, while the
        # stable device identity remains available for presence and policy.
        self._peer_id = str(peer_id or self._device_id).strip() or self._device_id
        self._recv_queue: asyncio.Queue[bytes] = asyncio.Queue()
        self._queued_bytes = 0
        self._closed = False
        self._outbound_drain_task: asyncio.Task[bool] | None = None
        self._next_chunk_id = 1
        self._inbound_chunks: dict[int, dict[str, object]] = {}

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
        if self._closed:
            raise RuntimeError("webrtc_yjs_adapter_closed")
        channel_state = str(getattr(self._dc, "readyState", "") or "").strip().lower()
        if channel_state and channel_state != "open":
            self.close()
            raise RuntimeError(f"webrtc_yjs_datachannel_not_open:{channel_state}")
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
            await self._wait_for_outbound_drain()
            buffered = self._buffered_amount()
            if buffered is not None and buffered > _MAX_QUEUE_BYTES:
                self._close_for_pressure(
                    "outbound_buffered_amount_high",
                    buffered_amount=buffered,
                    limit=_MAX_QUEUE_BYTES,
                    drain_target=_OUTBOUND_DRAIN_TARGET_BYTES,
                    drain_timeout_ms=_OUTBOUND_DRAIN_TIMEOUT_MS,
                )
                raise RuntimeError("webrtc_yjs_outbound_buffered_amount_high")
        if len(payload) > _CHUNK_PAYLOAD_BYTES:
            await self._send_chunked(payload)
            return
        await self._send_frame(payload)

    async def _send_frame(self, payload: bytes) -> None:
        try:
            self._dc.send(payload)
        except Exception as exc:
            self.close()
            raise RuntimeError("webrtc_yjs_datachannel_send_failed") from exc

    async def _send_chunked(self, payload: bytes) -> None:
        chunk_size = max(1, _CHUNK_PAYLOAD_BYTES)
        total = (len(payload) + chunk_size - 1) // chunk_size
        if total > _MAX_CHUNKS_PER_MESSAGE:
            self._close_for_pressure(
                "outbound_chunk_count_too_large",
                bytes_len=len(payload),
                chunks=total,
                limit=_MAX_CHUNKS_PER_MESSAGE,
            )
            raise RuntimeError("webrtc_yjs_outbound_chunk_count_too_large")
        chunk_id = self._next_chunk_id
        self._next_chunk_id = 1 if self._next_chunk_id >= 0x7FFFFFFF else self._next_chunk_id + 1
        _log.info(
            "sending chunked yjs datachannel message webspace=%s device=%s peer=%s bytes=%s chunks=%s chunk_bytes=%s",
            self._path,
            self._device_id,
            self._peer_id,
            len(payload),
            total,
            chunk_size,
        )
        for index in range(total):
            buffered = self._buffered_amount()
            if buffered is not None and buffered > _MAX_QUEUE_BYTES:
                await self._wait_for_outbound_drain()
                buffered = self._buffered_amount()
                if buffered is not None and buffered > _MAX_QUEUE_BYTES:
                    self._close_for_pressure(
                        "outbound_buffered_amount_high",
                        buffered_amount=buffered,
                        limit=_MAX_QUEUE_BYTES,
                        drain_target=_OUTBOUND_DRAIN_TARGET_BYTES,
                        drain_timeout_ms=_OUTBOUND_DRAIN_TIMEOUT_MS,
                        chunk_index=index,
                        chunks=total,
                    )
                    raise RuntimeError("webrtc_yjs_outbound_buffered_amount_high")
            start = index * chunk_size
            chunk = payload[start : start + chunk_size]
            frame = _CHUNK_HEADER.pack(_CHUNK_MAGIC, _CHUNK_FRAME_TYPE, chunk_id, index, total) + chunk
            await self._send_frame(frame)

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
        self._inbound_chunks.clear()
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

    async def _wait_for_outbound_drain(self) -> bool:
        task = self._outbound_drain_task
        if task and not task.done():
            return await task
        task = asyncio.create_task(self._wait_for_outbound_drain_once())
        self._outbound_drain_task = task
        try:
            return await task
        finally:
            if self._outbound_drain_task is task:
                self._outbound_drain_task = None

    async def _wait_for_outbound_drain_once(self) -> bool:
        target = min(_OUTBOUND_DRAIN_TARGET_BYTES, _MAX_QUEUE_BYTES)
        deadline = asyncio.get_running_loop().time() + (_OUTBOUND_DRAIN_TIMEOUT_MS / 1000.0)
        last_buffered = self._buffered_amount()
        _log.warning(
            "waiting for yjs datachannel outbound drain webspace=%s buffered_amount=%s target=%s timeout_ms=%s",
            self._path,
            last_buffered,
            target,
            _OUTBOUND_DRAIN_TIMEOUT_MS,
        )
        while not self._closed:
            buffered = self._buffered_amount()
            if buffered is None or buffered <= target:
                if last_buffered is not None:
                    _log.info(
                        "yjs datachannel outbound drain recovered webspace=%s buffered_amount=%s target=%s",
                        self._path,
                        buffered,
                        target,
                    )
                return True
            last_buffered = buffered
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(max(1, _OUTBOUND_DRAIN_POLL_MS) / 1000.0)
        return False

    def _enqueue(self, payload: bytes) -> None:
        if self._closed:
            return
        if payload[:1] == bytes([_CHUNK_MAGIC]):
            payload = self._accept_chunk_frame(payload)
            if payload is None:
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

    def _accept_chunk_frame(self, frame: bytes) -> bytes | None:
        if len(frame) < _CHUNK_HEADER.size:
            self._close_for_pressure("inbound_chunk_header_invalid", bytes_len=len(frame))
            return None
        try:
            magic, frame_type, chunk_id, index, total = _CHUNK_HEADER.unpack_from(frame, 0)
        except Exception:
            self._close_for_pressure("inbound_chunk_header_invalid", bytes_len=len(frame))
            return None
        if (
            magic != _CHUNK_MAGIC
            or frame_type != _CHUNK_FRAME_TYPE
            or total <= 0
            or total > _MAX_CHUNKS_PER_MESSAGE
            or index >= total
        ):
            self._close_for_pressure(
                "inbound_chunk_header_invalid",
                frame_type=frame_type,
                chunk_id=chunk_id,
                index=index,
                chunks=total,
                limit=_MAX_CHUNKS_PER_MESSAGE,
            )
            return None
        payload = frame[_CHUNK_HEADER.size :]
        current = self._inbound_chunks.get(chunk_id)
        if current is None:
            current = {
                "total": total,
                "received": 0,
                "bytes": 0,
                "parts": [None] * total,
            }
            self._inbound_chunks[chunk_id] = current
        elif int(current.get("total") or 0) != total:
            self._inbound_chunks.pop(chunk_id, None)
            self._close_for_pressure(
                "inbound_chunk_header_invalid",
                chunk_id=chunk_id,
                chunks=total,
                expected_chunks=current.get("total"),
            )
            return None
        parts = current.get("parts")
        if not isinstance(parts, list):
            self._inbound_chunks.pop(chunk_id, None)
            self._close_for_pressure("inbound_chunk_header_invalid", chunk_id=chunk_id)
            return None
        if parts[index] is None:
            parts[index] = payload
            current["received"] = int(current.get("received") or 0) + 1
            current["bytes"] = int(current.get("bytes") or 0) + len(payload)
        total_bytes = int(current.get("bytes") or 0)
        if total_bytes > _MAX_MESSAGE_BYTES:
            self._inbound_chunks.pop(chunk_id, None)
            self._close_for_pressure(
                "inbound_message_too_large",
                bytes_len=total_bytes,
                limit=_MAX_MESSAGE_BYTES,
                chunked=True,
            )
            return None
        if int(current.get("received") or 0) < total:
            return None
        self._inbound_chunks.pop(chunk_id, None)
        try:
            message = b"".join(part for part in parts if isinstance(part, bytes))
        except Exception:
            self._close_for_pressure("inbound_chunk_missing", chunk_id=chunk_id, chunks=total)
            return None
        if len(message) != total_bytes:
            self._close_for_pressure("inbound_chunk_missing", chunk_id=chunk_id, chunks=total)
            return None
        _log.info(
            "received chunked yjs datachannel message webspace=%s bytes=%s chunks=%s chunk_bytes=%s",
            self._path,
            len(message),
            total,
            _CHUNK_PAYLOAD_BYTES,
        )
        return message

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
        self._inbound_chunks.clear()
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
        room = None
        try:
            acquire_room = getattr(yjs_gateway, "_acquire_yws_room", None)
            if callable(acquire_room):
                room = await acquire_room(
                    self._path,
                    self._device_id,
                    yws_attempt_id=f"webrtc-yjs:{self._peer_id}",
                )
                await room.serve(self)
            else:  # pragma: no cover - compatibility with older gateway modules.
                await yjs_gateway.y_server.serve(self)  # type: ignore[arg-type]
        except RuntimeError:
            if not self._closed:
                _log.warning("yjs datachannel serve ended with runtime error webspace=%s", self._path, exc_info=True)
        except Exception:
            _log.debug("yjs datachannel serve ended with error webspace=%s", self._path, exc_info=True)
        finally:
            # ``Peer._close_yjs_binding`` cancels this task after closing the
            # adapter.  ypy-websocket's YRoom.serve removes a client only on
            # its normal/Exception path; asyncio cancellation is a
            # BaseException and used to strand the adapter in ``room.clients``.
            # Every later room update was then fanned out to every leaked
            # adapter (170 copies of one ~296 KiB payload were observed).  Make
            # membership cleanup an adapter-owned invariant as well.
            clients = getattr(room, "clients", None) if room is not None else None
            if isinstance(clients, list):
                room.clients = [client for client in clients if client is not self]
            self.close()
            _log.info(
                "yjs datachannel closed webspace=%s device=%s peer=%s",
                self._path,
                self._device_id,
                self._peer_id,
            )
