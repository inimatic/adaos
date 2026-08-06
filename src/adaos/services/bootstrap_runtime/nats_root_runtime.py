from __future__ import annotations

from typing import Any

from adaos.services.bootstrap_runtime.nats_transport_runtime import NatsRootTransportRuntime


async def start_nats_root_transport(
    service: Any,
    *,
    core_bus: Any,
    startup_stage_mark: Any,
    report_control_lifecycle: Any,
) -> None:
    """Compose and start the owned hub-root NATS transport runtime."""
    runtime = NatsRootTransportRuntime(
        service,
        core_bus=core_bus,
        startup_stage_mark=startup_stage_mark,
        report_control_lifecycle=report_control_lifecycle,
    )
    await runtime.run()
