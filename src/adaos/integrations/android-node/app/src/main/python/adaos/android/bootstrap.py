"""Minimal Android-owned AdaOS bootstrap used by the first APK proof."""

from __future__ import annotations

import json
import platform
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

_ALLOWED_ORIGINS = {
    "https://inimatic.com",
    "https://www.inimatic.com",
    "http://localhost:4200",
    "http://127.0.0.1:4200",
}
_lock = threading.RLock()
_server: ThreadingHTTPServer | None = None
_thread: threading.Thread | None = None
_runtime: dict[str, Any] = {}


class _LoopbackServer(ThreadingHTTPServer):
    allow_reuse_address = True
    daemon_threads = True


class _Handler(BaseHTTPRequestHandler):
    server_version = "AdaOSAndroidPoC/0.1"

    def do_OPTIONS(self) -> None:  # noqa: N802 - stdlib HTTP API
        self.send_response(204)
        self._cors_headers()
        self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
        self.send_header(
            "Access-Control-Allow-Headers",
            "Accept, Authorization, Content-Type, X-AdaOS-Token",
        )
        self.send_header("Access-Control-Allow-Private-Network", "true")
        self.send_header("Access-Control-Max-Age", "600")
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802 - stdlib HTTP API
        path = self.path.partition("?")[0].rstrip("/") or "/"
        if path in {"/api/ping", "/healthz"}:
            self._json(200, {"ok": True, "runtime_profile": "android_poc"})
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
        if path == "/":
            self._json(
                200,
                {
                    "ok": True,
                    "message": "AdaOS Android node bootstrap",
                    "next_gate": "Android y-py and YWS runtime",
                },
            )
            return
        self._json(404, {"ok": False, "error": "not_found", "path": path})

    def log_message(self, _format: str, *_args: object) -> None:
        return

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
    """Start the loopback sentinel and return a JSON lifecycle payload."""

    global _server, _thread, _runtime
    root = Path(data_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    with _lock:
        if _server is not None:
            return json.dumps(_snapshot(), sort_keys=True)
        node_id, subnet_id = _load_identity(root)
        server = _LoopbackServer(("127.0.0.1", int(port)), _Handler)
        actual_port = int(server.server_address[1])
        started_at = time.time()
        _runtime = {
            "ok": True,
            "ready": True,
            "runtime_profile": "android_poc",
            "implementation": "loopback_sentinel",
            "python_version": platform.python_version(),
            "data_root": str(root),
            "host": "127.0.0.1",
            "port": actual_port,
            "app_version": str(app_version),
            "node_id": node_id,
            "subnet_id": subnet_id,
            "started_at": started_at,
        }
        marker = root / "android-node-runtime.json"
        marker.write_text(json.dumps(_runtime, indent=2, sort_keys=True), encoding="utf-8")
        thread = threading.Thread(
            target=server.serve_forever,
            name="adaos-loopback-sentinel",
            daemon=True,
        )
        _server = server
        _thread = thread
        thread.start()
        return json.dumps(_runtime, sort_keys=True)


def stop() -> str:
    """Stop the loopback sentinel without terminating the embedded interpreter."""

    global _server, _thread, _runtime
    with _lock:
        server = _server
        thread = _thread
        _server = None
        _thread = None
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
            "yjs_ready": False,
            "skills_ready": False,
        },
        "environment": {
            "platform": "android",
            "local_api": True,
            "local_auth_required": False,
        },
    }
