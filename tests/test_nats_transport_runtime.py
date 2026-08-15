from __future__ import annotations

import asyncio
from types import SimpleNamespace

import nats
import pytest

from adaos.services.bootstrap_runtime import nats_root_runtime, nats_transport_runtime


def test_protocol_roundtrip_requires_confirmation_and_bounds_retry(monkeypatch) -> None:
    monkeypatch.delenv("HUB_NATS_ROUNDTRIP_FAILURES", raising=False)
    monkeypatch.delenv("HUB_NATS_ROUNDTRIP_RETRY_S", raising=False)

    assert nats_transport_runtime._nats_roundtrip_failure_limit() == 2
    assert nats_transport_runtime._nats_roundtrip_retry_s(interval_s=30.0) == 2.0

    monkeypatch.setenv("HUB_NATS_ROUNDTRIP_FAILURES", "1")
    monkeypatch.setenv("HUB_NATS_ROUNDTRIP_RETRY_S", "90")

    assert nats_transport_runtime._nats_roundtrip_failure_limit() == 2
    assert nats_transport_runtime._nats_roundtrip_retry_s(interval_s=30.0) == 30.0


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


@pytest.mark.asyncio
async def test_protocol_roundtrip_tracks_success_and_consecutive_failures() -> None:
    class _Client:
        def __init__(self) -> None:
            self.fail = False
            self._transport = SimpleNamespace()

        async def flush(self, *, timeout: float) -> None:
            assert timeout == 0.2
            if self.fail:
                raise asyncio.TimeoutError("fault injected")

    client = _Client()
    success = await nats_transport_runtime._probe_nats_protocol_roundtrip(client, timeout_s=0.2)

    assert success["ok"] is True
    assert client._adaos_roundtrip_success_total == 1
    assert client._adaos_roundtrip_consecutive_failures == 0
    assert client._transport._adaos_last_protocol_roundtrip_at == client._adaos_last_roundtrip_ok_at

    client.fail = True
    first_failure = await nats_transport_runtime._probe_nats_protocol_roundtrip(client, timeout_s=0.2)
    second_failure = await nats_transport_runtime._probe_nats_protocol_roundtrip(client, timeout_s=0.2)

    assert first_failure["ok"] is False
    assert second_failure["ok"] is False
    assert client._adaos_roundtrip_failure_total == 2
    assert client._adaos_roundtrip_consecutive_failures == 2
    assert "fault injected" in client._adaos_last_roundtrip_error


@pytest.mark.asyncio
async def test_protocol_roundtrip_detects_injected_nats_ping_blackhole() -> None:
    respond_to_ping = True

    async def _nats_server(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        writer.write(
            b'INFO {"server_id":"fault-test","version":"2.10.0","proto":1,'
            b'"max_payload":1048576}\r\n'
        )
        await writer.drain()
        try:
            while line := await reader.readline():
                if line == b"PING\r\n" and respond_to_ping:
                    writer.write(b"PONG\r\n")
                    await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(_nats_server, "127.0.0.1", 0)
    port = int(server.sockets[0].getsockname()[1])
    client = nats.aio.client.Client()
    try:
        await client.connect(
            servers=[f"nats://127.0.0.1:{port}"],
            allow_reconnect=False,
            connect_timeout=1.0,
            ping_interval=3600,
        )
        healthy = await nats_transport_runtime._probe_nats_protocol_roundtrip(client, timeout_s=0.2)
        assert healthy["ok"] is True

        respond_to_ping = False
        failed = await nats_transport_runtime._probe_nats_protocol_roundtrip(client, timeout_s=0.1)

        assert failed["ok"] is False
        assert isinstance(failed["error"], asyncio.TimeoutError)
        assert client._adaos_roundtrip_consecutive_failures == 1
        assert client._pongs == []

        respond_to_ping = True
        recovered = await nats_transport_runtime._probe_nats_protocol_roundtrip(client, timeout_s=0.2)
        assert recovered["ok"] is True
        assert client._adaos_roundtrip_consecutive_failures == 0
        assert client._reading_task is not None and not client._reading_task.done()
    finally:
        await client.close()
        server.close()
        await server.wait_closed()


def test_sidecar_failback_requires_stable_direct_transport_and_expired_quarantine() -> None:
    args = {
        "selected_server": "wss://ru.api.inimatic.com/nats",
        "local_sidecar_url": "nats://127.0.0.1:7422",
        "connected_for_s": 180.0,
        "stable_window_s": 120.0,
        "local_ready": True,
        "quarantine_until": 90.0,
        "now_monotonic": 100.0,
    }

    assert nats_transport_runtime._sidecar_failback_due(**args) is True
    assert nats_transport_runtime._sidecar_failback_due(**{**args, "local_ready": False}) is False
    assert nats_transport_runtime._sidecar_failback_due(**{**args, "connected_for_s": 30.0}) is False
    assert nats_transport_runtime._sidecar_failback_due(**{**args, "quarantine_until": 110.0}) is False
    assert nats_transport_runtime._sidecar_failback_due(
        **{**args, "selected_server": "nats://127.0.0.1:7422"}
    ) is False
