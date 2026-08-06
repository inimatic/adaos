from __future__ import annotations

import asyncio
import base64
import hashlib
import json as _json
import logging
import os
import tempfile
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from typing import Any, List

import nats as _nats

from adaos.domain import Event
from adaos.services.bootstrap_runtime.hub_route_proxy import (
    _dev_api_serve_core_update_sync_disabled,
    _dev_without_supervisor,
    _hub_route_node_status_supervisor_runtime,
)
from adaos.services.bootstrap_runtime.status_policy import (
    _bounded_interval_seconds,
    _env_truthy,
    _hub_channel_console_allow_rl,
    _hub_channel_console_trace_enabled,
)
from adaos.services.bootstrap_runtime.transport_cleanup import (
    _close_route_tunnels_bounded,
    _current_async_task_is_cancelling,
    _run_bounded_async_cleanup,
)
from adaos.services.hub_root_outbox_store import (
    load_outbox_items,
    outbox_store_path,
    save_outbox_items,
)
from adaos.services.nats_config import (
    nats_url_uses_websocket,
    order_nats_ws_candidates,
    public_nats_ws_api,
)
from adaos.services.nats_errors import (
    install_transient_nats_log_filter,
    is_transient_nats_error,
    nats_error_summary,
)
from adaos.services.node_config import load_config
from adaos.services.node_runtime_state import (
    load_nats_runtime_config,
    migrate_legacy_nats_runtime_config,
    save_nats_runtime_config,
)
from adaos.services.realtime_sidecar import (
    probe_realtime_sidecar_ready,
    realtime_sidecar_diag_path,
    realtime_sidecar_enabled,
    realtime_sidecar_host,
    realtime_sidecar_local_url,
    realtime_sidecar_log_path,
    realtime_sidecar_port,
    resolve_realtime_remote_candidates,
)
from adaos.services.reliability import (
    ReadinessStatus,
    configure_hub_root_transport_strategy,
    hub_root_protocol_class_policy,
    hub_root_protocol_traffic_class,
    mark_root_control_down,
    mark_root_control_up,
    mark_route_degraded,
    mark_route_ready,
    note_root_control_reconnect,
    note_route_incident,
    observe_hub_root_integration_outbox,
    observe_hub_root_protocol_publish,
    observe_hub_root_protocol_subscription,
    observe_hub_root_route_flow,
    observe_hub_root_route_runtime,
    observe_route_e2e,
    record_hub_root_transport_event,
    set_integration_readiness,
)
from adaos.services.root.core_update_sync import reconcile_hub_core_update
from adaos.services.runtime_identity import runtime_connect_name, runtime_identity_snapshot
from adaos.services.subnet_alias import save_subnet_alias
from adaos.services.zone_hosts import DEFAULT_PUBLIC_ROOT_BASE_URL, zone_public_base_url


class NatsRouteTunnelRuntime:
    """Own browser/root route tunnel state for one NATS connection."""

    def __init__(self, service: Any, *, rate_limited_log: Any, is_ready: Any) -> None:
        self._service = service
        self._rate_limited_log = rate_limited_log
        self._is_ready = is_ready
        self.hub_id: str | None = None
        self.tunnels: dict[str, dict[str, Any]] = {}
        self.tunnel_tasks: dict[str, asyncio.Task] = {}
        self.reset_callback: Any = None

    async def install(
        self,
        *,
        nc: Any,
        subscribe: Any,
        sub_workers: list[asyncio.Task],
        hub_id: str,
        candidate_passive_mode: bool,
        runtime_instance: str,
        hub_nats_verbose: bool,
        hub_nats_quiet: bool,
    ) -> None:
        service = self._service
        self.hub_id = hub_id
        _rl_log = self._rate_limited_log
        is_ready = self._is_ready
        _sub = subscribe
        route_policy = service._route_policy
        _hub_route_max_chunk_raw_bytes = route_policy.max_chunk_raw_bytes
        _hub_route_normalize_resend_chunk_indexes = route_policy.normalize_resend_chunk_indexes
        _hub_route_semantic_flow_for_path = route_policy.semantic_flow_for_path
        _hub_route_should_shed_sync_frame = route_policy.should_shed_sync_frame
        _hub_route_sync_frame_force_flush_enabled = route_policy.sync_frame_force_flush_enabled
        _hub_route_should_force_flush_reply = route_policy.should_force_flush_reply
        _hub_route_subnet_sync_payload_type = route_policy.subnet_sync_payload_type
        _hub_route_should_drop_subnet_sync_frame = route_policy.should_drop_subnet_sync_frame
        _hub_route_prefers_supervisor_public_status = route_policy.prefers_supervisor_public_status
        _hub_route_local_http_timeout = route_policy.local_http_timeout
        _hub_route_should_retry_http_upstream_error = route_policy.should_retry_http_upstream_error
        _hub_route_parse_resend_delays = route_policy.parse_resend_delays
        _hub_route_should_resend_http_resp = route_policy.should_resend_http_resp
        _build_hub_route_http_bases = route_policy.build_http_bases
        _build_hub_route_ws_bases = route_policy.build_ws_bases
        _hub_route_force_close_no_upstream_s = route_policy.force_close_no_upstream_s
        # Browser<->Hub routing over NATS (root proxy fallback).
        # Root publishes `route.to_hub.<key>` where key is "<hub_id>--<conn_id|http--req_id>" (no dots).
        # Hub responds on `route.to_browser.<same-key>`.
        try:
            if candidate_passive_mode:
                raise RuntimeError("candidate runtime keeps root route relay passive until cutover")
            # Optional dependency: if `websockets` is missing, keep HTTP proxy working
            # and gracefully deny WS tunnel opens.
            websockets_mod = None
            try:
                import websockets as _websockets  # type: ignore

                websockets_mod = _websockets
            except Exception:
                websockets_mod = None

            tunnels = self.tunnels
            tunnel_tasks = self.tunnel_tasks
            media_relay_sessions: dict[str, dict[str, Any]] = {}
            http_body_relay_sessions: dict[str, dict[str, Any]] = {}
            pending_chunks: dict[str, dict[str, Any]] = {}
            outbound_chunk_cache: dict[str, dict[str, Any]] = {}
            outbound_chunk_cache_bytes = 0
            pending_tunnel_events: dict[str, list[dict[str, Any]]] = {}
            pending_tunnel_meta: dict[str, dict[str, Any]] = {}
            pending_tunnel_close_tasks: dict[str, asyncio.Task] = {}
            sync_shed_tunnel_meta: dict[str, dict[str, Any]] = {}
            # Map route key -> reply subject so we can support both legacy v1 and v2 subjects.
            # v1:  route.to_browser.<key>
            # v2:  route.v2.to_browser.<hubId>.<key>
            reply_subjects: dict[str, str] = {}
            try:
                MAX_PENDING_TUNNEL_EVENTS = max(
                    8,
                    int(os.getenv("HUB_ROUTE_PENDING_EVENTS_MAX", "128") or "128"),
                )
            except Exception:
                MAX_PENDING_TUNNEL_EVENTS = 128

            _route_verbose = os.getenv("HUB_ROUTE_VERBOSE", "0") == "1"
            _route_diag = _route_verbose or os.getenv("HUB_ROUTE_DIAG", "0") == "1"
            # Tx logs are extremely noisy (one line per request / response). Keep them separately gated.
            _route_tx_verbose = os.getenv("HUB_ROUTE_CONSOLE_TX_VERBOSE", "0") == "1"
            # Trace is an opt-in "everything we know" log for debugging WS routing breaks.
            _route_trace = os.getenv("HUB_ROUTE_TRACE", "0") == "1"
            _route_http_trace = (
                _route_trace
                or os.getenv("HUB_ROUTE_HTTP_TRACE", "0") == "1"
                or os.getenv("HUB_TRACE", "0") == "1"
            )
            # Frame logs are extremely noisy; keep them explicitly gated.
            _route_frame_verbose = (
                os.getenv("HUB_ROUTE_FRAME_VERBOSE", "0") == "1"
                or os.getenv("ROUTE_PROXY_FRAME_VERBOSE", "0") == "1"
            )
            _route_no_upstream_close_after_s = _hub_route_force_close_no_upstream_s()

            try:
                route_run_id = uuid.uuid4().hex[:6]
            except Exception:
                route_run_id = "route"
            route_sub = None
            route_sub_v2 = None
            route_reset_total = 0
            # In WS-proxied NATS setups, route replies can sit in local buffers and root times out
            # waiting for `route.to_browser.*`. Keep fast drain enabled by default.
            _route_force_flush = os.getenv("HUB_ROUTE_FORCE_FLUSH", "1") == "1"
            try:
                _route_send_timeout_s = float(os.getenv("HUB_ROUTE_SEND_TIMEOUT_S", "2.0") or "2.0")
            except Exception:
                _route_send_timeout_s = 2.0
            try:
                _route_upstream_ws_send_timeout_s = float(
                    os.getenv("HUB_ROUTE_UPSTREAM_WS_SEND_TIMEOUT_S", "2.0") or "2.0"
                )
            except Exception:
                _route_upstream_ws_send_timeout_s = 2.0
            try:
                _route_flush_timeout_s = float(os.getenv("HUB_ROUTE_FLUSH_TIMEOUT_S", "1.0") or "1.0")
            except Exception:
                _route_flush_timeout_s = 1.0
            # YWS first sync is the authoritative browser bootstrap. Drain routed sync
            # frames deterministically by default; operators can opt out only when a
            # dedicated route path has enough independent backpressure control.
            _route_sync_frame_force_flush = _hub_route_sync_frame_force_flush_enabled()
            try:
                _route_sync_frame_flush_timeout_s = float(
                    os.getenv(
                        "HUB_ROUTE_SYNC_FRAME_FLUSH_TIMEOUT_S",
                        str(max(float(_route_flush_timeout_s), 2.0)),
                    )
                    or str(max(float(_route_flush_timeout_s), 2.0))
                )
            except Exception:
                _route_sync_frame_flush_timeout_s = max(float(_route_flush_timeout_s), 2.0)
            try:
                _route_publish_slow_warn_s = float(
                    os.getenv("HUB_ROUTE_PUBLISH_SLOW_WARN_S", "0.250") or "0.250"
                )
            except Exception:
                _route_publish_slow_warn_s = 0.250
            if _route_publish_slow_warn_s < 0.01:
                _route_publish_slow_warn_s = 0.01
            try:
                _route_starvation_warn_s = float(
                    os.getenv("HUB_ROUTE_STARVATION_WARN_S", "1.000") or "1.000"
                )
            except Exception:
                _route_starvation_warn_s = 1.0
            if _route_starvation_warn_s < 0.05:
                _route_starvation_warn_s = 0.05
            try:
                _route_pending_data_warn_bytes = int(
                    os.getenv("HUB_ROUTE_PENDING_DATA_WARN_BYTES", str(2 * 1024 * 1024)) or str(2 * 1024 * 1024)
                )
            except Exception:
                _route_pending_data_warn_bytes = 2 * 1024 * 1024
            if _route_pending_data_warn_bytes < 0:
                _route_pending_data_warn_bytes = 0
            MAX_CHUNK_RAW = _hub_route_max_chunk_raw_bytes(_route_pending_data_warn_bytes)
            try:
                _route_frame_flush_pending_bytes = int(
                    os.getenv(
                        "HUB_ROUTE_FRAME_FLUSH_PENDING_BYTES",
                        str(max(64 * 1024, MAX_CHUNK_RAW)),
                    )
                    or str(max(64 * 1024, MAX_CHUNK_RAW))
                )
            except Exception:
                _route_frame_flush_pending_bytes = max(64 * 1024, MAX_CHUNK_RAW)
            if _route_pending_data_warn_bytes > 0:
                _route_frame_flush_pending_bytes = min(
                    _route_frame_flush_pending_bytes,
                    max(64 * 1024, _route_pending_data_warn_bytes // 2),
                )
            if _route_frame_flush_pending_bytes < 0:
                _route_frame_flush_pending_bytes = 0
            try:
                _route_guard_pending_data_bytes = int(
                    os.getenv(
                        "HUB_ROUTE_GUARD_PENDING_DATA_BYTES",
                        str(max(_route_pending_data_warn_bytes, 4 * 1024 * 1024)),
                    )
                    or str(max(_route_pending_data_warn_bytes, 4 * 1024 * 1024))
                )
            except Exception:
                _route_guard_pending_data_bytes = max(_route_pending_data_warn_bytes, 4 * 1024 * 1024)
            if _route_guard_pending_data_bytes < 0:
                _route_guard_pending_data_bytes = 0
            try:
                _route_guard_oldest_age_s = float(
                    os.getenv(
                        "HUB_ROUTE_GUARD_OLDEST_AGE_S",
                        str(max(_route_starvation_warn_s, 1.5)),
                    )
                    or str(max(_route_starvation_warn_s, 1.5))
                )
            except Exception:
                _route_guard_oldest_age_s = max(_route_starvation_warn_s, 1.5)
            if _route_guard_oldest_age_s < 0.05:
                _route_guard_oldest_age_s = 0.05
            try:
                _route_sync_backpressure_shed_pending_bytes = int(
                    os.getenv(
                        "HUB_ROUTE_SYNC_BACKPRESSURE_SHED_PENDING_BYTES",
                        str(max(_route_guard_pending_data_bytes, _route_pending_data_warn_bytes, 4 * 1024 * 1024)),
                    )
                    or str(max(_route_guard_pending_data_bytes, _route_pending_data_warn_bytes, 4 * 1024 * 1024))
                )
            except Exception:
                _route_sync_backpressure_shed_pending_bytes = max(
                    _route_guard_pending_data_bytes,
                    _route_pending_data_warn_bytes,
                    4 * 1024 * 1024,
                )
            if _route_sync_backpressure_shed_pending_bytes < 0:
                _route_sync_backpressure_shed_pending_bytes = 0

            # Resend critical small HTTP replies after short delays. Root accepts the first http_resp
            # and unsubscribes, so later duplicates are harmless but recover single PUB loss/stall.
            try:
                raw_delays = os.getenv("HUB_ROUTE_HTTP_RESP_RESEND_S")
                if raw_delays is None:
                    raw_delays = os.getenv("HUB_ROUTE_PROBE_RESEND_S")
                if raw_delays is None:
                    raw_delays = "0.35,1.0,2.5"
                _route_http_resp_resend_delays_s = _hub_route_parse_resend_delays(raw_delays)
            except Exception:
                _route_http_resp_resend_delays_s = []
            try:
                _route_http_resp_resend_max_bytes = int(
                    os.getenv("HUB_ROUTE_HTTP_RESP_RESEND_MAX_BYTES", str(256 * 1024)) or str(256 * 1024)
                )
            except Exception:
                _route_http_resp_resend_max_bytes = 256 * 1024
            if _route_http_resp_resend_max_bytes < 0:
                _route_http_resp_resend_max_bytes = 0
            if _route_http_resp_resend_delays_s and (_route_verbose or _route_tx_verbose):
                try:
                    _rl_log(
                        "hub-route.http_resp_resend_cfg",
                        (
                            f"[hub-route] http_resp resend delays_s={_route_http_resp_resend_delays_s} "
                            f"max_bytes={_route_http_resp_resend_max_bytes}"
                        ),
                        every_s=60.0,
                    )
                except Exception:
                    pass
            _route_outbound_chunk_cache_ttl_s = _bounded_interval_seconds(
                os.getenv("HUB_ROUTE_OUTBOUND_CHUNK_CACHE_TTL_S"),
                default=30.0,
                minimum=1.0,
            )
            try:
                _route_outbound_chunk_cache_max_frames = int(
                    os.getenv("HUB_ROUTE_OUTBOUND_CHUNK_CACHE_MAX_FRAMES", "64") or "64"
                )
            except Exception:
                _route_outbound_chunk_cache_max_frames = 64
            if _route_outbound_chunk_cache_max_frames < 0:
                _route_outbound_chunk_cache_max_frames = 0
            try:
                _route_outbound_chunk_cache_max_bytes = int(
                    os.getenv("HUB_ROUTE_OUTBOUND_CHUNK_CACHE_MAX_BYTES", str(8 * 1024 * 1024))
                    or str(8 * 1024 * 1024)
                )
            except Exception:
                _route_outbound_chunk_cache_max_bytes = 8 * 1024 * 1024
            if _route_outbound_chunk_cache_max_bytes < 0:
                _route_outbound_chunk_cache_max_bytes = 0
            try:
                _route_outbound_chunk_resend_max_indexes = int(
                    os.getenv("HUB_ROUTE_OUTBOUND_CHUNK_RESEND_MAX_INDEXES", "128") or "128"
                )
            except Exception:
                _route_outbound_chunk_resend_max_indexes = 128
            if _route_outbound_chunk_resend_max_indexes < 1:
                _route_outbound_chunk_resend_max_indexes = 1

            route_diag_state: dict[str, Any] = {
                "open_request_total": 0,
                "http_request_total": 0,
                "last_open_path": "",
                "last_open_query_has_token": False,
                "last_open_base_total": 0,
                "last_http_path": "",
                "last_http_method": "",
                "pending_oldest_age_s": 0.0,
                "pending_oldest_key_tag": "",
                "pending_starved_total": 0,
                "last_pending_key_tag": "",
                "last_nc_pending_data_size": 0,
                "reply_publish_slow_total": 0,
                "reply_flush_slow_total": 0,
                "reply_publish_fail_total": 0,
                "http_resp_resend_scheduled_total": 0,
                "http_resp_resend_sent_total": 0,
                "http_resp_resend_skipped_total": 0,
                "last_http_resp_resend_key_tag": "",
                "last_http_resp_resend_delay_s": 0.0,
                "last_http_resp_resend_payload_bytes": 0,
                "outbound_chunk_cache_frames": 0,
                "outbound_chunk_cache_bytes": 0,
                "outbound_chunk_resend_req_total": 0,
                "outbound_chunk_resend_sent_total": 0,
                "outbound_chunk_resend_miss_total": 0,
                "last_outbound_chunk_resend_key_tag": "",
                "last_outbound_chunk_resend_id": "",
                "last_outbound_chunk_resend_missing": 0,
                "last_outbound_chunk_resend_sent": 0,
                "last_publish_slow_key_tag": "",
                "last_publish_slow_ms": 0.0,
                "last_flush_slow_key_tag": "",
                "last_flush_slow_ms": 0.0,
                "guardrail_active": False,
                "guardrail_reason": "",
                "guardrail_age_s": 0.0,
                "guardrail_activation_total": 0,
                "guardrail_clear_total": 0,
                "dispatch_queue_size": 0,
                "dispatch_queue_max": 0,
                "dispatch_enqueued_total": 0,
                "dispatch_handled_total": 0,
                "dispatch_drop_total": 0,
                "dispatch_slow_total": 0,
                "last_dispatch_ms": 0.0,
                "last_dispatch_slow_ms": 0.0,
                "last_dispatch_key_tag": "",
                "sync_backpressure_shed_total": 0,
                "last_sync_backpressure_key_tag": "",
                "last_sync_backpressure_path": "",
                "last_sync_backpressure_payload_bytes": 0,
                "sync_backpressure_late_drop_total": 0,
                "last_sync_backpressure_late_drop_key_tag": "",
                "last_sync_backpressure_late_drop_path": "",
                "subnet_sync_backpressure_drop_total": 0,
                "last_subnet_sync_backpressure_key_tag": "",
                "last_subnet_sync_backpressure_path": "",
                "last_subnet_sync_backpressure_type": "",
                "last_subnet_sync_backpressure_payload_bytes": 0,
            }

            def _route_refresh_starvation_state() -> None:
                try:
                    oldest_age_s = 0.0
                    oldest_key_tag = ""
                    now = time.monotonic()
                    for key0, st0 in pending_tunnel_meta.items():
                        if not isinstance(st0, dict):
                            continue
                        first_at0 = float(st0.get("first_at") or 0.0)
                        if first_at0 <= 0:
                            continue
                        age0 = max(0.0, now - first_at0)
                        if age0 > oldest_age_s:
                            oldest_age_s = age0
                            oldest_key_tag = _key_tag(str(key0))
                    route_diag_state["pending_oldest_age_s"] = round(oldest_age_s, 3)
                    route_diag_state["pending_oldest_key_tag"] = oldest_key_tag
                except Exception:
                    pass
                try:
                    route_diag_state["last_nc_pending_data_size"] = int(
                        getattr(nc, "_pending_data_size", 0) or 0
                    )
                except Exception:
                    pass
                try:
                    _route_refresh_guardrail_state()
                except Exception:
                    pass

            def _route_refresh_guardrail_state() -> None:
                try:
                    pending_oldest_age_s = float(route_diag_state.get("pending_oldest_age_s") or 0.0)
                except Exception:
                    pending_oldest_age_s = 0.0
                try:
                    pending_data_size = int(route_diag_state.get("last_nc_pending_data_size") or 0)
                except Exception:
                    pending_data_size = 0
                active = False
                reason = ""
                if _route_guard_pending_data_bytes > 0 and pending_data_size >= _route_guard_pending_data_bytes:
                    active = True
                    reason = "pending_data"
                elif _route_guard_oldest_age_s > 0.0 and pending_oldest_age_s >= _route_guard_oldest_age_s:
                    active = True
                    reason = "pending_age"
                was_active = bool(route_diag_state.get("guardrail_active"))
                previous_reason = str(route_diag_state.get("guardrail_reason") or "")
                changed = False
                now_ts = time.time()
                if active:
                    if not was_active:
                        route_diag_state["guardrail_activation_total"] = int(
                            route_diag_state.get("guardrail_activation_total") or 0
                        ) + 1
                        route_diag_state["guardrail_since_at"] = now_ts
                        changed = True
                    elif previous_reason != reason:
                        route_diag_state["guardrail_since_at"] = now_ts
                        changed = True
                    route_diag_state["guardrail_active"] = True
                    route_diag_state["guardrail_reason"] = reason
                    since_at = float(route_diag_state.get("guardrail_since_at") or 0.0)
                    route_diag_state["guardrail_age_s"] = (
                        round(max(0.0, now_ts - since_at), 3) if since_at > 0.0 else 0.0
                    )
                else:
                    if was_active:
                        route_diag_state["guardrail_clear_total"] = int(
                            route_diag_state.get("guardrail_clear_total") or 0
                        ) + 1
                        changed = True
                    route_diag_state["guardrail_active"] = False
                    route_diag_state["guardrail_reason"] = ""
                    route_diag_state["guardrail_since_at"] = 0.0
                    route_diag_state["guardrail_age_s"] = 0.0
                if changed:
                    _rl_log(
                        "hub-route.guardrail_state",
                        (
                            f"[hub-route] guardrail active={active} reason={reason or 'healthy'} "
                            f"pending_oldest_age_s={pending_oldest_age_s:.3f} "
                            f"pending_data_size={pending_data_size} "
                            f"activations={route_diag_state.get('guardrail_activation_total')} "
                            f"clears={route_diag_state.get('guardrail_clear_total')}"
                        ),
                        every_s=0.25,
                    )

            def _route_note_starvation(
                reason: str,
                *,
                key: str | None = None,
                extra: str | None = None,
            ) -> None:
                try:
                    flow_path = ""
                    try:
                        key0 = str(key or "")
                        if key0:
                            flow0 = _route_tunnel_flow(key0)
                            path0 = _route_tunnel_path(key0)
                            if flow0 or path0:
                                flow_path = f" flow={flow0 or '-'} path={path0 or '-'}"
                    except Exception:
                        flow_path = ""
                    msg = (
                        f"[hub-route] starvation reason={reason} "
                        f"key={_key_tag(key or '')} "
                        f"pending_oldest_age_s={route_diag_state.get('pending_oldest_age_s')} "
                        f"pending_oldest_key={route_diag_state.get('pending_oldest_key_tag')} "
                        f"pending_data_size={route_diag_state.get('last_nc_pending_data_size')}"
                        f"{flow_path}"
                    )
                    if extra:
                        msg += f" {extra}"
                    _rl_log(f"hub-route.starvation.{reason}", msg, every_s=1.0)
                except Exception:
                    pass

            def _update_route_protocol_runtime(**details: Any) -> None:
                try:
                    _route_refresh_starvation_state()
                    pending_events = 0
                    for items0 in pending_tunnel_events.values():
                        try:
                            pending_events += len(items0 or [])
                        except Exception:
                            continue
                    active_reader_tasks = 0
                    for task0 in tunnel_tasks.values():
                        try:
                            if task0 and not task0.done():
                                active_reader_tasks += 1
                        except Exception:
                            continue
                    observe_hub_root_route_runtime(
                        active_tunnels=len(tunnels),
                        active_reader_tasks=active_reader_tasks,
                        pending_tunnels=len(pending_tunnel_events),
                        pending_events=pending_events,
                        pending_chunks=len(pending_chunks),
                        max_pending_events=MAX_PENDING_TUNNEL_EVENTS,
                        no_upstream_close_after_s=_route_no_upstream_close_after_s,
                        legacy_v1_enabled=bool(route_sub is not None),
                        v2_enabled=bool(route_sub_v2 is not None),
                        **route_diag_state,
                        **details,
                    )
                except Exception:
                    pass

            def _route_log(msg: str) -> None:
                if not _hub_channel_console_trace_enabled():
                    return
                try:
                    print(f"[hub-route:{route_run_id}] {msg}")
                except Exception:
                    pass

            def _key_tag(key: str) -> str:
                try:
                    if not isinstance(key, str):
                        return "?"
                    return key[-8:] if len(key) > 12 else key
                except Exception:
                    return "?"

            def _route_lifecycle_log(
                phase: str,
                key: str,
                *,
                subject: str | None = None,
                payload: dict[str, Any] | None = None,
                extra: str | None = None,
            ) -> None:
                try:
                    p0 = payload or {}
                    t0 = str(p0.get("t") or "")
                    should_log = False
                    if t0 in ("http", "http_resp", "open", "open_ack", "close"):
                        should_log = bool(_route_http_trace or _route_trace)
                    elif t0 in ("frame", "chunk"):
                        should_log = bool(_route_frame_verbose)
                    elif _route_trace:
                        should_log = True
                    if not should_log:
                        return
                    subj0 = str(subject or "").strip()
                    msg = (
                        f"[hub-route] lifecycle phase={phase} key={_key_tag(key)} "
                        f"t={t0 or '?'}"
                    )
                    if subj0:
                        msg += f" subj={subj0}"
                    summary = _route_payload_summary(p0)
                    if summary:
                        msg += f" {summary}"
                    if extra:
                        msg += f" {extra}"
                    _route_log(msg)
                except Exception:
                    pass

            def _route_payload_summary(payload: dict[str, Any] | None) -> str:
                try:
                    p0 = payload or {}
                    t0 = str(p0.get("t") or "")
                    if t0 == "http":
                        m0 = str(p0.get("method") or "GET").upper()
                        pth0 = str(p0.get("path") or "")
                        return f"t=http method={m0} path={pth0}"
                    if t0 == "http_resp":
                        status0 = p0.get("status")
                        err0 = p0.get("err")
                        truncated0 = p0.get("truncated")
                        body0 = p0.get("body_b64")
                        body_len0 = len(body0) if isinstance(body0, str) else None
                        return f"t=http_resp status={status0} truncated={truncated0} body_b64_len={body_len0} err={err0}"
                    if t0 == "open":
                        pth0 = str(p0.get("path") or "")
                        q0 = str(p0.get("query") or "")
                        return (
                            f"t=open path={pth0} query_len={len(q0)} "
                            f"token={_query_has_token(q0)} dev={_query_param(q0, 'dev')} ws={_query_param(q0, 'ws')}"
                        )
                    if t0 in ("frame", "chunk"):
                        kind0 = p0.get("kind")
                        size0 = None
                        data0 = p0.get("data") or p0.get("data_b64")
                        try:
                            size0 = len(data0) if data0 is not None else None
                        except Exception:
                            size0 = None
                        if t0 == "chunk":
                            return (
                                f"t=chunk kind={kind0} idx={p0.get('idx')} total={p0.get('total')} size={size0}"
                            )
                        return f"t=frame kind={kind0} size={size0}"
                    if t0 == "close":
                        return f"t=close err={p0.get('err')}"
                    return f"t={t0}"
                except Exception:
                    return "t=?"

            route_key_prefixes: set[str] = set()
            try:
                if hub_id:
                    route_key_prefixes.add(f"{hub_id}--")
                cfg0 = getattr(service.ctx, "config", None)
                cfg_hub_id = str(getattr(cfg0, "subnet_id", "") or "").strip() if cfg0 is not None else ""
                if cfg_hub_id:
                    route_key_prefixes.add(f"{cfg_hub_id}--")
                extra_prefixes = str(os.getenv("HUB_ROUTE_KEY_PREFIXES", "") or "")
                for item in extra_prefixes.split(","):
                    item = item.strip()
                    if not item:
                        continue
                    route_key_prefixes.add(item if item.endswith("--") else f"{item}--")
            except Exception:
                route_key_prefixes = {f"{hub_id}--"} if hub_id else set()
            try:
                route_diag_state["accepted_key_prefixes"] = sorted(route_key_prefixes)
            except Exception:
                pass

            def _query_has_token(query: str) -> bool:
                if not isinstance(query, str) or not query:
                    return False
                try:
                    from urllib.parse import parse_qs

                    raw = query[1:] if query.startswith("?") else query
                    q = parse_qs(raw, keep_blank_values=True)
                    return "token" in q
                except Exception:
                    return "token=" in query

            def _query_param(query: str, key: str) -> str | None:
                if not isinstance(query, str) or not query or not key:
                    return None
                try:
                    from urllib.parse import parse_qs

                    raw = query[1:] if query.startswith("?") else query
                    q = parse_qs(raw, keep_blank_values=True)
                    vals = q.get(key)
                    if isinstance(vals, list) and vals:
                        v0 = str(vals[0]).strip()
                        return v0 or None
                    if isinstance(vals, str):
                        v0 = str(vals).strip()
                        return v0 or None
                    return None
                except Exception:
                    return None

            def _route_payload_bytes(payload: dict[str, Any] | None) -> int | None:
                try:
                    p0 = payload or {}
                    t0 = str(p0.get("t") or "")
                    kind0 = str(p0.get("kind") or "")
                    if t0 in ("frame", "chunk"):
                        if kind0 == "bin":
                            b64 = p0.get("data_b64")
                            if isinstance(b64, str) and b64:
                                return len(base64.b64decode(b64.encode("ascii")))
                        data0 = p0.get("data")
                        if isinstance(data0, str):
                            return len(data0.encode("utf-8"))
                        return None
                    return len(_json.dumps(p0, ensure_ascii=False).encode("utf-8"))
                except Exception:
                    return None

            def _route_observe_flow(
                flow: str,
                event: str,
                *,
                direction: str | None = None,
                payload: dict[str, Any] | None = None,
                payload_bytes: int | None = None,
                error: str | None = None,
                pending: bool = False,
            ) -> None:
                try:
                    size = payload_bytes if payload_bytes is not None else _route_payload_bytes(payload)
                    observe_hub_root_route_flow(
                        flow,
                        event,
                        direction=direction,
                        payload_bytes=size,
                        error=error,
                        pending=pending,
                    )
                except Exception:
                    pass

            def _drop_pending_chunks_for_key(key: str) -> None:
                try:
                    for pid in [pid for pid, st in list(pending_chunks.items()) if st.get("key") == key]:
                        pending_chunks.pop(pid, None)
                except Exception:
                    pass

            def _sync_outbound_chunk_cache_diag() -> None:
                try:
                    route_diag_state["outbound_chunk_cache_frames"] = len(outbound_chunk_cache)
                    route_diag_state["outbound_chunk_cache_bytes"] = int(outbound_chunk_cache_bytes)
                except Exception:
                    pass

            def _trim_outbound_chunk_cache(now: float | None = None) -> None:
                nonlocal outbound_chunk_cache_bytes
                try:
                    now0 = time.monotonic() if now is None else float(now)
                except Exception:
                    now0 = time.monotonic()
                ttl = max(1.0, float(_route_outbound_chunk_cache_ttl_s))
                for cid, st in list(outbound_chunk_cache.items()):
                    try:
                        created = float(st.get("created_at") or 0.0)
                    except Exception:
                        created = 0.0
                    if created <= 0.0 or now0 - created > ttl:
                        outbound_chunk_cache.pop(cid, None)
                        try:
                            outbound_chunk_cache_bytes = max(
                                0,
                                outbound_chunk_cache_bytes - int(st.get("bytes") or 0),
                            )
                        except Exception:
                            pass
                try:
                    max_frames = int(_route_outbound_chunk_cache_max_frames or 0)
                except Exception:
                    max_frames = 0
                try:
                    max_bytes = int(_route_outbound_chunk_cache_max_bytes or 0)
                except Exception:
                    max_bytes = 0
                if max_frames <= 0 or max_bytes <= 0:
                    for cid, st in list(outbound_chunk_cache.items()):
                        outbound_chunk_cache.pop(cid, None)
                        try:
                            outbound_chunk_cache_bytes = max(
                                0,
                                outbound_chunk_cache_bytes - int(st.get("bytes") or 0),
                            )
                        except Exception:
                            pass
                    _sync_outbound_chunk_cache_diag()
                    return
                while len(outbound_chunk_cache) > max_frames or outbound_chunk_cache_bytes > max_bytes:
                    oldest_id = ""
                    oldest_at = float("inf")
                    for cid, st in outbound_chunk_cache.items():
                        try:
                            created = float(st.get("created_at") or 0.0)
                        except Exception:
                            created = 0.0
                        if created < oldest_at:
                            oldest_id = cid
                            oldest_at = created
                    if not oldest_id:
                        break
                    st = outbound_chunk_cache.pop(oldest_id, None) or {}
                    try:
                        outbound_chunk_cache_bytes = max(
                            0,
                            outbound_chunk_cache_bytes - int(st.get("bytes") or 0),
                        )
                    except Exception:
                        pass
                _sync_outbound_chunk_cache_diag()

            def _drop_outbound_chunk_cache_for_key(key: str) -> None:
                nonlocal outbound_chunk_cache_bytes
                try:
                    for cid, st in list(outbound_chunk_cache.items()):
                        if st.get("key") != key:
                            continue
                        outbound_chunk_cache.pop(cid, None)
                        try:
                            outbound_chunk_cache_bytes = max(
                                0,
                                outbound_chunk_cache_bytes - int(st.get("bytes") or 0),
                            )
                        except Exception:
                            pass
                except Exception:
                    pass
                _sync_outbound_chunk_cache_diag()

            def _cache_outbound_chunk_payloads(
                key: str,
                cid: str,
                frame_kind: str,
                total: int,
                payloads: list[dict[str, Any]],
            ) -> None:
                nonlocal outbound_chunk_cache_bytes
                try:
                    if not cid or not key or total <= 0 or not payloads:
                        return
                    if _route_outbound_chunk_cache_max_frames <= 0 or _route_outbound_chunk_cache_max_bytes <= 0:
                        return
                    payload_bytes = 0
                    for payload in payloads:
                        try:
                            payload_bytes += len(_json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                        except Exception:
                            payload_bytes += 0
                    if payload_bytes <= 0 or payload_bytes > int(_route_outbound_chunk_cache_max_bytes):
                        return
                    _trim_outbound_chunk_cache()
                    old = outbound_chunk_cache.pop(cid, None)
                    if old:
                        try:
                            outbound_chunk_cache_bytes = max(
                                0,
                                outbound_chunk_cache_bytes - int(old.get("bytes") or 0),
                            )
                        except Exception:
                            pass
                    outbound_chunk_cache[cid] = {
                        "key": key,
                        "kind": frame_kind,
                        "total": int(total),
                        "chunks": list(payloads),
                        "created_at": time.monotonic(),
                        "bytes": int(payload_bytes),
                    }
                    outbound_chunk_cache_bytes += int(payload_bytes)
                    _trim_outbound_chunk_cache()
                except Exception:
                    pass
                _sync_outbound_chunk_cache_diag()

            async def _resend_outbound_chunks(key: str, payload: dict[str, Any]) -> None:
                nonlocal outbound_chunk_cache_bytes
                try:
                    route_diag_state["outbound_chunk_resend_req_total"] = int(
                        route_diag_state.get("outbound_chunk_resend_req_total") or 0
                    ) + 1
                    cid = str((payload or {}).get("id") or "")
                    total = int((payload or {}).get("total") or 0)
                    missing = _hub_route_normalize_resend_chunk_indexes(
                        (payload or {}).get("missing"),
                        total,
                        max_items=_route_outbound_chunk_resend_max_indexes,
                    )
                    route_diag_state["last_outbound_chunk_resend_key_tag"] = _key_tag(key)
                    route_diag_state["last_outbound_chunk_resend_id"] = cid
                    route_diag_state["last_outbound_chunk_resend_missing"] = len(missing)
                    route_diag_state["last_outbound_chunk_resend_sent"] = 0
                    if not cid or not missing:
                        return
                    _trim_outbound_chunk_cache()
                    st = outbound_chunk_cache.get(cid)
                    if not isinstance(st, dict) or st.get("key") != key or int(st.get("total") or 0) != total:
                        route_diag_state["outbound_chunk_resend_miss_total"] = int(
                            route_diag_state.get("outbound_chunk_resend_miss_total") or 0
                        ) + 1
                        return
                    chunks = st.get("chunks")
                    if not isinstance(chunks, list):
                        route_diag_state["outbound_chunk_resend_miss_total"] = int(
                            route_diag_state.get("outbound_chunk_resend_miss_total") or 0
                        ) + 1
                        return
                    sent = 0
                    for idx in missing:
                        if idx < 0 or idx >= len(chunks):
                            continue
                        chunk_payload = chunks[idx]
                        if not isinstance(chunk_payload, dict):
                            continue
                        await _route_reply(key, chunk_payload)
                        sent += 1
                    route_diag_state["outbound_chunk_resend_sent_total"] = int(
                        route_diag_state.get("outbound_chunk_resend_sent_total") or 0
                    ) + sent
                    route_diag_state["last_outbound_chunk_resend_sent"] = sent
                    if sent <= 0:
                        route_diag_state["outbound_chunk_resend_miss_total"] = int(
                            route_diag_state.get("outbound_chunk_resend_miss_total") or 0
                        ) + 1
                    try:
                        _update_route_protocol_runtime()
                    except Exception:
                        pass
                    if (_route_verbose or _route_trace) and sent > 0:
                        try:
                            _route_log(
                                f"[hub-route] resend chunks key={_key_tag(key)} id={cid} "
                                f"missing={len(missing)} sent={sent}"
                            )
                        except Exception:
                            pass
                except Exception as e:
                    try:
                        route_diag_state["outbound_chunk_resend_miss_total"] = int(
                            route_diag_state.get("outbound_chunk_resend_miss_total") or 0
                        ) + 1
                    except Exception:
                        pass
                    if _route_verbose or _route_trace:
                        try:
                            _route_log(
                                f"[hub-route] resend chunks failed key={_key_tag(key)}: {type(e).__name__}: {e}"
                            )
                        except Exception:
                            pass
                finally:
                    _sync_outbound_chunk_cache_diag()

            def _mark_pending(key: str) -> None:
                try:
                    st = pending_tunnel_meta.get(key)
                    now = time.monotonic()
                    if st is None:
                        pending_tunnel_meta[key] = {"first_at": now, "last_at": now, "count": 1}
                    else:
                        st["last_at"] = now
                        st["count"] = int(st.get("count") or 0) + 1
                    task = pending_tunnel_close_tasks.get(key)
                    if (
                        _route_no_upstream_close_after_s > 0
                        and (task is None or task.done())
                    ):
                        pending_tunnel_close_tasks[key] = asyncio.create_task(
                            _pending_tunnel_force_close_task(key),
                            name=f"hub-route-pending-close-{_key_tag(key)}",
                        )
                    _route_refresh_starvation_state()
                    age_now = float(route_diag_state.get("pending_oldest_age_s") or 0.0)
                    route_diag_state["last_pending_key_tag"] = _key_tag(key)
                    if age_now >= _route_starvation_warn_s:
                        route_diag_state["pending_starved_total"] = int(
                            route_diag_state.get("pending_starved_total") or 0
                        ) + 1
                        _route_note_starvation(
                            "pending_age",
                            key=key,
                            extra=f"age_s={age_now:.3f} threshold_s={_route_starvation_warn_s:.3f}",
                        )
                except Exception:
                    pass

            def _cancel_pending_tunnel_close(key: str) -> None:
                try:
                    task = pending_tunnel_close_tasks.get(key)
                    if not task:
                        return
                    if task is asyncio.current_task():
                        pending_tunnel_close_tasks.pop(key, None)
                        return
                    pending_tunnel_close_tasks.pop(key, None)
                    task.cancel()
                except Exception:
                    pass

            def _clear_pending_tunnel_state(key: str, *, drop_events: bool) -> None:
                try:
                    _cancel_pending_tunnel_close(key)
                except Exception:
                    pass
                try:
                    pending_tunnel_meta.pop(key, None)
                except Exception:
                    pass
                try:
                    reply_subjects.pop(key, None)
                except Exception:
                    pass
                if drop_events:
                    try:
                        pending_tunnel_events.pop(key, None)
                    except Exception:
                        pass
                try:
                    _update_route_protocol_runtime()
                except Exception:
                    pass

            async def _reset_route_runtime(*, reason: str, notify_browser: bool) -> dict[str, Any]:
                nonlocal route_reset_total
                reason0 = str(reason or "").strip() or "route_reset"
                notify0 = bool(notify_browser)
                closed_tunnels = 0
                closed_tunnels_completed = 0
                dropped_pending = 0
                notified_browser = 0
                keys: list[str] = []
                notify_tasks: list[asyncio.Task[Any]] = []
                close_tasks: list[asyncio.Task[Any]] = []

                def _consume_reset_task(task: asyncio.Task[Any]) -> None:
                    try:
                        task.result()
                    except BaseException:
                        pass

                async def _notify_route_close(key0: str) -> None:
                    await _route_reply(key0, {"t": "close", "err": reason0})

                async def _close_route_ws(ws0: Any) -> None:
                    close0 = getattr(ws0, "close", None)
                    if callable(close0):
                        result0 = close0()
                        if asyncio.iscoroutine(result0):
                            await result0

                try:
                    keys = list(
                        dict.fromkeys(
                            [
                                *[str(k) for k in tunnels.keys()],
                                *[str(k) for k in pending_tunnel_events.keys()],
                                *[str(k) for k in reply_subjects.keys()],
                                *[str(k) for k in media_relay_sessions.keys()],
                                *[str(k) for k in http_body_relay_sessions.keys()],
                                *[
                                    str(st.get("key"))
                                    for st in pending_chunks.values()
                                    if isinstance(st, dict) and st.get("key")
                                ],
                                *[
                                    str(st.get("key"))
                                    for st in outbound_chunk_cache.values()
                                    if isinstance(st, dict) and st.get("key")
                                ],
                            ]
                        )
                    )
                except Exception:
                    keys = []
                for key in keys:
                    rec = tunnels.pop(key, None)
                    ws = rec.get("ws") if isinstance(rec, dict) else None
                    task = tunnel_tasks.pop(key, None)
                    try:
                        if task:
                            task.cancel()
                    except Exception:
                        pass
                    try:
                        dropped_pending += len(pending_tunnel_events.get(key) or [])
                    except Exception:
                        pass
                    if notify0 and str(reply_subjects.get(key) or "").strip():
                        try:
                            task0 = asyncio.create_task(
                                _notify_route_close(key),
                                name=f"hub-route-reset-notify-{_key_tag(key)}",
                            )
                            task0.add_done_callback(_consume_reset_task)
                            notify_tasks.append(task0)
                        except Exception:
                            pass
                    try:
                        _drop_pending_chunks_for_key(key)
                    except Exception:
                        pass
                    try:
                        _drop_outbound_chunk_cache_for_key(key)
                    except Exception:
                        pass
                    try:
                        _cleanup_media_relay_session(key, remove_temp=True)
                    except Exception:
                        pass
                    try:
                        _cleanup_http_body_relay_session(key, remove_temp=True)
                    except Exception:
                        pass
                    try:
                        _clear_pending_tunnel_state(key, drop_events=True)
                    except Exception:
                        pass
                    if ws:
                        try:
                            task1 = asyncio.create_task(
                                _close_route_ws(ws),
                                name=f"hub-route-reset-close-{_key_tag(key)}",
                            )
                            task1.add_done_callback(_consume_reset_task)
                            close_tasks.append(task1)
                            closed_tunnels += 1
                        except Exception:
                            pass
                if notify_tasks:
                    try:
                        done0, _pending0 = await asyncio.wait(set(notify_tasks), timeout=0.25)
                        for task0 in done0:
                            try:
                                if not task0.cancelled() and task0.exception() is None:
                                    notified_browser += 1
                            except BaseException:
                                pass
                    except Exception:
                        pass
                if close_tasks:
                    try:
                        done1, _pending1 = await asyncio.wait(set(close_tasks), timeout=0.25)
                        for task1 in done1:
                            try:
                                if not task1.cancelled() and task1.exception() is None:
                                    closed_tunnels_completed += 1
                            except BaseException:
                                pass
                    except Exception:
                        pass
                try:
                    sync_shed_tunnel_meta.clear()
                except Exception:
                    pass
                route_reset_total += 1
                try:
                    _route_observe_flow("control", "runtime_reset", error=reason0)
                except Exception:
                    pass
                try:
                    _update_route_protocol_runtime(
                        last_reset_at=time.time(),
                        last_reset_reason=reason0,
                        last_reset_closed_tunnels=closed_tunnels,
                        last_reset_dropped_pending=dropped_pending,
                        last_reset_notified_browser=notified_browser,
                        reset_total=route_reset_total,
                    )
                except Exception:
                    pass
                try:
                    note_route_incident(
                        status="runtime_reset",
                        summary="hub route relay runtime reset",
                        details={
                            "reason": reason0,
                            "closed_tunnels": closed_tunnels,
                            "closed_tunnels_completed": closed_tunnels_completed,
                            "dropped_pending": dropped_pending,
                            "notified_browser": notified_browser,
                        },
                    )
                except Exception:
                    pass
                if _route_trace or _route_verbose:
                    try:
                        _route_log(
                            f"[hub-route] runtime reset reason={reason0} closed={closed_tunnels} "
                            f"pending={dropped_pending} notified={notified_browser}"
                        )
                    except Exception:
                        pass
                return {
                    "ok": True,
                    "reason": reason0,
                    "notify_browser": notify0,
                    "closed_tunnels": closed_tunnels,
                    "closed_tunnels_completed": closed_tunnels_completed,
                    "dropped_pending": dropped_pending,
                    "notified_browser": notified_browser,
                    "reset_total": route_reset_total,
                }

            async def _maybe_force_close_no_upstream(key: str) -> None:
                if _route_no_upstream_close_after_s <= 0:
                    return
                try:
                    st = pending_tunnel_meta.get(key)
                    if not st:
                        return
                    first_at = float(st.get("first_at") or 0.0)
                    if first_at <= 0:
                        return
                    age = time.monotonic() - first_at
                    if age < _route_no_upstream_close_after_s:
                        return
                    rec = tunnels.get(key)
                    ws = rec.get("ws") if isinstance(rec, dict) else None
                    if ws:
                        _clear_pending_tunnel_state(key, drop_events=False)
                        return
                    # Ask root to close this tunnel so it re-opens with an "open" handshake.
                    try:
                        await _route_reply(key, {"t": "close", "err": "no_upstream"})
                    finally:
                        _clear_pending_tunnel_state(key, drop_events=True)
                    try:
                        note_route_incident(
                            status="forced_close_no_upstream",
                            summary="hub route forced close due to missing upstream",
                            details={
                                "key_tag": _key_tag(key),
                                "age_s": round(float(age), 3),
                            },
                        )
                    except Exception:
                        pass
                    _route_observe_flow(
                        "control",
                        "forced_close_no_upstream",
                        error="no_upstream",
                    )
                    try:
                        _update_route_protocol_runtime(last_force_close_at=time.time())
                    except Exception:
                        pass
                    if _route_trace:
                        _route_log(
                            f"[hub-route] forced close key={_key_tag(key)} age_s={age:.2f} reason=no_upstream"
                        )
                except Exception:
                    pass

            async def _pending_tunnel_force_close_task(key: str) -> None:
                try:
                    await asyncio.sleep(_route_no_upstream_close_after_s)
                    await _maybe_force_close_no_upstream(key)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    pass
                finally:
                    try:
                        task = pending_tunnel_close_tasks.get(key)
                        if task is asyncio.current_task():
                            pending_tunnel_close_tasks.pop(key, None)
                    except Exception:
                        pass

            def _route_nc_diag() -> str:
                try:
                    tr = getattr(nc, "_transport", None)
                    ws = getattr(tr, "_ws", None) if tr is not None else None
                    ws_closed = getattr(ws, "closed", None) if ws is not None else None
                    ws_close_code = getattr(ws, "close_code", None) if ws is not None else None
                    ws_close_reason = getattr(ws, "close_reason", None) if ws is not None else None
                    ws_exc = None
                    try:
                        exf = getattr(ws, "exception", None)
                        if callable(exf):
                            ws_exc = exf()
                    except Exception:
                        ws_exc = None
                    ws_proto = None
                    try:
                        ws_proto = getattr(ws, "protocol", None) if ws is not None else None
                    except Exception:
                        ws_proto = None
                    try:
                        if not ws_proto and ws is not None and getattr(ws, "_response", None) is not None:
                            ws_proto = ws._response.headers.get("Sec-WebSocket-Protocol")  # type: ignore[attr-defined]
                    except Exception:
                        ws_proto = ws_proto or None

                    last_rx_ago_s = None
                    last_tx_ago_s = None
                    try:
                        last_rx_at = getattr(tr, "_adaos_last_rx_at", None) if tr is not None else None
                        last_tx_at = getattr(tr, "_adaos_last_tx_at", None) if tr is not None else None
                        if isinstance(last_rx_at, (int, float)):
                            last_rx_ago_s = round(time.monotonic() - float(last_rx_at), 3)
                        if isinstance(last_tx_at, (int, float)):
                            last_tx_ago_s = round(time.monotonic() - float(last_tx_at), 3)
                    except Exception:
                        last_rx_ago_s = last_rx_ago_s or None
                        last_tx_ago_s = last_tx_ago_s or None
                    last_recv_err = None
                    last_recv_err_ago_s = None
                    try:
                        last_recv_err = getattr(tr, "_adaos_last_recv_error", None) if tr is not None else None
                        last_recv_err_at = getattr(tr, "_adaos_last_recv_error_at", None) if tr is not None else None
                        if isinstance(last_recv_err_at, (int, float)):
                            last_recv_err_ago_s = round(time.monotonic() - float(last_recv_err_at), 3)
                    except Exception:
                        last_recv_err = last_recv_err or None
                        last_recv_err_ago_s = last_recv_err_ago_s or None

                    pending_data_size = getattr(nc, "_pending_data_size", None)
                    pings_outstanding = getattr(nc, "_pings_outstanding", None)
                    pongs_q = None
                    try:
                        pongs = getattr(nc, "_pongs", None)
                        if isinstance(pongs, list):
                            pongs_q = len(pongs)
                    except Exception:
                        pongs_q = None
                    return (
                        f"ws_closed={ws_closed} close_code={ws_close_code} close_reason={ws_close_reason} "
                        f"ws_exc={ws_exc} ws_proto={ws_proto} "
                        f"last_rx_ago_s={last_rx_ago_s} last_tx_ago_s={last_tx_ago_s} "
                        f"last_recv_err={type(last_recv_err).__name__ if last_recv_err is not None else None} "
                        f"last_recv_err_ago_s={last_recv_err_ago_s} "
                        f"pending_data_size={pending_data_size} pings_outstanding={pings_outstanding} pongs_q={pongs_q}"
                    )
                except Exception:
                    return ""

            async def _route_reply(
                key: str,
                payload: dict[str, Any],
                *,
                resend_http_resp: bool = False,
            ) -> None:
                reply_subject = ""
                try:
                    reply_subject = str(reply_subjects.get(key) or "")
                except Exception:
                    reply_subject = ""
                if not reply_subject:
                    # Prefer v2 subjects by default; legacy v1 is opt-in and explicitly recorded in reply_subjects.
                    reply_subject = f"route.v2.to_browser.{hub_id}.{key}"
                reply_started = time.monotonic()
                t0 = None
                try:
                    t0 = (payload or {}).get("t")
                except Exception:
                    t0 = None
                _route_lifecycle_log("reply.start", key, subject=reply_subject, payload=payload)
                publish_elapsed_s = 0.0
                try:
                    try:
                        publish_step_started = time.monotonic()
                        await asyncio.wait_for(
                            nc.publish(
                                reply_subject,
                                _json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                            ),
                            timeout=max(0.1, float(_route_send_timeout_s)),
                        )
                        publish_elapsed_s = max(0.0, time.monotonic() - publish_step_started)
                    except asyncio.TimeoutError:
                        raise RuntimeError("publish timeout")
                    if publish_elapsed_s >= _route_publish_slow_warn_s:
                        route_diag_state["reply_publish_slow_total"] = int(
                            route_diag_state.get("reply_publish_slow_total") or 0
                        ) + 1
                        route_diag_state["last_publish_slow_key_tag"] = _key_tag(key)
                        route_diag_state["last_publish_slow_ms"] = round(publish_elapsed_s * 1000.0, 1)
                        _route_refresh_starvation_state()
                        _route_note_starvation(
                            "publish_slow",
                            key=key,
                            extra=(
                                f"publish_ms={publish_elapsed_s * 1000.0:.1f} "
                                f"threshold_ms={_route_publish_slow_warn_s * 1000.0:.1f}"
                            ),
                        )
                    try:
                        observe_hub_root_protocol_publish(
                            reply_subject,
                            ok=True,
                            traffic_class="route",
                            payload_bytes=len(_json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                            latency_ms=(time.monotonic() - reply_started) * 1000.0,
                        )
                    except Exception:
                        pass
                    if t0 in ("frame", "chunk"):
                        _route_observe_flow(
                            "frame",
                            f"browser_{t0}",
                            direction="to_browser",
                            payload=payload,
                        )
                    elif t0 in ("http_resp", "close"):
                        _route_observe_flow(
                            "control",
                            f"browser_{t0}",
                            direction="to_browser",
                            payload=payload,
                        )
                    took_ms = (time.monotonic() - reply_started) * 1000.0
                    _route_lifecycle_log(
                        "reply.published",
                        key,
                        subject=reply_subject,
                        payload=payload,
                        extra=f"took_ms={took_ms:.1f}",
                    )
                    # Ensure the reply is actually flushed quickly; otherwise Root may time out
                    # waiting on `route.to_browser.<key>` (especially over websocket-proxied NATS).
                    t = (payload or {}).get("t")
                    if _route_trace:
                        try:
                            if t in ("close", "http_resp") or (_route_frame_verbose and t in ("frame", "chunk")):
                                status = (payload or {}).get("status")
                                kind = (payload or {}).get("kind")
                                size = None
                                if t == "frame":
                                    data = (payload or {}).get("data") or (payload or {}).get("data_b64")
                                    try:
                                        size = len(data) if data is not None else None
                                    except Exception:
                                        size = None
                                if t == "chunk":
                                    data = (payload or {}).get("data") or (payload or {}).get("data_b64")
                                    try:
                                        size = len(data) if data is not None else None
                                    except Exception:
                                        size = None
                                _route_log(
                                    f"[hub-route] tx t={t} key={_key_tag(key)} status={status} kind={kind} size={size}"
                                )
                        except Exception:
                            pass
                    route_flow0 = ""
                    try:
                        if t in ("frame", "chunk"):
                            route_flow0 = _route_tunnel_flow(key)
                    except Exception:
                        route_flow0 = ""
                    try:
                        _route_refresh_starvation_state()
                    except Exception:
                        pass
                    should_force_flush = _hub_route_should_force_flush_reply(
                        payload,
                        route_force_flush=_route_force_flush,
                        route_sync_frame_force_flush=_route_sync_frame_force_flush,
                        tunnel_flow=route_flow0,
                        pending_data_size=route_diag_state.get("last_nc_pending_data_size"),
                        frame_flush_pending_bytes=_route_frame_flush_pending_bytes,
                    )
                    sync_frame_force_flush_this = False
                    if should_force_flush and t in ("frame", "chunk"):
                        try:
                            payload_flow0 = str((payload or {}).get("flow") or "").strip().lower()
                        except Exception:
                            payload_flow0 = ""
                        sync_frame_force_flush_this = (
                            payload_flow0 == "sync" or route_flow0 == "sync"
                        )
                    if should_force_flush:
                        # Fast-drain pending bytes without relying on NATS PING/PONG.
                        # This avoids `flush()` (which can time out when PONGs are flaky behind WS proxies).
                        try:
                            if sync_frame_force_flush_this:
                                tout = max(0.1, float(_route_sync_frame_flush_timeout_s))
                            else:
                                tout = max(0.1, float(_route_flush_timeout_s))
                        except Exception:
                            tout = 1.0
                        flush_err = None
                        flush_started = time.monotonic()
                        fp = getattr(nc, "_flush_pending", None)
                        if callable(fp):
                            try:
                                try:
                                    await asyncio.wait_for(fp(force_flush=True), timeout=tout)
                                except TypeError:
                                    try:
                                        await asyncio.wait_for(fp(True), timeout=tout)
                                    except TypeError:
                                        await asyncio.wait_for(fp(), timeout=tout)
                            except Exception as e:
                                flush_err = e
                        else:
                            # Fallback: old clients might not have `_flush_pending`.
                            try:
                                await nc.flush(timeout=tout)
                            except Exception as e:
                                flush_err = e
                        flush_took_s = time.monotonic() - flush_started
                        if flush_err is not None:
                            try:
                                _rl_log(
                                    "hub-route.flush_fail",
                                    f"[hub-route] flush failed t={t} key={key}: {type(flush_err).__name__}: {flush_err} {_route_nc_diag()}",
                                    every_s=1.0,
                                )
                            except Exception:
                                pass
                        elif flush_took_s >= max(0.5, float(tout) * 0.9):
                            route_diag_state["reply_flush_slow_total"] = int(
                                route_diag_state.get("reply_flush_slow_total") or 0
                            ) + 1
                            route_diag_state["last_flush_slow_key_tag"] = _key_tag(key)
                            route_diag_state["last_flush_slow_ms"] = round(flush_took_s * 1000.0, 1)
                            _route_refresh_starvation_state()
                            _route_note_starvation(
                                "flush_slow",
                                key=key,
                                extra=(
                                    f"t={t} flush_ms={flush_took_s * 1000.0:.1f} "
                                    f"timeout_ms={tout * 1000.0:.1f}"
                                ),
                            )
                        if (
                            _route_pending_data_warn_bytes > 0
                            and int(route_diag_state.get("last_nc_pending_data_size") or 0)
                            >= _route_pending_data_warn_bytes
                        ):
                            _route_note_starvation(
                                "pending_data",
                                key=key,
                                extra=f"threshold_bytes={_route_pending_data_warn_bytes}",
                            )
                        if _route_tx_verbose:
                            try:
                                print(f"[hub-route] tx {t} key={key}")
                            except Exception:
                                pass
                        elif t in ("http_resp", "close", "open_ack"):
                            _route_lifecycle_log(
                                "reply.flushed",
                                key,
                                subject=reply_subject,
                                payload=payload,
                                extra=f"flush_ms={flush_took_s * 1000.0:.1f}",
                            )
                    try:
                        _update_route_protocol_runtime()
                    except Exception:
                        pass
                except Exception as e:
                    try:
                        observe_hub_root_protocol_publish(
                            reply_subject,
                            ok=False,
                            traffic_class="route",
                            payload_bytes=len(_json.dumps(payload, ensure_ascii=False).encode("utf-8")),
                            error=f"{type(e).__name__}: {e}",
                        )
                    except Exception:
                        pass
                    if t0 in ("frame", "chunk"):
                        _route_observe_flow(
                            "frame",
                            f"{t0}_publish_fail",
                            payload=payload,
                            error=str(e),
                        )
                    elif t0 in ("http_resp", "close"):
                        _route_observe_flow(
                            "control",
                            f"{t0}_publish_fail",
                            payload=payload,
                            error=str(e),
                        )
                    route_diag_state["reply_publish_fail_total"] = int(
                        route_diag_state.get("reply_publish_fail_total") or 0
                    ) + 1
                    try:
                        _update_route_protocol_runtime(last_publish_fail_at=time.time())
                    except Exception:
                        pass
                    # Do not silently drop probe replies: Root will time out and surface `hub_unreachable`.
                    if t0 in ("http_resp", "close") or _route_verbose:
                        try:
                            _rl_log(
                                "hub-route.publish_fail",
                                f"[hub-route] publish to_browser failed t={t0} key={key}: {type(e).__name__}: {e} {_route_nc_diag()}",
                                every_s=1.0,
                            )
                        except Exception:
                            pass
                        try:
                            note_route_incident(
                                status="publish_fail",
                                summary="hub route reply publish failed",
                                details={
                                    "t": t0,
                                    "key_tag": _key_tag(key),
                                    "reply_subject": reply_subject,
                                    "err_type": type(e).__name__,
                                    "err": str(e),
                                },
                            )
                        except Exception:
                            pass
                    _route_lifecycle_log(
                        "reply.fail",
                        key,
                        subject=reply_subject,
                        payload=payload,
                        extra=f"err={type(e).__name__}: {e} {_route_nc_diag()}",
                    )

                if not (
                    resend_http_resp
                    and t0 == "http_resp"
                    and _route_http_resp_resend_delays_s
                ):
                    return
                try:
                    payload_bytes = len(_json.dumps(payload, ensure_ascii=False).encode("utf-8"))
                except Exception:
                    payload_bytes = 0
                if payload_bytes > int(_route_http_resp_resend_max_bytes):
                    try:
                        route_diag_state["http_resp_resend_skipped_total"] = int(
                            route_diag_state.get("http_resp_resend_skipped_total") or 0
                        ) + 1
                        route_diag_state["last_http_resp_resend_key_tag"] = _key_tag(key)
                        route_diag_state["last_http_resp_resend_payload_bytes"] = payload_bytes
                    except Exception:
                        pass
                    return

                for delay_s in _route_http_resp_resend_delays_s:

                    async def _resend_http_resp(delay_s: float = float(delay_s)) -> None:
                        try:
                            await asyncio.sleep(max(0.0, delay_s))
                            await _route_reply(key, payload, resend_http_resp=False)
                            try:
                                route_diag_state["http_resp_resend_sent_total"] = int(
                                    route_diag_state.get("http_resp_resend_sent_total") or 0
                                ) + 1
                                route_diag_state["last_http_resp_resend_key_tag"] = _key_tag(key)
                                route_diag_state["last_http_resp_resend_delay_s"] = float(delay_s)
                                route_diag_state["last_http_resp_resend_payload_bytes"] = payload_bytes
                                _update_route_protocol_runtime()
                            except Exception:
                                pass
                            if _route_tx_verbose or _route_verbose:
                                try:
                                    _rl_log(
                                        "hub-route.http_resp_resend",
                                        (
                                            f"[hub-route] http_resp resend delay_s={delay_s} "
                                            f"key={key} payload_bytes={payload_bytes}"
                                        ),
                                        every_s=1.0,
                                    )
                                except Exception:
                                    pass
                        except Exception:
                            return

                    try:
                        route_diag_state["http_resp_resend_scheduled_total"] = int(
                            route_diag_state.get("http_resp_resend_scheduled_total") or 0
                        ) + 1
                        route_diag_state["last_http_resp_resend_key_tag"] = _key_tag(key)
                        route_diag_state["last_http_resp_resend_delay_s"] = float(delay_s)
                        route_diag_state["last_http_resp_resend_payload_bytes"] = payload_bytes
                        asyncio.create_task(
                            _resend_http_resp(),
                            name=f"hub-route-http-resp-resend-{str(key)[-8:]}-{int(float(delay_s) * 1000)}",
                        )
                    except Exception:
                        pass
                try:
                    _update_route_protocol_runtime()
                except Exception:
                    pass

            def _cleanup_media_relay_session(key: str, *, remove_temp: bool) -> None:
                session = media_relay_sessions.pop(key, None)
                if not isinstance(session, dict):
                    return
                handle = session.get("handle")
                try:
                    if handle:
                        handle.close()
                except Exception:
                    pass
                if remove_temp:
                    tmp_path = session.get("tmp_path")
                    try:
                        if tmp_path is not None:
                            Path(tmp_path).unlink(missing_ok=True)
                    except Exception:
                        pass

            def _cleanup_http_body_relay_session(key: str, *, remove_temp: bool) -> None:
                session = http_body_relay_sessions.pop(key, None)
                if not isinstance(session, dict):
                    return
                handle = session.get("handle")
                try:
                    if handle:
                        handle.close()
                except Exception:
                    pass
                if remove_temp:
                    tmp_path = session.get("tmp_path")
                    try:
                        if tmp_path is not None:
                            Path(tmp_path).unlink(missing_ok=True)
                    except Exception:
                        pass

            async def _route_media_reply_json(
                key: str,
                *,
                status: int,
                payload: dict[str, Any],
            ) -> None:
                raw = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
                await _route_reply(
                    key,
                    {
                        "t": "media_http_meta",
                        "status": int(status),
                        "headers": {
                            "content-type": "application/json",
                            "content-length": str(len(raw)),
                        },
                    },
                )
                idx0 = 0
                for off in range(0, len(raw), 256 * 1024):
                    part = raw[off : off + (256 * 1024)]
                    await _route_reply(
                        key,
                        {
                            "t": "media_http_chunk",
                            "idx": idx0,
                            "data_b64": base64.b64encode(bytes(part)).decode("ascii"),
                        },
                    )
                    idx0 += 1
                await _route_reply(
                    key,
                    {
                        "t": "media_http_end",
                        "total_bytes": len(raw),
                        "truncated": False,
                    },
                )

            def _route_local_http_request(
                *,
                method: str,
                path: str,
                search: str,
                headers: Any,
                body: Any = None,
                content_length: int | None = None,
            ) -> dict[str, Any]:
                import requests  # type: ignore

                try:
                    from adaos.services.node_config import load_config

                    cfg = getattr(service.ctx, "config", None) or load_config(ctx=service.ctx)
                    bases = _build_hub_route_http_bases(
                        path_norm=(path.rstrip("/") or "/") if isinstance(path, str) else "/",
                        method=method,
                        cfg=cfg,
                        ctx=service.ctx,
                    )
                    token_local = getattr(cfg, "token", None) or os.getenv("ADAOS_TOKEN", "") or None
                except Exception:
                    bases = _build_hub_route_http_bases(
                        path_norm=(path.rstrip("/") or "/") if isinstance(path, str) else "/",
                        method=method,
                        cfg=None,
                        ctx=service.ctx,
                    )
                    token_local = os.getenv("ADAOS_TOKEN", "") or None

                try:
                    from urllib.parse import urlparse

                    u0 = urlparse(bases[0])
                    h0 = u0.hostname or "127.0.0.1"
                    p0 = u0.port
                    scheme0 = u0.scheme or "http"
                    alt_port_raw = os.getenv("ADAOS_TARGET_PORT") or os.getenv("ADAOS_CORE_PORT") or ""
                    alt_port = int(alt_port_raw) if alt_port_raw.strip() else 8788
                    if (p0 in (None, 8777)) and alt_port and alt_port != p0:
                        bases.append(f"{scheme0}://{h0}:{alt_port}")
                except Exception:
                    pass

                h2: dict[str, str] = {}
                if token_local:
                    h2["X-AdaOS-Token"] = str(token_local)
                if isinstance(headers, dict):
                    ct = headers.get("content-type") or headers.get("Content-Type")
                    if isinstance(ct, str) and ct:
                        h2["Content-Type"] = ct
                if isinstance(content_length, int) and content_length >= 0:
                    h2["Content-Length"] = str(content_length)

                sess = requests.Session()
                body_handle = None
                try:
                    try:
                        sess.trust_env = False
                    except Exception:
                        pass
                    body_data = body
                    if isinstance(body, (str, Path)):
                        body_handle = open(body, "rb")
                        body_data = body_handle
                    last_exc: Exception | None = None
                    resp = None
                    for base in bases:
                        url_try = f"{base}{path}{search}"
                        try:
                            timeout = _hub_route_local_http_timeout(path)
                            resp = sess.request(method, url_try, data=body_data, headers=h2, timeout=timeout)
                            last_exc = None
                            break
                        except Exception as e:
                            last_exc = e
                            if body_handle:
                                try:
                                    body_handle.seek(0)
                                except Exception:
                                    pass
                            if _route_verbose:
                                try:
                                    print(
                                        f"[hub-route] http upstream failed url={url_try}: {type(e).__name__}: {e}"
                                    )
                                except Exception:
                                    pass
                            if not _hub_route_should_retry_http_upstream_error(
                                method=method,
                                path=path,
                                error_kind=type(e).__name__,
                                body=body,
                            ):
                                break
                    if resp is None:
                        raise last_exc or RuntimeError("http upstream failed")
                    raw = resp.content or b""
                    limit = 2 * 1024 * 1024
                    truncated = len(raw) > limit
                    if truncated:
                        raw = raw[:limit]
                    out_headers: dict[str, str] = {}
                    try:
                        cth = resp.headers.get("content-type")
                        if cth:
                            out_headers["content-type"] = cth
                    except Exception:
                        pass
                    return {
                        "t": "http_resp",
                        "status": int(resp.status_code),
                        "headers": out_headers,
                        "body_b64": base64.b64encode(raw).decode("ascii"),
                        "truncated": truncated,
                    }
                finally:
                    try:
                        if body_handle:
                            body_handle.close()
                    except Exception:
                        pass
                    try:
                        sess.close()
                    except Exception:
                        pass

            def _parse_media_range(range_header: str | None, size_bytes: int) -> tuple[int, int] | None:
                raw = str(range_header or "").strip()
                if not raw.lower().startswith("bytes="):
                    return None
                spec = raw[6:].strip()
                if not spec or "," in spec:
                    return None
                start_s, _sep, end_s = spec.partition("-")
                if not _sep:
                    return None
                try:
                    if start_s and end_s:
                        start = int(start_s)
                        end = int(end_s)
                    elif start_s:
                        start = int(start_s)
                        end = size_bytes - 1
                    elif end_s:
                        suffix_len = int(end_s)
                        if suffix_len <= 0:
                            return None
                        start = max(0, size_bytes - suffix_len)
                        end = size_bytes - 1
                    else:
                        return None
                except Exception:
                    return None
                if start < 0 or end < start or start >= size_bytes:
                    return None
                end = min(end, size_bytes - 1)
                return (start, end)

            async def _route_media_reply_file(
                key: str,
                *,
                target: Path,
                method: str,
                request_headers: dict[str, Any] | None,
            ) -> None:
                from adaos.services.media_library import ROOT_MEDIA_RELAY_CHUNK_BYTES, guess_media_type

                stat = target.stat()
                total_size = int(stat.st_size)
                headers_in = request_headers if isinstance(request_headers, dict) else {}
                range_header = str(headers_in.get("range") or headers_in.get("Range") or "").strip()
                range_spec = _parse_media_range(range_header or None, total_size)
                if range_header and range_spec is None:
                    await _route_reply(
                        key,
                        {
                            "t": "media_http_meta",
                            "status": 416,
                            "headers": {
                                "content-range": f"bytes */{total_size}",
                                "content-length": "0",
                            },
                        },
                    )
                    await _route_reply(key, {"t": "media_http_end", "total_bytes": 0, "truncated": False})
                    return

                start = 0
                end = total_size - 1
                status = 200
                if range_spec is not None:
                    start, end = range_spec
                    status = 206
                length = max(0, end - start + 1)
                headers = {
                    "content-type": guess_media_type(target.name),
                    "content-length": str(length),
                    "accept-ranges": "bytes",
                    "content-disposition": f'inline; filename="{target.name}"',
                }
                if status == 206:
                    headers["content-range"] = f"bytes {start}-{end}/{total_size}"
                await _route_reply(
                    key,
                    {
                        "t": "media_http_meta",
                        "status": status,
                        "headers": headers,
                    },
                )
                if str(method or "").upper() == "HEAD" or length <= 0:
                    await _route_reply(key, {"t": "media_http_end", "total_bytes": 0, "truncated": False})
                    return

                sent = 0
                with target.open("rb") as handle:
                    handle.seek(start)
                    idx0 = 0
                    remaining = length
                    while remaining > 0:
                        blob = handle.read(min(int(ROOT_MEDIA_RELAY_CHUNK_BYTES), remaining))
                        if not blob:
                            break
                        await _route_reply(
                            key,
                            {
                                "t": "media_http_chunk",
                                "idx": idx0,
                                "data_b64": base64.b64encode(blob).decode("ascii"),
                            },
                        )
                        idx0 += 1
                        sent += len(blob)
                        remaining -= len(blob)
                await _route_reply(
                    key,
                    {
                        "t": "media_http_end",
                        "total_bytes": sent,
                        "truncated": False,
                    },
                )

            try:
                service._hub_root_route_reset = _reset_route_runtime
                self.reset_callback = _reset_route_runtime
            except Exception:
                pass

            def _hub_key_match(key: str) -> bool:
                try:
                    if not isinstance(key, str) or not key:
                        return False
                    prefixes = route_key_prefixes or ({f"{hub_id}--"} if hub_id else set())
                    return any(key.startswith(prefix) for prefix in prefixes if prefix)
                except Exception:
                    return False

            def _route_tunnel_path(key: str) -> str:
                try:
                    rec0 = tunnels.get(key) or {}
                    if isinstance(rec0, dict):
                        return str(rec0.get("path") or "")
                except Exception:
                    pass
                try:
                    meta0 = _recent_sync_shed_tunnel(key)
                    if isinstance(meta0, dict):
                        return str(meta0.get("path") or "")
                except Exception:
                    pass
                return ""

            def _route_tunnel_flow(key: str) -> str:
                try:
                    rec0 = tunnels.get(key) or {}
                    if isinstance(rec0, dict):
                        flow0 = str(rec0.get("flow") or "").strip()
                        if flow0:
                            return flow0
                except Exception:
                    pass
                try:
                    if _recent_sync_shed_tunnel(key):
                        return "sync"
                except Exception:
                    pass
                return _hub_route_semantic_flow_for_path(_route_tunnel_path(key))

            def _remember_sync_shed_tunnel(key: str, *, path: str, payload_bytes: int) -> None:
                try:
                    ttl_s = max(5.0, float(_route_no_upstream_close_after_s or 0.0) * 4.0)
                except Exception:
                    ttl_s = 6.0
                try:
                    sync_shed_tunnel_meta[str(key)] = {
                        "path": str(path or ""),
                        "payload_bytes": max(0, int(payload_bytes or 0)),
                        "expires_at": time.monotonic() + ttl_s,
                    }
                except Exception:
                    pass

            def _recent_sync_shed_tunnel(key: str) -> dict[str, Any] | None:
                key0 = str(key or "")
                if not key0:
                    return None
                now0 = time.monotonic()
                try:
                    for k0, meta0 in list(sync_shed_tunnel_meta.items()):
                        if not isinstance(meta0, dict):
                            sync_shed_tunnel_meta.pop(k0, None)
                            continue
                        expires_at0 = float(meta0.get("expires_at") or 0.0)
                        if expires_at0 <= now0:
                            sync_shed_tunnel_meta.pop(k0, None)
                except Exception:
                    pass
                meta = sync_shed_tunnel_meta.get(key0)
                return dict(meta) if isinstance(meta, dict) else None

            def _drop_late_sync_shed_frame(key: str, payload: dict[str, Any]) -> bool:
                meta0 = _recent_sync_shed_tunnel(key)
                if not meta0:
                    return False
                path0 = str(meta0.get("path") or "")
                route_diag_state["sync_backpressure_late_drop_total"] = int(
                    route_diag_state.get("sync_backpressure_late_drop_total") or 0
                ) + 1
                route_diag_state["last_sync_backpressure_late_drop_key_tag"] = _key_tag(key)
                route_diag_state["last_sync_backpressure_late_drop_path"] = path0
                _route_observe_flow(
                    "frame",
                    "sync_backpressure_late_drop",
                    payload=payload,
                    error="route_sync_backpressure",
                )
                if _route_trace:
                    try:
                        _route_log(
                            f"[hub-route] drop late sync frame key={_key_tag(key)} path={path0 or '-'} reason=route_sync_backpressure"
                        )
                    except Exception:
                        pass
                try:
                    _update_route_protocol_runtime()
                except Exception:
                    pass
                return True

            async def _shed_sync_tunnel_if_backpressured(key: str, ws, payload_bytes: int) -> bool:
                path0 = _route_tunnel_path(key)
                if _route_tunnel_flow(key) != "sync":
                    return False
                try:
                    _route_refresh_starvation_state()
                except Exception:
                    pass
                should_shed = _hub_route_should_shed_sync_frame(
                    path0 or "/yws",
                    pending_data_size=route_diag_state.get("last_nc_pending_data_size"),
                    guardrail_active=route_diag_state.get("guardrail_active"),
                    frame_flush_pending_bytes=_route_frame_flush_pending_bytes,
                    sync_shed_pending_bytes=_route_sync_backpressure_shed_pending_bytes,
                    payload_bytes=payload_bytes,
                )
                if not should_shed:
                    return False
                try:
                    rec0 = tunnels.get(key)
                    if isinstance(rec0, dict):
                        rec0["close_err"] = "route_sync_backpressure"
                except Exception:
                    pass
                route_diag_state["sync_backpressure_shed_total"] = int(
                    route_diag_state.get("sync_backpressure_shed_total") or 0
                ) + 1
                route_diag_state["last_sync_backpressure_key_tag"] = _key_tag(key)
                route_diag_state["last_sync_backpressure_path"] = path0
                route_diag_state["last_sync_backpressure_payload_bytes"] = max(0, int(payload_bytes or 0))
                _remember_sync_shed_tunnel(key, path=path0, payload_bytes=payload_bytes)
                _route_note_starvation(
                    "sync_backpressure_shed",
                    key=key,
                    extra=(
                        f"path={path0 or '-'} "
                        f"payload_bytes={max(0, int(payload_bytes or 0))} "
                        f"threshold_bytes={_route_sync_backpressure_shed_pending_bytes}"
                    ),
                )
                _route_observe_flow(
                    "frame",
                    "sync_backpressure_shed",
                    direction="to_browser",
                    payload_bytes=max(0, int(payload_bytes or 0)),
                    error="route_sync_backpressure",
                )
                try:
                    await ws.close(code=1013, reason="route_sync_backpressure")
                except Exception:
                    pass
                try:
                    _update_route_protocol_runtime()
                except Exception:
                    pass
                return True

            async def _tunnel_reader(key: str, ws) -> None:
                try:
                    async for msg in ws:
                        if _route_frame_verbose:
                            try:
                                if isinstance(msg, (bytes, bytearray)):
                                    _route_log(f"[hub-route] rx upstream frame key={_key_tag(key)} kind=bin size={len(msg)}")
                                else:
                                    _route_log(
                                        f"[hub-route] rx upstream frame key={_key_tag(key)} kind=text size={len(str(msg))}"
                                    )
                            except Exception:
                                pass
                        if isinstance(msg, (bytes, bytearray)):
                            raw = bytes(msg)
                            if await _shed_sync_tunnel_if_backpressured(key, ws, len(raw)):
                                break
                            if len(raw) > MAX_CHUNK_RAW:
                                cid = f"c_{uuid.uuid4().hex}"
                                total = (len(raw) + MAX_CHUNK_RAW - 1) // MAX_CHUNK_RAW
                                flow0 = _route_tunnel_flow(key)
                                payloads: list[dict[str, Any]] = []
                                for idx in range(total):
                                    chunk = raw[idx * MAX_CHUNK_RAW : (idx + 1) * MAX_CHUNK_RAW]
                                    payloads.append(
                                        {
                                            "t": "chunk",
                                            "flow": flow0,
                                            "id": cid,
                                            "kind": "bin",
                                            "idx": idx,
                                            "total": total,
                                            "data_b64": base64.b64encode(chunk).decode("ascii"),
                                        }
                                    )
                                _cache_outbound_chunk_payloads(key, cid, "bin", total, payloads)
                                for payload in payloads:
                                    await _route_reply(key, payload)
                            else:
                                await _route_reply(
                                    key,
                                    {
                                        "t": "frame",
                                        "flow": _route_tunnel_flow(key),
                                        "kind": "bin",
                                        "data_b64": base64.b64encode(raw).decode("ascii"),
                                    },
                                )
                        else:
                            text = str(msg)
                            text_payload_bytes = len(text.encode("utf-8"))
                            path0 = _route_tunnel_path(key)
                            subnet_payload_type = _hub_route_subnet_sync_payload_type(path0, text)
                            if subnet_payload_type:
                                try:
                                    _route_refresh_starvation_state()
                                except Exception:
                                    pass
                                if _hub_route_should_drop_subnet_sync_frame(
                                    path0,
                                    subnet_payload_type,
                                    pending_data_size=route_diag_state.get("last_nc_pending_data_size"),
                                    guardrail_active=route_diag_state.get("guardrail_active"),
                                    frame_flush_pending_bytes=_route_frame_flush_pending_bytes,
                                    payload_bytes=text_payload_bytes,
                                ):
                                    route_diag_state["subnet_sync_backpressure_drop_total"] = int(
                                        route_diag_state.get("subnet_sync_backpressure_drop_total") or 0
                                    ) + 1
                                    route_diag_state["last_subnet_sync_backpressure_key_tag"] = _key_tag(key)
                                    route_diag_state["last_subnet_sync_backpressure_path"] = path0
                                    route_diag_state["last_subnet_sync_backpressure_type"] = subnet_payload_type
                                    route_diag_state["last_subnet_sync_backpressure_payload_bytes"] = text_payload_bytes
                                    _route_note_starvation(
                                        "subnet_sync_backpressure_drop",
                                        key=key,
                                        extra=(
                                            f"path={path0 or '-'} "
                                            f"type={subnet_payload_type} "
                                            f"payload_bytes={text_payload_bytes} "
                                            f"threshold_bytes={_route_frame_flush_pending_bytes}"
                                        ),
                                    )
                                    _route_observe_flow(
                                        "frame",
                                        "subnet_sync_backpressure_drop",
                                        direction="to_browser",
                                        payload_bytes=text_payload_bytes,
                                        error="route_subnet_sync_backpressure",
                                    )
                                    try:
                                        _update_route_protocol_runtime()
                                    except Exception:
                                        pass
                                    continue
                            if await _shed_sync_tunnel_if_backpressured(key, ws, text_payload_bytes):
                                break
                            if len(text) > MAX_CHUNK_RAW:
                                cid = f"c_{uuid.uuid4().hex}"
                                parts = [text[i : i + MAX_CHUNK_RAW] for i in range(0, len(text), MAX_CHUNK_RAW)]
                                flow0 = _route_tunnel_flow(key)
                                payloads = []
                                for idx, part in enumerate(parts):
                                    payloads.append(
                                        {
                                            "t": "chunk",
                                            "flow": flow0,
                                            "id": cid,
                                            "kind": "text",
                                            "idx": idx,
                                            "total": len(parts),
                                            "data": part,
                                        }
                                    )
                                _cache_outbound_chunk_payloads(key, cid, "text", len(parts), payloads)
                                for payload in payloads:
                                    await _route_reply(key, payload)
                            else:
                                await _route_reply(
                                    key,
                                    {
                                        "t": "frame",
                                        "flow": _route_tunnel_flow(key),
                                        "kind": "text",
                                        "data": text,
                                    },
                                )
                except Exception as e:
                    if _route_trace:
                        try:
                            _route_log(
                                f"[hub-route] upstream reader error key={_key_tag(key)} err={type(e).__name__}: {e}"
                            )
                        except Exception:
                            pass
                finally:
                    if _route_trace:
                        try:
                            code = getattr(ws, "close_code", None)
                            reason = getattr(ws, "close_reason", None)
                            exc = None
                            try:
                                exf = getattr(ws, "exception", None)
                                if callable(exf):
                                    exc = exf()
                            except Exception:
                                exc = None
                            _route_log(
                                f"[hub-route] upstream closed key={_key_tag(key)} code={code} reason={reason} exc={exc}"
                            )
                        except Exception:
                            pass
                    _route_observe_flow("control", "upstream_closed")
                    try:
                        close_payload: dict[str, Any] = {"t": "close"}
                        try:
                            rec0 = tunnels.get(key) or {}
                            if isinstance(rec0, dict) and rec0.get("close_err"):
                                close_payload["err"] = str(rec0.get("close_err") or "")
                        except Exception:
                            pass
                        await _route_reply(key, close_payload)
                    except Exception:
                        pass
                    tunnels.pop(key, None)
                    t = tunnel_tasks.pop(key, None)
                    try:
                        if t:
                            t.cancel()
                    except Exception:
                        pass
                    try:
                        _drop_pending_chunks_for_key(key)
                    except Exception:
                        pass
                    try:
                        _drop_outbound_chunk_cache_for_key(key)
                    except Exception:
                        pass
                    try:
                        _clear_pending_tunnel_state(key, drop_events=True)
                    except Exception:
                        pass
                    try:
                        await ws.close()
                    except Exception:
                        pass
                    try:
                        _update_route_protocol_runtime()
                    except Exception:
                        pass

            def _queue_pending_tunnel_event(key: str, payload: dict[str, Any]) -> None:
                try:
                    items = pending_tunnel_events.get(key)
                    if items is None:
                        items = []
                        pending_tunnel_events[key] = items
                    if len(items) >= MAX_PENDING_TUNNEL_EVENTS:
                        items.pop(0)
                    items.append(dict(payload))
                except Exception:
                    pass
                try:
                    _update_route_protocol_runtime()
                except Exception:
                    pass

            async def _send_tunnel_event(key: str, ws, payload: dict[str, Any]) -> None:
                kind = (payload or {}).get("t")
                if kind == "frame":
                    frame_kind = (payload or {}).get("kind")
                    if frame_kind == "bin":
                        b64 = (payload or {}).get("data_b64")
                        if isinstance(b64, str) and b64:
                            raw = base64.b64decode(b64.encode("ascii"))
                            await asyncio.wait_for(
                                ws.send(raw),
                                timeout=max(0.1, float(_route_upstream_ws_send_timeout_s)),
                            )
                            _route_observe_flow(
                                "frame",
                                "frame_upstream_sent",
                                direction="to_upstream",
                                payload_bytes=len(raw),
                            )
                    else:
                        txt = (payload or {}).get("data")
                        if isinstance(txt, str):
                            await asyncio.wait_for(
                                ws.send(txt),
                                timeout=max(0.1, float(_route_upstream_ws_send_timeout_s)),
                            )
                            _route_observe_flow(
                                "frame",
                                "frame_upstream_sent",
                                direction="to_upstream",
                                payload_bytes=len(txt.encode("utf-8")),
                            )
                    return

                if kind != "chunk":
                    return

                cid = (payload or {}).get("id")
                idx = int((payload or {}).get("idx") or 0)
                total = int((payload or {}).get("total") or 0)
                frame_kind = "text" if (payload or {}).get("kind") == "text" else "bin"
                if not isinstance(cid, str) or not cid or total <= 0 or idx < 0 or idx >= total:
                    return
                st = pending_chunks.get(cid)
                if not st:
                    st = {"key": key, "kind": frame_kind, "total": total, "parts": [None] * total}
                    pending_chunks[cid] = st
                if st.get("key") != key or st.get("kind") != frame_kind or int(st.get("total") or 0) != total:
                    return
                parts = st.get("parts")
                if not isinstance(parts, list) or len(parts) != total:
                    st["parts"] = [None] * total
                    parts = st["parts"]
                if frame_kind == "bin":
                    b64 = (payload or {}).get("data_b64")
                    if not isinstance(b64, str):
                        return
                    parts[idx] = base64.b64decode(b64.encode("ascii"))
                else:
                    txt = (payload or {}).get("data")
                    if not isinstance(txt, str):
                        return
                    parts[idx] = txt
                if any(p is None for p in parts):
                    return
                pending_chunks.pop(cid, None)
                if frame_kind == "bin":
                    blob = b"".join([p for p in parts if isinstance(p, (bytes, bytearray))])
                    await asyncio.wait_for(
                        ws.send(blob),
                        timeout=max(0.1, float(_route_upstream_ws_send_timeout_s)),
                    )
                    _route_observe_flow(
                        "frame",
                        "chunk_upstream_sent",
                        direction="to_upstream",
                        payload_bytes=len(blob),
                    )
                else:
                    text_blob = "".join([p for p in parts if isinstance(p, str)])
                    await asyncio.wait_for(
                        ws.send(text_blob),
                        timeout=max(0.1, float(_route_upstream_ws_send_timeout_s)),
                    )
                    _route_observe_flow(
                        "frame",
                        "chunk_upstream_sent",
                        direction="to_upstream",
                        payload_bytes=len(text_blob.encode("utf-8")),
                    )

            async def _route_handle_msg(msg) -> None:
                key = ""
                subject = ""
                is_http_key = False
                route_t = "?"
                route_outcome = "start"
                route_started = time.monotonic()
                http_method = ""
                http_path = ""
                http_kind = ""
                try:
                    subject = str(getattr(msg, "subject", "") or "")
                    # Legacy v1: route.to_hub.<key>
                    # v2: route.v2.to_hub.<hubId>.<key>
                    parts = subject.split(".")
                    if subject.startswith("route.v2.to_hub."):
                        if len(parts) < 5:
                            route_outcome = "drop_bad_subject"
                            if _route_diag:
                                try:
                                    _rl_log(
                                        "hub-route.drop_subject",
                                        f"[hub-route] drop: bad subject={subject!s}",
                                        every_s=2.0,
                                    )
                                except Exception:
                                    pass
                            return
                        subj_hub_id = str(parts[3] or "")
                        if subj_hub_id and subj_hub_id != hub_id:
                            route_outcome = "drop_hub_mismatch"
                            if _route_diag:
                                try:
                                    _rl_log(
                                        "hub-route.drop_hub",
                                        f"[hub-route] drop: hub mismatch subject={subject!s} hub={subj_hub_id!s} local={hub_id!s}",
                                        every_s=2.0,
                                    )
                                except Exception:
                                    pass
                            return
                        key = str(parts[4] or "")
                        if key:
                            try:
                                reply_subjects[key] = f"route.v2.to_browser.{hub_id}.{key}"
                            except Exception:
                                pass
                    else:
                        # route.to_hub.<key>
                        if len(parts) < 3:
                            route_outcome = "drop_bad_subject"
                            if _route_diag:
                                try:
                                    _rl_log(
                                        "hub-route.drop_subject",
                                        f"[hub-route] drop: bad subject={subject!s}",
                                        every_s=2.0,
                                    )
                                except Exception:
                                    pass
                            return
                        key = str(parts[2] or "")
                        if key:
                            try:
                                reply_subjects[key] = f"route.to_browser.{key}"
                            except Exception:
                                pass

                    if not key:
                        route_outcome = "drop_bad_subject"
                        return
                    is_http_key = isinstance(key, str) and "--http--" in key
                    if not _hub_key_match(key):
                        route_outcome = "drop_key_mismatch"
                        if _route_diag:
                            try:
                                _rl_log(
                                    "hub-route.drop_key",
                                    f"[hub-route] drop: key mismatch subject={subject!s} key={key!s} expected_prefixes={sorted(route_key_prefixes) or [f'{hub_id}--']}",
                                    every_s=2.0,
                                )
                            except Exception:
                                pass
                        return

                    try:
                        raw = bytes(getattr(msg, "data", b"") or b"")
                    except Exception:
                        raw = b""
                    try:
                        data = _json.loads(raw.decode("utf-8"))
                    except Exception as e:
                        route_outcome = "drop_invalid_json"
                        if _route_diag:
                            try:
                                _rl_log(
                                    "hub-route.drop_json",
                                    f"[hub-route] drop: invalid json key={key} bytes={len(raw)} err={type(e).__name__}: {e}",
                                    every_s=2.0,
                                )
                            except Exception:
                                pass
                        # Avoid systematic `hub_unreachable` timeouts for HTTP keys.
                        try:
                            if is_http_key:
                                await _route_reply(
                                    key,
                                    {"t": "http_resp", "status": 502, "headers": {}, "body_b64": "", "truncated": False, "err": "invalid_json"},
                                )
                        except Exception:
                            pass
                        return
                    if not isinstance(data, dict):
                        route_outcome = "drop_invalid_payload"
                        if _route_diag:
                            try:
                                _rl_log(
                                    "hub-route.drop_payload",
                                    f"[hub-route] drop: unexpected payload type key={key} type={type(data).__name__}",
                                    every_s=2.0,
                                )
                            except Exception:
                                pass
                        try:
                            if is_http_key:
                                await _route_reply(
                                    key,
                                    {"t": "http_resp", "status": 502, "headers": {}, "body_b64": "", "truncated": False, "err": "invalid_payload"},
                                )
                        except Exception:
                            pass
                        return
                    t = (data or {}).get("t")
                    route_t = str(t or "?")
                    if not isinstance(t, str) or not t:
                        route_outcome = "drop_missing_t"
                        if _route_diag:
                            try:
                                _rl_log(
                                    "hub-route.drop_missing_t",
                                    f"[hub-route] drop: missing t key={key}",
                                    every_s=2.0,
                                )
                            except Exception:
                                pass
                        try:
                            if is_http_key:
                                await _route_reply(
                                    key,
                                    {"t": "http_resp", "status": 502, "headers": {}, "body_b64": "", "truncated": False, "err": "missing_t"},
                                )
                        except Exception:
                            pass
                        return
                    if t == "http":
                        route_diag_state["http_request_total"] = int(route_diag_state.get("http_request_total") or 0) + 1
                        route_diag_state["last_http_path"] = str((data or {}).get("path") or "")
                        route_diag_state["last_http_method"] = str((data or {}).get("method") or "GET").upper()
                        _update_route_protocol_runtime()
                        try:
                            http_method = str((data or {}).get("method") or "GET").upper()
                            http_path = str((data or {}).get("path") or "")
                            http_kind = (
                                "probe"
                                if http_path in ("/api/node/status", "/api/ping", "/healthz")
                                else "app"
                            )
                            observe_route_e2e(
                                details={
                                    f"last_http_{http_kind}_rx_at": time.time(),
                                    "last_http_rx_path": http_path,
                                    "last_http_rx_method": http_method,
                                    "last_http_rx_key_tag": _key_tag(key),
                                }
                            )
                        except Exception:
                            pass
                    if _route_http_trace and (is_http_key or t in ("open", "close")):
                        _route_lifecycle_log(
                            "request.rx",
                            key,
                            subject=subject,
                            payload=data,
                            extra=f"bytes={len(raw)}",
                        )
                    if _route_verbose or _route_trace:
                        try:
                            if t == "http":
                                _m = http_method or "GET"
                                _p = http_path or ""
                                if _p not in ("/api/node/status", "/api/ping", "/healthz"):
                                    _route_log(f"[hub-route] rx http key={_key_tag(key)} {_m} {_p}")
                                else:
                                    try:
                                        _rl_log(
                                            "hub-route.rx_http_probe",
                                            f"[hub-route] rx http probe key={key} {_m} {_p}",
                                            every_s=5.0,
                                        )
                                    except Exception:
                                        pass
                            elif t == "open":
                                _p = str((data or {}).get("path") or "")
                                if _p not in ("/api/node/status", "/api/ping"):
                                    _route_log(f"[hub-route] rx open key={_key_tag(key)} path={_p}")
                            elif t == "close":
                                _route_log(f"[hub-route] rx close key={_key_tag(key)}")
                            else:
                                # Frames are extremely noisy; enable explicitly when debugging.
                                if t == "frame" and not _route_frame_verbose:
                                    pass
                                else:
                                    _route_log(f"[hub-route] rx t={t} key={_key_tag(key)}")
                        except Exception:
                            pass

                        if _route_trace:
                            try:
                                if t == "open":
                                    _p = str((data or {}).get("path") or "")
                                    _q = str((data or {}).get("query") or "")
                                    _dev = _query_param(_q, "dev")
                                    _wsq = _query_param(_q, "ws")
                                    _route_log(
                                        f"[hub-route] open req key={_key_tag(key)} path={_p} query_len={len(_q)} token={_query_has_token(_q)} dev={_dev} ws={_wsq}"
                                    )
                                elif t == "frame":
                                    _kind = (data or {}).get("kind")
                                    _size = None
                                    _body = (data or {}).get("data") or (data or {}).get("data_b64")
                                    try:
                                        _size = len(_body) if _body is not None else None
                                    except Exception:
                                        _size = None
                                    if _route_frame_verbose:
                                        _route_log(
                                            f"[hub-route] frame req key={_key_tag(key)} kind={_kind} size={_size}"
                                        )
                                elif t == "chunk":
                                    if _route_frame_verbose:
                                        _route_log(
                                            f"[hub-route] chunk req key={_key_tag(key)} idx={(data or {}).get('idx')} total={(data or {}).get('total')}"
                                        )
                                elif t == "close":
                                    _route_log(f"[hub-route] close req key={_key_tag(key)}")
                            except Exception:
                                pass

                    if t == "open":
                        route_outcome = "open"
                        try:
                            sync_shed_tunnel_meta.pop(key, None)
                        except Exception:
                            pass
                        _route_observe_flow("control", "open_request", payload=data)
                        # Open a local WS to the hub server and start pumping frames.
                        if websockets_mod is None:
                            route_outcome = "open_no_websockets"
                            _clear_pending_tunnel_state(key, drop_events=True)
                            if _route_trace:
                                _route_log(f"[hub-route] open upstream failed key={_key_tag(key)} err=websockets_unavailable")
                            _route_observe_flow(
                                "control",
                                "open_connect_fail",
                                payload=data,
                                error="websockets_unavailable",
                            )
                            await _route_reply(key, {"t": "close", "err": "websockets_unavailable"})
                            return
                        path = str((data or {}).get("path") or "/ws")
                        query = str((data or {}).get("query") or "")
                        route_diag_state["open_request_total"] = int(route_diag_state.get("open_request_total") or 0) + 1
                        route_diag_state["last_open_path"] = path
                        route_diag_state["last_open_query_has_token"] = bool(_query_has_token(query))
                        # Local hub server is always reachable inside the hub machine/container.
                        try:
                            from adaos.services.node_config import load_config

                            cfg = getattr(service.ctx, "config", None) or load_config(ctx=service.ctx)
                            ws_bases = _build_hub_route_ws_bases(cfg=cfg, path=path, ctx=service.ctx)
                            token_local = getattr(cfg, "token", None) or os.getenv("ADAOS_TOKEN", "") or None
                        except Exception:
                            ws_bases = _build_hub_route_ws_bases(cfg=None, path=path, ctx=service.ctx)
                            token_local = os.getenv("ADAOS_TOKEN", "") or None
                        route_diag_state["last_open_base_total"] = len(list(ws_bases or []))
                        _update_route_protocol_runtime()
                        # Translate root-proxy JWT token into local hub token for upstream hub WS auth.
                        # Local hub expects `token=<X-AdaOS-Token>`; forwarding the session JWT makes the
                        # hub close immediately and the browser retries endlessly.
                        try:
                            from urllib.parse import parse_qs, urlencode

                            if query.startswith("?"):
                                q = parse_qs(query[1:], keep_blank_values=True)
                            else:
                                q = parse_qs(query, keep_blank_values=True)
                            if token_local:
                                q["token"] = [str(token_local)]
                            else:
                                # If we don't have a local token, do not forward the root session JWT.
                                q.pop("token", None)
                            query = "?" + urlencode(q, doseq=True) if q else ""
                        except Exception:
                            pass
                        # Ensure we don't leak multiple opens for same key.
                        try:
                            old = tunnels.get(key)
                            if old and old.get("ws"):
                                try:
                                    await old["ws"].close()
                                except Exception:
                                    pass
                                try:
                                    _drop_outbound_chunk_cache_for_key(key)
                                except Exception:
                                    pass
                        except Exception:
                            pass
                        try:
                            # Yjs sync frames can exceed 1 MiB; do not enforce a small client-side cap.
                            try:
                                ws_connect_timeout_s = float(
                                    os.getenv("HUB_ROUTE_UPSTREAM_WS_CONNECT_TIMEOUT_S", "2.5") or "2.5"
                                )
                            except Exception:
                                ws_connect_timeout_s = 2.5
                            if ws_connect_timeout_s < 0.1:
                                ws_connect_timeout_s = 0.1
                            if _route_trace:
                                _route_log(
                                    f"[hub-route] upstream.connect start key={_key_tag(key)} timeout_s={ws_connect_timeout_s}"
                                )
                            ws = None
                            last_exc = None
                            for base_ws in ws_bases:
                                url = f"{base_ws}{path}{query}"
                                if _route_verbose or _route_trace:
                                    try:
                                        _route_log(f"[hub-route] open upstream url={url}")
                                    except Exception:
                                        pass
                                t0 = time.monotonic()
                                try:
                                    ws = await asyncio.wait_for(
                                        websockets_mod.connect(url, max_size=None),
                                        timeout=ws_connect_timeout_s,
                                    )
                                    if _route_trace:
                                        took = time.monotonic() - t0
                                        proto = getattr(ws, "subprotocol", None) or getattr(ws, "protocol", None)
                                        remote = getattr(ws, "remote_address", None)
                                        _route_log(
                                            f"[hub-route] upstream.connect ok key={_key_tag(key)} took_s={took:.3f} proto={proto} remote={remote}"
                                        )
                                    break
                                except Exception as exc:
                                    last_exc = exc
                                    if _route_trace:
                                        try:
                                            _route_log(
                                                f"[hub-route] upstream.connect retry key={_key_tag(key)} url={url} err={type(exc).__name__}: {exc}"
                                            )
                                        except Exception:
                                            pass
                            if ws is None:
                                raise last_exc or RuntimeError("hub route websocket upstream failed")
                        except Exception as e:
                            route_outcome = f"open_connect_fail:{type(e).__name__}"
                            _clear_pending_tunnel_state(key, drop_events=True)
                            if _route_trace:
                                _route_log(
                                    f"[hub-route] upstream.connect fail key={_key_tag(key)} err={type(e).__name__}: {e}"
                                )
                            _route_observe_flow(
                                "control",
                                "open_connect_fail",
                                payload=data,
                                error=str(e),
                            )
                            await _route_reply(key, {"t": "close", "err": str(e)})
                            return
                        route_outcome = "open_connected"
                        tunnels[key] = {
                            "ws": ws,
                            "url": url,
                            "path": path,
                            "flow": _hub_route_semantic_flow_for_path(path),
                        }
                        _clear_pending_tunnel_state(key, drop_events=False)
                        tunnel_tasks[key] = asyncio.create_task(_tunnel_reader(key, ws), name=f"hub-route-{key}")
                        try:
                            await _route_reply(key, {"t": "open_ack"})
                        except Exception:
                            pass
                        pending = pending_tunnel_events.pop(key, None) or []
                        for pending_payload in pending:
                            try:
                                await _send_tunnel_event(key, ws, pending_payload)
                            except Exception as e:
                                if _route_verbose or _route_trace:
                                    try:
                                        _route_log(
                                            f"[hub-route] flush pending failed key={_key_tag(key)}: {type(e).__name__}: {e}"
                                        )
                                    except Exception:
                                        pass
                                break
                        try:
                            _update_route_protocol_runtime()
                        except Exception:
                            pass
                        _route_observe_flow("control", "open_ready", payload=data)
                        route_outcome = "open_ready"
                        return

                    if t == "close":
                        route_outcome = "close_local"
                        _route_observe_flow("control", "close_local", payload=data)
                        rec = tunnels.pop(key, None)
                        task = tunnel_tasks.pop(key, None)
                        _clear_pending_tunnel_state(key, drop_events=True)
                        try:
                            if task:
                                task.cancel()
                        except Exception:
                            pass
                        try:
                            _drop_outbound_chunk_cache_for_key(key)
                        except Exception:
                            pass
                        try:
                            _update_route_protocol_runtime()
                        except Exception:
                            pass
                        try:
                            if rec and rec.get("ws"):
                                await rec["ws"].close()
                        except Exception:
                            pass
                        if _route_trace:
                            _route_log(f"[hub-route] upstream close req key={_key_tag(key)}")
                        return

                    if t == "resend_chunks":
                        route_outcome = "resend_chunks"
                        _route_observe_flow(
                            "frame",
                            "resend_chunks",
                            direction="to_browser",
                            payload=data,
                        )
                        await _resend_outbound_chunks(key, data)
                        return

                    if t == "frame":
                        rec = tunnels.get(key)
                        ws = rec.get("ws") if isinstance(rec, dict) else None
                        if not ws:
                            if _drop_late_sync_shed_frame(key, data):
                                route_outcome = "frame_drop_after_sync_backpressure"
                                return
                            route_outcome = "frame_no_upstream"
                            _route_observe_flow(
                                "frame",
                                "frame_no_upstream",
                                payload=data,
                                error="no_upstream",
                                pending=True,
                            )
                            _queue_pending_tunnel_event(key, data)
                            _mark_pending(key)
                            try:
                                st = pending_tunnel_meta.get(key) or {}
                                count = int(st.get("count") or 0)
                                if count <= 1:
                                    first_at = float(st.get("first_at") or 0.0)
                                    age_s = round(time.monotonic() - first_at, 3) if first_at > 0 else None
                                    note_route_incident(
                                        status="no_upstream",
                                        summary="hub route frame arrived while upstream is not connected",
                                        details={"key_tag": _key_tag(key), "age_s": age_s, "t": "frame"},
                                    )
                            except Exception:
                                pass
                            try:
                                _update_route_protocol_runtime(last_no_upstream_at=time.time())
                            except Exception:
                                pass
                            await _maybe_force_close_no_upstream(key)
                            if _route_trace:
                                try:
                                    st = pending_tunnel_meta.get(key) or {}
                                    first_at = float(st.get("first_at") or 0.0)
                                    age_s = time.monotonic() - first_at if first_at > 0 else None
                                    count = st.get("count")
                                except Exception:
                                    age_s = None
                                    count = None
                                _route_log(
                                    f"[hub-route] queue frame key={_key_tag(key)} reason=no_upstream age_s={age_s} count={count}"
                                )
                            return
                        try:
                            await _send_tunnel_event(key, ws, data)
                            route_outcome = "frame_sent"
                        except Exception as e:
                            route_outcome = f"frame_send_fail:{type(e).__name__}"
                            _route_observe_flow(
                                "frame",
                                "frame_send_fail",
                                payload=data,
                                error=str(e),
                            )
                            if _route_verbose or _route_trace:
                                try:
                                    _route_log(
                                        f"[hub-route] ws.send(frame) failed key={_key_tag(key)}: {type(e).__name__}: {e}"
                                    )
                                except Exception:
                                    pass
                        return
                    
                    if t == "chunk":
                        rec = tunnels.get(key)
                        ws = rec.get("ws") if isinstance(rec, dict) else None
                        if not ws:
                            if _drop_late_sync_shed_frame(key, data):
                                route_outcome = "chunk_drop_after_sync_backpressure"
                                return
                            route_outcome = "chunk_no_upstream"
                            _route_observe_flow(
                                "frame",
                                "chunk_no_upstream",
                                payload=data,
                                error="no_upstream",
                                pending=True,
                            )
                            _queue_pending_tunnel_event(key, data)
                            _mark_pending(key)
                            try:
                                st = pending_tunnel_meta.get(key) or {}
                                count = int(st.get("count") or 0)
                                if count <= 1:
                                    first_at = float(st.get("first_at") or 0.0)
                                    age_s = round(time.monotonic() - first_at, 3) if first_at > 0 else None
                                    note_route_incident(
                                        status="no_upstream",
                                        summary="hub route chunk arrived while upstream is not connected",
                                        details={"key_tag": _key_tag(key), "age_s": age_s, "t": "chunk"},
                                    )
                            except Exception:
                                pass
                            try:
                                _update_route_protocol_runtime(last_no_upstream_at=time.time())
                            except Exception:
                                pass
                            await _maybe_force_close_no_upstream(key)
                            if _route_trace:
                                try:
                                    st = pending_tunnel_meta.get(key) or {}
                                    first_at = float(st.get("first_at") or 0.0)
                                    age_s = time.monotonic() - first_at if first_at > 0 else None
                                    count = st.get("count")
                                except Exception:
                                    age_s = None
                                    count = None
                                _route_log(
                                    f"[hub-route] queue chunk key={_key_tag(key)} reason=no_upstream age_s={age_s} count={count}"
                                )
                            return
                        try:
                            await _send_tunnel_event(key, ws, data)
                            route_outcome = "chunk_sent"
                        except Exception as e:
                            route_outcome = f"chunk_send_fail:{type(e).__name__}"
                            _route_observe_flow(
                                "frame",
                                "chunk_send_fail",
                                payload=data,
                                error=str(e),
                            )
                            if _route_verbose or _route_trace:
                                try:
                                    _route_log(
                                        f"[hub-route] ws.send(chunked) failed key={_key_tag(key)}: {type(e).__name__}: {e}"
                                    )
                                except Exception:
                                    pass
                        return

                    if t == "media_http_open":
                        route_outcome = "media_http_open"
                        try:
                            from urllib.parse import unquote

                            from adaos.services.media_library import (
                                ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES as MEDIA_RELAY_MAX_UPLOAD_BYTES,
                                ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES,
                                guess_media_type,
                                list_media_files,
                                media_capabilities,
                                media_file_path,
                                media_runtime_snapshot,
                                media_snapshot,
                            )

                            method = str((data or {}).get("method") or "GET").upper()
                            path = str((data or {}).get("path") or "/media/files")
                            path_norm = (path.rstrip("/") or "/") if isinstance(path, str) else "/"
                            headers = (data or {}).get("headers") if isinstance((data or {}).get("headers"), dict) else {}
                            content_length = int((data or {}).get("content_length") or 0)

                            if method in ("GET", "HEAD") and path_norm == "/media/files":
                                payload0 = media_snapshot()
                                payload0["proxy_limits"] = {
                                    "root_routed_response_limit_bytes": ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES,
                                    "root_media_relay_max_upload_bytes": MEDIA_RELAY_MAX_UPLOAD_BYTES,
                                }
                                await _route_media_reply_json(key, status=200, payload=payload0)
                                route_outcome = "media_files_replied"
                                return

                            if method in ("GET", "HEAD") and path_norm == "/media/runtime":
                                runtime0 = media_runtime_snapshot()
                                runtime0["ok"] = True
                                runtime0["proxy_limits"] = {
                                    "root_routed_response_limit_bytes": ROOT_ROUTED_MEDIA_BODY_LIMIT_BYTES,
                                    "root_media_relay_max_upload_bytes": MEDIA_RELAY_MAX_UPLOAD_BYTES,
                                }
                                runtime0["capabilities"] = media_capabilities()
                                runtime0["files"] = {
                                    "items": list_media_files(),
                                }
                                await _route_media_reply_json(key, status=200, payload=runtime0)
                                route_outcome = "media_runtime_replied"
                                return

                            media_indexer_content_prefixes = (
                                "/media/media-indexer/content/",
                                "/api/node/media-indexer/content/",
                            )
                            media_indexer_playback_id = ""
                            for _prefix in media_indexer_content_prefixes:
                                if method in ("GET", "HEAD") and path_norm.startswith(_prefix):
                                    media_indexer_playback_id = unquote(path_norm[len(_prefix):])
                                    break
                            if media_indexer_playback_id:
                                try:
                                    from adaos.services.media_indexer_library import resolve_media_indexer_content

                                    target, _payload = resolve_media_indexer_content(media_indexer_playback_id)
                                except ValueError as exc:
                                    await _route_media_reply_json(
                                        key,
                                        status=400,
                                        payload={"ok": False, "detail": str(exc)},
                                    )
                                    route_outcome = "media_indexer_content_bad_request"
                                    return
                                except PermissionError as exc:
                                    await _route_media_reply_json(
                                        key,
                                        status=403,
                                        payload={"ok": False, "detail": str(exc)},
                                    )
                                    route_outcome = "media_indexer_content_forbidden"
                                    return
                                except FileNotFoundError as exc:
                                    await _route_media_reply_json(
                                        key,
                                        status=404,
                                        payload={"ok": False, "detail": str(exc)},
                                    )
                                    route_outcome = "media_indexer_content_missing"
                                    return
                                await _route_media_reply_file(
                                    key,
                                    target=target,
                                    method=method,
                                    request_headers=headers,
                                )
                                route_outcome = "media_indexer_content_replied"
                                return

                            if method in ("GET", "HEAD") and path_norm.startswith("/media/files/content/"):
                                filename = unquote(path_norm[len("/media/files/content/"):])
                                try:
                                    target = media_file_path(filename)
                                except ValueError as exc:
                                    try:
                                        from adaos.services.media_indexer_library import resolve_media_indexer_content_by_name

                                        target, _payload = resolve_media_indexer_content_by_name(filename)
                                    except ValueError:
                                        await _route_media_reply_json(
                                            key,
                                            status=400,
                                            payload={"ok": False, "detail": str(exc)},
                                        )
                                        route_outcome = "media_content_bad_request"
                                        return
                                    except PermissionError as idx_exc:
                                        await _route_media_reply_json(
                                            key,
                                            status=403,
                                            payload={"ok": False, "detail": str(idx_exc)},
                                        )
                                        route_outcome = "media_indexer_content_forbidden"
                                        return
                                    except FileNotFoundError:
                                        await _route_media_reply_json(
                                            key,
                                            status=400,
                                            payload={"ok": False, "detail": str(exc)},
                                        )
                                        route_outcome = "media_content_bad_request"
                                        return
                                if not target.exists() or not target.is_file():
                                    try:
                                        from adaos.services.media_indexer_library import resolve_media_indexer_content_by_name

                                        target, _payload = resolve_media_indexer_content_by_name(filename)
                                    except ValueError as exc:
                                        await _route_media_reply_json(
                                            key,
                                            status=400,
                                            payload={"ok": False, "detail": str(exc)},
                                        )
                                        route_outcome = "media_content_bad_request"
                                        return
                                    except PermissionError as exc:
                                        await _route_media_reply_json(
                                            key,
                                            status=403,
                                            payload={"ok": False, "detail": str(exc)},
                                        )
                                        route_outcome = "media_indexer_content_forbidden"
                                        return
                                    except FileNotFoundError:
                                        await _route_media_reply_json(
                                            key,
                                            status=404,
                                            payload={"ok": False, "detail": "media_file_not_found"},
                                        )
                                        route_outcome = "media_content_missing"
                                        return
                                await _route_media_reply_file(
                                    key,
                                    target=target,
                                    method=method,
                                    request_headers=headers,
                                )
                                route_outcome = "media_content_replied"
                                return

                            if method == "DELETE" and path_norm.startswith("/media/files/"):
                                filename = unquote(path_norm[len("/media/files/"):])
                                try:
                                    target = media_file_path(filename)
                                except ValueError as exc:
                                    await _route_media_reply_json(
                                        key,
                                        status=400,
                                        payload={"ok": False, "detail": str(exc)},
                                    )
                                    route_outcome = "media_delete_bad_request"
                                    return
                                existed = target.exists()
                                if existed:
                                    target.unlink()
                                await _route_media_reply_json(
                                    key,
                                    status=200,
                                    payload={
                                        "ok": True,
                                        "filename": target.name,
                                        "deleted": existed,
                                        "items": list_media_files(),
                                    },
                                )
                                route_outcome = "media_delete_replied"
                                return

                            if method == "PUT" and path_norm.startswith("/media/files/"):
                                filename = unquote(path_norm[len("/media/files/"):])
                                try:
                                    target = media_file_path(filename)
                                except ValueError as exc:
                                    await _route_media_reply_json(
                                        key,
                                        status=400,
                                        payload={"ok": False, "detail": str(exc)},
                                    )
                                    route_outcome = "media_upload_bad_request"
                                    return
                                if content_length > int(MEDIA_RELAY_MAX_UPLOAD_BYTES):
                                    await _route_media_reply_json(
                                        key,
                                        status=413,
                                        payload={
                                            "ok": False,
                                            "detail": "media_upload_too_large",
                                            "max_upload_bytes": int(MEDIA_RELAY_MAX_UPLOAD_BYTES),
                                        },
                                    )
                                    route_outcome = "media_upload_too_large"
                                    return
                                tmp_path = target.with_name(
                                    f"{target.name}.relay-{os.getpid()}-{int(time.time() * 1000)}.part"
                                )
                                handle = tmp_path.open("wb")
                                media_relay_sessions[key] = {
                                    "mode": "upload",
                                    "target": target,
                                    "tmp_path": tmp_path,
                                    "handle": handle,
                                    "size_bytes": 0,
                                    "replaced": target.exists(),
                                    "mime_type": guess_media_type(target.name),
                                    "max_upload_bytes": int(MEDIA_RELAY_MAX_UPLOAD_BYTES),
                                }
                                route_outcome = "media_upload_open"
                                return

                            await _route_media_reply_json(
                                key,
                                status=404,
                                payload={"ok": False, "detail": "media_route_not_found"},
                            )
                            route_outcome = "media_not_found"
                            return
                        except Exception as e:
                            await _route_reply(
                                key,
                                {
                                    "t": "media_http_error",
                                    "status": 502,
                                    "error": "media_route_open_failed",
                                    "detail": str(e),
                                },
                            )
                            route_outcome = f"media_http_open_fail:{type(e).__name__}"
                            return

                    if t == "media_http_req_chunk":
                        session = media_relay_sessions.get(key)
                        if not isinstance(session, dict) or str(session.get("mode") or "") != "upload":
                            route_outcome = "media_chunk_without_session"
                            return
                        try:
                            b64 = (data or {}).get("data_b64")
                            if not isinstance(b64, str) or not b64:
                                route_outcome = "media_chunk_empty"
                                return
                            blob = base64.b64decode(b64.encode("ascii"))
                            size_bytes = int(session.get("size_bytes") or 0) + len(blob)
                            if size_bytes > int(session.get("max_upload_bytes") or 0):
                                await _route_media_reply_json(
                                    key,
                                    status=413,
                                    payload={
                                        "ok": False,
                                        "detail": "media_upload_too_large",
                                        "max_upload_bytes": int(session.get("max_upload_bytes") or 0),
                                    },
                                )
                                _cleanup_media_relay_session(key, remove_temp=True)
                                route_outcome = "media_chunk_too_large"
                                return
                            handle = session.get("handle")
                            if not handle:
                                route_outcome = "media_chunk_no_handle"
                                return
                            handle.write(blob)
                            session["size_bytes"] = size_bytes
                            route_outcome = "media_chunk_written"
                        except Exception as e:
                            _cleanup_media_relay_session(key, remove_temp=True)
                            await _route_reply(
                                key,
                                {
                                    "t": "media_http_error",
                                    "status": 502,
                                    "error": "media_upload_write_failed",
                                    "detail": str(e),
                                },
                            )
                            route_outcome = f"media_chunk_fail:{type(e).__name__}"
                        return

                    if t == "media_http_req_end":
                        session = media_relay_sessions.get(key)
                        if not isinstance(session, dict) or str(session.get("mode") or "") != "upload":
                            route_outcome = "media_end_without_session"
                            return
                        try:
                            handle = session.get("handle")
                            if handle:
                                handle.close()
                            target = Path(session.get("target"))
                            tmp_path = Path(session.get("tmp_path"))
                            tmp_path.replace(target)
                            _cleanup_media_relay_session(key, remove_temp=False)
                            await _route_media_reply_json(
                                key,
                                status=200,
                                payload={
                                    "ok": True,
                                    "filename": target.name,
                                    "size_bytes": int(session.get("size_bytes") or 0),
                                    "mime_type": str(session.get("mime_type") or ""),
                                    "replaced": bool(session.get("replaced")),
                                },
                            )
                            route_outcome = "media_upload_done"
                        except Exception as e:
                            _cleanup_media_relay_session(key, remove_temp=True)
                            await _route_reply(
                                key,
                                {
                                    "t": "media_http_error",
                                    "status": 502,
                                    "error": "media_upload_finalize_failed",
                                    "detail": str(e),
                                },
                            )
                            route_outcome = f"media_end_fail:{type(e).__name__}"
                        return

                    if t == "media_http_abort":
                        _cleanup_media_relay_session(key, remove_temp=True)
                        route_outcome = "media_http_abort"
                        return

                    if t == "http_req_open":
                        try:
                            from adaos.services.media_library import (
                                ROOT_MEDIA_RELAY_MAX_UPLOAD_BYTES as HTTP_RELAY_MAX_UPLOAD_BYTES,
                            )

                            method = str((data or {}).get("method") or "GET").upper()
                            path = str((data or {}).get("path") or "/api/ping")
                            search = str((data or {}).get("search") or "")
                            headers = (data or {}).get("headers") or {}
                            content_length = int((data or {}).get("content_length") or 0)
                            max_upload_bytes = int(HTTP_RELAY_MAX_UPLOAD_BYTES)
                            if content_length > max_upload_bytes:
                                await _route_reply(
                                    key,
                                    {
                                        "t": "http_resp",
                                        "status": 413,
                                        "headers": {"content-type": "application/json"},
                                        "body_b64": base64.b64encode(
                                            _json.dumps(
                                                {
                                                    "ok": False,
                                                    "detail": "http_upload_too_large",
                                                    "max_upload_bytes": max_upload_bytes,
                                                },
                                                ensure_ascii=False,
                                            ).encode("utf-8")
                                        ).decode("ascii"),
                                        "truncated": False,
                                    },
                                )
                                route_outcome = "http_req_open_too_large"
                                return
                            relay_dir = Path(tempfile.gettempdir()) / "adaos-route-http"
                            relay_dir.mkdir(parents=True, exist_ok=True)
                            tmp_path = relay_dir / f"{key}.{os.getpid()}.{int(time.time() * 1000)}.body"
                            handle = tmp_path.open("wb")
                            http_body_relay_sessions[key] = {
                                "method": method,
                                "path": path,
                                "search": search,
                                "headers": headers,
                                "content_length": content_length,
                                "tmp_path": tmp_path,
                                "handle": handle,
                                "size_bytes": 0,
                                "max_upload_bytes": max_upload_bytes,
                            }
                            route_outcome = "http_req_open"
                        except Exception as e:
                            _cleanup_http_body_relay_session(key, remove_temp=True)
                            await _route_reply(
                                key,
                                {
                                    "t": "http_resp",
                                    "status": 502,
                                    "headers": {},
                                    "body_b64": "",
                                    "truncated": False,
                                    "err": f"http_req_open_failed: {e}",
                                },
                            )
                            route_outcome = f"http_req_open_fail:{type(e).__name__}"
                        return

                    if t == "http_req_chunk":
                        session = http_body_relay_sessions.get(key)
                        if not isinstance(session, dict):
                            route_outcome = "http_req_chunk_without_session"
                            return
                        try:
                            b64 = (data or {}).get("data_b64")
                            if not isinstance(b64, str) or not b64:
                                route_outcome = "http_req_chunk_empty"
                                return
                            blob = base64.b64decode(b64.encode("ascii"))
                            size_bytes = int(session.get("size_bytes") or 0) + len(blob)
                            if size_bytes > int(session.get("max_upload_bytes") or 0):
                                _cleanup_http_body_relay_session(key, remove_temp=True)
                                await _route_reply(
                                    key,
                                    {
                                        "t": "http_resp",
                                        "status": 413,
                                        "headers": {"content-type": "application/json"},
                                        "body_b64": base64.b64encode(
                                            _json.dumps(
                                                {
                                                    "ok": False,
                                                    "detail": "http_upload_too_large",
                                                    "max_upload_bytes": int(session.get("max_upload_bytes") or 0),
                                                },
                                                ensure_ascii=False,
                                            ).encode("utf-8")
                                        ).decode("ascii"),
                                        "truncated": False,
                                    },
                                )
                                route_outcome = "http_req_chunk_too_large"
                                return
                            handle = session.get("handle")
                            if not handle:
                                route_outcome = "http_req_chunk_no_handle"
                                return
                            handle.write(blob)
                            session["size_bytes"] = size_bytes
                            route_outcome = "http_req_chunk_written"
                        except Exception as e:
                            _cleanup_http_body_relay_session(key, remove_temp=True)
                            await _route_reply(
                                key,
                                {
                                    "t": "http_resp",
                                    "status": 502,
                                    "headers": {},
                                    "body_b64": "",
                                    "truncated": False,
                                    "err": f"http_req_chunk_failed: {e}",
                                },
                            )
                            route_outcome = f"http_req_chunk_fail:{type(e).__name__}"
                        return

                    if t == "http_req_end":
                        session = http_body_relay_sessions.get(key)
                        if not isinstance(session, dict):
                            route_outcome = "http_req_end_without_session"
                            return
                        try:
                            handle = session.get("handle")
                            if handle:
                                handle.close()
                                session["handle"] = None
                            tmp_path = Path(session.get("tmp_path"))
                            method = str(session.get("method") or "GET").upper()
                            path = str(session.get("path") or "/api/ping")
                            search = str(session.get("search") or "")
                            headers = session.get("headers") or {}
                            size_bytes = int(session.get("size_bytes") or 0)

                            def _do_streamed_http() -> dict[str, Any]:
                                try:
                                    return _route_local_http_request(
                                        method=method,
                                        path=path,
                                        search=search,
                                        headers=headers,
                                        body=tmp_path,
                                        content_length=size_bytes,
                                    )
                                except Exception as e:
                                    return {
                                        "t": "http_resp",
                                        "status": 502,
                                        "headers": {},
                                        "body_b64": "",
                                        "truncated": False,
                                        "err": str(e),
                                    }

                            resp = await asyncio.to_thread(_do_streamed_http)
                            _cleanup_http_body_relay_session(key, remove_temp=True)
                            await _route_reply(
                                key,
                                resp,
                                resend_http_resp=_hub_route_should_resend_http_resp(path),
                            )
                            route_outcome = f"http_req_replied:{resp.get('status')}"
                        except Exception as e:
                            _cleanup_http_body_relay_session(key, remove_temp=True)
                            await _route_reply(
                                key,
                                {
                                    "t": "http_resp",
                                    "status": 502,
                                    "headers": {},
                                    "body_b64": "",
                                    "truncated": False,
                                    "err": f"http_req_end_failed: {e}",
                                },
                            )
                            route_outcome = f"http_req_end_fail:{type(e).__name__}"
                        return

                    if t == "http_req_abort":
                        _cleanup_http_body_relay_session(key, remove_temp=True)
                        route_outcome = "http_req_abort"
                        return

                    if t == "http":
                        route_outcome = "http"
                        method = str((data or {}).get("method") or "GET").upper()
                        path = str((data or {}).get("path") or "/api/ping")
                        # Be tolerant: root might send trailing slashes.
                        path_norm = (path.rstrip("/") or "/") if isinstance(path, str) else "/"
                        search = str((data or {}).get("search") or "")
                        headers = (data or {}).get("headers") or {}
                        body_b64 = (data or {}).get("body_b64")

                        # Root continuously probes `/api/node/status` (and `/api/ping`) with a short timeout
                        # to decide whether the hub is reachable. When the hub is under load (YJS/WebRTC
                        # init) the local HTTP stack may respond slowly, and root will surface
                        # `hub_unreachable` / `yjs_sync_timeout`.
                        #
                        # Return these probe endpoints inline (no local HTTP) so the browser can log in
                        # even when the hub API is busy.
                        try:
                            if method in ("GET", "HEAD") and path_norm in ("/api/node/status", "/api/ping", "/healthz"):
                                if path_norm == "/api/node/status":
                                    try:
                                        cfg = getattr(service.ctx, "config", None) or load_config(ctx=service.ctx)
                                    except Exception:
                                        cfg = load_config(ctx=service.ctx)
                                    payload0 = {
                                        "node_id": str(getattr(cfg, "node_id", "") or ""),
                                        "subnet_id": str(getattr(cfg, "subnet_id", "") or ""),
                                        "role": str(getattr(cfg, "role", "") or ""),
                                        "ready": bool(is_ready()),
                                    }
                                    supervisor_runtime = _hub_route_node_status_supervisor_runtime(service.ctx)
                                    core_update_status = supervisor_runtime.get("status")
                                    payload0["runtime"] = {
                                        "supervisor_available": bool(supervisor_runtime.get("available")),
                                        "supervisor_runtime": supervisor_runtime,
                                        "core_update_status": core_update_status if isinstance(core_update_status, dict) else {},
                                    }
                                else:
                                    payload0 = {"ok": True, "ts": time.time()}
                                raw = _json.dumps(payload0, ensure_ascii=False).encode("utf-8")
                                resp = {
                                    "t": "http_resp",
                                    "status": 200,
                                    "headers": {"content-type": "application/json"},
                                    "body_b64": base64.b64encode(raw).decode("ascii"),
                                    "truncated": False,
                                }
                                try:
                                    await _route_reply(key, resp, resend_http_resp=True)
                                    route_outcome = "http_inline_probe_replied"
                                except Exception:
                                    pass
                                try:
                                    if _route_diag:
                                        _rl_log(
                                            "hub-route.inline_probe",
                                            f"[hub-route] http inline ok path={path_norm} key={key}",
                                            every_s=5.0,
                                        )
                                except Exception:
                                    pass
                                return
                        except Exception:
                            pass

                        def _do_http() -> dict[str, Any]:
                            try:
                                if _hub_route_prefers_supervisor_public_status(path_norm, method) and _dev_without_supervisor():
                                    from adaos.services.core_update import (
                                        read_public_update_status as _read_public_update_status,
                                    )

                                    payload = _read_public_update_status()
                                    raw = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
                                    return {
                                        "t": "http_resp",
                                        "status": 200,
                                        "headers": {"content-type": "application/json; charset=utf-8"},
                                        "body_b64": base64.b64encode(raw).decode("ascii"),
                                        "truncated": False,
                                    }
                                import requests  # type: ignore

                                try:
                                    from adaos.services.node_config import load_config

                                    cfg = getattr(service.ctx, "config", None) or load_config(ctx=service.ctx)
                                    # IMPORTANT: Route-proxy HTTP requests must target the local hub instance,
                                    # not the public Root proxy URL that might be stored in node.yaml as hub_url.
                                    bases = _build_hub_route_http_bases(
                                        path_norm=path_norm,
                                        method=method,
                                        cfg=cfg,
                                        ctx=service.ctx,
                                    )
                                    token_local = getattr(cfg, "token", None) or os.getenv("ADAOS_TOKEN", "") or None
                                except Exception:
                                    bases = _build_hub_route_http_bases(
                                        path_norm=path_norm,
                                        method=method,
                                        cfg=None,
                                        ctx=service.ctx,
                                    )
                                    token_local = os.getenv("ADAOS_TOKEN", "") or None

                                # Add optional target/core port fallback for local setups.
                                try:
                                    from urllib.parse import urlparse

                                    u0 = urlparse(bases[0])
                                    h0 = u0.hostname or "127.0.0.1"
                                    p0 = u0.port
                                    scheme0 = u0.scheme or "http"
                                    alt_port_raw = os.getenv("ADAOS_TARGET_PORT") or os.getenv("ADAOS_CORE_PORT") or ""
                                    alt_port = int(alt_port_raw) if alt_port_raw.strip() else 8788
                                    if (p0 in (None, 8777)) and alt_port and alt_port != p0:
                                        bases.append(f"{scheme0}://{h0}:{alt_port}")
                                except Exception:
                                    pass

                                url = f"{bases[0]}{path}{search}"
                                if _route_verbose and path not in ("/api/node/status", "/api/ping"):
                                    try:
                                        _route_log(f"[hub-route] http upstream url={url}")
                                    except Exception:
                                        pass
                                body = None
                                if isinstance(body_b64, str) and body_b64:
                                    try:
                                        body = base64.b64decode(body_b64.encode("ascii"))
                                    except Exception:
                                        body = None
                                # Minimal header allowlist.
                                h2: dict[str, str] = {}
                                if token_local:
                                    h2["X-AdaOS-Token"] = str(token_local)
                                if isinstance(headers, dict):
                                    ct = headers.get("content-type") or headers.get("Content-Type")
                                    if isinstance(ct, str) and ct:
                                        h2["Content-Type"] = ct
                                # Do not inherit HTTP(S)_PROXY environment from the host/container:
                                # local hub calls must stay local, otherwise they can hang on a proxy.
                                def _do_http_upstream() -> dict[str, Any]:
                                    sess = requests.Session()
                                    try:
                                        try:
                                            sess.trust_env = False
                                        except Exception:
                                            pass
                                        last_exc: Exception | None = None
                                        resp = None
                                        for base in bases:
                                            url_try = f"{base}{path}{search}"
                                            try:
                                                # Root times out fairly quickly while waiting for
                                                # route.to_browser.* replies. Keep local proxy attempts
                                                # short and, critically, run them off the event loop
                                                # thread because the local hub HTTP server lives in this
                                                # same process.
                                                timeout = _hub_route_local_http_timeout(path)
                                                resp = sess.request(method, url_try, data=body, headers=h2, timeout=timeout)
                                                last_exc = None
                                                break
                                            except Exception as e:
                                                last_exc = e
                                                if _route_verbose:
                                                    try:
                                                        print(
                                                            f"[hub-route] http upstream failed url={url_try}: {type(e).__name__}: {e}"
                                                        )
                                                    except Exception:
                                                        pass
                                                if not _hub_route_should_retry_http_upstream_error(
                                                    method=method,
                                                    path=path,
                                                    error_kind=type(e).__name__,
                                                    body=body,
                                                ):
                                                    break
                                        if resp is None:
                                            raise last_exc or RuntimeError("http upstream failed")
                                        raw = resp.content or b""
                                        limit = 2 * 1024 * 1024
                                        truncated = len(raw) > limit
                                        if truncated:
                                            raw = raw[:limit]
                                        out_headers: dict[str, str] = {}
                                        try:
                                            cth = resp.headers.get("content-type")
                                            if cth:
                                                out_headers["content-type"] = cth
                                        except Exception:
                                            pass
                                        return {
                                            "t": "http_resp",
                                            "status": int(resp.status_code),
                                            "headers": out_headers,
                                            "body_b64": base64.b64encode(raw).decode("ascii"),
                                            "truncated": truncated,
                                        }
                                    finally:
                                        try:
                                            sess.close()
                                        except Exception:
                                            pass

                                return _do_http_upstream()
                            except Exception as e:
                                return {"t": "http_resp", "status": 502, "headers": {}, "body_b64": "", "err": str(e)}

                        resp = await asyncio.to_thread(_do_http)
                        route_outcome = f"http_local_done:{resp.get('status')}"
                        if _route_http_trace:
                            try:
                                _route_log(
                                    f"[hub-route] http.local.done key={_key_tag(key)} status={resp.get('status')} err={resp.get('err')} truncated={resp.get('truncated')}"
                                )
                            except Exception:
                                pass
                        try:
                            await _route_reply(
                                key,
                                resp,
                                resend_http_resp=_hub_route_should_resend_http_resp(path),
                            )
                            route_outcome = f"http_replied:{resp.get('status')}"
                        except Exception:
                            pass
                        return
                    # Unknown route message type: for HTTP keys, reply with an error so Root does not time out.
                    try:
                        route_outcome = f"unsupported_t:{t}"
                        if is_http_key:
                            await _route_reply(
                                key,
                                {
                                    "t": "http_resp",
                                    "status": 502,
                                    "headers": {},
                                    "body_b64": "",
                                    "truncated": False,
                                    "err": f"unsupported_t:{t}",
                                },
                            )
                    except Exception:
                        pass
                    return
                except Exception as e:
                    route_outcome = f"handler_failed:{type(e).__name__}"
                    if _route_verbose:
                        try:
                            _route_log(f"[hub-route] handler failed key={key}: {type(e).__name__}: {e}")
                        except Exception:
                            pass
                    # Avoid pure timeouts for HTTP keys; surface an error response instead.
                    try:
                        if is_http_key and key:
                            await _route_reply(
                                key,
                                {
                                    "t": "http_resp",
                                    "status": 502,
                                    "headers": {},
                                    "body_b64": "",
                                    "truncated": False,
                                    "err": f"handler_failed:{type(e).__name__}",
                                },
                            )
                    except Exception:
                        pass
                finally:
                    took_ms = (time.monotonic() - route_started) * 1000.0
                    if http_path:
                        try:
                            observe_route_e2e(
                                details={
                                    f"last_http_{http_kind or 'app'}_reply_at": time.time(),
                                    "last_http_reply_path": http_path,
                                    "last_http_reply_method": http_method or "",
                                    "last_http_reply_took_ms": round(took_ms, 1),
                                    "last_http_reply_outcome": route_outcome,
                                    "last_http_reply_key_tag": _key_tag(key),
                                }
                            )
                        except Exception:
                            pass

                        # Detect "late replies" relative to the Root route proxy timeouts.
                        # This is an end-to-end signal: Root likely already timed out waiting.
                        try:
                            expected_timeout_ms = 15000
                            if http_path in ("/api/node/status", "/api/ping", "/healthz"):
                                expected_timeout_ms = 6500
                            elif http_path == "/api/tools/call":
                                expected_timeout_ms = 60000
                            # Give a small buffer to avoid false positives around the edge.
                            if (
                                http_kind == "app"
                                and expected_timeout_ms > 0
                                and took_ms >= float(expected_timeout_ms) * 0.98
                            ):
                                note_route_incident(
                                    status="late_reply",
                                    summary="hub route reply exceeded root proxy timeout",
                                    details={
                                        "path": http_path,
                                        "method": http_method or "",
                                        "took_ms": round(took_ms, 1),
                                        "expected_timeout_ms": int(expected_timeout_ms),
                                        "key_tag": _key_tag(key),
                                        "outcome": route_outcome,
                                    },
                                )
                                observe_route_e2e(
                                    details={
                                        "last_http_app_late_reply_at": time.time(),
                                        "last_http_app_late_reply_details": {
                                            "path": http_path,
                                            "method": http_method or "",
                                            "took_ms": round(took_ms, 1),
                                            "expected_timeout_ms": int(expected_timeout_ms),
                                            "key_tag": _key_tag(key),
                                            "outcome": route_outcome,
                                        },
                                    }
                                )
                        except Exception:
                            pass

                    if _route_http_trace and key:
                        try:
                            _route_log(
                                f"[hub-route] cb.done key={_key_tag(key)} subj={subject} t={route_t} outcome={route_outcome} took_ms={took_ms:.1f}"
                            )
                        except Exception:
                            pass
                    return

            class _QueuedRouteMsg:
                __slots__ = ("subject", "data")

                def __init__(service, subject: str, data: bytes) -> None:
                    service.subject = subject
                    service.data = data

            try:
                route_handler_queue_max = int(os.getenv("HUB_ROUTE_HANDLER_QUEUE_MAX", "4096") or "4096")
            except Exception:
                route_handler_queue_max = 4096
            if route_handler_queue_max < 128:
                route_handler_queue_max = 128
            route_handler_queue: asyncio.Queue[tuple[str, bytes]] = asyncio.Queue(maxsize=route_handler_queue_max)
            route_diag_state["dispatch_queue_max"] = int(route_handler_queue_max)

            def _route_key_from_subject(subject: str) -> str:
                try:
                    parts = subject.split(".")
                    if subject.startswith("route.v2.to_hub.") and len(parts) >= 5:
                        return str(parts[4] or "")
                    if subject.startswith("route.to_hub.") and len(parts) >= 3:
                        return str(parts[2] or "")
                except Exception:
                    pass
                return ""

            async def _route_handler_worker() -> None:
                while True:
                    subject, raw = await route_handler_queue.get()
                    started0 = time.monotonic()
                    key0 = _route_key_from_subject(subject)
                    try:
                        route_diag_state["dispatch_queue_size"] = int(route_handler_queue.qsize())
                        route_diag_state["last_dispatch_key_tag"] = _key_tag(key0) if key0 else ""
                        await _route_handle_msg(_QueuedRouteMsg(subject, raw))
                        route_diag_state["dispatch_handled_total"] = int(route_diag_state.get("dispatch_handled_total") or 0) + 1
                    except asyncio.CancelledError:
                        raise
                    except Exception as e:
                        try:
                            service._log.warning(
                                "hub route handler failed subject=%s type=%s err=%s",
                                subject,
                                type(e).__name__,
                                e,
                            )
                        except Exception:
                            pass
                    finally:
                        try:
                            took_ms = (time.monotonic() - started0) * 1000.0
                            route_diag_state["last_dispatch_ms"] = round(took_ms, 1)
                            if took_ms >= 250.0:
                                route_diag_state["dispatch_slow_total"] = int(route_diag_state.get("dispatch_slow_total") or 0) + 1
                                route_diag_state["last_dispatch_slow_ms"] = round(took_ms, 1)
                        except Exception:
                            pass
                        try:
                            route_handler_queue.task_done()
                        except Exception:
                            pass
                        try:
                            route_diag_state["dispatch_queue_size"] = int(route_handler_queue.qsize())
                        except Exception:
                            pass

            async def _route_cb(msg) -> None:
                try:
                    subject = str(getattr(msg, "subject", "") or "")
                except Exception:
                    subject = ""
                try:
                    raw = bytes(getattr(msg, "data", b"") or b"")
                except Exception:
                    raw = b""
                try:
                    route_handler_queue.put_nowait((subject, raw))
                    route_diag_state["dispatch_enqueued_total"] = int(route_diag_state.get("dispatch_enqueued_total") or 0) + 1
                    route_diag_state["dispatch_queue_size"] = int(route_handler_queue.qsize())
                except asyncio.QueueFull:
                    key0 = _route_key_from_subject(subject)
                    route_diag_state["dispatch_drop_total"] = int(route_diag_state.get("dispatch_drop_total") or 0) + 1
                    route_diag_state["dispatch_queue_size"] = int(route_handler_queue.qsize())
                    route_diag_state["last_dispatch_key_tag"] = _key_tag(key0) if key0 else ""
                    try:
                        _rl_log(
                            "hub-route.dispatch_queue_full",
                            (
                                "[hub-route] dispatch queue full "
                                f"qsize={route_handler_queue.qsize()} max={route_handler_queue_max} key={_key_tag(key0)}"
                            ),
                            every_s=1.0,
                        )
                    except Exception:
                        pass
                    try:
                        note_route_incident(
                            status="dispatch_queue_full",
                            summary="hub route handler queue is full; dropping inbound route frame",
                            details={
                                "key_tag": _key_tag(key0),
                                "queue_size": int(route_handler_queue.qsize()),
                                "queue_max": int(route_handler_queue_max),
                            },
                        )
                    except Exception:
                        pass

            try:
                route_handler_task = asyncio.create_task(
                    _route_handler_worker(),
                    name="adaos-hub-route-handler",
                )
                sub_workers.append(route_handler_task)
            except Exception:
                pass

            try:
                # Legacy v1 subject. Disabled by default because it cannot be isolated by hub id,
                # so it allows cross-hub route traffic and can cause hard-to-debug flaps.
                if os.getenv("HUB_ROUTE_V1", "0") == "1":
                    route_sub = await _sub("route.to_hub.*", cb=_route_cb)
            except Exception:
                route_sub = None
            try:
                # v2: route.v2.to_hub.<hubId>.<key>
                route_sub_v2 = await _sub(f"route.v2.to_hub.{hub_id}.*", cb=_route_cb)
            except Exception:
                route_sub_v2 = None
            if route_sub_v2 is None:
                raise RuntimeError("hub route v2 subscription was not installed")
            if hub_nats_verbose or not hub_nats_quiet:
                if route_sub is not None:
                    print("[hub-io] NATS subscribe route.to_hub.* (hub route proxy, legacy v1)")
                print(f"[hub-io] NATS subscribe route.v2.to_hub.{hub_id}.* (hub route proxy)")
            try:
                if route_sub is not None:
                    service._log.info("nats bridge subscribed subject=route.to_hub.* (legacy v1)")
                service._log.info("nats bridge subscribed subject=route.v2.to_hub.%s.*", hub_id)
            except Exception:
                pass
            try:
                mark_route_ready(
                    summary="hub route relay subscription installed",
                    details={
                        "subjects": [
                            f"route.v2.to_hub.{hub_id}.*",
                            *(
                                ["route.to_hub.*"]
                                if route_sub is not None
                                else []
                            ),
                        ]
                    },
                )
            except Exception:
                pass
            service._mark_hub_root_authority_ready()
            try:
                _update_route_protocol_runtime()
            except Exception:
                pass
        except Exception as e:
            # Do not fail the whole IO stack: this is an optional fallback used only when
            # browser connects through Root (api.inimatic.com) and needs a NATS tunnel.
            if candidate_passive_mode and str(e) == "candidate runtime keeps root route relay passive until cutover":
                try:
                    service._log.info(
                        "nats route relay kept passive for candidate runtime hub_id=%s instance=%s",
                        hub_id,
                        runtime_instance,
                    )
                except Exception:
                    pass
                e = None
            try:
                current_route_reset = getattr(service, "_hub_root_route_reset", None)
                if current_route_reset is _reset_route_runtime:
                    setattr(service, "_hub_root_route_reset", None)
            except Exception:
                pass
            if e is not None:
                try:
                    if os.getenv("HUB_ROUTE_VERBOSE", "0") == "1" or os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                        print(f"[hub-io] NATS route proxy init failed: {type(e).__name__}: {e}")
                        try:
                            tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
                            print(tb.rstrip())
                        except Exception:
                            pass
                    else:
                        print(f"[hub-io] NATS route proxy disabled: {type(e).__name__}: {e}")
                except Exception:
                    pass
                try:
                    mark_route_degraded(
                        summary=f"hub route relay initialization failed ({type(e).__name__})",
                        details={"error": str(e)},
                    )
                except Exception:
                    pass


    async def close(self) -> None:
        service = self._service
        try:
            current_route_reset = getattr(service, "_hub_root_route_reset", None)
            if current_route_reset is self.reset_callback:
                setattr(service, "_hub_root_route_reset", None)
        except Exception:
            pass
        try:
            tunnel_cleanup = await _close_route_tunnels_bounded(self.tunnels, timeout_s=1.0)
            if int(tunnel_cleanup.get("failed_or_timed_out") or 0) > 0:
                service._log.warning(
                    "nats route tunnel cleanup bounded hub_id=%s attempted=%s completed=%s failed_or_timed_out=%s",
                    self.hub_id,
                    tunnel_cleanup.get("attempted"),
                    tunnel_cleanup.get("completed"),
                    tunnel_cleanup.get("failed_or_timed_out"),
                )
        except Exception:
            pass
        try:
            for key, task in list(self.tunnel_tasks.items()):
                try:
                    task.cancel()
                except Exception:
                    pass
                self.tunnel_tasks.pop(key, None)
        except Exception:
            pass
