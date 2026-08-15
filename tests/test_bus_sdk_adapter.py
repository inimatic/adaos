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


async def _emit_service_issue() -> None:
    await bus.emit(
        "skill.service.issue",
        {
            "skill": "media_indexer_skill",
            "issue": {
                "id": "iss.test",
                "type": "endpoint_unhealthy",
                "severity": "warning",
                "message": "healthcheck failed",
                "details": {"token": "must-not-be-logged"},
            },
        },
        source="skill.service",
        actor="pytest",
    )
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

    asyncio.run(_emit_service_issue())
    issue_record = next(
        record
        for record in reversed(records)
        if getattr(record, "extra", {}).get("type") == "skill.service.issue"
    )
    issue_extra = getattr(issue_record, "extra", {})
    assert issue_extra["payload"] is None
    assert issue_extra["skill"] == "media_indexer_skill"
    assert issue_extra["issue_id"] == "iss.test"
    assert issue_extra["issue_type"] == "endpoint_unhealthy"
    assert issue_extra["issue_severity"] == "warning"
    assert issue_extra["issue_message"] == "healthcheck failed"
    assert "must-not-be-logged" not in str(issue_extra)
