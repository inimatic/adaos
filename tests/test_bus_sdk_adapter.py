from __future__ import annotations

import asyncio
import logging

from adaos.sdk.data import bus
from adaos.services.agent_context import get_ctx
from adaos.services.logging import attach_event_logger


async def _run_bus_flow(seen: dict) -> None:
    async def handler(payload: dict):
        seen.update(payload)

    await bus.on("unit.test", handler)
    await bus.emit("unit.test", {"hello": "world"}, source="testcase", actor="pytest")
    await asyncio.sleep(0)


def test_bus_emit_and_on(monkeypatch) -> None:
    ctx = get_ctx()
    seen: dict[str, str] = {}
    records: list[logging.LogRecord] = []
    monkeypatch.setenv("ADAOS_LOG_EVENTS", "1")

    class _RecordHandler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            records.append(record)

    logger = logging.getLogger(f"adaos.test.bus-sdk.{id(records)}")
    logger.handlers = [_RecordHandler()]
    logger.propagate = False
    logger.setLevel(logging.INFO)
    attach_event_logger(ctx.bus, logger)

    asyncio.run(_run_bus_flow(seen))

    assert seen.get("hello") == "world"
    assert records
    assert records[-1].msg == "event"
    assert getattr(records[-1], "extra", {}).get("type") == "unit.test"
