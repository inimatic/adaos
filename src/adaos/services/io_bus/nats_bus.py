from __future__ import annotations
"""NATS JetStream IO bus (skeleton).

Streams:
  - TG_INPUT  (subjects: tg.input.*)
  - TG_OUTPUT (subjects: tg.output.*)
  - TG_DLQ    (subjects: tg.dlq.*)
"""
from typing import Callable, Awaitable, Any, Optional

try:
    import asyncio
    from nats.aio.client import Client as NATS  # type: ignore
    from nats.js.api import StreamConfig  # type: ignore
except Exception:  # pragma: no cover
    NATS = None  # type: ignore
    StreamConfig = None  # type: ignore


class NatsIoBus:
    def __init__(self, nats_url: str) -> None:
        self._url = nats_url
        self._nc: Any = None
        self._js: Any = None

    async def connect(self) -> None:
        if NATS is None:
            raise RuntimeError("nats-py is not installed")
        self._nc = await NATS().connect(self._url)
        self._js = self._nc.jetstream()
        await self._ensure_streams()

    async def close(self) -> None:
        if self._nc:
            await self._nc.close()

    async def _ensure_streams(self) -> None:
        if StreamConfig is None:
            return
        streams = {
            "TG_INPUT": {"subjects": ["tg.input.*"]},
            "TG_OUTPUT": {"subjects": ["tg.output.*"]},
            "TG_DLQ": {"subjects": ["tg.dlq.*"]},
        }
        for name, cfg in streams.items():
            try:
                await self._js.add_stream(StreamConfig(name=name, subjects=cfg["subjects"]))
            except Exception:
                # already exists
                pass

    async def publish_input(self, hub_id: str, envelope: dict) -> None:
        subject = f"tg.input.{hub_id}"
        await self._js.publish(subject, json_bytes(envelope))

    async def subscribe_output(self, bot_id: str, handler: Callable[[str, bytes], Awaitable[None]]) -> Any:
        subject = f"tg.output.{bot_id}.>"
        async def _cb(msg):  # type: ignore
            await handler(msg.subject, msg.data)
        return await self._js.subscribe(subject, cb=_cb)


def json_bytes(payload: dict) -> bytes:
    import json
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")

