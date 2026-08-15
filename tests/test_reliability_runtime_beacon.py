from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import replace

from starlette.responses import JSONResponse

from adaos.services.reliability_runtime_beacon import ReliabilityRuntimeBeaconExecutor


def test_runtime_beacon_serves_explicit_stale_cache_after_callback_timeout(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RELIABILITY_RUNTIME_BEACON_TIMEOUT_S", "0.05")
    monkeypatch.setenv("ADAOS_RELIABILITY_RUNTIME_BEACON_MAX_STALE_S", "5")
    runtime = ReliabilityRuntimeBeaconExecutor(max_workers=1)
    block = threading.Event()
    release = threading.Event()
    slow = False

    def _build(*, webspace_id: str) -> JSONResponse:
        nonlocal slow
        if slow:
            block.set()
            release.wait(timeout=2.0)
        return JSONResponse({"ok": True, "webspaceId": webspace_id, "builtAt": time.time()})

    async def _run() -> None:
        nonlocal slow
        fresh = await runtime.run(_build, webspace_id="desktop")
        assert fresh.status_code == 200
        assert fresh.headers["x-adaos-runtime-stale"] == "0"

        slow = True
        started = time.perf_counter()
        stale = await runtime.run(_build, webspace_id="desktop")
        assert time.perf_counter() - started < 0.25
        assert block.is_set()
        assert stale.status_code == 200
        assert stale.headers["x-adaos-runtime-stale"] == "1"
        assert stale.headers["x-adaos-runtime-fallback"] == "timeout"
        assert b'"webspaceId":"desktop"' in stale.body
        release.set()
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    snapshot = runtime.snapshot()
    assert snapshot["submitted_total"] == 2
    assert snapshot["timeout_total"] == 1
    assert snapshot["stale_served_total"] == 1
    assert snapshot["late_completed_total"] == 1


def test_runtime_beacon_coalesces_timed_out_requests_and_reports_unavailable(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RELIABILITY_RUNTIME_BEACON_TIMEOUT_S", "0.05")
    runtime = ReliabilityRuntimeBeaconExecutor(max_workers=1)
    started = threading.Event()
    release = threading.Event()

    def _build() -> JSONResponse:
        started.set()
        release.wait(timeout=2.0)
        return JSONResponse({"ok": True})

    def _fallback(*, reason: str, timeout_s: float) -> JSONResponse:
        return JSONResponse(
            {"ok": False, "reason": reason, "timeout": timeout_s},
            status_code=503,
        )

    async def _run() -> None:
        first = asyncio.create_task(runtime.run(_build, timeout_fallback=_fallback))
        while not started.is_set():
            await asyncio.sleep(0.001)
        second = asyncio.create_task(runtime.run(_build, timeout_fallback=_fallback))
        responses = await asyncio.gather(first, second)
        assert [response.status_code for response in responses] == [503, 503]
        release.set()
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    snapshot = runtime.snapshot()
    assert snapshot["submitted_total"] == 1
    assert snapshot["coalesced_total"] == 1
    assert snapshot["timeout_total"] == 2
    assert snapshot["unavailable_total"] == 2
    assert snapshot["late_completed_total"] == 1


def test_runtime_beacon_does_not_serve_cache_past_stale_window(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_RELIABILITY_RUNTIME_BEACON_TIMEOUT_S", "0.05")
    monkeypatch.setenv("ADAOS_RELIABILITY_RUNTIME_BEACON_MAX_STALE_S", "1")
    runtime = ReliabilityRuntimeBeaconExecutor(max_workers=1)
    release = threading.Event()
    slow = False

    def _build() -> JSONResponse:
        if slow:
            release.wait(timeout=2.0)
        return JSONResponse({"ok": True})

    def _fallback(*, reason: str, timeout_s: float) -> JSONResponse:
        return JSONResponse({"ok": False, "reason": reason, "timeout": timeout_s}, status_code=503)

    async def _run() -> None:
        nonlocal slow
        assert (await runtime.run(_build)).status_code == 200
        with runtime._lock:
            key = next(iter(runtime._cache))
            runtime._cache[key] = replace(runtime._cache[key], captured_at=time.time() - 5.0)
        slow = True
        response = await runtime.run(_build, timeout_fallback=_fallback)
        assert response.status_code == 503
        release.set()
        await asyncio.sleep(0.05)

    asyncio.run(_run())
    snapshot = runtime.snapshot()
    assert snapshot["stale_served_total"] == 0
    assert snapshot["cache_expired_total"] == 1
    assert snapshot["unavailable_total"] == 1
