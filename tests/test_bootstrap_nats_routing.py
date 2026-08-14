from __future__ import annotations

import asyncio
import base64
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from adaos.services import bootstrap as bootstrap_mod
from adaos.services.bootstrap_runtime import HubRouteProxyPolicy, NatsBridgePolicy
from adaos.services.bootstrap_runtime import hub_route_proxy as _hub_route_proxy
from adaos.services.bootstrap_runtime import nats_bridge as _nats_bridge
from adaos.services.bootstrap_runtime import status_policy as _status_policy
from adaos.services.bootstrap_runtime import transport_cleanup as _transport_cleanup
from adaos.services.bootstrap_runtime.nats_transport_runtime import _sidecar_tail_summary
from adaos.services.system_model import service as system_model_service


@pytest.fixture(autouse=True)
def _generic_public_route(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADAOS_ZONE_ID", raising=False)
    monkeypatch.delenv("ROOT_BASE_URL", raising=False)


def test_nats_url_needs_public_ws_refresh_for_legacy_public_tcp_url() -> None:
    assert _nats_bridge._nats_url_needs_public_ws_refresh("nats://nats.inimatic.com:4222") is True
    assert _nats_bridge._nats_url_needs_public_ws_refresh("nats://api.inimatic.com:4222") is True


def test_nats_bridge_policy_is_typed_bootstrap_dependency() -> None:
    policy = NatsBridgePolicy()

    assert policy.url_needs_public_ws_refresh("nats://api.inimatic.com:4222") is True
    assert policy.transport_kind("wss://api.inimatic.com/nats") == "ws"
    assert policy.canonical_identity(local_hub_id="hub-a", nats_user=None) == ("hub-a", "hub_hub-a")


def test_hub_route_proxy_policy_owns_discovery_cache() -> None:
    first = HubRouteProxyPolicy()
    second = HubRouteProxyPolicy()
    first.discovery.cache.update({"value": "http://127.0.0.1:8778", "expires_at": 10**12})

    assert first.discover_active_runtime_local_base() == "http://127.0.0.1:8778"
    assert second.discovery.snapshot()["cache"]["value"] is None


def test_nats_url_does_not_need_public_ws_refresh_for_local_or_ws_url() -> None:
    assert _nats_bridge._nats_url_needs_public_ws_refresh("nats://127.0.0.1:4222") is False
    assert _nats_bridge._nats_url_needs_public_ws_refresh("nats://localhost:4222") is False
    assert _nats_bridge._nats_url_needs_public_ws_refresh("wss://nats.inimatic.com/nats") is False


def test_loop_hang_watchdog_requires_explicit_unsafe_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_LOOP_HANG_WATCHDOG", "1")
    monkeypatch.delenv("ADAOS_LOOP_HANG_WATCHDOG_UNSAFE", raising=False)

    assert _status_policy._loop_hang_watchdog_enabled_from_env() is False

    monkeypatch.setenv("ADAOS_LOOP_HANG_WATCHDOG_UNSAFE", "1")

    assert _status_policy._loop_hang_watchdog_enabled_from_env() is True


def test_realtime_sidecar_fallback_candidates_disable_tcp_fallback_by_default(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_REALTIME_ALLOW_TCP_FALLBACK", raising=False)

    assert (
        _nats_bridge._build_realtime_sidecar_fallback_candidates(
            ["nats://nats.inimatic.com:4222", "wss://nats.inimatic.com/nats"],
            local_candidate="nats://127.0.0.1:7422",
        )
        == ["wss://nats.inimatic.com/nats"]
    )


def test_realtime_sidecar_fallback_candidates_can_keep_raw_tcp_fallback(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_REALTIME_ALLOW_TCP_FALLBACK", "1")

    assert _nats_bridge._build_realtime_sidecar_fallback_candidates(
        ["nats://nats.inimatic.com:4222", "wss://nats.inimatic.com/nats"],
        local_candidate="nats://127.0.0.1:7422",
    ) == ["nats://nats.inimatic.com:4222", "wss://nats.inimatic.com/nats"]


def test_nats_quarantine_skips_local_realtime_sidecar_candidate() -> None:
    assert (
        _nats_bridge._should_quarantine_nats_candidate(
            "nats://127.0.0.1:7422",
            local_sidecar_url="nats://127.0.0.1:7422",
        )
        is False
    )
    assert (
        _nats_bridge._should_quarantine_nats_candidate(
            "wss://ru.api.inimatic.com/nats",
            local_sidecar_url="nats://127.0.0.1:7422",
        )
        is True
    )


def test_sidecar_transient_failover_is_explicit_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HUB_NATS_SIDECAR_FAILOVER_ON_TRANSIENT", raising=False)
    assert _nats_bridge._hub_nats_sidecar_failover_on_transient() is False

    monkeypatch.setenv("HUB_NATS_SIDECAR_FAILOVER_ON_TRANSIENT", "1")
    assert _nats_bridge._hub_nats_sidecar_failover_on_transient() is True


def test_resolve_nats_log_server_prefers_current_attempt() -> None:
    assert (
        _nats_bridge._resolve_nats_log_server(
            current_attempt="nats://127.0.0.1:7422",
            connected_server="nats://nats.inimatic.com:4222",
        )
        == "nats://127.0.0.1:7422"
    )


def test_hub_nats_prefer_dedicated_defaults_to_api_domain(monkeypatch) -> None:
    monkeypatch.delenv("HUB_NATS_PREFER_DEDICATED", raising=False)

    assert _nats_bridge._hub_nats_prefer_dedicated() == "0"


def test_hub_nats_prefer_dedicated_respects_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv("HUB_NATS_PREFER_DEDICATED", "1")

    assert _nats_bridge._hub_nats_prefer_dedicated() == "1"


def test_normalize_hub_nats_ws_url_rewrites_public_dedicated_by_default(monkeypatch) -> None:
    monkeypatch.delenv("HUB_NATS_PREFER_DEDICATED", raising=False)

    assert _nats_bridge._normalize_hub_nats_ws_url("wss://nats.inimatic.com/nats") == "wss://api.inimatic.com/nats"


def test_normalize_hub_nats_ws_url_keeps_public_dedicated_on_opt_in(monkeypatch) -> None:
    monkeypatch.setenv("HUB_NATS_PREFER_DEDICATED", "1")

    assert _nats_bridge._normalize_hub_nats_ws_url("wss://nats.inimatic.com/nats") == "wss://nats.inimatic.com/nats"


def test_hub_public_ws_candidates_default_to_api_only(monkeypatch) -> None:
    monkeypatch.delenv("HUB_NATS_PREFER_DEDICATED", raising=False)

    assert _nats_bridge._hub_public_ws_candidates(None) == ["wss://api.inimatic.com/nats"]


def test_hub_public_ws_candidates_rewrite_public_dedicated_default(monkeypatch) -> None:
    monkeypatch.delenv("HUB_NATS_PREFER_DEDICATED", raising=False)

    assert _nats_bridge._hub_public_ws_candidates("wss://nats.inimatic.com/nats") == [
        "wss://api.inimatic.com/nats"
    ]


def test_hub_public_ws_candidates_can_opt_in_dedicated(monkeypatch) -> None:
    monkeypatch.setenv("HUB_NATS_PREFER_DEDICATED", "1")

    assert _nats_bridge._hub_public_ws_candidates("wss://nats.inimatic.com/nats") == [
        "wss://nats.inimatic.com/nats",
        "wss://api.inimatic.com/nats",
    ]


def test_hub_route_force_close_no_upstream_defaults_enabled(monkeypatch) -> None:
    monkeypatch.delenv("HUB_ROUTE_FORCE_CLOSE_NO_UPSTREAM_S", raising=False)

    assert _hub_route_proxy._hub_route_force_close_no_upstream_s() == 1.5


def test_hub_route_force_close_no_upstream_can_disable(monkeypatch) -> None:
    monkeypatch.setenv("HUB_ROUTE_FORCE_CLOSE_NO_UPSTREAM_S", "0")

    assert _hub_route_proxy._hub_route_force_close_no_upstream_s() == 0.0


def test_hub_route_upstream_close_payload_preserves_guard_signal() -> None:
    ws = SimpleNamespace(
        close_code=1013,
        close_reason="yws_guard_client_recovery_in_progress",
    )

    assert _hub_route_proxy._hub_route_upstream_close_payload(ws) == {
        "t": "close",
        "code": 1013,
        "reason": "yws_guard_client_recovery_in_progress",
    }


def test_hub_route_upstream_close_payload_keeps_explicit_relay_error() -> None:
    ws = SimpleNamespace(close_code=None, close_reason=None)

    assert _hub_route_proxy._hub_route_upstream_close_payload(
        ws,
        error="route_sync_backpressure",
    ) == {
        "t": "close",
        "err": "route_sync_backpressure",
    }


@pytest.mark.asyncio
async def test_hub_root_reconnect_rearms_completed_bridge_task() -> None:
    service = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="hub")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )
    started = asyncio.Event()
    stop = asyncio.Event()

    async def bridge() -> None:
        started.set()
        await stop.wait()

    old_task = asyncio.create_task(asyncio.sleep(0), name=service._hub_root_bridge_task_name)
    await old_task
    service._hub_root_bridge_factory = bridge
    service._boot_tasks.append(old_task)

    try:
        result = await service.request_hub_root_reconnect()

        assert result["ok"] is True
        assert result["close"]["attempted"] is False
        assert result["bridge"]["attempted"] is True
        assert result["bridge"]["started"] is True
        await asyncio.wait_for(started.wait(), timeout=1.0)
        live_task = service._find_live_boot_task(service._hub_root_bridge_task_name)
        assert live_task is not None
        assert live_task is not old_task
    finally:
        stop.set()
        for task in list(service._boot_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*service._boot_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_candidate_bridge_is_registered_without_connecting_until_promotion() -> None:
    service = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="hub")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )
    started = asyncio.Event()
    stop = asyncio.Event()

    async def bridge() -> None:
        started.set()
        await stop.wait()

    assert service._start_hub_root_bridge_task(bridge, start_immediately=False) is None
    assert service._find_live_boot_task(service._hub_root_bridge_task_name) is None
    assert started.is_set() is False

    try:
        result = await service.request_hub_root_reconnect()

        assert result["ok"] is True
        assert result["bridge"]["started"] is True
        await asyncio.wait_for(started.wait(), timeout=1.0)
    finally:
        stop.set()
        for task in list(service._boot_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*service._boot_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_hub_root_reconnect_rearms_running_bridge_without_active_connection() -> None:
    service = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="hub")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )
    started = asyncio.Event()
    stop = asyncio.Event()

    async def bridge() -> None:
        started.set()
        await stop.wait()

    old_task = asyncio.create_task(asyncio.sleep(3600), name=service._hub_root_bridge_task_name)
    service._hub_root_bridge_factory = bridge
    service._boot_tasks.append(old_task)

    try:
        result = await service.request_hub_root_reconnect()

        assert result["ok"] is True
        assert result["close"]["attempted"] is False
        assert result["bridge"]["attempted"] is True
        assert result["bridge"]["started"] is True
        assert result["bridge"]["state"] == "rearmed"
        assert result["bridge"]["reason"] == "manual_reconnect_without_active_nats"
        await asyncio.wait_for(started.wait(), timeout=1.0)
        live_task = service._find_live_boot_task(service._hub_root_bridge_task_name)
        assert live_task is not None
        assert live_task is not old_task
        assert old_task.cancelled() or old_task.done()
    finally:
        stop.set()
        for task in list(service._boot_tasks):
            if not task.done():
                task.cancel()
        old_task.cancel()
        await asyncio.gather(*service._boot_tasks, old_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_hub_root_bridge_watchdog_rearms_unexpectedly_completed_bridge() -> None:
    service = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="hub")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )
    started = asyncio.Event()
    stop = asyncio.Event()

    async def bridge() -> None:
        started.set()
        await stop.wait()

    completed = asyncio.create_task(asyncio.sleep(0), name=service._hub_root_bridge_task_name)
    await completed
    service._hub_root_bridge_factory = bridge
    service._boot_tasks.append(completed)

    try:
        result = await service._repair_missing_hub_root_bridge(reason="test_abnormal_close")

        assert result["attempted"] is True
        assert result["state"] == "rearmed"
        assert service._hub_root_bridge_watchdog_rearm_total == 1
        await asyncio.wait_for(started.wait(), timeout=1.0)
        assert service._find_live_boot_task(service._hub_root_bridge_task_name) is not None
    finally:
        stop.set()
        for task in list(service._boot_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*service._boot_tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_hub_root_reconnect_waits_for_active_route_authority() -> None:
    service = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="hub")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )
    stop = asyncio.Event()

    async def bridge() -> None:
        await asyncio.sleep(0)
        service._mark_hub_root_authority_ready()
        await stop.wait()

    old_task = asyncio.create_task(asyncio.sleep(0), name=service._hub_root_bridge_task_name)
    await old_task
    service._hub_root_bridge_factory = bridge
    service._boot_tasks.append(old_task)

    try:
        result = await service.request_hub_root_reconnect(wait_for_authority=True)

        assert result["ok"] is True
        assert result["authority"]["required"] is True
        assert result["authority"]["ready"] is True
        assert result["authority"]["ready_at"] is not None
    finally:
        stop.set()
        for task in list(service._boot_tasks):
            if not task.done():
                task.cancel()
        await asyncio.gather(*service._boot_tasks, return_exceptions=True)


def test_sidecar_error_tail_is_byte_bounded_for_large_diag_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    diag_path = tmp_path / "realtime_sidecar.jsonl"
    large_prefix = "x" * (1024 * 1024)
    diag_path.write_text(
        "\n".join(
            [
                f'{{"ts": 1, "line": "{large_prefix}"}}',
                '{"ts": 2, "line": "middle"}',
                '{"ts": 3, "line": "tail"}',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HUB_SIDECAR_TAIL_READ_BYTES", "4096")
    monkeypatch.setenv("HUB_SIDECAR_TAIL_MAX_LINE_CHARS", "256")

    lines = _nats_bridge._read_sidecar_tail_lines(diag_path, lines=2)

    assert lines == ['{"ts": 2, "line": "middle"}', '{"ts": 3, "line": "tail"}']


def test_sidecar_error_tail_summary_compacts_structured_diagnostics() -> None:
    summary = _sidecar_tail_summary(
        [
            '{"session_id":"rt-a","active_session":false,'
            '"remote_connect_retrying":true,"last_error":"connection reset",'
            '"large":"' + ("x" * 2_000) + '"}'
        ]
    )

    assert summary == (
        "session_id=rt-a active_session=False remote_connect_retrying=True "
        "last_error=connection reset"
    )


def test_sidecar_error_tail_summary_bounds_plain_text() -> None:
    summary = _sidecar_tail_summary(["x" * 300], max_chars=120)

    assert summary == ("x" * 120) + "..."


def test_hub_route_max_chunk_raw_accounts_for_base64_overhead(monkeypatch) -> None:
    monkeypatch.delenv("HUB_ROUTE_MAX_CHUNK_RAW_BYTES", raising=False)

    raw = _hub_route_proxy._hub_route_max_chunk_raw_bytes(256 * 1024)

    assert raw < 256 * 1024
    assert raw % (4 * 1024) == 0
    assert len(base64.b64encode(b"x" * raw)) + (16 * 1024) <= 256 * 1024


def test_hub_route_max_chunk_raw_clamps_explicit_value_to_guard(monkeypatch) -> None:
    monkeypatch.setenv("HUB_ROUTE_MAX_CHUNK_RAW_BYTES", str(300_000))

    assert _hub_route_proxy._hub_route_max_chunk_raw_bytes(256 * 1024) < 256 * 1024


def test_hub_route_max_chunk_raw_respects_smaller_explicit_value(monkeypatch) -> None:
    monkeypatch.setenv("HUB_ROUTE_MAX_CHUNK_RAW_BYTES", str(64 * 1024))

    assert _hub_route_proxy._hub_route_max_chunk_raw_bytes(256 * 1024) == 64 * 1024


def test_hub_route_normalize_resend_chunk_indexes_deduplicates_and_bounds() -> None:
    assert _hub_route_proxy._hub_route_normalize_resend_chunk_indexes(
        [3, "1", 3, -1, "bad", 6, 2],
        5,
        max_items=3,
    ) == [3, 1, 2]


def test_hub_route_normalize_resend_chunk_indexes_rejects_invalid_inputs() -> None:
    assert _hub_route_proxy._hub_route_normalize_resend_chunk_indexes("1,2", 4) == []
    assert _hub_route_proxy._hub_route_normalize_resend_chunk_indexes([0, 1], 0) == []


def test_hub_route_semantic_flow_classifies_control_and_sync_paths() -> None:
    assert _hub_route_proxy._hub_route_semantic_flow_for_path("/ws?token=secret") == "control"
    assert _hub_route_proxy._hub_route_semantic_flow_for_path("/ws/subnet") == "subnet"
    assert _hub_route_proxy._hub_route_semantic_flow_for_path("/yws/desktop") == "sync"
    assert _hub_route_proxy._hub_route_semantic_flow_for_path("/api/node/status") == "route"


def test_hub_route_sheds_sync_frames_only_when_pending_bytes_cross_sync_threshold() -> None:
    assert (
        _hub_route_proxy._hub_route_should_shed_sync_frame(
            "/yws/desktop",
            pending_data_size=96 * 1024,
            guardrail_active=False,
            frame_flush_pending_bytes=128 * 1024,
            sync_shed_pending_bytes=512 * 1024,
            payload_bytes=64 * 1024,
        )
        is False
    )
    assert (
        _hub_route_proxy._hub_route_should_shed_sync_frame(
            "/yws/desktop",
            pending_data_size=128 * 1024,
            guardrail_active=False,
            frame_flush_pending_bytes=64 * 1024,
            sync_shed_pending_bytes=512 * 1024,
            payload_bytes=64 * 1024,
        )
        is False
    )
    assert (
        _hub_route_proxy._hub_route_should_shed_sync_frame(
            "/yws/desktop",
            pending_data_size=512 * 1024,
            guardrail_active=False,
            frame_flush_pending_bytes=64 * 1024,
            sync_shed_pending_bytes=512 * 1024,
            payload_bytes=64 * 1024,
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_shed_sync_frame(
            "/ws",
            pending_data_size=96 * 1024,
            guardrail_active=True,
            frame_flush_pending_bytes=128 * 1024,
            sync_shed_pending_bytes=512 * 1024,
            payload_bytes=64 * 1024,
        )
        is False
    )
    assert (
        _hub_route_proxy._hub_route_should_shed_sync_frame(
            "/yws/desktop",
            pending_data_size=0,
            guardrail_active=False,
            frame_flush_pending_bytes=128 * 1024,
            sync_shed_pending_bytes=512 * 1024,
            payload_bytes=64 * 1024,
        )
        is False
    )


def test_hub_route_does_not_use_flush_threshold_as_sync_shed_threshold() -> None:
    assert (
        _hub_route_proxy._hub_route_should_shed_sync_frame(
            "/yws/desktop",
            pending_data_size=96 * 1024,
            guardrail_active=False,
            frame_flush_pending_bytes=64 * 1024,
            payload_bytes=221 * 1024,
        )
        is False
    )


def test_hub_route_sync_frame_force_flush_defaults_to_disabled(monkeypatch) -> None:
    monkeypatch.delenv("HUB_ROUTE_SYNC_FRAME_FORCE_FLUSH", raising=False)

    assert _hub_route_proxy._hub_route_sync_frame_force_flush_enabled() is False


def test_hub_route_sync_frame_force_flush_allows_explicit_opt_in(monkeypatch) -> None:
    for value in ("1", "true", "yes", "on"):
        monkeypatch.setenv("HUB_ROUTE_SYNC_FRAME_FORCE_FLUSH", value)

        assert _hub_route_proxy._hub_route_sync_frame_force_flush_enabled() is True


def test_hub_route_force_flushes_all_sync_chunks_when_configured() -> None:
    common = {
        "route_force_flush": True,
        "route_sync_frame_force_flush": True,
        "tunnel_flow": "sync",
        "pending_data_size": 512 * 1024,
        "frame_flush_pending_bytes": 64 * 1024,
    }

    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "chunk", "flow": "sync", "idx": 0, "total": 4},
            **common,
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "chunk", "flow": "sync", "idx": 3, "total": 4},
            **common,
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "frame", "flow": "sync", "kind": "bin"},
            **common,
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "chunk", "flow": "route", "idx": 0, "total": 4},
            **{**common, "tunnel_flow": "route"},
        )
        is True
    )


def test_hub_route_flushes_sync_chunks_when_pending_pressure_is_high() -> None:
    common = {
        "route_force_flush": True,
        "route_sync_frame_force_flush": False,
        "tunnel_flow": "sync",
        "pending_data_size": 0,
        "frame_flush_pending_bytes": 64 * 1024,
    }

    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "chunk", "flow": "sync", "idx": 3, "total": 4},
            **common,
        )
        is False
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "chunk", "flow": "sync", "idx": 0, "total": 4},
            **{**common, "pending_data_size": 128 * 1024},
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "chunk", "flow": "sync", "idx": 3, "total": 4},
            **{**common, "pending_data_size": 128 * 1024},
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "frame", "flow": "sync", "kind": "bin"},
            **common,
        )
        is False
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "frame", "flow": "sync", "kind": "bin"},
            **{**common, "pending_data_size": 128 * 1024},
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_force_flush_reply(
            {"t": "frame", "flow": "route", "kind": "bin"},
            **{**common, "tunnel_flow": "route", "pending_data_size": 128 * 1024},
        )
        is True
    )


def test_hub_route_subnet_sync_policy_drops_only_raw_yjs_under_pressure() -> None:
    assert (
        _hub_route_proxy._hub_route_subnet_sync_payload_type(
            "/ws/subnet",
            '{"t":"yjs.update","update_b64":"abc"}',
        )
        == "yjs.update"
    )
    assert (
        _hub_route_proxy._hub_route_should_drop_subnet_sync_frame(
            "/ws/subnet",
            "yjs.update",
            pending_data_size=0,
            guardrail_active=False,
            frame_flush_pending_bytes=64 * 1024,
            payload_bytes=128 * 1024,
        )
        is True
    )
    assert (
        _hub_route_proxy._hub_route_should_drop_subnet_sync_frame(
            "/ws/subnet",
            "yjs.node_state",
            pending_data_size=128 * 1024,
            guardrail_active=True,
            frame_flush_pending_bytes=64 * 1024,
            payload_bytes=128 * 1024,
        )
        is False
    )
    assert (
        _hub_route_proxy._hub_route_should_drop_subnet_sync_frame(
            "/ws",
            "yjs.update",
            pending_data_size=128 * 1024,
            guardrail_active=True,
            frame_flush_pending_bytes=64 * 1024,
            payload_bytes=128 * 1024,
        )
        is False
    )


def test_hub_id_from_nats_user_extracts_canonical_hub_id() -> None:
    assert _nats_bridge._hub_id_from_nats_user("hub_sn_92ffc943") == "sn_92ffc943"
    assert _nats_bridge._hub_id_from_nats_user("hub_9d91f466-0349-475d-9887-2d2bb3c783ee") == "9d91f466-0349-475d-9887-2d2bb3c783ee"
    assert _nats_bridge._hub_id_from_nats_user("alias_hub") is None


def test_canonical_hub_nats_identity_prefers_response_hub_id() -> None:
    hub_id, user = _nats_bridge._canonical_hub_nats_identity(
        local_hub_id="local-stale",
        nats_user="hub_remote-live",
        response_hub_id="sn_92ffc943",
    )

    assert hub_id == "sn_92ffc943"
    assert user == "hub_sn_92ffc943"


def test_canonical_hub_nats_identity_falls_back_to_canonical_nats_user() -> None:
    hub_id, user = _nats_bridge._canonical_hub_nats_identity(
        local_hub_id="local-stale",
        nats_user="hub_9d91f466-0349-475d-9887-2d2bb3c783ee",
        response_hub_id=None,
    )

    assert hub_id == "9d91f466-0349-475d-9887-2d2bb3c783ee"
    assert user == "hub_9d91f466-0349-475d-9887-2d2bb3c783ee"


def test_build_hub_route_ws_bases_ignores_remote_hub_url(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.setattr(_hub_route_proxy, "_discover_active_runtime_local_base", lambda **_: None)

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb")

    assert _hub_route_proxy._build_hub_route_ws_bases(cfg=cfg) == [
        "ws://127.0.0.1:8778",
        "ws://127.0.0.1:8777",
    ]


def test_build_hub_route_ws_bases_prefers_process_runtime_port(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_SELF_BASE_URL", "http://127.0.0.1:8779")
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8780")
    monkeypatch.setattr(_hub_route_proxy, "_discover_active_runtime_local_base", lambda **_: None)

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb")

    assert _hub_route_proxy._build_hub_route_ws_bases(cfg=cfg) == [
        "ws://127.0.0.1:8780",
        "ws://127.0.0.1:8779",
        "ws://127.0.0.1:8778",
        "ws://127.0.0.1:8777",
    ]


def test_build_hub_route_http_bases_prefers_process_runtime_port_over_stale_state(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")
    monkeypatch.setattr(
        _hub_route_proxy,
        "_active_runtime_state_local_http_bases",
        lambda ctx=None: ["http://127.0.0.1:8778"],
    )
    monkeypatch.setattr(
        _hub_route_proxy,
        "_discover_active_runtime_local_base",
        lambda **_: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb")

    assert _hub_route_proxy._build_hub_route_http_bases(
        path_norm="/api/tools/call",
        method="POST",
        cfg=cfg,
    )[:2] == [
        "http://127.0.0.1:8777",
        "http://127.0.0.1:8778",
    ]


def test_build_hub_route_http_bases_prefers_supervisor_state_over_legacy_env(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.setenv("ADAOS_BASE", "http://127.0.0.1:8777")
    monkeypatch.setenv("ADAOS_API_BASE", "http://127.0.0.1:8777")
    monkeypatch.setattr(
        _hub_route_proxy,
        "_active_runtime_state_local_http_bases",
        lambda ctx=None: ["http://127.0.0.1:8778"],
    )
    monkeypatch.setattr(
        _hub_route_proxy,
        "_discover_active_runtime_local_base",
        lambda **_: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )

    cfg = SimpleNamespace(hub_url="http://127.0.0.1:8777")

    assert _hub_route_proxy._build_hub_route_http_bases(
        path_norm="/api/node/reliability/summary",
        method="GET",
        cfg=cfg,
    )[:2] == [
        "http://127.0.0.1:8778",
        "http://127.0.0.1:8777",
    ]


def test_build_hub_route_ws_bases_prefers_supervisor_state_over_legacy_env(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.setenv("ADAOS_BASE", "http://127.0.0.1:8777")
    monkeypatch.setattr(
        _hub_route_proxy,
        "_active_runtime_state_local_http_bases",
        lambda ctx=None: ["http://127.0.0.1:8778"],
    )
    monkeypatch.setattr(_hub_route_proxy, "realtime_sidecar_route_tunnel_ws_bases", lambda **_: [])
    monkeypatch.setattr(
        _hub_route_proxy,
        "_discover_active_runtime_local_base",
        lambda **_: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )

    cfg = SimpleNamespace(hub_url="http://127.0.0.1:8777")

    assert _hub_route_proxy._build_hub_route_ws_bases(cfg=cfg, path="/ws")[:2] == [
        "ws://127.0.0.1:8778",
        "ws://127.0.0.1:8777",
    ]


def test_hub_route_local_http_timeout_allows_tools_call_to_finish() -> None:
    assert _hub_route_proxy._hub_route_local_http_timeout("/api/tools/call") == (1.5, 55.0)
    assert _hub_route_proxy._hub_route_local_http_timeout(
        "/api/tools/call",
        {"X-AdaOS-Timeout-Ms": "180000"},
    ) == (1.5, 185.0)
    assert _hub_route_proxy._hub_route_local_http_timeout(
        "/api/tools/call",
        {"x-adaos-timeout-ms": "900000"},
    ) == (1.5, 605.0)
    assert _hub_route_proxy._hub_route_local_http_timeout("/api/ping") == (0.5, 1.2)


def test_hub_route_local_http_timeout_allows_skill_file_upload_to_finish() -> None:
    assert _hub_route_proxy._hub_route_local_http_timeout("/api/skills/new_face_vision_skill/files/meta.jsonl") == (
        3.0,
        300.0,
    )


def test_hub_route_tools_call_retries_only_transport_safe_or_idempotent_failures() -> None:
    assert _hub_route_proxy._hub_route_should_retry_http_upstream_error(
        method="POST",
        path="/api/tools/call",
        error_kind="ReadTimeout",
        body=b'{"tool":"notes:save","arguments":{"content":"a"}}',
    ) is False
    assert _hub_route_proxy._hub_route_should_retry_http_upstream_error(
        method="POST",
        path="/api/tools/call",
        error_kind="ConnectionError",
    ) is True
    assert _hub_route_proxy._hub_route_should_retry_http_upstream_error(
        method="POST",
        path="/api/tools/call",
        error_kind="ReadTimeout",
        body=b'{"tool":"notes:save","idempotency_key":"idem-1","arguments":{"content":"a"}}',
    ) is True
    assert _hub_route_proxy._hub_route_should_retry_http_upstream_error(
        method="POST",
        path="/api/tools/call",
        error_kind="ReadTimeout",
        body=b'{"tool":"notes:save","arguments":{"_meta":{"request_id":"req-1"}}}',
    ) is True
    assert _hub_route_proxy._hub_route_should_retry_http_upstream_error(
        method="GET",
        path="/api/ping",
        error_kind="ConnectionError",
    ) is True


def test_hub_route_parse_resend_delays_filters_and_clamps_values() -> None:
    assert _hub_route_proxy._hub_route_parse_resend_delays("0.35, bad, -1, 1, 1.0, 30") == [
        0.35,
        1.0,
        10.0,
    ]


def test_hub_route_should_resend_http_resp_only_for_critical_control_paths() -> None:
    assert _hub_route_proxy._hub_route_should_resend_http_resp("/api/node/status") is True
    assert _hub_route_proxy._hub_route_should_resend_http_resp("/api/node/ui/diagnostics") is True
    assert (
        _hub_route_proxy._hub_route_should_resend_http_resp(
            "/api/node/yjs/webspaces/desktop/materialization?include_runtime=1"
        )
        is True
    )
    assert _hub_route_proxy._hub_route_should_resend_http_resp("/api/media/files/example.bin") is False


def test_build_hub_route_http_bases_prefers_supervisor_active_runtime(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.setattr(
        _hub_route_proxy,
        "_discover_active_runtime_local_base",
        lambda **_: "http://127.0.0.1:8777",
    )

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb")

    assert _hub_route_proxy._build_hub_route_http_bases(
        path_norm="/api/ws/test",
        method="GET",
        cfg=cfg,
    )[:2] == [
        "http://127.0.0.1:8777",
        "http://127.0.0.1:8778",
    ]


def test_build_hub_route_ws_bases_prefers_supervisor_active_runtime(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.setattr(
        _hub_route_proxy,
        "_discover_active_runtime_local_base",
        lambda **_: "http://127.0.0.1:8777",
    )

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb")

    assert _hub_route_proxy._build_hub_route_ws_bases(cfg=cfg)[:2] == [
        "ws://127.0.0.1:8777",
        "ws://127.0.0.1:8778",
    ]


def test_build_hub_route_ws_bases_prefers_sidecar_route_tunnel_for_matching_path(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.setattr(_hub_route_proxy, "_discover_active_runtime_local_base", lambda **_: None)
    monkeypatch.setattr(
        _hub_route_proxy,
        "realtime_sidecar_route_tunnel_ws_bases",
        lambda *, path=None, role=None: ["ws://127.0.0.1:7424"] if str(path or "").startswith("/yws") else [],
    )

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb", role="hub")

    assert _hub_route_proxy._build_hub_route_ws_bases(cfg=cfg, path="/yws?token=dev")[:3] == [
        "ws://127.0.0.1:7424",
        "ws://127.0.0.1:8778",
        "ws://127.0.0.1:8777",
    ]


def test_build_hub_route_ws_bases_keeps_runtime_bases_for_non_route_tunnel_paths(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.delenv("ADAOS_RUNTIME_PORT", raising=False)
    monkeypatch.setattr(_hub_route_proxy, "_discover_active_runtime_local_base", lambda **_: None)
    monkeypatch.setattr(
        _hub_route_proxy,
        "realtime_sidecar_route_tunnel_ws_bases",
        lambda *, path=None, role=None: [],
    )

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb", role="hub")

    assert _hub_route_proxy._build_hub_route_ws_bases(cfg=cfg, path="/custom/socket") == [
        "ws://127.0.0.1:8778",
        "ws://127.0.0.1:8777",
    ]


def test_build_hub_route_ws_bases_skips_discovery_when_runtime_port_available(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")
    monkeypatch.setattr(
        _hub_route_proxy,
        "_discover_active_runtime_local_base",
        lambda **_: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb")

    assert _hub_route_proxy._build_hub_route_ws_bases(cfg=cfg)[:2] == [
        "ws://127.0.0.1:8777",
        "ws://127.0.0.1:8778",
    ]


def test_build_hub_route_http_bases_skips_discovery_when_runtime_port_available(monkeypatch) -> None:
    monkeypatch.delenv("ADAOS_SELF_BASE_URL", raising=False)
    monkeypatch.setenv("ADAOS_RUNTIME_PORT", "8777")
    monkeypatch.setattr(
        _hub_route_proxy,
        "_discover_active_runtime_local_base",
        lambda **_: (_ for _ in ()).throw(AssertionError("discovery should not run")),
    )

    cfg = SimpleNamespace(hub_url="https://ru.api.inimatic.com/hubs/sn_b249afeb")

    assert _hub_route_proxy._build_hub_route_http_bases(
        path_norm="/api/ws/test",
        method="GET",
        cfg=cfg,
    )[:2] == [
        "http://127.0.0.1:8777",
        "http://127.0.0.1:8778",
    ]


def test_bootstrap_shutdown_stops_scheduler(monkeypatch) -> None:
    calls: list[str] = []

    class _Supervisor:
        async def shutdown(self) -> None:
            calls.append("supervisor")

    async def _emit(*args, **kwargs) -> None:
        calls.append(str(args[0]))

    async def _stop_scheduler() -> None:
        calls.append("scheduler")

    monkeypatch.setattr(bootstrap_mod.bus, "emit", _emit)
    monkeypatch.setattr(bootstrap_mod, "get_service_supervisor", lambda: _Supervisor())
    monkeypatch.setattr(bootstrap_mod, "stop_scheduler", _stop_scheduler)

    svc = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="member")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )

    asyncio.run(svc.shutdown())

    assert "scheduler" in calls
    assert calls[0] == "sys.stopping"
    assert calls[-1] == "sys.stopped"


def test_node_status_push_heartbeat_rejects_non_finite_values(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_NODE_STATUS_PUSH_HEARTBEAT_S", "nan")
    assert system_model_service.node_status_push_heartbeat_s() == 5.0

    monkeypatch.setenv("ADAOS_NODE_STATUS_PUSH_HEARTBEAT_S", "inf")
    assert system_model_service.node_status_push_heartbeat_s() == 5.0


def test_bootstrap_bounded_interval_rejects_non_finite_values() -> None:
    assert _status_policy._bounded_interval_seconds("nan", default=15.0, minimum=5.0) == 15.0
    assert _status_policy._bounded_interval_seconds("inf", default=15.0, minimum=5.0) == 15.0
    assert _status_policy._bounded_interval_seconds("1", default=15.0, minimum=5.0) == 5.0


@pytest.mark.asyncio
async def test_nats_cleanup_timeout_does_not_trap_reconnect_supervisor() -> None:
    cancelled = asyncio.Event()

    async def _stuck_close() -> None:
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    cleaned = await _transport_cleanup._run_bounded_async_cleanup(_stuck_close, timeout_s=0.01)

    assert cleaned is False
    await asyncio.wait_for(cancelled.wait(), timeout=0.1)


@pytest.mark.asyncio
async def test_nats_cleanup_reports_successful_close() -> None:
    closed = False

    async def _close() -> None:
        nonlocal closed
        closed = True

    cleaned = await _transport_cleanup._run_bounded_async_cleanup(_close, timeout_s=0.1)

    assert cleaned is True
    assert closed is True


@pytest.mark.asyncio
async def test_nats_cleanup_does_not_promote_child_cancellation_to_supervisor_shutdown() -> None:
    async def _self_cancelled_close() -> None:
        raise asyncio.CancelledError()

    assert await _transport_cleanup._run_bounded_async_cleanup(_self_cancelled_close, timeout_s=0.1) is False


@pytest.mark.asyncio
async def test_nats_cleanup_preserves_owner_requested_task_cancellation() -> None:
    started = asyncio.Event()

    async def _stuck_close() -> None:
        started.set()
        await asyncio.Future()

    async def _worker() -> bool:
        return await _transport_cleanup._run_bounded_async_cleanup(_stuck_close, timeout_s=10.0)

    task = asyncio.create_task(_worker())
    await asyncio.wait_for(started.wait(), timeout=1.0)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_nats_route_tunnel_cleanup_is_concurrent_and_bounded() -> None:
    cancelled: set[str] = set()

    class StuckWebSocket:
        def __init__(self, key: str) -> None:
            self.key = key

        async def close(self) -> None:
            try:
                await asyncio.Future()
            finally:
                cancelled.add(self.key)

    tunnels = {
        f"route-{index}": {"ws": StuckWebSocket(f"route-{index}")}
        for index in range(4)
    }
    started = asyncio.get_running_loop().time()

    result = await _transport_cleanup._close_route_tunnels_bounded(tunnels, timeout_s=0.02)

    elapsed = asyncio.get_running_loop().time() - started
    assert elapsed < 0.1
    assert result == {"attempted": 4, "completed": 0, "failed_or_timed_out": 4}
    assert tunnels == {}
    assert cancelled == {"route-0", "route-1", "route-2", "route-3"}


def test_should_forward_node_status_to_members_skips_member_originated_payloads() -> None:
    assert _status_policy._should_forward_node_status_to_members({}) is True
    assert (
        _status_policy._should_forward_node_status_to_members(
            {"_meta": {"subnet_origin_node_id": "member-1"}}
        )
        is False
    )


def test_should_forward_webio_control_to_members_requires_node_target() -> None:
    assert _status_policy._should_forward_webio_control_to_members(
        {"receiver": "infrastate.skills", "webspace_id": "desktop"}
    ) is False
    assert _status_policy._should_forward_webio_control_to_members(
        {
            "receiver": "infrastate.skills",
            "webspace_id": "desktop",
            "target_node_id": "member-1",
        }
    ) is True
    assert _status_policy._should_forward_webio_control_to_members(
        {
            "receiver": "infrastate.skills",
            "webspace_id": "desktop",
            "_meta": {"node_id": "member-2"},
        }
    ) is True


def test_should_emit_node_status_suppresses_duplicate_fingerprint_within_window() -> None:
    payload = {
        "node_id": "hub-1",
        "subnet_id": "sn-1",
        "role": "hub",
        "node_names": [],
        "primary_node_name": "hub",
        "ready": True,
        "node_state": "ready",
        "draining": False,
        "route_mode": "hub",
        "connected_to_hub": None,
        "trigger": "heartbeat",
    }

    should_emit, fingerprint = _status_policy._should_emit_node_status(
        payload=payload,
        now=100.0,
        last_emitted_at=0.0,
        last_fingerprint=None,
    )
    assert should_emit is True

    should_emit, fingerprint2 = _status_policy._should_emit_node_status(
        payload=dict(payload),
        now=105.0,
        last_emitted_at=100.0,
        last_fingerprint=fingerprint,
    )
    assert should_emit is False
    assert fingerprint2 == fingerprint

    should_emit, _ = _status_policy._should_emit_node_status(
        payload={**payload, "trigger": "sys.ready"},
        now=100.5,
        last_emitted_at=100.0,
        last_fingerprint=fingerprint,
    )
    assert should_emit is True


def test_should_emit_node_status_allows_explicit_short_dedupe_window() -> None:
    payload = {
        "node_id": "hub-1",
        "subnet_id": "sn-1",
        "role": "hub",
        "node_names": [],
        "primary_node_name": "hub",
        "ready": True,
        "node_state": "ready",
        "draining": False,
        "route_mode": "hub",
        "connected_to_hub": None,
        "trigger": "heartbeat",
    }
    _, fingerprint = _status_policy._should_emit_node_status(
        payload=payload,
        now=100.0,
        last_emitted_at=0.0,
        last_fingerprint=None,
    )

    should_emit, _ = _status_policy._should_emit_node_status(
        payload=dict(payload),
        now=105.0,
        last_emitted_at=100.0,
        last_fingerprint=fingerprint,
        dedupe_window_s=1.0,
    )

    assert should_emit is True


def test_node_status_dedupe_window_rejects_non_finite_values(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_NODE_STATUS_DEDUPE_WINDOW_S", "nan")
    assert _status_policy._node_status_dedupe_window_s() == 30.0

    monkeypatch.setenv("ADAOS_NODE_STATUS_DEDUPE_WINDOW_S", "inf")
    assert _status_policy._node_status_dedupe_window_s() == 30.0


def test_node_status_emit_fingerprint_reads_connected_to_subnet_alias() -> None:
    payload = {
        "node_id": "member-1",
        "subnet_id": "sn-1",
        "role": "member",
        "node_names": ["Kitchen member"],
        "primary_node_name": "Kitchen member",
        "ready": True,
        "node_state": "ready",
        "draining": False,
        "route_mode": "p2p",
        "connected_to_subnet": False,
        "trigger": "heartbeat",
    }

    fingerprint = _status_policy._node_status_emit_fingerprint(payload)

    assert fingerprint[-2] is False


def test_start_boot_task_once_reuses_existing_named_task() -> None:
    svc = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="hub")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )

    started = asyncio.Event()
    release = asyncio.Event()

    async def _runner() -> None:
        started.set()
        await release.wait()

    async def _exercise() -> None:
        t1 = svc._start_boot_task_once("adaos-node-status-push-heartbeat", _runner)
        await started.wait()
        t2 = svc._start_boot_task_once("adaos-node-status-push-heartbeat", _runner)
        assert t1 is t2
        assert len([task for task in svc._boot_tasks if not task.done()]) == 1
        release.set()
        await asyncio.gather(t1, return_exceptions=True)

    asyncio.run(_exercise())


def test_run_boot_sequence_deduplicates_concurrent_starts(monkeypatch) -> None:
    svc = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="hub")),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )

    calls = 0
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _impl(self, app) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        await release.wait()
        self._booted = True
        self._ready.set()

    monkeypatch.setattr(bootstrap_mod.BootstrapService, "_run_boot_sequence_impl", _impl)

    async def _exercise() -> None:
        t1 = asyncio.create_task(svc.run_boot_sequence(object()))
        await entered.wait()
        t2 = asyncio.create_task(svc.run_boot_sequence(object()))
        await asyncio.sleep(0)
        assert calls == 1
        release.set()
        await asyncio.gather(t1, t2)
        assert calls == 1
        assert svc._booted is True

    asyncio.run(_exercise())


def test_member_register_and_heartbeat_recovers_after_transient_register_failure(monkeypatch) -> None:
    monkeypatch.setenv("ADAOS_MEMBER_REGISTER_RETRY_INITIAL_S", "0.01")
    monkeypatch.setattr(bootstrap_mod, "load_member_hub_token", lambda: None)

    events: list[tuple[str, object | None]] = []

    async def _emit(event_type, payload=None, **_kwargs):
        events.append((str(event_type), payload))

    monkeypatch.setattr(bootstrap_mod.bus, "emit", _emit)

    class _Heartbeat:
        def __init__(self) -> None:
            self.register_calls = 0
            self.heartbeat_calls = 0
            self.heartbeat_started: asyncio.Event | None = None

        async def register(self, *_args, **_kwargs) -> bool:
            self.register_calls += 1
            return self.register_calls >= 2

        async def heartbeat(self, *_args, **_kwargs) -> bool:
            self.heartbeat_calls += 1
            if self.heartbeat_started is not None:
                self.heartbeat_started.set()
            await asyncio.sleep(3600)
            return True

    heartbeat = _Heartbeat()
    svc = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=SimpleNamespace(role="member")),
        heartbeat=heartbeat,
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )
    conf = SimpleNamespace(
        hub_url="https://ru.api.inimatic.com/hubs/sn_test",
        token="member-token",
        node_id="member-1",
        subnet_id="sn_test",
    )

    async def _exercise() -> None:
        registered = asyncio.Event()
        heartbeat.heartbeat_started = asyncio.Event()

        async def _on_registered() -> None:
            registered.set()

        task = await svc._member_register_and_heartbeat(conf, on_registered=_on_registered)
        assert task is not None
        await asyncio.wait_for(registered.wait(), timeout=1.0)
        await asyncio.wait_for(heartbeat.heartbeat_started.wait(), timeout=1.0)
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(_exercise())

    assert heartbeat.register_calls == 2
    assert heartbeat.heartbeat_calls == 1
    assert ("net.subnet.register.error", {"status": "non-200"}) in events
    assert ("net.subnet.registered", {"hub": conf.hub_url}) in events


def test_member_reconnect_reuses_boot_readiness_callback(monkeypatch) -> None:
    conf = SimpleNamespace(
        role="member",
        hub_url="https://ru.api.inimatic.com/hubs/sn_test",
        token="",
        node_id="member-1",
        subnet_id="sn_test",
    )
    svc = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=conf),
        heartbeat=SimpleNamespace(),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )
    callback_calls: list[str] = []

    async def _ready_callback() -> None:
        callback_calls.append("ready")
        svc._ready.set()

    async def _register(_conf, *, on_registered=None):
        assert on_registered is _ready_callback
        await on_registered()
        return asyncio.create_task(asyncio.sleep(3600), name="adaos-heartbeat")

    class _LinkClient:
        async def stop(self) -> None:
            return None

        async def start(self) -> None:
            return None

    link_client_module = SimpleNamespace(get_member_link_client=lambda: _LinkClient())
    monkeypatch.setitem(sys.modules, "adaos.services.subnet.link_client", link_client_module)
    monkeypatch.setattr(bootstrap_mod, "load_config", lambda ctx=None: conf)
    monkeypatch.setattr(bootstrap_mod, "load_member_hub_token", lambda: "signed-member-session")
    monkeypatch.setattr(svc, "_member_hub_transition_snapshot", lambda: {"recovery_blocked": False})
    monkeypatch.setattr(svc, "_member_register_and_heartbeat", _register)
    svc._member_ready_callback = _ready_callback

    async def _exercise() -> dict[str, object]:
        result = await svc.request_member_hub_reconnect()
        for task in list(svc._boot_tasks):
            task.cancel()
        await asyncio.gather(*svc._boot_tasks, return_exceptions=True)
        return result

    result = asyncio.run(_exercise())

    assert result["ok"] is True
    assert result["accepted"] is True
    assert callback_calls == ["ready"]
    assert svc.is_ready() is True


def test_switch_role_to_hub_runs_root_bootstrap_before_boot(monkeypatch) -> None:
    current = SimpleNamespace(role="member", hub_url="https://ru.api.inimatic.com/hubs/sn_member", subnet_id="sn_member")
    provisional = SimpleNamespace(role="hub", hub_url=None, subnet_id="sn_provisional")
    rooted = SimpleNamespace(role="hub", hub_url=None, subnet_id="sn_rooted")
    calls: list[tuple[str, object]] = []

    class _RootResult:
        subnet_id = "sn_rooted"
        reused = False
        hub_key_path = "/tmp/hub_private.pem"
        hub_cert_path = "/tmp/hub_cert.pem"
        ca_cert_path = "/tmp/ca.cert"
        workspace_path = "/tmp/sn_rooted"

    class _RootDeveloperService:
        def init(self, *, preferred_subnet_id=None):
            calls.append(("root_init", preferred_subnet_id))
            current.role = rooted.role
            current.hub_url = rooted.hub_url
            current.subnet_id = rooted.subnet_id
            return _RootResult()

    async def _shutdown(self) -> None:
        calls.append(("shutdown", current.role))

    async def _run_boot_sequence(self, app) -> None:
        calls.append(("boot", current.subnet_id))

    monkeypatch.setattr(bootstrap_mod.BootstrapService, "shutdown", _shutdown)
    monkeypatch.setattr(bootstrap_mod.BootstrapService, "run_boot_sequence", _run_boot_sequence)
    monkeypatch.setattr(bootstrap_mod, "load_config", lambda ctx=None: current)
    monkeypatch.setattr(bootstrap_mod, "generate_provisional_subnet_id", lambda: "sn_provisional")
    monkeypatch.setattr(bootstrap_mod, "cfg_set_role", lambda role, hub_url=None, subnet_id=None, ctx=None: provisional)
    monkeypatch.setitem(sys.modules, "adaos.services.root.service", SimpleNamespace(RootDeveloperService=_RootDeveloperService))

    svc = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=current),
        heartbeat=SimpleNamespace(deregister=lambda *args, **kwargs: None),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )

    result = asyncio.run(svc.switch_role(object(), "hub"))

    assert result.subnet_id == "sn_rooted"
    assert calls == [
        ("shutdown", "member"),
        ("root_init", "sn_provisional"),
        ("boot", "sn_rooted"),
    ]
    assert svc._last_role_switch_root_init["ok"] is True
    assert svc._last_role_switch_root_init["subnet_id"] == "sn_rooted"


def test_switch_role_to_hub_fails_when_root_bootstrap_fails(monkeypatch) -> None:
    current = SimpleNamespace(role="member", hub_url="https://ru.api.inimatic.com/hubs/sn_member", subnet_id="sn_member")
    provisional = SimpleNamespace(role="hub", hub_url=None, subnet_id="sn_provisional")
    boot_called = False

    class _RootDeveloperService:
        def init(self, **kwargs):
            raise RuntimeError("no certs today")

    async def _shutdown(self) -> None:
        return None

    async def _run_boot_sequence(self, app) -> None:
        nonlocal boot_called
        boot_called = True

    monkeypatch.setattr(bootstrap_mod.BootstrapService, "shutdown", _shutdown)
    monkeypatch.setattr(bootstrap_mod.BootstrapService, "run_boot_sequence", _run_boot_sequence)
    monkeypatch.setattr(bootstrap_mod, "load_config", lambda ctx=None: current)
    monkeypatch.setattr(bootstrap_mod, "generate_provisional_subnet_id", lambda: "sn_provisional")
    monkeypatch.setattr(bootstrap_mod, "cfg_set_role", lambda role, hub_url=None, subnet_id=None, ctx=None: provisional)
    monkeypatch.setitem(sys.modules, "adaos.services.root.service", SimpleNamespace(RootDeveloperService=_RootDeveloperService))

    svc = bootstrap_mod.BootstrapService(
        SimpleNamespace(config=current),
        heartbeat=SimpleNamespace(deregister=lambda *args, **kwargs: None),
        skills_loader=SimpleNamespace(),
        subnet_registry=SimpleNamespace(),
    )

    try:
        asyncio.run(svc.switch_role(object(), "hub"))
    except RuntimeError as exc:
        assert "no certs today" in str(exc)
    else:  # pragma: no cover - defensive
        raise AssertionError("switch_role should fail when Root bootstrap fails")

    assert boot_called is False
    assert svc._last_role_switch_root_init["ok"] is False


def test_runtime_candidate_mode_follows_transition_role(monkeypatch) -> None:
    monkeypatch.setattr(_nats_bridge, "runtime_transition_role", lambda: "candidate")
    assert _nats_bridge._runtime_candidate_mode() is True

    monkeypatch.setattr(_nats_bridge, "runtime_transition_role", lambda: "active")
    assert _nats_bridge._runtime_candidate_mode() is False
