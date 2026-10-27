"""Read-only negative protocol proof in an explicitly isolated Webspace."""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
from urllib.parse import urlencode, urlsplit, urlunsplit

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from adaos.apps.cli.active_control import resolve_control_token


async def qualify(hub: str, webspace: str) -> dict:
    token = resolve_control_token(base_url=hub)
    base = urlsplit(hub)
    uri = urlunsplit(("wss" if base.scheme == "https" else "ws", base.netloc,
        "/yws/" + webspace, urlencode({"dev": "e2e-replay-budget", "token": token}), ""))
    async with connect(uri, max_size=16 * 1024 * 1024, open_timeout=30, close_timeout=5) as socket:
        # Sync / STEP1 / one-byte state vector containing an unterminated uint.
        invalid = bytes([0, 0, 1, 128])
        await socket.send(invalid)
        while True:
            message = await asyncio.wait_for(socket.recv(), 30)
            if isinstance(message, bytes) and message[:2] == bytes([0, 1]):
                first_replay_bytes = len(message)
                break
        await socket.send(invalid)
        try:
            while True:
                message = await asyncio.wait_for(socket.recv(), 10)
                if isinstance(message, bytes) and message[:2] == bytes([0, 1]):
                    raise AssertionError("repeated malformed vector triggered another full replay")
        except ConnectionClosed as closed:
            assert closed.rcvd is not None and closed.rcvd.code == 1013
            assert closed.rcvd.reason == "yws_guard_projection_generation_mismatch"
            return {"passed": True, "first_replay_bytes": first_replay_bytes,
                "close_code": closed.rcvd.code, "close_reason": closed.rcvd.reason}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", default="http://127.0.0.1:8777")
    parser.add_argument("--webspace", required=True)
    args = parser.parse_args()
    if not args.webspace.startswith("desktop-codex-"):
        parser.error("an explicitly isolated desktop-codex- Webspace is required")
    logging.disable(logging.CRITICAL)
    print(json.dumps(asyncio.run(qualify(args.hub, args.webspace))))


if __name__ == "__main__":
    main()
