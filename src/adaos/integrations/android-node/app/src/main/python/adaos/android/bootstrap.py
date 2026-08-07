"""Android-owned AdaOS loopback runtime used by the experimental APK."""

from __future__ import annotations

import base64
import copy
import hashlib
import json
import platform
import socket
import struct
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

_ALLOWED_ORIGINS = {
    "https://inimatic.com",
    "https://www.inimatic.com",
    "http://localhost:4200",
    "http://127.0.0.1:4200",
}
_WEBSOCKET_GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
_MAX_WEBSOCKET_MESSAGE_BYTES = 4 * 1024 * 1024
_MAX_YJS_UPDATES = 512
_MAX_YJS_JOURNAL_BYTES = 8 * 1024 * 1024
_BUNDLE_ROOT = Path(__file__).with_name("bundle")
_SKILL_WEBUI_FILES = (
    ("web_desktop_skill", "web_desktop_skill.webui.json"),
    ("weather_skill", "weather_skill.webui.json"),
    ("adaos_connect", "adaos_connect.webui.json"),
    ("notebook_skill", "notebook_skill.webui.json"),
)

_lock = threading.RLock()
_yjs_lock = threading.RLock()
_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_runtime: dict[str, Any] = {}
_desktop_snapshot: dict[str, Any] = {}
_base_yjs_update = b""
_yjs_updates: list[bytes] = []
_yjs_update_hashes: set[str] = set()
_websocket_peers: set["_WebSocketPeer"] = set()
_journal_path: Path | None = None


class _LoopbackServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _WebSocketPeer:
    def __init__(self, connection: socket.socket, kind: str) -> None:
        self.connection = connection
        self.kind = kind
        self.send_lock = threading.Lock()
        self.closed = False

    def send(self, opcode: int, payload: bytes = b"") -> None:
        if self.closed:
            return
        frame = _encode_websocket_frame(opcode, payload)
        with self.send_lock:
            if self.closed:
                return
            try:
                self.connection.sendall(frame)
            except OSError:
                self.closed = True

    def close(self) -> None:
        with self.send_lock:
            if self.closed:
                return
            self.closed = True
            try:
                self.connection.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
            try:
                self.connection.close()
            except OSError:
                pass


class _Handler(BaseHTTPRequestHandler):
    server_version = "AdaOSAndroidPoC/0.2"
    protocol_version = "HTTP/1.1"

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib HTTP API
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Accept, Authorization, Content-Type, If-None-Match, X-AdaOS-Device-Id, "
            "X-AdaOS-Token, X-AdaOS-Trace-Id",
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP API
        request = urlsplit(self.path)
        path = request.path.rstrip("/") or "/"
        if str(self.headers.get("Upgrade") or "").strip().lower() == "websocket":
            if path == "/ws":
                self._serve_websocket("control")
                return
            if path == "/yws" or path.startswith("/yws/"):
                room = path.removeprefix("/yws/") or "desktop"
                if room != "desktop":
                    self._json(404, {"ok": False, "error": "webspace_not_found", "webspace_id": room})
                    return
                self._serve_websocket("yjs")
                return
            self._json(404, {"ok": False, "error": "websocket_route_not_found", "path": path})
            return

        if path in {"/api/ping", "/healthz"}:
            runtime = _snapshot()
            self._json(
                200,
                {
                    "ok": True,
                    "node_id": runtime["node_id"],
                    "subnet_id": runtime["subnet_id"],
                    "runtime_profile": "android_poc",
                    "runtime": {"transition_role": "active"},
                    "environment": {
                        "platform": "android",
                        "local_api": True,
                        "local_auth_required": False,
                    },
                },
            )
            return
        if path == "/api/node/status":
            self._json(200, _node_status())
            return
        if path == "/api/subnet/alias":
            runtime = _snapshot()
            self._json(
                200,
                {
                    "ok": True,
                    "schema": "adaos.subnet.identity.v1",
                    "subnet_id": runtime["subnet_id"],
                    "alias": "AdaOS Android PoC",
                    "assistant_name": "AdaOS Android PoC",
                },
            )
            return
        if path == "/api/browser/session/authorize":
            query = parse_qs(request.query)
            self._json(
                200,
                {
                    "ok": True,
                    "kind": "browser",
                    "device_id": str((query.get("dev") or [""])[0]),
                    "webspace_id": str((query.get("ws") or ["desktop"])[0]) or "desktop",
                    "allowed": True,
                    "reason": None,
                    "next": "continue",
                    "terminal": False,
                    "local_auth_required": False,
                },
            )
            return
        if path == "/api/node/yjs/webspaces/desktop/materialization/snapshot":
            self._json(200, _materialization_snapshot_payload())
            return
        if path == "/api/node/yjs/webspaces/desktop/materialization":
            payload = _materialization_snapshot_payload()
            self._json(
                200,
                {
                    "ok": True,
                    "accepted": True,
                    "webspace_id": "desktop",
                    "materialization": payload["materialization"],
                    "rebuild": payload["rebuild"],
                },
            )
            return
        if path == "/api/node/projection-demand":
            self._json(200, _projection_snapshot())
            return
        if path in {
            "/api/node/reliability",
            "/api/node/reliability/runtime",
            "/api/node/reliability/summary",
        }:
            query = parse_qs(request.query)
            mode = str((query.get("mode") or ["runtime"])[0] or "runtime")
            self._json(200, _reliability_payload(mode=mode))
            return
        if path == "/":
            self._json(
                200,
                {
                    "ok": True,
                    "message": "AdaOS Android node",
                    "webspace_id": "desktop",
                    "scenario_id": "web_desktop",
                    "yws": "/yws/desktop",
                },
            )
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP API
        path = urlsplit(self.path).path.rstrip("/") or "/"
        body = self._read_json_body()
        if path in {
            "/api/node/projection-demand/client",
            "/api/node/projection-demand/browser-state",
        }:
            self._json(
                200,
                {
                    "ok": True,
                    "accepted": True,
                    "webspace_id": str(body.get("webspace_id") or "desktop"),
                    "record": body,
                    "snapshot": _projection_snapshot(),
                    "yjs": {
                        "ok": True,
                        "accepted": True,
                        "mode": "packaged_seed_plus_update_journal",
                    },
                },
            )
            return
        if path == "/api/node/ui/diagnostics":
            self._json(202, {"ok": True, "accepted": True, "stored": False})
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def do_DELETE(self) -> None:  # noqa: N802 - stdlib HTTP API
        path = urlsplit(self.path).path.rstrip("/") or "/"
        if path.startswith("/api/node/projection-demand/client/"):
            self._json(
                200,
                {
                    "ok": True,
                    "accepted": True,
                    "deleted": True,
                    "webspace_id": "desktop",
                    "snapshot": _projection_snapshot(),
                },
            )
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_json_body(self) -> dict[str, Any]:
        try:
            length = min(int(self.headers.get("Content-Length") or "0"), 1024 * 1024)
            if length <= 0:
                return {}
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
            return payload if isinstance(payload, dict) else {}
        except (OSError, UnicodeDecodeError, ValueError, TypeError):
            return {}

    def _json(self, status: int, payload: dict[str, Any]) -> None:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self.send_response(status)
        self._cors_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _cors_headers(self) -> None:
        origin = str(self.headers.get("Origin") or "").strip()
        if origin in _ALLOWED_ORIGINS:
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Vary", "Origin")

    def _serve_websocket(self, kind: str) -> None:
        origin = str(self.headers.get("Origin") or "").strip()
        key = str(self.headers.get("Sec-WebSocket-Key") or "").strip()
        if origin not in _ALLOWED_ORIGINS or not key:
            self._json(403, {"ok": False, "error": "websocket_origin_denied"})
            return
        accept = base64.b64encode(
            hashlib.sha1(f"{key}{_WEBSOCKET_GUID}".encode("ascii")).digest()
        ).decode("ascii")
        self.send_response(101, "Switching Protocols")
        self.send_header("Upgrade", "websocket")
        self.send_header("Connection", "Upgrade")
        self.send_header("Sec-WebSocket-Accept", accept)
        self.end_headers()
        self.wfile.flush()

        peer = _WebSocketPeer(self.connection, kind)
        with _yjs_lock:
            _websocket_peers.add(peer)
        try:
            if kind == "yjs":
                peer.send(0x2, _encode_sync_message(0, b"\x00"))
            self._websocket_loop(peer)
        finally:
            with _yjs_lock:
                _websocket_peers.discard(peer)
            peer.close()
            self.close_connection = True

    def _websocket_loop(self, peer: _WebSocketPeer) -> None:
        fragmented_opcode: int | None = None
        fragmented_payload = bytearray()
        while not peer.closed:
            frame = _read_websocket_frame(self.rfile)
            if frame is None:
                return
            fin, opcode, payload = frame
            if opcode == 0x8:
                peer.send(0x8, payload[:125])
                return
            if opcode == 0x9:
                peer.send(0xA, payload[:125])
                continue
            if opcode == 0xA:
                continue
            if opcode in {0x1, 0x2}:
                if fin:
                    self._dispatch_websocket_message(peer, opcode, payload)
                    continue
                fragmented_opcode = opcode
                fragmented_payload = bytearray(payload)
                continue
            if opcode == 0x0 and fragmented_opcode is not None:
                fragmented_payload.extend(payload)
                if len(fragmented_payload) > _MAX_WEBSOCKET_MESSAGE_BYTES:
                    return
                if fin:
                    self._dispatch_websocket_message(
                        peer,
                        fragmented_opcode,
                        bytes(fragmented_payload),
                    )
                    fragmented_opcode = None
                    fragmented_payload.clear()

    def _dispatch_websocket_message(
        self,
        peer: _WebSocketPeer,
        opcode: int,
        payload: bytes,
    ) -> None:
        if len(payload) > _MAX_WEBSOCKET_MESSAGE_BYTES:
            peer.close()
            return
        if peer.kind == "yjs" and opcode == 0x2:
            _handle_yjs_message(peer, payload)
            return
        if peer.kind == "control" and opcode == 0x1:
            _handle_control_message(peer, payload)


def _encode_websocket_frame(opcode: int, payload: bytes) -> bytes:
    length = len(payload)
    head = bytearray([0x80 | (opcode & 0x0F)])
    if length < 126:
        head.append(length)
    elif length <= 0xFFFF:
        head.append(126)
        head.extend(struct.pack("!H", length))
    else:
        head.append(127)
        head.extend(struct.pack("!Q", length))
    return bytes(head) + payload


def _read_exact(stream: Any, length: int) -> bytes | None:
    chunks = bytearray()
    while len(chunks) < length:
        chunk = stream.read(length - len(chunks))
        if not chunk:
            return None
        chunks.extend(chunk)
    return bytes(chunks)


def _read_websocket_frame(stream: Any) -> tuple[bool, int, bytes] | None:
    head = _read_exact(stream, 2)
    if head is None:
        return None
    fin = bool(head[0] & 0x80)
    opcode = head[0] & 0x0F
    masked = bool(head[1] & 0x80)
    length = head[1] & 0x7F
    if length == 126:
        raw = _read_exact(stream, 2)
        if raw is None:
            return None
        length = struct.unpack("!H", raw)[0]
    elif length == 127:
        raw = _read_exact(stream, 8)
        if raw is None:
            return None
        length = struct.unpack("!Q", raw)[0]
    if length > _MAX_WEBSOCKET_MESSAGE_BYTES:
        return None
    mask = _read_exact(stream, 4) if masked else None
    if masked and mask is None:
        return None
    payload = _read_exact(stream, length)
    if payload is None:
        return None
    if mask is not None:
        payload = bytes(value ^ mask[index % 4] for index, value in enumerate(payload))
    return fin, opcode, payload


def _encode_var_uint(value: int) -> bytes:
    number = max(0, int(value))
    encoded = bytearray()
    while number > 0x7F:
        encoded.append((number & 0x7F) | 0x80)
        number >>= 7
    encoded.append(number)
    return bytes(encoded)


def _read_var_uint(payload: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(payload) and shift <= 63:
        current = payload[offset]
        offset += 1
        value |= (current & 0x7F) << shift
        if not current & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid_var_uint")


def _encode_var_bytes(payload: bytes) -> bytes:
    return _encode_var_uint(len(payload)) + payload


def _read_var_bytes(payload: bytes, offset: int) -> tuple[bytes, int]:
    length, offset = _read_var_uint(payload, offset)
    end = offset + length
    if length > _MAX_WEBSOCKET_MESSAGE_BYTES or end > len(payload):
        raise ValueError("invalid_var_bytes")
    return payload[offset:end], end


def _encode_sync_message(kind: int, payload: bytes) -> bytes:
    return _encode_var_uint(0) + _encode_var_uint(kind) + _encode_var_bytes(payload)


def _handle_yjs_message(peer: _WebSocketPeer, payload: bytes) -> None:
    try:
        message_type, offset = _read_var_uint(payload)
        if message_type == 0:
            sync_type, offset = _read_var_uint(payload, offset)
            update, _ = _read_var_bytes(payload, offset)
            if sync_type == 0:
                # The packaged seed completes the y-websocket sync handshake;
                # persisted browser updates are replayed immediately after it.
                seed = _base_yjs_update or b"\x00\x00"
                peer.send(0x2, _encode_sync_message(1, seed))
                with _yjs_lock:
                    replay = list(_yjs_updates)
                for stored in replay:
                    peer.send(0x2, _encode_sync_message(2, stored))
                return
            if sync_type in {1, 2} and update not in {b"", b"\x00\x00"}:
                if _remember_yjs_update(update):
                    _broadcast_yjs(_encode_sync_message(2, update), exclude=peer)
                return
        if message_type == 1:
            _broadcast_yjs(payload, exclude=peer)
    except (ValueError, OSError):
        return


def _handle_control_message(peer: _WebSocketPeer, payload: bytes) -> None:
    try:
        message = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, ValueError, TypeError):
        return
    if not isinstance(message, dict):
        return
    if message.get("type") == "ping":
        peer.send(0x1, json.dumps({"type": "pong"}, separators=(",", ":")).encode("utf-8"))
        return
    if message.get("ch") != "events" or message.get("t") != "cmd" or not message.get("id"):
        return
    kind = str(message.get("kind") or "")
    request_payload = message.get("payload") if isinstance(message.get("payload"), dict) else {}
    data: dict[str, Any] = {"ok": True, "accepted": True}
    if kind == "device.register":
        data.update(
            {
                "device_id": str(request_payload.get("device_id") or ""),
                "webspace_id": str(request_payload.get("webspace_id") or "desktop"),
            }
        )
    elif kind == "desktop.scenario.set":
        data.update(
            {
                "webspace_id": "desktop",
                "scenario_id": "web_desktop",
                "switch_skipped": True,
                "skip_reason": "android_poc_fixed_bundle",
            }
        )
    response = {
        "ch": "events",
        "t": "ack",
        "id": str(message["id"]),
        "kind": kind,
        "data": data,
    }
    peer.send(0x1, json.dumps(response, separators=(",", ":")).encode("utf-8"))


def _broadcast_yjs(payload: bytes, *, exclude: _WebSocketPeer | None = None) -> None:
    with _yjs_lock:
        peers = [peer for peer in _websocket_peers if peer.kind == "yjs" and peer is not exclude]
    for peer in peers:
        peer.send(0x2, payload)


def _remember_yjs_update(update: bytes) -> bool:
    digest = hashlib.sha256(update).hexdigest()
    with _yjs_lock:
        if digest in _yjs_update_hashes:
            return False
        _yjs_updates.append(bytes(update))
        _yjs_update_hashes.add(digest)
        while len(_yjs_updates) > _MAX_YJS_UPDATES or sum(map(len, _yjs_updates)) > _MAX_YJS_JOURNAL_BYTES:
            removed = _yjs_updates.pop(0)
            _yjs_update_hashes.discard(hashlib.sha256(removed).hexdigest())
        _persist_yjs_journal_locked()
    return True


def _load_yjs_journal(path: Path) -> None:
    global _journal_path, _yjs_updates, _yjs_update_hashes
    updates: list[bytes] = []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        entries = raw.get("updates") if isinstance(raw, dict) else []
        for item in entries if isinstance(entries, list) else []:
            update = base64.b64decode(str(item), validate=True)
            if update and len(update) <= _MAX_WEBSOCKET_MESSAGE_BYTES:
                updates.append(update)
    except (OSError, ValueError, TypeError):
        updates = []
    with _yjs_lock:
        _journal_path = path
        _yjs_updates = updates[-_MAX_YJS_UPDATES:]
        _yjs_update_hashes = {hashlib.sha256(item).hexdigest() for item in _yjs_updates}


def _persist_yjs_journal_locked() -> None:
    if _journal_path is None:
        return
    payload = {
        "schema": "adaos.android.yjs-update-journal.v1",
        "webspace_id": "desktop",
        "updates": [base64.b64encode(item).decode("ascii") for item in _yjs_updates],
    }
    temporary = _journal_path.with_suffix(".tmp")
    try:
        temporary.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        temporary.replace(_journal_path)
    except OSError:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass


def _load_json(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"bundle entry must be an object: {path.name}")
    return payload


def _load_base_yjs_update() -> bytes:
    path = _BUNDLE_ROOT / "web_desktop.seed.yjs.b64"
    try:
        update = base64.b64decode(path.read_text(encoding="ascii").strip(), validate=True)
    except (OSError, ValueError, TypeError):
        return b""
    if len(update) > _MAX_WEBSOCKET_MESSAGE_BYTES:
        return b""
    return update


def _merge_by_id(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for item in items:
        token = str(item.get("id") or "").strip()
        if token:
            merged[token] = item
    return list(merged.values())


def _dedupe_text(items: list[Any]) -> list[str]:
    result: list[str] = []
    for item in items:
        token = str(item or "").strip()
        if token and token not in result:
            result.append(token)
    return result


def _set_snapshot_path(snapshot: dict[str, Any], path: str, value: Any) -> None:
    parts = [part for part in str(path or "").split("/") if part]
    if not parts or parts[0] not in {"ui", "data", "registry", "runtime"}:
        return
    target: dict[str, Any] = snapshot
    for part in parts[:-1]:
        current = target.get(part)
        if not isinstance(current, dict):
            current = {}
            target[part] = current
        target = current
    target.setdefault(parts[-1], copy.deepcopy(value))


def _build_desktop_snapshot() -> dict[str, Any]:
    seed = _load_json(_BUNDLE_ROOT / "web_desktop.seed.json")
    application = copy.deepcopy(seed["application"])
    catalog = copy.deepcopy(seed["catalog"])
    installed = copy.deepcopy(seed["installed"])
    registry = copy.deepcopy(seed["registry"])
    skill_declarations: list[tuple[str, dict[str, Any]]] = []

    for skill_name, filename in _SKILL_WEBUI_FILES:
        declaration = _load_json(_BUNDLE_ROOT / filename)
        skill_declarations.append((skill_name, declaration))
        for app in declaration.get("apps") or []:
            if isinstance(app, dict):
                item = copy.deepcopy(app)
                item.setdefault("origin", f"skill:{skill_name}")
                item.setdefault("dev", False)
                catalog["apps"].append(item)
        for widget in declaration.get("widgets") or []:
            if isinstance(widget, dict):
                item = copy.deepcopy(widget)
                item.setdefault("origin", f"skill:{skill_name}")
                item.setdefault("dev", False)
                catalog["widgets"].append(item)
        skill_registry = declaration.get("registry") if isinstance(declaration.get("registry"), dict) else {}
        modal_definitions = skill_registry.get("modals")
        if isinstance(modal_definitions, dict):
            for modal_id, modal in modal_definitions.items():
                token = str(modal_id or "").strip()
                if not token:
                    continue
                application["modals"].setdefault(token, copy.deepcopy(modal))
                registry["modals"].append(token)
        elif isinstance(modal_definitions, list):
            registry["modals"].extend(modal_definitions)
        widget_definitions = skill_registry.get("widgets")
        if isinstance(widget_definitions, dict):
            registry["widgets"].extend(widget_definitions.keys())
        elif isinstance(widget_definitions, list):
            registry["widgets"].extend(widget_definitions)
        for contribution in declaration.get("contributions") or []:
            if not isinstance(contribution, dict) or not contribution.get("autoInstall"):
                continue
            token = str(contribution.get("id") or "").strip()
            if contribution.get("extensionPoint") == "desktop.apps" and token:
                installed["apps"].append(token)
            if contribution.get("extensionPoint") == "desktop.widgets" and token:
                installed["widgets"].append(token)
        interfaces = declaration.get("interfaces")
        if isinstance(interfaces, dict):
            application.setdefault("interfaces", {}).update(copy.deepcopy(interfaces))
        interface = declaration.get("interface")
        if isinstance(interface, dict):
            application.setdefault("interfaces", {})[skill_name] = copy.deepcopy(interface)
        resources = declaration.get("resources")
        if isinstance(resources, list):
            application.setdefault("resources", []).extend(copy.deepcopy(resources))

    taiga = _load_json(_BUNDLE_ROOT / "taiga_ui_demo_scenario.scenario.json")
    catalog["apps"].append(
        {
            "id": "scenario:taiga_ui_demo_scenario",
            "title": str(taiga.get("title") or taiga.get("name") or "Taiga UI Demo"),
            "icon": "color-palette-outline",
            "scenario_id": "taiga_ui_demo_scenario",
            "origin": "scenario:taiga_ui_demo_scenario",
            "dev": False,
        }
    )

    catalog["apps"] = _merge_by_id(catalog["apps"])
    catalog["widgets"] = _merge_by_id(catalog["widgets"])
    installed["apps"] = _dedupe_text(installed["apps"])
    installed["widgets"] = _dedupe_text(installed["widgets"])
    registry["modals"] = _dedupe_text(registry["modals"])
    registry["widgets"] = _dedupe_text(registry["widgets"])
    desktop = copy.deepcopy(application.get("desktop") or {})
    desktop["installed"] = copy.deepcopy(installed)

    snapshot: dict[str, Any] = {
        "ui": {
            "current_scenario": "web_desktop",
            "application": application,
        },
        "data": {
            "catalog": catalog,
            "installed": installed,
            "desktop": desktop,
            # The desktop widget-data contract distinguishes an absent node
            # projection from an intentionally empty local projection.
            "nodes": {},
        },
        "registry": {"merged": registry},
        "runtime": {
            "environment": {
                "platform": "android",
                "runtime_profile": "android_poc",
                "materialization": {
                    "scenario_id": "web_desktop",
                    "bundle_id": "android_poc_v1",
                },
            }
        },
    }
    for _skill_name, declaration in skill_declarations:
        defaults = declaration.get("ydoc_defaults")
        if not isinstance(defaults, dict):
            continue
        for path, value in defaults.items():
            _set_snapshot_path(snapshot, str(path), value)
    return snapshot


def _materialization_snapshot_payload() -> dict[str, Any]:
    snapshot = copy.deepcopy(_desktop_snapshot)
    now = time.time()
    return {
        "ok": True,
        "accepted": True,
        "degraded": False,
        "state": "ready",
        "reason": "android_packaged_bundle",
        "stale": False,
        "source": "android_packaged_bundle",
        "last_good_snapshot_at": now,
        "webspace_id": "desktop",
        "snapshot_scope": "essential",
        "snapshot": snapshot,
        "materialization": {
            "ready": True,
            "readiness_state": "ready",
            "missing_branches": [],
            "current_scenario": "web_desktop",
            "scenario_id": "web_desktop",
            "source": "android_packaged_bundle",
            "observed_at": now,
        },
        "seed_health": {
            "state": "ready",
            "reason": "android_packaged_bundle",
            "source": "android_packaged_bundle",
            "stale": False,
            "last_good_snapshot_at": now,
        },
        "rebuild": {
            "state": "ready",
            "scenario_id": "web_desktop",
            "bundle_id": "android_poc_v1",
        },
    }


def _projection_snapshot() -> dict[str, Any]:
    return {
        "ok": True,
        "schema": "adaos.projection-demand.snapshot.v1",
        "webspace_id": "desktop",
        "records": [],
        "registry": {"write_endpoint": "/api/node/projection-demand/client"},
    }


def _reliability_payload(*, mode: str = "runtime") -> dict[str, Any]:
    runtime = _snapshot()
    now_ms = int(time.time() * 1000)
    selected_mode = str(mode or "runtime").strip().lower() or "runtime"
    with _yjs_lock:
        update_count = len(_yjs_updates)
        yjs_connection_count = sum(
            1 for peer in _websocket_peers if peer.kind == "yjs"
        )
    return {
        "ok": True,
        "available": True,
        "schema": f"adaos.reliability_summary.{selected_mode}.v1",
        "source": "api.node.reliability.summary",
        "mode": selected_mode,
        "updatedAt": now_ms,
        "webspaceId": "desktop",
        "observer": {
            "schema": "adaos.runtime_observer.v1",
            "domain": "node_browser",
            "role": "member",
            "nodeId": runtime["node_id"],
            "authority": "local_runtime_only",
            "doesNotImply": ["hub", "root"],
        },
        "connectivity": {
            "requiredUpstreamLink": {
                "kind": "local_runtime",
                "transportState": "ready",
                "transitionState": "ready",
                "plannedTransition": {"active": False, "reason": None},
                "reason": "android_loopback_ready",
                "blockers": [],
                "servedBy": "android_runtime",
            },
            "browserControlRoute": {
                "kind": "browser_control_route",
                "transportState": "ready",
                "transitionState": "ready",
                "plannedTransition": {"active": False, "reason": None},
                "reason": "android_loopback_ready",
                "blockers": [],
                "servedBy": "android_runtime",
            },
        },
        "stateSync": {
            "webspaceId": "desktop",
            "transportState": "attached",
            "firstSyncState": "complete",
            "semanticState": "ready",
            "freshnessState": "fresh",
            "lastGoodSyncAt": now_ms,
            "lastMaterializationAt": now_ms,
            "replay": {"mode": "snapshot_plus_diff", "cursor": str(update_count)},
            "fallbackMode": "off",
            "materialization": {
                "ready": True,
                "readinessState": "ready",
                "scenarioId": "web_desktop",
                "transitionExpected": False,
            },
            "blockers": [],
        },
        "webrtcYjs": {
            "state": "ready",
            "reason": "android_yws_ready",
            "activeWsConnections": 0,
            "activeYwsConnections": yjs_connection_count,
            "openYjsChannels": 0,
        },
        "sidecarEnablement": {
            "enabled": False,
            "defaultEnabled": False,
            "explicit": True,
            "source": "android_profile",
            "role": "member",
            "reason": "android_owned_lifecycle",
        },
    }


def _load_identity(data_root: Path) -> tuple[str, str]:
    identity_path = data_root / "android-node-identity.json"
    try:
        raw = json.loads(identity_path.read_text(encoding="utf-8"))
        node_id = str(raw.get("node_id") or "").strip()
        subnet_id = str(raw.get("subnet_id") or "").strip()
        if node_id and subnet_id:
            return node_id, subnet_id
    except (OSError, ValueError, TypeError):
        pass
    node_id = f"android-{uuid.uuid4().hex[:12]}"
    subnet_id = f"local-{uuid.uuid4().hex[:12]}"
    identity_path.write_text(
        json.dumps({"node_id": node_id, "subnet_id": subnet_id}, sort_keys=True),
        encoding="utf-8",
    )
    return node_id, subnet_id


def start(data_root: str, app_version: str, port: int = 8777) -> str:
    """Start the loopback runtime and return a JSON lifecycle payload."""

    global _server, _thread, _runtime, _desktop_snapshot, _base_yjs_update
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _server is not None:
            return json.dumps(_snapshot(), sort_keys=True)
        node_id, subnet_id = _load_identity(root)
        _desktop_snapshot = _build_desktop_snapshot()
        _base_yjs_update = _load_base_yjs_update()
        _load_yjs_journal(root / "android-yjs-updates.json")
        server = _LoopbackServer(("127.0.0.1", int(port)), _Handler)
        actual_port = int(server.server_address[1])
        started_at = time.time()
        _runtime = {
            "ok": True,
            "ready": True,
            "runtime_profile": "android_poc",
            "implementation": "packaged_yjs_seed_journal",
            "python_version": platform.python_version(),
            "data_root": str(root),
            "host": "127.0.0.1",
            "port": actual_port,
            "app_version": str(app_version),
            "node_id": node_id,
            "subnet_id": subnet_id,
            "started_at": started_at,
            "bundle_id": "android_poc_v1",
        }
        marker = root / "android-node-runtime.json"
        marker.write_text(json.dumps(_runtime, indent=2, sort_keys=True), encoding="utf-8")
        thread = threading.Thread(
            target=server.serve_forever,
            name="adaos-loopback-runtime",
            daemon=True,
        )
        _server = server
        _thread = thread
        thread.start()
        return json.dumps(_runtime, sort_keys=True)


def stop() -> str:
    """Stop the loopback runtime without terminating the embedded interpreter."""

    global _server, _thread, _runtime
    with _lock:
        server = _server
        thread = _thread
        _server = None
        _thread = None
    with _yjs_lock:
        peers = list(_websocket_peers)
    for peer in peers:
        peer.close()
    if server is not None:
        server.shutdown()
        server.server_close()
    if thread is not None and thread is not threading.current_thread():
        thread.join(timeout=5.0)
    with _lock:
        previous = dict(_runtime)
        _runtime = {}
    return json.dumps({"ok": True, "stopped": True, "previous": previous}, sort_keys=True)


def status() -> str:
    return json.dumps(_snapshot(), sort_keys=True)


def _snapshot() -> dict[str, Any]:
    with _lock:
        return dict(_runtime)


def _node_status() -> dict[str, Any]:
    runtime = _snapshot()
    with _yjs_lock:
        yjs_clients = sum(1 for peer in _websocket_peers if peer.kind == "yjs")
        control_clients = sum(1 for peer in _websocket_peers if peer.kind == "control")
        update_count = len(_yjs_updates)
    return {
        "node_id": runtime["node_id"],
        "subnet_id": runtime["subnet_id"],
        "role": "member",
        "node_names": ["Android phone"],
        "primary_node_name": "Android phone",
        "node_label": "Android phone",
        "node_compact_label": "Phone",
        "ready": True,
        "node_state": "ready",
        "draining": False,
        "route_mode": "loopback",
        "connected_to_subnet": False,
        "connected_to_hub": False,
        "runtime": {
            "profile": runtime["runtime_profile"],
            "implementation": runtime["implementation"],
            "python_version": runtime["python_version"],
            "app_version": runtime["app_version"],
            "transition_role": "active",
            "yjs_ready": True,
            "yjs_mode": "packaged_seed_plus_update_journal",
            "yjs_seed_ready": bool(_base_yjs_update),
            "yjs_clients": yjs_clients,
            "yjs_update_count": update_count,
            "control_clients": control_clients,
            "skills_ready": False,
            "skill_descriptors_ready": True,
            "bundle_id": runtime["bundle_id"],
        },
        "environment": {
            "platform": "android",
            "local_api": True,
            "local_auth_required": False,
            "webspace_id": "desktop",
            "scenario_id": "web_desktop",
        },
    }
