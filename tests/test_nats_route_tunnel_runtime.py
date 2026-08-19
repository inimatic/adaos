from __future__ import annotations

import asyncio
import base64
import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from adaos.services.bootstrap_runtime import HubRouteProxyPolicy
from adaos.services.bootstrap_runtime.route_tunnel_runtime import (
    _IsolatedMediaFileReader,
    NatsRouteTunnelRuntime,
    _MediaRelayFlowWindow,
    _run_blocking_io_cancellation_safe,
)


@pytest.mark.asyncio
async def test_isolated_media_reader_reads_and_seeks(tmp_path) -> None:
    target = tmp_path / "reader.bin"
    target.write_bytes(b"0123456789")
    reader = await _IsolatedMediaFileReader.open(target, 5.0, 5.0)
    try:
        assert reader.size == 10
        assert await reader.read(4) == b"0123"
        assert reader.seek(7) == 7
        assert await reader.read(8) == b"789"
    finally:
        await reader.close()


@pytest.mark.asyncio
async def test_isolated_media_reader_times_out_and_terminates_stalled_child(tmp_path) -> None:
    target = tmp_path / "reader.bin"
    target.write_bytes(b"content")
    worker = tmp_path / "stall_reader.py"
    worker.write_text(
        """
import os
import struct
import sys
import time

def read_exact(size):
    data = b''
    while len(data) < size:
        chunk = sys.stdin.buffer.read(size - len(data))
        if not chunk:
            raise EOFError
        data += chunk
    return data

path_size = struct.unpack('!I', read_exact(4))[0]
read_exact(path_size)
sys.stdout.buffer.write(struct.pack('!BQ', 0, 7))
sys.stdout.buffer.flush()
read_exact(12)
time.sleep(30)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    reader = await _IsolatedMediaFileReader.open(target, 5.0, 0.5, worker_path=worker)
    started = asyncio.get_running_loop().time()
    with pytest.raises(TimeoutError):
        await reader.read(4)
    assert asyncio.get_running_loop().time() - started < 2.0
    await reader.close()


@pytest.mark.asyncio
async def test_blocking_media_io_does_not_stall_event_loop() -> None:
    started = threading.Event()
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)

    def _blocking_read() -> bytes:
        started.set()
        assert release.wait(timeout=2.0)
        return b"chunk"

    try:
        read_task = asyncio.create_task(_run_blocking_io_cancellation_safe(executor, _blocking_read))
        for _ in range(50):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        assert started.is_set()
        await asyncio.wait_for(asyncio.sleep(0.01), timeout=0.1)
        assert not read_task.done()
        release.set()
        assert await read_task == b"chunk"
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_cancelled_media_io_keeps_worker_ownership_until_read_returns() -> None:
    started = threading.Event()
    release = threading.Event()
    executor = ThreadPoolExecutor(max_workers=1)

    def _blocking_read() -> bytes:
        started.set()
        assert release.wait(timeout=2.0)
        return b"chunk"

    try:
        read_task = asyncio.create_task(_run_blocking_io_cancellation_safe(executor, _blocking_read))
        for _ in range(50):
            if started.is_set():
                break
            await asyncio.sleep(0.001)
        read_task.cancel()
        await asyncio.sleep(0.01)
        assert not read_task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await read_task
    finally:
        release.set()
        executor.shutdown(wait=True, cancel_futures=True)


@pytest.mark.asyncio
async def test_media_flow_window_bounds_in_flight_chunks_and_deduplicates_acks() -> None:
    flow = _MediaRelayFlowWindow(enabled=True, window_chunks=2, ack_timeout_s=1.0)
    await flow.before_send(0)
    await flow.before_send(1)

    blocked = asyncio.create_task(flow.before_send(2))
    await asyncio.sleep(0)
    assert not blocked.done()
    assert flow.in_flight == 2

    assert flow.acknowledge(0) is True
    await asyncio.wait_for(blocked, timeout=0.1)
    assert flow.in_flight == 2
    assert flow.acknowledge(0) is False
    assert flow.in_flight == 2
    assert flow.sent_total == 3
    assert flow.acked_total == 1


@pytest.mark.asyncio
async def test_route_media_file_waits_for_root_credit_without_blocking_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path,
) -> None:
    from adaos.services import media_library
    from adaos.services.media_core import ROOT_MEDIA_RELAY_CHUNK_BYTES

    target = tmp_path / "movie.bin"
    target.write_bytes(b"x" * (int(ROOT_MEDIA_RELAY_CHUNK_BYTES) * 5))
    monkeypatch.setattr(media_library, "media_file_path", lambda _name: target)

    published: list[dict] = []
    subscriptions: dict[str, object] = {}

    class _Nats:
        _pending_data_size = 0

        async def publish(self, _subject: str, payload: bytes) -> None:
            published.append(json.loads(payload.decode("utf-8")))

        async def flush(self, *args, **kwargs) -> None:
            return None

    async def _subscribe(subject: str, *, cb: object) -> object:
        subscriptions[subject] = cb
        return SimpleNamespace(subject=subject, cb=cb)

    service = SimpleNamespace(
        _log=logging.getLogger("test.nats-route-media-flow"),
        _route_policy=HubRouteProxyPolicy(),
        ctx=SimpleNamespace(config=SimpleNamespace()),
        _mark_hub_root_authority_ready=lambda: None,
    )
    workers: list[asyncio.Task] = []
    runtime = NatsRouteTunnelRuntime(
        service,
        rate_limited_log=lambda *args, **kwargs: None,
        is_ready=lambda: True,
    )
    await runtime.install(
        nc=_Nats(),
        subscribe=_subscribe,
        sub_workers=workers,
        hub_id="sn_flow",
        candidate_passive_mode=False,
        runtime_instance="active",
        hub_nats_verbose=False,
        hub_nats_quiet=True,
    )
    callback = subscriptions["route.v2.to_hub.sn_flow.*"]
    key = "sn_flow--media--request"
    subject = f"route.v2.to_hub.sn_flow.{key}"

    async def _send(payload: dict) -> None:
        await callback(
            SimpleNamespace(
                subject=subject,
                data=json.dumps(payload).encode("utf-8"),
            )
        )

    async def _wait_for_chunks(count: int) -> None:
        for _ in range(200):
            if sum(1 for item in published if item.get("t") == "media_http_chunk") >= count:
                return
            await asyncio.sleep(0.005)
        raise AssertionError(f"timed out waiting for {count} chunks")

    try:
        await _send(
            {
                "t": "media_http_open",
                "method": "GET",
                "path": "/media/files/content/movie.bin",
                "headers": {},
                "flow_control": "media_ack_v1",
                "window_chunks": 2,
            }
        )
        await _wait_for_chunks(2)
        await asyncio.sleep(0.02)
        chunks = [item for item in published if item.get("t") == "media_http_chunk"]
        assert len(chunks) == 2
        assert len(base64.b64decode(chunks[0]["data_b64"])) == 64 * 1024
        assert len(base64.b64decode(chunks[1]["data_b64"])) == 64 * 1024

        await _send({"t": "media_http_ack", "idx": 0})
        await _wait_for_chunks(3)

        from adaos.services.reliability import hub_root_protocol_snapshot

        route_runtime = hub_root_protocol_snapshot()["route_runtime"]
        io_totals = route_runtime["media_io_total_by_operation"]
        assert io_totals["stat"] >= 1
        assert io_totals["open"] >= 1
        assert io_totals["read"] >= 3
        assert route_runtime["media_io_max_ms_by_operation"]["read"] >= 0
        assert route_runtime["media_io_oldest_active_operation"] == ""
        await _send({"t": "media_http_abort", "reason": "test_complete"})
    finally:
        await runtime.close()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_candidate_route_runtime_stays_passive_without_subscribing() -> None:
    subscriptions: list[str] = []
    service = SimpleNamespace(
        _log=logging.getLogger("test.nats-route-runtime"),
        _route_policy=HubRouteProxyPolicy(),
    )

    async def _subscribe(subject: str, *, cb: object) -> None:
        subscriptions.append(subject)

    runtime = NatsRouteTunnelRuntime(
        service,
        rate_limited_log=lambda *args, **kwargs: None,
        is_ready=lambda: True,
    )

    await runtime.install(
        nc=SimpleNamespace(),
        subscribe=_subscribe,
        sub_workers=[],
        hub_id="sn_candidate",
        candidate_passive_mode=True,
        runtime_instance="candidate",
        hub_nats_verbose=False,
        hub_nats_quiet=True,
    )

    assert subscriptions == []
    assert runtime.hub_id == "sn_candidate"
    assert not hasattr(service, "_hub_root_route_reset")


@pytest.mark.asyncio
async def test_route_runtime_reports_control_state_after_subscription_is_ready() -> None:
    subscriptions: list[str] = []
    reports: list[str] = []
    authority_ready = asyncio.Event()
    service = SimpleNamespace(
        _log=logging.getLogger("test.nats-route-runtime"),
        _route_policy=HubRouteProxyPolicy(),
        _mark_hub_root_authority_ready=authority_ready.set,
    )

    async def _subscribe(subject: str, *, cb: object) -> object:
        subscriptions.append(subject)
        return SimpleNamespace(subject=subject, cb=cb)

    async def _report(trigger: str) -> None:
        assert authority_ready.is_set()
        reports.append(trigger)

    workers: list[asyncio.Task] = []
    runtime = NatsRouteTunnelRuntime(
        service,
        rate_limited_log=lambda *args, **kwargs: None,
        is_ready=lambda: True,
        report_control_lifecycle=_report,
    )

    await runtime.install(
        nc=SimpleNamespace(),
        subscribe=_subscribe,
        sub_workers=workers,
        hub_id="sn_ready",
        candidate_passive_mode=False,
        runtime_instance="active",
        hub_nats_verbose=False,
        hub_nats_quiet=True,
    )
    await asyncio.sleep(0)

    assert subscriptions == ["route.v2.to_hub.sn_ready.*"]
    assert reports == ["route.ready"]
    await runtime.close()
    for worker in workers:
        worker.cancel()
    await asyncio.gather(*workers, return_exceptions=True)


@pytest.mark.asyncio
async def test_route_runtime_closes_owned_tunnels_and_tasks() -> None:
    closed = asyncio.Event()

    class _WebSocket:
        async def close(self) -> None:
            closed.set()

    async def _pending() -> None:
        await asyncio.Event().wait()

    service = SimpleNamespace(
        _log=logging.getLogger("test.nats-route-runtime"),
        _route_policy=HubRouteProxyPolicy(),
    )
    runtime = NatsRouteTunnelRuntime(
        service,
        rate_limited_log=lambda *args, **kwargs: None,
        is_ready=lambda: True,
    )
    runtime.tunnels["route-key"] = {"ws": _WebSocket()}
    task = asyncio.create_task(_pending())
    runtime.tunnel_tasks["route-key"] = task
    reset_callback = object()
    runtime.reset_callback = reset_callback
    service._hub_root_route_reset = reset_callback

    await runtime.close()
    await asyncio.sleep(0)

    assert closed.is_set()
    assert runtime.tunnels == {}
    assert runtime.tunnel_tasks == {}
    assert task.cancelled()
    assert service._hub_root_route_reset is None
