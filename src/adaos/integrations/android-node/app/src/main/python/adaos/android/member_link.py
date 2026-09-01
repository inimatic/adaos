"""Bounded outbound member link for the experimental Android runtime."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import queue
import socket
import ssl
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse, urlunparse
from urllib.request import Request, urlopen


_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_FRAME_BYTES = 4 * 1024 * 1024
_MAX_OUTBOUND_MESSAGES = 128
_MAX_MEMBER_YJS_UPDATE_BYTES = 512 * 1024
_MAX_ROOT_HTTP_RESPONSE_BYTES = 8 * 1024 * 1024
_HUB_ACTIVITY_STALE_AFTER_SECONDS = 15.0
_ALLOWED_MEMBER_EVENTS = frozenset({"nlp.intent.not_obtained"})


def _redacted_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    host = parsed.hostname or ""
    if parsed.port:
        host = f"{host}:{parsed.port}"
    return urlunparse((parsed.scheme, host, parsed.path.rstrip("/"), "", "", ""))


def _websocket_url(base_url: str) -> str:
    parsed = urlparse(str(base_url or "").strip())
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("member_hub_url_invalid")
    scheme = {"http": "ws", "https": "wss"}.get(parsed.scheme, parsed.scheme)
    path = f"{parsed.path.rstrip('/')}/ws/subnet"
    return urlunparse((scheme, parsed.netloc, path, "", "", ""))


def _canonical_root_url(value: str) -> str:
    """Keep local development HTTP, but never use plaintext for public AdaOS Root."""

    parsed = urlparse(str(value or "").strip().rstrip("/"))
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError("member_root_url_invalid")
    hostname = str(parsed.hostname or "").lower()
    scheme = parsed.scheme
    if scheme == "http" and (
        hostname == "inimatic.com" or hostname.endswith(".inimatic.com")
    ):
        scheme = "https"
    return urlunparse((scheme, parsed.netloc, parsed.path.rstrip("/"), "", "", ""))


def _root_http_base_url(value: str) -> str:
    parsed = urlparse(str(value or "").strip().rstrip("/"))
    if parsed.scheme not in {"http", "https", "ws", "wss"} or not parsed.netloc:
        raise ValueError("member_root_url_invalid")
    scheme = {"ws": "http", "wss": "https"}.get(parsed.scheme, parsed.scheme)
    path = parsed.path.rstrip("/")
    hub_marker = "/hubs/"
    if hub_marker in path:
        path = path.split(hub_marker, 1)[0].rstrip("/")
    elif path.endswith("/ws/subnet"):
        path = path[: -len("/ws/subnet")]
    return _canonical_root_url(urlunparse((scheme, parsed.netloc, path, "", "", "")))


def _joined_hub_url(root_url: str, response_url: str) -> str:
    """Do not accept a Root response which downgrades an HTTPS join to plaintext."""

    root = urlparse(_canonical_root_url(root_url))
    raw = str(response_url or "").strip().rstrip("/") or urlunparse(
        (root.scheme, root.netloc, root.path, "", "", "")
    )
    joined = urlparse(raw)
    if joined.scheme not in {"http", "https", "ws", "wss"} or not joined.netloc:
        raise ValueError("member_hub_url_invalid")
    scheme = joined.scheme
    if root.scheme == "https" and scheme in {"http", "ws"}:
        scheme = "https" if scheme == "http" else "wss"
    return urlunparse((scheme, joined.netloc, joined.path.rstrip("/"), "", "", ""))


def _read_exact(connection: socket.socket, length: int) -> bytes:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = connection.recv(length - len(chunks))
        if not chunk:
            raise ConnectionError("member_link_closed")
        chunks.extend(chunk)
    return bytes(chunks)


class _WebSocketClient:
    def __init__(self, url: str, token: str, *, timeout: float = 5.0) -> None:
        parsed = urlparse(url)
        if parsed.scheme not in {"ws", "wss"} or not parsed.hostname:
            raise ValueError("member_websocket_url_invalid")
        port = parsed.port or (443 if parsed.scheme == "wss" else 80)
        connection = socket.create_connection((parsed.hostname, port), timeout=timeout)
        if parsed.scheme == "wss":
            context = ssl.create_default_context()
            connection = context.wrap_socket(connection, server_hostname=parsed.hostname)
        connection.settimeout(timeout)
        key = base64.b64encode(os.urandom(16)).decode("ascii")
        resource = parsed.path or "/"
        if parsed.query:
            resource = f"{resource}?{parsed.query}"
        request = (
            f"GET {resource} HTTP/1.1\r\n"
            f"Host: {parsed.netloc}\r\n"
            "Upgrade: websocket\r\n"
            "Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            "Sec-WebSocket-Version: 13\r\n"
            f"X-AdaOS-Token: {token}\r\n"
            "\r\n"
        ).encode("ascii")
        connection.sendall(request)
        response = bytearray()
        while b"\r\n\r\n" not in response:
            if len(response) > 32 * 1024:
                connection.close()
                raise ConnectionError("member_websocket_handshake_too_large")
            response.extend(connection.recv(4096))
        header_block = bytes(response).split(b"\r\n\r\n", 1)[0]
        lines = header_block.decode("latin-1").split("\r\n")
        if not lines or " 101 " not in f" {lines[0]} ":
            connection.close()
            raise ConnectionError(f"member_websocket_handshake_rejected:{lines[0] if lines else ''}")
        headers: dict[str, str] = {}
        for line in lines[1:]:
            name, separator, value = line.partition(":")
            if separator:
                headers[name.strip().lower()] = value.strip()
        expected = base64.b64encode(
            hashlib.sha1(f"{key}{_WEBSOCKET_GUID}".encode("ascii")).digest()
        ).decode("ascii")
        if headers.get("sec-websocket-accept") != expected:
            connection.close()
            raise ConnectionError("member_websocket_accept_invalid")
        self.connection = connection
        self._send_lock = threading.Lock()
        self.closed = False

    def _send_frame(self, opcode: int, payload: bytes = b"") -> None:
        if self.closed:
            raise ConnectionError("member_link_closed")
        if len(payload) > _MAX_FRAME_BYTES:
            raise ValueError("member_link_frame_too_large")
        mask = os.urandom(4)
        header = bytearray([0x80 | (opcode & 0x0F)])
        length = len(payload)
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        masked = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
        with self._send_lock:
            self.connection.sendall(bytes(header) + mask + masked)

    def send_json(self, payload: dict[str, Any]) -> None:
        self._send_frame(
            0x1,
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"),
        )

    def recv_json(self, *, timeout: float) -> dict[str, Any] | None:
        self.connection.settimeout(timeout)
        fragments = bytearray()
        first_opcode: int | None = None
        while True:
            try:
                head = _read_exact(self.connection, 2)
            except socket.timeout:
                return None
            fin = bool(head[0] & 0x80)
            opcode = head[0] & 0x0F
            masked = bool(head[1] & 0x80)
            length = head[1] & 0x7F
            if length == 126:
                length = struct.unpack("!H", _read_exact(self.connection, 2))[0]
            elif length == 127:
                length = struct.unpack("!Q", _read_exact(self.connection, 8))[0]
            if length > _MAX_FRAME_BYTES:
                raise ConnectionError("member_link_inbound_frame_too_large")
            mask = _read_exact(self.connection, 4) if masked else b""
            payload = _read_exact(self.connection, length)
            if mask:
                payload = bytes(
                    value ^ mask[index % 4] for index, value in enumerate(payload)
                )
            if opcode == 0x8:
                raise ConnectionError("member_link_remote_closed")
            if opcode == 0x9:
                self._send_frame(0xA, payload[:125])
                continue
            if opcode == 0xA:
                continue
            if opcode in {0x1, 0x2}:
                first_opcode = opcode
                fragments = bytearray(payload)
            elif opcode == 0x0 and first_opcode is not None:
                fragments.extend(payload)
            else:
                continue
            if len(fragments) > _MAX_FRAME_BYTES:
                raise ConnectionError("member_link_fragmented_frame_too_large")
            if not fin:
                continue
            if first_opcode != 0x1:
                return None
            decoded = json.loads(bytes(fragments).decode("utf-8"))
            return decoded if isinstance(decoded, dict) else None

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        # ``close`` is also called from the configuration/join thread while
        # the member worker may be blocked in recv or send.  A graceful frame
        # would contend on ``_send_lock`` and can leave both threads waiting
        # forever after a broken route.  Shutdown the socket first so every
        # in-flight operation is interrupted deterministically.
        try:
            self.connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.connection.close()
        except OSError:
            pass


class AndroidMemberLink:
    """Small protocol-compatible member client with bounded reconnect state."""

    def __init__(
        self,
        data_root: Path,
        *,
        node_id: str,
        local_subnet_id: str,
        status_provider: Callable[[], dict[str, Any]],
        document_provider: Callable[[], dict[str, Any]],
        apply_yjs_update: Callable[[bytes], bool],
        state_changed: Callable[[dict[str, Any]], None],
        rpc_handler: Callable[[str, dict[str, Any]], Any] | None = None,
    ) -> None:
        self.path = Path(data_root) / "android-member-link.json"
        self.node_id = str(node_id)
        self.local_subnet_id = str(local_subnet_id)
        self.status_provider = status_provider
        self.document_provider = document_provider
        self.apply_yjs_update = apply_yjs_update
        self.state_changed = state_changed
        self.rpc_handler = rpc_handler
        self._lock = threading.RLock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._outbound: queue.Queue[dict[str, Any]] = queue.Queue(
            maxsize=_MAX_OUTBOUND_MESSAGES
        )
        self._connection: _WebSocketClient | None = None
        self._thread: threading.Thread | None = None
        self._config_revision = 0
        self._state = "offline"
        self._connected = False
        self._connected_at = 0.0
        self._last_message_at = 0.0
        self._last_pong_at = 0.0
        self._hello_ack_ok = False
        self._hello_ack_at = 0.0
        self._last_error = ""
        self._connect_attempts = 0
        self._reconnect_total = 0
        self._received_yjs_total = 0
        self._ignored_hub_yjs_total = 0
        self._sent_yjs_total = 0
        self._sent_node_state_total = 0
        self._node_state_queued = False
        self._dropped_messages = 0
        self._pending_rpc: dict[str, queue.Queue[dict[str, Any]]] = {}
        self._rpc_requested_total = 0
        self._rpc_completed_total = 0
        self._rpc_failed_total = 0
        self._inbound_rpc_total = 0
        self._inbound_rpc_failed_total = 0
        self._config = self._load_config()
        self._worker_generation = 0
        if not self._config.get("enabled"):
            self._last_error = "not_configured"

    def _load_config(self) -> dict[str, Any]:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict):
                return {}
            changed = False
            hub = urlparse(str(payload.get("hub_url") or ""))
            hostname = str(hub.hostname or "").lower()
            if hub.scheme == "http" and (
                hostname == "inimatic.com" or hostname.endswith(".inimatic.com")
            ):
                payload["hub_url"] = urlunparse(
                    ("https", hub.netloc, hub.path.rstrip("/"), "", "", "")
                )
                payload.setdefault("root_url", f"https://{hub.netloc}")
                payload["updated_at"] = time.time()
                changed = True
            root_candidate = str(payload.get("root_url") or payload.get("hub_url") or "")
            try:
                root_url = _root_http_base_url(root_candidate)
            except ValueError:
                root_url = ""
            if root_url and root_url != str(payload.get("root_url") or ""):
                payload["root_url"] = root_url
                payload["updated_at"] = time.time()
                changed = True
            if changed:
                self._persist_config(payload)
            return payload
        except (OSError, ValueError, TypeError):
            return {}

    def _persist_config(self, payload: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        temporary.replace(self.path)

    def start(self, *, replace: bool = False) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive() and not replace:
                return
            self._stop.clear()
            self._worker_generation += 1
            worker_generation = self._worker_generation
            thread = threading.Thread(
                target=self._run,
                args=(worker_generation,),
                name="adaos-android-member-link",
                daemon=True,
            )
            self._thread = thread
        thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        with self._lock:
            connection = self._connection
        if connection is not None:
            connection.close()
        self._fail_pending_rpc("member_link_stopped")
        thread = self._thread
        if thread is not None:
            thread.join(timeout=5)
        self._set_state("offline", connected=False, error="expected_stop")

    def configure(
        self,
        *,
        hub_url: str,
        subnet_id: str,
        token: str,
        root_url: str = "",
    ) -> dict[str, Any]:
        hub = str(hub_url or "").strip().rstrip("/")
        subnet = str(subnet_id or "").strip()
        secret = str(token or "").strip()
        _websocket_url(hub)
        if not subnet:
            raise ValueError("member_subnet_id_required")
        if not secret:
            raise ValueError("member_token_required")
        payload = {
            "schema": "adaos.android.member_link.v1",
            "enabled": True,
            "hub_url": hub,
            "subnet_id": subnet,
            "token": secret,
            "updated_at": time.time(),
        }
        if str(root_url or "").strip():
            payload["root_url"] = _root_http_base_url(root_url)
        else:
            payload["root_url"] = _root_http_base_url(hub)
        with self._lock:
            previous_identity = (
                str(self._config.get("hub_url") or ""),
                str(self._config.get("subnet_id") or ""),
                str(self._config.get("token") or ""),
            )
        next_identity = (hub, subnet, secret)
        self._persist_config(payload)
        with self._lock:
            self._config = payload
            self._config_revision += 1
            connection = self._connection
        if previous_identity != next_identity:
            self._clear_outbound()
        if connection is not None:
            connection.close()
        self._wake.set()
        # Configuration is an explicit recovery boundary.  A previous worker
        # can be alive but wedged in a native socket operation; a generation
        # replacement lets the new route connect immediately and makes the old
        # worker harmless when it eventually unwinds.
        self.start(replace=True)
        self._set_state("connecting", connected=False, error="")
        return self.snapshot()

    def join(self, *, root_url: str, code: str) -> dict[str, Any]:
        root = _canonical_root_url(root_url)
        join_code = str(code or "").strip()
        if not join_code:
            raise ValueError("member_join_code_required")
        status = self._member_status()
        node_label = str(
            status.get("node_label")
            or status.get("primary_node_name")
            or "Android phone"
        ).strip()
        body = json.dumps(
            {"code": join_code, "node_id": self.node_id, "hostname": node_label}
        ).encode("utf-8")
        errors: list[str] = []
        response: dict[str, Any] | None = None
        for endpoint in ("/v1/subnets/join", "/api/node/join"):
            request = Request(
                f"{root}{endpoint}",
                data=body,
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                method="POST",
            )
            try:
                with urlopen(request, timeout=8) as opened:
                    value = json.loads(opened.read(256 * 1024).decode("utf-8"))
                if isinstance(value, dict) and value.get("ok") is True:
                    response = value
                    break
                errors.append(f"{endpoint}:invalid_response")
            except HTTPError as exc:
                server_error = ""
                try:
                    error_payload = json.loads(exc.read(64 * 1024).decode("utf-8"))
                    if isinstance(error_payload, dict):
                        server_error = str(
                            error_payload.get("error")
                            or error_payload.get("detail")
                            or ""
                        ).strip()
                except (OSError, UnicodeDecodeError, ValueError, TypeError):
                    pass
                errors.append(f"{endpoint}:{server_error or f'http_{exc.code}'}")
                if exc.code not in {404, 405}:
                    break
            except (URLError, OSError, ValueError) as exc:
                errors.append(f"{endpoint}:{type(exc).__name__}")
        if response is None:
            raise RuntimeError("member_join_failed:" + ",".join(errors))
        response_root = _canonical_root_url(str(response.get("root_url") or root))
        hub_url = _joined_hub_url(root, str(response.get("hub_url") or root))
        result = self.configure(
            hub_url=hub_url,
            subnet_id=str(response.get("subnet_id") or ""),
            token=str(response.get("token") or ""),
            root_url=response_root,
        )
        result["joined"] = True
        result["root_url"] = _redacted_url(response_root)
        return result

    def root_http_post_json(
        self,
        path: str,
        payload: dict[str, Any],
        *,
        accept: str = "application/json",
        timeout: float = 40.0,
    ) -> tuple[int, dict[str, str], bytes]:
        normalized_path = str(path or "").strip()
        if not normalized_path.startswith("/"):
            raise ValueError("root_http_path_invalid")
        with self._lock:
            config = dict(self._config)
        root = _root_http_base_url(
            str(config.get("root_url") or config.get("hub_url") or "").strip()
        )
        token = str(config.get("token") or "").strip()
        subnet_id = str(config.get("subnet_id") or "").strip()
        if not token:
            raise PermissionError("member_token_required")
        if not subnet_id:
            raise PermissionError("member_subnet_id_required")
        body = json.dumps(dict(payload or {}), ensure_ascii=False).encode("utf-8")
        request = Request(
            f"{root}{normalized_path}",
            data=body,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": accept,
                "Authorization": f"Bearer {token}",
                "X-AdaOS-Token": token,
                "X-AdaOS-Subnet-Id": subnet_id,
                "X-AdaOS-Node-Id": self.node_id,
            },
            method="POST",
        )
        try:
            with urlopen(request, timeout=max(1.0, float(timeout))) as opened:
                data = opened.read(_MAX_ROOT_HTTP_RESPONSE_BYTES + 1)
                if len(data) > _MAX_ROOT_HTTP_RESPONSE_BYTES:
                    raise ValueError("root_http_response_too_large")
                return (
                    int(getattr(opened, "status", 200)),
                    {str(key).lower(): str(value) for key, value in opened.headers.items()},
                    data,
                )
        except HTTPError as exc:
            data = exc.read(_MAX_ROOT_HTTP_RESPONSE_BYTES + 1)
            if len(data) > _MAX_ROOT_HTTP_RESPONSE_BYTES:
                data = data[:_MAX_ROOT_HTTP_RESPONSE_BYTES]
            return (
                int(exc.code),
                {str(key).lower(): str(value) for key, value in exc.headers.items()},
                data,
            )

    def disconnect(self, *, forget: bool = False) -> dict[str, Any]:
        with self._lock:
            payload = {} if forget else dict(self._config)
            payload["enabled"] = False
            if forget:
                payload = {"schema": "adaos.android.member_link.v1", "enabled": False}
            self._persist_config(payload)
            self._config = payload
            self._config_revision += 1
            connection = self._connection
        if connection is not None:
            connection.close()
        self._clear_outbound()
        self._wake.set()
        self._set_state("offline", connected=False, error="disabled")
        return self.snapshot()

    def send_yjs_update(self, update: bytes) -> bool:
        """Compatibility shim: publish bounded node-owned state, never a raw YDoc."""

        payload = bytes(update)
        if not payload or len(payload) > _MAX_MEMBER_YJS_UPDATE_BYTES:
            return False
        return self.send_node_state(reason="local_yjs_changed")

    def send_node_state(self, *, reason: str = "local_state_changed") -> bool:
        with self._lock:
            if not self._config.get("enabled"):
                return False
            if self._node_state_queued:
                return True
            self._node_state_queued = True
        queued = self._enqueue(
            {"t": "_node_state.refresh", "reason": str(reason or "local_state_changed")}
        )
        if not queued:
            with self._lock:
                self._node_state_queued = False
        return queued

    def send_status(self, *, reason: str = "local_status_changed") -> bool:
        return self._enqueue(
            {
                "t": "node.status",
                "status": self._member_status(),
                "reason": str(reason or "local_status_changed"),
                "ts": time.time(),
            }
        )

    def send_bus_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        source: str = "android.member",
    ) -> bool:
        normalized = str(event_type or "").strip()
        if normalized not in _ALLOWED_MEMBER_EVENTS:
            return False
        with self._lock:
            if not self._is_connected_locked():
                return False
        return self._enqueue(
            {
                "t": "bus.emit",
                "event": {
                    "type": normalized,
                    "payload": dict(payload or {}),
                    "source": str(source or "android.member"),
                    "ts": time.time(),
                },
            }
        )

    def call_hub_tool(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        timeout: float = 40.0,
    ) -> Any:
        normalized_tool = str(tool or "").strip()
        if ":" not in normalized_tool and normalized_tool != "node.voice.activation.claim":
            raise ValueError("member_rpc_tool_invalid")
        with self._lock:
            if not self._is_connected_locked():
                raise ConnectionError("member_link_not_connected")
        request_id = f"android_rpc_{uuid.uuid4().hex}"
        response_queue: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=1)
        with self._lock:
            self._pending_rpc[request_id] = response_queue
            self._rpc_requested_total += 1
        if not self._enqueue(
            {
                "t": "rpc.req",
                "id": request_id,
                "method": "tools.call",
                "params": {
                    "tool": normalized_tool,
                    "arguments": dict(arguments or {}),
                    "timeout": min(50.0, max(5.0, float(timeout))),
                    "dev": False,
                },
            }
        ):
            with self._lock:
                self._pending_rpc.pop(request_id, None)
                self._rpc_failed_total += 1
            raise ConnectionError("member_rpc_queue_full")
        try:
            response = response_queue.get(timeout=min(55.0, max(6.0, float(timeout) + 5.0)))
        except queue.Empty as exc:
            with self._lock:
                self._rpc_failed_total += 1
            raise TimeoutError("member_rpc_timeout") from exc
        finally:
            with self._lock:
                self._pending_rpc.pop(request_id, None)
        if response.get("ok") is not True:
            with self._lock:
                self._rpc_failed_total += 1
            raise RuntimeError(str(response.get("error") or "member_rpc_failed"))
        with self._lock:
            self._rpc_completed_total += 1
        return response.get("result")

    def _fail_pending_rpc(self, error: str) -> None:
        with self._lock:
            pending = list(self._pending_rpc.values())
            self._pending_rpc.clear()
        for response_queue in pending:
            try:
                response_queue.put_nowait({"ok": False, "error": str(error)})
            except queue.Full:
                pass

    def _enqueue(self, message: dict[str, Any]) -> bool:
        try:
            self._outbound.put_nowait(message)
            return True
        except queue.Full:
            try:
                dropped = self._outbound.get_nowait()
                if dropped.get("t") == "_node_state.refresh":
                    with self._lock:
                        self._node_state_queued = False
            except queue.Empty:
                pass
            self._dropped_messages += 1
            try:
                self._outbound.put_nowait(message)
                return True
            except queue.Full:
                return False

    def _clear_outbound(self) -> None:
        while True:
            try:
                self._outbound.get_nowait()
            except queue.Empty:
                with self._lock:
                    self._node_state_queued = False
                return

    def _is_connected_locked(self, now: float | None = None) -> bool:
        if not self._connected or not self._hello_ack_ok:
            return False
        current = time.time() if now is None else float(now)
        last_activity = max(
            float(self._last_pong_at or 0.0),
            float(self._last_message_at or 0.0),
            float(self._hello_ack_at or 0.0),
        )
        return bool(
            last_activity > 0.0
            and current - last_activity <= _HUB_ACTIVITY_STALE_AFTER_SECONDS
        )

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            now = time.time()
            config = dict(self._config)
            hub_url = _redacted_url(str(config.get("hub_url") or ""))
            transport_security = "unconfigured"
            if hub_url:
                transport_security = (
                    "tls" if hub_url.startswith(("https://", "wss://")) else "plaintext"
                )
            connected = self._is_connected_locked(now)
            state = self._state
            last_error = self._last_error
            if self._connected and not connected:
                state = "recovering"
                last_error = "hub_activity_timeout"
            return {
                "schema": "adaos.android.member_link.status.v1",
                "configured": bool(
                    config.get("hub_url") and config.get("subnet_id") and config.get("token")
                ),
                "enabled": bool(config.get("enabled")),
                "connected": connected,
                "state": state,
                "hub_url": hub_url,
                "root_url": _redacted_url(str(config.get("root_url") or "")),
                "subnet_id": str(config.get("subnet_id") or self.local_subnet_id),
                "token_present": bool(config.get("token")),
                "transport_security": transport_security,
                "connected_at": self._connected_at,
                "last_message_at": self._last_message_at,
                "last_message_ago_s": (
                    round(max(0.0, now - self._last_message_at), 3)
                    if self._last_message_at
                    else None
                ),
                "last_pong_at": self._last_pong_at,
                "last_pong_ago_s": (
                    round(max(0.0, now - self._last_pong_at), 3)
                    if self._last_pong_at
                    else None
                ),
                "hello_ack_ok": self._hello_ack_ok,
                "hello_ack_at": self._hello_ack_at,
                "heartbeat_stale_after_s": _HUB_ACTIVITY_STALE_AFTER_SECONDS,
                "last_error": last_error,
                "connect_attempts": self._connect_attempts,
                "reconnect_total": self._reconnect_total,
                "queued_messages": self._outbound.qsize(),
                "dropped_messages": self._dropped_messages,
                "sent_yjs_total": self._sent_yjs_total,
                "received_yjs_total": self._received_yjs_total,
                "ignored_hub_yjs_total": self._ignored_hub_yjs_total,
                "sent_node_state_total": self._sent_node_state_total,
                "pending_rpc": len(self._pending_rpc),
                "rpc_requested_total": self._rpc_requested_total,
                "rpc_completed_total": self._rpc_completed_total,
                "rpc_failed_total": self._rpc_failed_total,
                "inbound_rpc_total": self._inbound_rpc_total,
                "inbound_rpc_failed_total": self._inbound_rpc_failed_total,
            }

    def _set_state(
        self,
        state: str,
        *,
        connected: bool,
        error: str,
        worker_generation: int | None = None,
    ) -> None:
        with self._lock:
            if (
                worker_generation is not None
                and worker_generation != self._worker_generation
            ):
                return
            changed = (
                self._state != state
                or self._connected != connected
                or self._last_error != error
            )
            self._state = state
            self._connected = connected
            self._last_error = str(error or "")[:240]
            if connected and not self._connected_at:
                self._connected_at = time.time()
            if not connected:
                self._connected_at = 0.0
                self._hello_ack_ok = False
            snapshot = self.snapshot()
        if changed:
            try:
                self.state_changed(snapshot)
            except Exception:
                pass

    def _member_status(self) -> dict[str, Any]:
        status = self.status_provider()
        return status if isinstance(status, dict) else {}

    def _member_document(self) -> dict[str, Any]:
        snapshot = self.document_provider()
        return snapshot if isinstance(snapshot, dict) else {}

    def _send_initial_state(self, connection: _WebSocketClient) -> None:
        now = time.time()
        status = self._member_status()
        connection.send_json({"t": "node.status", "status": status, "ts": now})
        document = self._member_document()
        connection.send_json(
            {
                "t": "node.catalog",
                "snapshot": {
                    "node_id": self.node_id,
                    "status": status,
                    "desktop_catalog": document.get("data", {}).get("catalog", {}),
                    "captured_at": now,
                },
                "ts": now,
            }
        )
        connection.send_json(
            {
                "t": "yjs.node_state",
                "webspace_id": "desktop",
                "state": document,
                "reason": "member_link_connected",
                "ts": now,
            }
        )
        self._sent_node_state_total += 1

    def _handle_message(self, connection: _WebSocketClient, message: dict[str, Any]) -> None:
        self._last_message_at = time.time()
        kind = str(message.get("t") or "")
        if kind == "ping":
            connection.send_json({"t": "pong", "ts": time.time()})
        elif kind == "pong":
            self._last_pong_at = time.time()
        elif kind == "node.status.request":
            connection.send_json(
                {"t": "node.status", "status": self._member_status(), "ts": time.time()}
            )
        elif kind in {"node.catalog.request", "node.snapshot.request"}:
            self._send_initial_state(connection)
        elif kind == "yjs.update":
            # A member owns its local webspace. Hub state is projected into the
            # Hub's data.nodes/<node_id> branch and must not be merged into the
            # phone's standalone desktop YDoc.
            self._received_yjs_total += 1
            self._ignored_hub_yjs_total += 1
        elif kind == "rpc.res":
            request_id = str(message.get("id") or "")
            with self._lock:
                response_queue = self._pending_rpc.get(request_id)
            if response_queue is not None:
                try:
                    response_queue.put_nowait(dict(message))
                except queue.Full:
                    pass
        elif kind == "rpc.req":
            request_id = str(message.get("id") or "")
            params = message.get("params") if isinstance(message.get("params"), dict) else {}
            tool = str(params.get("tool") or "")
            arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
            self._inbound_rpc_total += 1
            try:
                if str(message.get("method") or "") != "tools.call" or self.rpc_handler is None:
                    raise PermissionError("rpc_not_supported_android_poc")
                result = self.rpc_handler(tool, arguments)
                connection.send_json({"t": "rpc.res", "id": request_id, "ok": True, "result": result})
            except Exception as exc:
                self._inbound_rpc_failed_total += 1
                connection.send_json(
                    {
                        "t": "rpc.res",
                        "id": request_id,
                        "ok": False,
                        "error": f"{type(exc).__name__}:{str(exc)[:240]}",
                    }
                )
        elif kind == "core.update.request":
            connection.send_json(
                {
                    "t": "core.update.result",
                    "result": {
                        "ok": False,
                        "accepted": False,
                        "error": "android_updates_require_apk",
                    },
                    "ts": time.time(),
                }
            )

    def _run(self, worker_generation: int) -> None:
        backoff = 1.0
        ever_connected = False
        while not self._stop.is_set():
            with self._lock:
                if worker_generation != self._worker_generation:
                    break
                config = dict(self._config)
                revision = self._config_revision
            if not config.get("enabled"):
                self._set_state(
                    "offline",
                    connected=False,
                    error="not_configured",
                    worker_generation=worker_generation,
                )
                self._wake.wait(2.0)
                self._wake.clear()
                continue
            connection: _WebSocketClient | None = None
            try:
                with self._lock:
                    if worker_generation != self._worker_generation:
                        break
                    self._connect_attempts += 1
                self._set_state(
                    "connecting",
                    connected=False,
                    error="",
                    worker_generation=worker_generation,
                )
                connection = _WebSocketClient(
                    _websocket_url(str(config.get("hub_url") or "")),
                    str(config.get("token") or ""),
                )
                with self._lock:
                    if worker_generation != self._worker_generation:
                        raise ConnectionError("member_worker_replaced")
                    self._connection = connection
                connection.send_json(
                    {
                        "t": "hello",
                        "node_id": self.node_id,
                        "subnet_id": str(config.get("subnet_id") or ""),
                        "hostname": str(
                            self._member_status().get("node_label") or "Android phone"
                        ),
                        "roles": ["member"],
                        "node_names": [
                            str(self._member_status().get("node_label") or "Android phone")
                        ],
                        "base_url": None,
                        "capacity": {"profile": "android_poc"},
                    }
                )
                acknowledgement = connection.recv_json(timeout=5.0)
                if not isinstance(acknowledgement, dict) or (
                    acknowledgement.get("t") != "hello.ack"
                    or acknowledgement.get("ok") is not True
                ):
                    reason = (
                        str((acknowledgement or {}).get("error") or "hello_ack_rejected")
                    )
                    raise ConnectionError(reason)
                if ever_connected:
                    self._reconnect_total += 1
                ever_connected = True
                backoff = 1.0
                acknowledged_at = time.time()
                self._last_message_at = acknowledged_at
                self._hello_ack_at = acknowledged_at
                self._hello_ack_ok = True
                self._set_state(
                    "connected",
                    connected=True,
                    error="",
                    worker_generation=worker_generation,
                )
                self._send_initial_state(connection)
                last_ping = time.monotonic()
                last_status = time.monotonic()
                while not self._stop.is_set():
                    with self._lock:
                        if worker_generation != self._worker_generation:
                            raise ConnectionError("member_worker_replaced")
                        if revision != self._config_revision:
                            raise ConnectionError("member_configuration_changed")
                    while True:
                        try:
                            outbound = self._outbound.get_nowait()
                        except queue.Empty:
                            break
                        outbound_kind = str(outbound.get("t") or "")
                        if outbound_kind == "_node_state.refresh":
                            with self._lock:
                                self._node_state_queued = False
                            outbound = {
                                "t": "yjs.node_state",
                                "webspace_id": "desktop",
                                "state": self._member_document(),
                                "reason": str(
                                    outbound.get("reason") or "local_state_changed"
                                ),
                                "ts": time.time(),
                            }
                        try:
                            connection.send_json(outbound)
                        except Exception:
                            if outbound.get("t") == "yjs.node_state":
                                self.send_node_state(
                                    reason=str(outbound.get("reason") or "send_retry")
                                )
                            else:
                                self._enqueue(outbound)
                            raise
                        if outbound.get("t") == "yjs.update":
                            self._sent_yjs_total += 1
                        elif outbound.get("t") == "yjs.node_state":
                            self._sent_node_state_total += 1
                    now = time.monotonic()
                    if now - last_ping >= 5.0:
                        connection.send_json({"t": "ping", "ts": time.time()})
                        last_ping = now
                    if now - last_status >= 20.0:
                        connection.send_json(
                            {
                                "t": "node.status",
                                "status": self._member_status(),
                                "ts": time.time(),
                            }
                        )
                        last_status = now
                    message = connection.recv_json(timeout=0.5)
                    if message is not None:
                        self._handle_message(connection, message)
                    with self._lock:
                        last_activity = max(
                            float(self._last_pong_at or 0.0),
                            float(self._last_message_at or 0.0),
                            float(self._hello_ack_at or 0.0),
                        )
                    if (
                        last_activity > 0.0
                        and time.time() - last_activity
                        > _HUB_ACTIVITY_STALE_AFTER_SECONDS
                    ):
                        raise ConnectionError("hub_activity_timeout")
            except Exception as exc:
                with self._lock:
                    is_current_worker = worker_generation == self._worker_generation
                if not self._stop.is_set() and is_current_worker:
                    self._set_state(
                        "offline",
                        connected=False,
                        error=f"{type(exc).__name__}:{str(exc)[:180]}",
                        worker_generation=worker_generation,
                    )
            finally:
                with self._lock:
                    is_current_worker = worker_generation == self._worker_generation
                    if self._connection is connection:
                        self._connection = None
                if is_current_worker:
                    self._fail_pending_rpc("member_link_disconnected")
                if connection is not None:
                    connection.close()
            with self._lock:
                is_current_worker = worker_generation == self._worker_generation
            if self._stop.is_set() or not is_current_worker:
                break
            self._wake.wait(backoff)
            self._wake.clear()
            backoff = min(backoff * 2.0, 15.0)


__all__ = ["AndroidMemberLink"]
