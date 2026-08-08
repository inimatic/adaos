"""Write or verify a marker through the Android node's real y-websocket endpoint."""

from __future__ import annotations

import argparse
import json
import time
from typing import Any

import y_py as Y
from websockets.sync.client import connect


def _encode_var_uint(value: int) -> bytes:
    encoded = bytearray()
    while value > 0x7F:
        encoded.append((value & 0x7F) | 0x80)
        value >>= 7
    encoded.append(value)
    return bytes(encoded)


def _read_var_uint(payload: bytes, offset: int = 0) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(payload):
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if byte < 0x80:
            return value, offset
        shift += 7
    raise ValueError("truncated varuint")


def _encode_sync(sync_type: int, payload: bytes) -> bytes:
    return b"".join(
        (
            _encode_var_uint(0),
            _encode_var_uint(sync_type),
            _encode_var_uint(len(payload)),
            payload,
        )
    )


def _read_sync(payload: bytes) -> tuple[int, bytes]:
    message_type, offset = _read_var_uint(payload)
    sync_type, offset = _read_var_uint(payload, offset)
    length, offset = _read_var_uint(payload, offset)
    if message_type != 0 or offset + length > len(payload):
        raise ValueError("invalid y-websocket sync message")
    return sync_type, payload[offset : offset + length]


def _sync_document(uri: str) -> tuple[Any, Any]:
    document = Y.YDoc()
    websocket = connect(
        uri,
        origin="https://inimatic.com",
        open_timeout=5,
        close_timeout=2,
    )
    sync_type, _ = _read_sync(websocket.recv(timeout=5))
    if sync_type != 0:
        websocket.close()
        raise RuntimeError(f"expected SyncStep1, got {sync_type}")
    websocket.send(_encode_sync(0, bytes(Y.encode_state_vector(document))))
    sync_type, update = _read_sync(websocket.recv(timeout=5))
    if sync_type != 1:
        websocket.close()
        raise RuntimeError(f"expected SyncStep2, got {sync_type}")
    Y.apply_update(document, update)
    return document, websocket


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("write", "verify"))
    parser.add_argument("--uri", default="ws://127.0.0.1:18777/yws/desktop")
    parser.add_argument("--key", default="android_restart_probe")
    parser.add_argument("--value")
    arguments = parser.parse_args()
    expected = arguments.value or f"probe-{time.time_ns()}"

    document, websocket = _sync_document(arguments.uri)
    try:
        runtime = json.loads(document.get_map("runtime").to_json())
        if arguments.mode == "verify":
            actual = runtime.get(arguments.key)
            if actual != expected:
                raise RuntimeError(f"restart marker mismatch: {actual!r} != {expected!r}")
        else:
            before = bytes(Y.encode_state_vector(document))
            with document.begin_transaction() as transaction:
                document.get_map("runtime").set(transaction, arguments.key, expected)
            websocket.send(
                _encode_sync(2, bytes(Y.encode_state_as_update(document, before)))
            )
    finally:
        websocket.close()

    print(json.dumps({"ok": True, "mode": arguments.mode, "value": expected}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
