from __future__ import annotations

from types import SimpleNamespace

import pytest

from adaos.services.bootstrap_runtime import nats_root_runtime, nats_transport_runtime


@pytest.mark.asyncio
async def test_root_entrypoint_only_composes_transport_owner(monkeypatch) -> None:
    calls: list[tuple[str, object]] = []

    class _Runtime:
        def __init__(self, service, **dependencies) -> None:
            calls.append(("init", (service, dependencies)))

        async def run(self) -> None:
            calls.append(("run", None))

    monkeypatch.setattr(nats_root_runtime, "NatsRootTransportRuntime", _Runtime)
    service = SimpleNamespace()

    await nats_root_runtime.start_nats_root_transport(
        service,
        core_bus="bus",
        startup_stage_mark="stage",
        report_control_lifecycle="lifecycle",
    )

    assert calls == [
        (
            "init",
            (
                service,
                {
                    "core_bus": "bus",
                    "startup_stage_mark": "stage",
                    "report_control_lifecycle": "lifecycle",
                },
            ),
        ),
        ("run", None),
    ]


@pytest.mark.asyncio
async def test_transport_owner_forwards_composed_dependencies(monkeypatch) -> None:
    calls: list[tuple[object, dict[str, object]]] = []

    async def _run(service, **dependencies) -> None:
        calls.append((service, dependencies))

    monkeypatch.setattr(nats_transport_runtime, "_run_nats_root_transport", _run)
    service = SimpleNamespace()
    runtime = nats_transport_runtime.NatsRootTransportRuntime(
        service,
        core_bus="bus",
        startup_stage_mark="stage",
        report_control_lifecycle="lifecycle",
    )

    await runtime.run()

    assert calls == [
        (
            service,
            {
                "core_bus": "bus",
                "startup_stage_mark": "stage",
                "report_control_lifecycle": "lifecycle",
            },
        )
    ]
