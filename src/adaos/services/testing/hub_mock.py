from __future__ import annotations
import asyncio
import json
import os
from typing import Any

from adaos.services.io_bus.nats_bus import NatsIoBus


async def run() -> None:
    nats_url = os.getenv("NATS_URL", "nats://127.0.0.1:4222")
    hub_id = os.getenv("HUB_ID", os.getenv("DEFAULT_HUB", "hub-a"))
    bot_id = os.getenv("BOT_ID", "main-bot")
    bus = NatsIoBus(nats_url)
    await bus.connect()

    async def on_input(subject: str, data: bytes) -> None:
        env = json.loads(data.decode("utf-8"))
        payload = env.get("payload", {})
        evt = payload
        out = {
            "target": {"bot_id": bot_id, "hub_id": hub_id, "chat_id": evt.get("chat_id")},
            "messages": [],
        }
        t = (evt.get("type") or "").lower()
        if t == "text":
            txt = (evt.get("payload") or {}).get("text")
            out["messages"].append({"type": "text", "text": f"echo: {txt}"})
        elif t == "action":
            data_id = (evt.get("payload") or {}).get("action", {}).get("id")
            out["messages"].append({"type": "text", "text": f"clicked: {data_id}"})
        elif t in ("audio", "photo", "document"):
            out["messages"].append({"type": "text", "text": f"received {t}"})
        else:
            out["messages"].append({"type": "text", "text": f"unknown update"})
        subject_out = f"tg.output.{bot_id}.{hub_id}"
        await bus._js.publish(subject_out, json.dumps(out, ensure_ascii=False).encode("utf-8"))  # type: ignore[attr-defined]

    # subscribe to tg.input.{hub}
    await bus._js.subscribe(f"tg.input.{hub_id}", cb=lambda msg: asyncio.create_task(on_input(msg.subject, msg.data)))  # type: ignore[attr-defined]
    print(f"hub-mock listening on tg.input.{hub_id}")
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(run())

