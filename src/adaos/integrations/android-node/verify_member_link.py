"""Protocol-compatible Root/Hub fixture for Android member-link verification."""

from __future__ import annotations

import argparse
import base64
import json
import threading
import time
from collections import Counter
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import y_py as Y
from websockets.sync.server import ServerConnection, serve


class Evidence:
    def __init__(self, *, code: str, token: str, subnet_id: str, hub_url: str) -> None:
        self.code = code
        self.token = token
        self.subnet_id = subnet_id
        self.hub_url = hub_url
        self.lock = threading.RLock()
        self.sessions = 0
        self.hellos: list[dict[str, Any]] = []
        self.types: Counter[str] = Counter()
        self.last_status: dict[str, Any] = {}
        self.last_catalog: dict[str, Any] = {}
        self.last_node_state: dict[str, Any] = {}
        self.join_total = 0
        self.join_rejected_total = 0
        self.inbound_probe_sent = False

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {
                "ok": True,
                "schema": "adaos.android.member_link.evidence.v1",
                "sessions": self.sessions,
                "hellos": list(self.hellos),
                "message_types": dict(self.types),
                "join_total": self.join_total,
                "join_rejected_total": self.join_rejected_total,
                "node_status_total": self.types["node.status"],
                "node_catalog_total": self.types["node.catalog"],
                "yjs_node_state_total": self.types["yjs.node_state"],
                "yjs_update_total": self.types["yjs.update"],
                "pong_total": self.types["pong"],
                "inbound_probe_sent": self.inbound_probe_sent,
                "last_node_id": str((self.hellos[-1] if self.hellos else {}).get("node_id") or ""),
                "last_status_connected": bool(self.last_status),
                "last_catalog_received": bool(self.last_catalog),
                "last_node_state_received": bool(self.last_node_state),
                "last_node_label": str(
                    (
                        (
                            (self.last_node_state.get("desktop") or {}).get("subnet_env")
                            or {}
                        ).get("current")
                        or {}
                    ).get("node_label")
                    or ""
                ),
            }


def _probe_update() -> bytes:
    document = Y.YDoc()
    with document.begin_transaction() as transaction:
        document.get_map("runtime").set(
            transaction,
            "member_hub_probe",
            "received-from-protocol-hub",
        )
    return bytes(Y.encode_state_as_update(document))


def _root_handler(evidence: Evidence) -> type[BaseHTTPRequestHandler]:
    class RootHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802
            length = min(int(self.headers.get("Content-Length") or "0"), 64 * 1024)
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
            except (OSError, UnicodeDecodeError, ValueError, TypeError):
                body = {}
            if self.path.rstrip("/") not in {"/v1/subnets/join", "/api/node/join"}:
                self._json(404, {"ok": False, "error": "not_found"})
                return
            if not isinstance(body, dict) or str(body.get("code") or "") != evidence.code:
                with evidence.lock:
                    evidence.join_rejected_total += 1
                self._json(403, {"ok": False, "error": "join_code_invalid"})
                return
            with evidence.lock:
                evidence.join_total += 1
            self._json(
                200,
                {
                    "ok": True,
                    "hub_url": evidence.hub_url,
                    "subnet_id": evidence.subnet_id,
                    "token": evidence.token,
                },
            )

        def do_GET(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/evidence":
                self._json(200, evidence.snapshot())
                return
            self._json(404, {"ok": False, "error": "not_found"})

        def _json(self, status: int, payload: dict[str, Any]) -> None:
            encoded = json.dumps(payload, sort_keys=True).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(encoded)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    return RootHandler


def _hub_handler(evidence: Evidence):
    probe = base64.b64encode(_probe_update()).decode("ascii")

    def handle(connection: ServerConnection) -> None:
        try:
            raw = connection.recv(timeout=5)
            hello = json.loads(raw)
        except Exception:
            connection.close(code=1002, reason="hello_required")
            return
        request_token = str(connection.request.headers.get("X-AdaOS-Token") or "")
        if (
            not isinstance(hello, dict)
            or hello.get("t") != "hello"
            or request_token != evidence.token
            or str(hello.get("subnet_id") or "") != evidence.subnet_id
        ):
            connection.send(json.dumps({"t": "hello.ack", "ok": False, "error": "invalid_member"}))
            connection.close(code=1008, reason="invalid_member")
            return
        with evidence.lock:
            evidence.sessions += 1
            session = evidence.sessions
            evidence.hellos.append(
                {
                    "node_id": str(hello.get("node_id") or ""),
                    "subnet_id": str(hello.get("subnet_id") or ""),
                    "roles": list(hello.get("roles") or []),
                }
            )
        connection.send(
            json.dumps(
                {
                    "t": "hello.ack",
                    "ok": True,
                    "hub_node_id": "android-proof-hub",
                    "subnet_id": evidence.subnet_id,
                    "server_time": time.time(),
                }
            )
        )
        connection.send(json.dumps({"t": "node.status.request"}))
        connection.send(json.dumps({"t": "node.catalog.request"}))
        required = {"node.status", "node.catalog", "yjs.node_state"}
        seen: set[str] = set()
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            try:
                raw = connection.recv(timeout=1)
            except TimeoutError:
                continue
            except Exception:
                return
            try:
                message = json.loads(raw)
            except (TypeError, ValueError):
                continue
            if not isinstance(message, dict):
                continue
            kind = str(message.get("t") or "")
            with evidence.lock:
                evidence.types[kind] += 1
                if kind == "node.status" and isinstance(message.get("status"), dict):
                    evidence.last_status = dict(message["status"])
                elif kind == "node.catalog" and isinstance(message.get("snapshot"), dict):
                    evidence.last_catalog = dict(message["snapshot"])
                elif kind == "yjs.node_state" and isinstance(message.get("state"), dict):
                    evidence.last_node_state = dict(message["state"])
            seen.add(kind)
            if kind == "ping":
                connection.send(json.dumps({"t": "pong", "ts": time.time()}))
            if session == 1 and required <= seen:
                connection.close(code=1012, reason="forced_reconnect_proof")
                return
            if session >= 2 and required <= seen:
                with evidence.lock:
                    send_probe = not evidence.inbound_probe_sent
                    if send_probe:
                        evidence.inbound_probe_sent = True
                if send_probe:
                    connection.send(
                        json.dumps(
                            {
                                "t": "yjs.update",
                                "webspace_id": "desktop",
                                "update_b64": probe,
                                "origin_node_id": "android-proof-hub",
                                "ts": time.time(),
                            }
                        )
                    )
                required.clear()

    return handle


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root-port", type=int, default=18778)
    parser.add_argument("--hub-port", type=int, default=18779)
    parser.add_argument("--advertised-host", default="127.0.0.1")
    parser.add_argument("--code", default="ANDROID-POC-JOIN")
    parser.add_argument("--token", default="android-poc-member-token")
    parser.add_argument("--subnet-id", default="android-poc-subnet")
    arguments = parser.parse_args()
    evidence = Evidence(
        code=arguments.code,
        token=arguments.token,
        subnet_id=arguments.subnet_id,
        hub_url=f"http://{arguments.advertised_host}:{arguments.hub_port}",
    )
    root = ThreadingHTTPServer(
        ("127.0.0.1", arguments.root_port),
        _root_handler(evidence),
    )
    root_thread = threading.Thread(target=root.serve_forever, daemon=True)
    root_thread.start()
    print(
        json.dumps(
            {
                "ok": True,
                "root_url": f"http://{arguments.advertised_host}:{arguments.root_port}",
                "hub_url": evidence.hub_url,
                "subnet_id": arguments.subnet_id,
            }
        ),
        flush=True,
    )
    try:
        with serve(
            _hub_handler(evidence),
            "127.0.0.1",
            arguments.hub_port,
            compression=None,
            max_size=4 * 1024 * 1024,
        ) as hub:
            hub.serve_forever()
    finally:
        root.shutdown()
        root.server_close()
        root_thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
