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
from adaos.services.bootstrap_runtime.nats_credentials import NatsCredentialService
from adaos.services.bootstrap_runtime.route_tunnel_runtime import NatsRouteTunnelRuntime
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


def _sidecar_tail_log_each_enabled() -> bool:
    return str(os.getenv("ADAOS_SIDECAR_TAIL_LOG_EACH") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _sidecar_tail_summary(lines: list[str], *, max_chars: int = 480) -> str:
    nonempty = [str(line or "").strip() for line in lines if str(line or "").strip()]
    if not nonempty:
        return "empty"
    last = nonempty[-1]
    try:
        payload = _json.loads(last)
    except Exception:
        payload = None
    if isinstance(payload, dict):
        fields = []
        for name in (
            "session_id",
            "active_session",
            "remote_connect_retrying",
            "last_error",
            "last_remote_connect_error",
        ):
            value = payload.get(name)
            if value is None or value == "":
                continue
            fields.append(f"{name}={value}")
        if fields:
            last = " ".join(fields)
    limit = max(120, int(max_chars or 480))
    if len(last) > limit:
        last = f"{last[:limit]}..."
    return last


async def _run_nats_root_transport(
    service: Any,
    *,
    core_bus: Any,
    startup_stage_mark: Any,
    report_control_lifecycle: Any,
) -> None:
    """Start the composed hub-root NATS and route runtime.

    The Bootstrap service owns composition and lifecycle only. Long-lived
    transport loops live here and consume the policies composed by Bootstrap.
    """
    _read_sidecar_tail_lines = service._nats_policy.read_sidecar_tail_lines
    _nats_credentials_refresh_evidence = service._nats_policy.credentials_refresh_evidence
    _hub_root_transport_kind = service._nats_policy.transport_kind
    _hub_root_candidate_passive_mode = service._nats_policy.candidate_passive_mode
    _should_quarantine_nats_candidate = service._nats_policy.should_quarantine_candidate
    _hub_nats_sidecar_failover_on_transient = service._nats_policy.sidecar_failover_on_transient
    _hub_nats_sidecar_quarantine_s = service._nats_policy.sidecar_quarantine_s
    _resolve_nats_log_server = service._nats_policy.resolve_log_server
    _canonical_hub_nats_identity = service._nats_policy.canonical_identity
    _hub_route_max_chunk_raw_bytes = service._route_policy.max_chunk_raw_bytes
    _hub_route_normalize_resend_chunk_indexes = service._route_policy.normalize_resend_chunk_indexes
    _hub_route_semantic_flow_for_path = service._route_policy.semantic_flow_for_path
    _hub_route_should_shed_sync_frame = service._route_policy.should_shed_sync_frame
    _hub_route_sync_frame_force_flush_enabled = service._route_policy.sync_frame_force_flush_enabled
    _hub_route_should_force_flush_reply = service._route_policy.should_force_flush_reply
    _hub_route_subnet_sync_payload_type = service._route_policy.subnet_sync_payload_type
    _hub_route_should_drop_subnet_sync_frame = service._route_policy.should_drop_subnet_sync_frame
    _hub_route_prefers_supervisor_public_status = service._route_policy.prefers_supervisor_public_status
    _hub_route_local_http_timeout = service._route_policy.local_http_timeout
    _hub_route_should_retry_http_upstream_error = service._route_policy.should_retry_http_upstream_error
    _hub_route_parse_resend_delays = service._route_policy.parse_resend_delays
    _hub_route_should_resend_http_resp = service._route_policy.should_resend_http_resp
    _build_hub_route_http_bases = service._route_policy.build_http_bases
    _build_hub_route_ws_bases = service._route_policy.build_ws_bases
    _hub_route_force_close_no_upstream_s = service._route_policy.force_close_no_upstream_s
    is_ready = service.is_ready
    try:
        _bridge_setup_started = startup_stage_mark("bootstrap_inbound_bridge_setup")
        # Hot-reload friendly: read persisted runtime NATS config on every connect attempt.
        hub_id = load_config(ctx=service.ctx).subnet_id
        if hub_id:
            try:
                if os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                    print(f"[hub-io] nats init: hub_id={hub_id}")
            except Exception:
                pass

            # Track connectivity state to log/emit only on transitions
            reported_down = False
            nats_last_log_at: dict[str, float] = {}
            nats_last_ok_at: float | None = None
            # Track flaky NATS WS endpoints and temporarily avoid them after short transient drops.
            nats_server_quarantine_until: dict[str, float] = {}
            nats_last_server: str | None = None
            nats_attempt_server: str | None = None

            def _rl_log(key: str, msg: str, *, every_s: float = 5.0) -> None:
                """
                Rate-limited console log helper for noisy NATS diagnostics.
                Uses monotonic time to avoid being affected by clock changes.
                """
                try:
                    if not _hub_channel_console_allow_rl(key, msg):
                        return
                    now = time.monotonic()
                    last = nats_last_log_at.get(key, 0.0)
                    if now - last < every_s:
                        return
                    nats_last_log_at[key] = now
                    print(msg)
                except Exception:
                    return

            credentials = NatsCredentialService(service, hub_id=hub_id)

            def _read_node_nats() -> tuple[str | None, str | None, str | None]:
                return credentials.read()

            async def _fetch_nats_credentials() -> bool:
                nonlocal hub_id
                fetched = await credentials.fetch()
                if credentials.hub_id:
                    hub_id = credentials.hub_id
                return fetched

            # Correlate hub-side NATS WS sessions with root-side ws-nats-proxy logs + optionally snapshot root logs.
            ws_connect_tag: str | None = None
            established_ws_tag: str | None = None
            last_root_snapshot_at: float | None = None
            last_ws_transport: str | None = None

            async def _nats_bridge() -> None:
                nonlocal hub_id
                nonlocal reported_down
                nonlocal nats_last_ok_at
                nonlocal nats_attempt_server
                nonlocal nats_last_server
                nonlocal last_ws_transport
                backoff = 1.0
                trace = os.getenv("HUB_NATS_TRACE", "0") == "1"
                runtime_identity = runtime_identity_snapshot()
                runtime_role = str(runtime_identity.get("transition_role") or "active")
                runtime_instance = str(runtime_identity.get("runtime_instance_id") or "")
                candidate_passive_mode = service._nats_policy.candidate_passive_mode()
                if trace or os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                    try:
                        import asyncio as _asyncio

                        policy = _asyncio.get_event_loop_policy()
                        try:
                            loop = _asyncio.get_running_loop()
                        except RuntimeError:
                            loop = None
                            _rl_log(
                                "loop.info",
                                f"[hub-io] asyncio loop policy={type(policy).__name__} loop={type(loop).__name__ if loop else None} role={runtime_role} instance={runtime_instance}",
                                every_s=3600.0,
                            )
                        if loop is not None and os.name == "nt" and "Selector" in type(loop).__name__:
                            _rl_log(
                                "loop.warn",
                                "[hub-io] Windows Selector event loop detected; NATS-over-WS may stall on PUB load. Prefer default Proactor loop and only set ADAOS_WIN_SELECTOR_LOOP=1 for targeted diagnostics.",
                                every_s=3600.0,
                            )
                    except Exception:
                        pass
                raw_keepalive_task: asyncio.Task | None = None
                try:
                    realtime_enabled = realtime_sidecar_enabled(
                        role=str(getattr(service.ctx.config, "role", "") or "").strip().lower()
                    )
                except Exception:
                    realtime_enabled = False
                try:
                    realtime_remote_candidates = resolve_realtime_remote_candidates() if realtime_enabled else []
                except Exception:
                    realtime_remote_candidates = []
                # Best-effort outbox for telegram replies when NATS is flapping.
                try:
                    if not hasattr(service, "_tg_output_pending"):
                        setattr(service, "_tg_output_pending", load_outbox_items("telegram"))
                    setattr(service, "_tg_output_persist_path", outbox_store_path("telegram"))
                except Exception:
                    try:
                        setattr(service, "_tg_output_pending", deque())
                    except Exception:
                        pass
                if realtime_enabled and realtime_remote_candidates:
                    last_ws_transport = "sidecar"
                    if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                        _rl_log(
                            "nats.ws_transport",
                            f"[hub-io] nats ws transport: sidecar (internal WS client disabled, local={realtime_sidecar_local_url()})",
                            every_s=3600.0,
                        )
                else:
                    # NATS WS transport: use `websockets` (avoid aiohttp WS flaps under PUB load).
                    try:
                        from adaos.services.nats_ws_transport import install_nats_ws_transport_patch

                        ws_transport = install_nats_ws_transport_patch(verbose=False)
                        last_ws_transport = ws_transport
                        if (os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace) and ws_transport:
                            _rl_log(
                                "nats.ws_transport",
                                f"[hub-io] nats ws transport: {ws_transport}",
                                every_s=3600.0,
                            )
                    except Exception as _patch_e:
                        if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                            _rl_log(
                                "nats.ws_transport_patch_err",
                                f"[hub-io] nats ws transport patch error: {type(_patch_e).__name__}: {_patch_e}",
                                every_s=5.0,
                            )

                def _explain_connect_error(err: Exception) -> str:
                    try:
                        msg = str(err) or ""
                        low = msg.lower()
                        if isinstance(err, TypeError) and "argument of type 'int' is not iterable" in low:
                            return "root nats authentication error: WS closed after CONNECT; verify persisted runtime NATS credentials"
                    except Exception:
                        pass
                    # fallback – include class and message
                    try:
                        return f"{type(err).__name__}: {str(err)}"
                    except Exception:
                        return type(err).__name__

                while True:
                    route_runtime: NatsRouteTunnelRuntime | None = None
                    try:
                        cfg_now = getattr(service.ctx, "config", None) or load_config(ctx=service.ctx)
                        current_hub_id = str(getattr(cfg_now, "subnet_id", "") or "").strip()
                        if current_hub_id:
                            hub_id = current_hub_id
                            credentials.update_hub_id(current_hub_id)
                    except Exception:
                        pass
                    runtime_identity = runtime_identity_snapshot()
                    runtime_role = str(runtime_identity.get("transition_role") or "active")
                    runtime_instance = str(runtime_identity.get("runtime_instance_id") or "")
                    candidate_passive_mode = service._nats_policy.candidate_passive_mode()
                    try:
                        nats_attempt_server = None
                        nurl, nuser, npass = _read_node_nats()
                        requested_transport = str(os.getenv("HUB_NATS_TRANSPORT", "") or "").strip().lower()
                        if not nurl or not nuser or not npass:
                            fetched = await _fetch_nats_credentials()
                            if fetched:
                                # re-read persisted runtime NATS state on next loop
                                await asyncio.sleep(0.1)
                                continue
                            # Wait for `adaos dev telegram` to provision credentials.
                            if os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                                print("[hub-io] NATS disabled: missing persisted runtime nats.ws_url/user/pass")
                            await asyncio.sleep(2.0)
                            continue
                        if requested_transport in {"ws", "websocket", "websockets"} and not nats_url_uses_websocket(nurl):
                            fetched = await _fetch_nats_credentials()
                            if fetched:
                                await asyncio.sleep(0.1)
                                continue
                        if nats_url_uses_websocket(nurl) or (
                            realtime_enabled and service._nats_policy.url_needs_public_ws_refresh(nurl)
                        ):
                            fetched = await _fetch_nats_credentials()
                            if fetched:
                                await asyncio.sleep(0.1)
                                continue

                        user = nuser
                        pw = npass
                        pw_mask = (pw[:3] + "***" + pw[-2:]) if pw and len(pw) > 6 else ("***" if pw else None)
                        # Build candidates without mixing WS and TCP schemes to avoid client errors.
                        candidates: List[str] = []

                        def _dedup_push(url: str) -> None:
                            if not url:
                                return
                            s = str(url).strip()
                            if not s:
                                return
                            # For NATS WS clients, it's safer to always have an explicit WS path.
                            # In our deployment NATS WS is mounted at `/nats` (not `/`).
                            if s.startswith("ws://") or s.startswith("wss://"):
                                ws_default_path = os.getenv("NATS_WS_DEFAULT_PATH", "/nats") or "/nats"
                                if not ws_default_path.startswith("/"):
                                    ws_default_path = "/" + ws_default_path
                                try:
                                    from urllib.parse import urlparse, urlunparse

                                    pr0 = urlparse(s)
                                    # Keep an explicit "/" WS mount intact: some deployments terminate WS on "/".
                                    # Only inject the default mount when the path is missing entirely.
                                    if not pr0.path:
                                        pr0 = pr0._replace(path=ws_default_path)
                                        s = urlunparse(pr0)
                                except Exception:
                                    if s.endswith("://") or s.endswith("://localhost") or s.endswith("://127.0.0.1"):
                                        s = s.rstrip("/") + ws_default_path
                            if s not in candidates:
                                candidates.append(s)

                        base = (nurl or "").rstrip("/")

                        try:
                            from urllib.parse import urlparse, urlunparse

                            pr = urlparse(base) if base else None
                            scheme = (pr.scheme if pr else "").lower()
                            # If base is http(s), normalize to ws(s)
                            if scheme in ("http", "https"):
                                base = "ws" + base[4:]
                                pr = urlparse(base)
                                scheme = pr.scheme.lower()
                            # Default to WS mode when uncertain or when base points to cluster alias
                            is_ws_mode = (not base) or scheme.startswith("ws")
                            if not is_ws_mode and scheme == "nats":
                                host = (pr.hostname or "").lower()
                                # Avoid using internal docker alias from host-based hub
                                if host in ("nats", "localhost", "127.0.0.1"):
                                    is_ws_mode = True

                            if is_ws_mode:
                                # Prefer WS endpoints only.
                                # IMPORTANT: Keep this conservative — probing extra mounts/hosts has caused
                                # "Authentication Timeout" hangs when we accidentally hit non-NATS WS endpoints.
                                # The dedicated public hostname is opt-in only. In this environment it has
                                # been closing long-lived hub WS sessions shortly after the first client ping.
                                for item in service._nats_policy.public_ws_candidates(base):
                                    _dedup_push(item)
                                # Allow explicit WS alternates via env (comma-separated)
                                extra = os.getenv("NATS_WS_URL_ALT")
                                if extra:
                                    for it in [x.strip() for x in extra.split(",") if x.strip()]:
                                        if it.startswith("ws"):
                                            _dedup_push(it)
                            else:
                                # TCP mode: prefer nats:// endpoints.
                                if base:
                                    _dedup_push(base)
                                else:
                                    for item in service._nats_policy.public_tcp_candidates(base):
                                        _dedup_push(item)
                                # Optional TCP alternates via env (comma-separated)
                                extra = os.getenv("NATS_TCP_URL_ALT")
                                if extra:
                                    for it in [x.strip() for x in extra.split(",") if x.strip()]:
                                        if it.startswith("nats://") or it.startswith("tls://"):
                                            _dedup_push(it)
                        except Exception:
                            # Fallback: if base present, use it only; otherwise default to the api ingress.
                            if base:
                                _dedup_push(base)
                            else:
                                _dedup_push(public_nats_ws_api())
                        try:
                            now_m = time.monotonic()
                            available = [s for s in candidates if now_m >= float(nats_server_quarantine_until.get(str(s), 0.0))]
                            if available:
                                candidates = available
                        except Exception:
                            pass

                        # Prefer the api-domain ingress by default. The dedicated hostname remains opt-in via
                        # `HUB_NATS_PREFER_DEDICATED=1` for environments where it is known to be healthier.
                        try:
                            pref_ded = service._nats_policy.prefer_dedicated()
                            if candidates and str(candidates[0]).startswith(("ws://", "wss://")):
                                candidates = order_nats_ws_candidates(
                                    candidates,
                                    explicit_url=base,
                                    prefer_dedicated=pref_ded,
                                )
                        except Exception:
                            pass
                        remote_candidates: list[str] = []
                        try:
                            if realtime_enabled:
                                remote_candidates = resolve_realtime_remote_candidates()
                                if remote_candidates:
                                    original_candidates = list(candidates)
                                    local_candidate = realtime_sidecar_local_url()
                                    local_ready = await probe_realtime_sidecar_ready(
                                        host=realtime_sidecar_host(),
                                        port=realtime_sidecar_port(),
                                        timeout_s=1.5,
                                    )
                                    fallback_candidates = service._nats_policy.sidecar_fallback_candidates(
                                        original_candidates,
                                        local_candidate=local_candidate,
                                    )
                                    if local_ready:
                                        candidates = [local_candidate, *fallback_candidates]
                                        try:
                                            now_m = time.monotonic()
                                            available = [
                                                s
                                                for s in candidates
                                                if now_m >= float(nats_server_quarantine_until.get(str(s), 0.0))
                                            ]
                                            if available:
                                                candidates = available
                                        except Exception:
                                            pass
                                        _rl_log(
                                            "nats.sidecar_route",
                                            f"[hub-io] nats realtime sidecar local={local_candidate} remote={remote_candidates}"
                                            + (f" fallback={fallback_candidates}" if fallback_candidates else ""),
                                            every_s=60.0,
                                        )
                                    else:
                                        candidates = list(fallback_candidates)
                                        _rl_log(
                                            "nats.sidecar_unready",
                                            f"[hub-io] nats realtime sidecar not ready local={local_candidate}; "
                                            f"falling back to {fallback_candidates}",
                                            every_s=15.0,
                                        )
                        except Exception:
                            pass

                        try:
                            configure_hub_root_transport_strategy(
                                requested_transport=str(os.getenv("HUB_NATS_TRANSPORT", "") or "").strip().lower() or None,
                                selected_server=nats_last_server or nats_attempt_server or (candidates[0] if candidates else None),
                                url_override=str(os.getenv("HUB_NATS_URL_OVERRIDE", "") or "").strip() or None,
                                candidates=list(candidates),
                                failover_policy={
                                    "sidecar_enabled": bool(realtime_enabled),
                                    "sidecar_remote_candidates": list(remote_candidates),
                                    "allow_tcp_fallback": _env_truthy(
                                        os.getenv("ADAOS_REALTIME_ALLOW_TCP_FALLBACK"),
                                        default=False,
                                    ),
                                    "ws_impl_auto_fallback": _env_truthy(
                                        os.getenv("HUB_NATS_WS_AUTO_FALLBACK"),
                                        default=False,
                                    ),
                                },
                                hypothesis={
                                    "selector_loop": bool(os.name == "nt" and os.getenv("ADAOS_WIN_SELECTOR_LOOP", "0") == "1"),
                                    "ws_impl": str(os.getenv("HUB_NATS_WS_IMPL", "") or "").strip() or None,
                                    "raw_keepalive": _env_truthy(os.getenv("HUB_NATS_RAW_KEEPALIVE"), default=False),
                                    "rx_timeout_s": str(os.getenv("HUB_NATS_RX_TIMEOUT_S", "") or "").strip() or None,
                                },
                            )
                        except Exception:
                            pass

                        hub_nats_verbose = os.getenv("HUB_NATS_VERBOSE", "0") == "1"
                        hub_nats_quiet = os.getenv("HUB_NATS_QUIET", "1") == "1"
                        if hub_nats_verbose or not hub_nats_quiet:
                            print(f"[hub-io] Connecting NATS candidates={candidates} user={user} pass={pw_mask}")

                        def _emit_down(kind: str, err: Exception | None) -> None:
                            nonlocal reported_down
                            if not reported_down:
                                et = type(err).__name__ if err else kind
                                log_server = _resolve_nats_log_server(
                                    current_attempt=nats_attempt_server,
                                    connected_server=nats_last_server,
                                )
                                try:
                                    record_hub_root_transport_event(
                                        "down" if kind in {"disconnected", "eof"} else kind,
                                        transport=_hub_root_transport_kind(log_server),
                                        server=log_server,
                                        summary=f"hub-root transport down ({kind})",
                                        error=str(err) if err else None,
                                        details={"kind": kind},
                                    )
                                except Exception:
                                    pass
                                # Produce a richer one-time diagnostics line to aid debugging WS/TLS/DNS issues
                                if hub_nats_verbose or not hub_nats_quiet:
                                    try:
                                        if os.getenv("SILENCE_NATS_EOF", "0") == "1" and kind == "disconnected":
                                            # Suppress idle disconnect chatter in dev
                                            pass
                                        else:
                                            details = ""
                                            if err is not None:
                                                msg = str(err) or repr(err)
                                                # Extract handshake info if present
                                                status = getattr(err, "status", None)
                                                url = getattr(err, "url", None) or getattr(getattr(err, "request_info", None), "real_url", None)
                                                if status:
                                                    details += f" status={status}"
                                                if url:
                                                    details += f" url={url}"
                                                # Include a short class:message tail
                                                details = (details + f" msg={msg}").strip()
                                        if not (os.getenv("SILENCE_NATS_EOF", "0") == "1" and kind == "disconnected"):
                                            print(f"[hub-io] nats server unreachable ({et}){(': ' + details) if details else ''}")
                                    except Exception:
                                        pass
                                try:
                                    service.ctx.bus.publish(
                                        Event(type="subnet.nats.down", payload={"kind": kind, "error": str(err) if err else None, "ts": time.time()}, source="io.nats")
                                    )
                                except Exception:
                                    pass
                                try:
                                    mark_root_control_down(
                                        summary=f"hub-root control session down ({kind})",
                                        details={
                                            "kind": kind,
                                            "error": str(err) if err else None,
                                            "server": log_server,
                                        },
                                    )
                                    mark_route_degraded(
                                        summary="hub route relay degraded because root control is down",
                                        details={"cause": kind},
                                    )
                                except Exception:
                                    pass
                                try:
                                    asyncio.create_task(
                                        service._reset_hub_root_route_runtime(
                                            reason=f"nats_{kind}",
                                            notify_browser=False,
                                        )
                                    )
                                except Exception:
                                    pass
                                try:
                                    asyncio.create_task(report_control_lifecycle(f"subnet.nats.down:{kind}"))
                                except Exception:
                                    pass
                                reported_down = True

                        def _emit_up() -> None:
                            nonlocal reported_down
                            if reported_down:
                                log_server = _resolve_nats_log_server(
                                    current_attempt=nats_attempt_server,
                                    connected_server=nats_last_server,
                                )
                                if hub_nats_verbose or not hub_nats_quiet:
                                    try:
                                        print("[hub-io] nats connection restored")
                                    except Exception:
                                        pass
                                try:
                                    service.ctx.bus.publish(Event(type="subnet.nats.up", payload={"ts": time.time()}, source="io.nats"))
                                except Exception:
                                    pass
                                try:
                                    record_hub_root_transport_event(
                                        "connected",
                                        transport=_hub_root_transport_kind(log_server),
                                        server=log_server,
                                        summary="hub-root control session established",
                                        details={"ws_tag": ws_connect_tag if isinstance(ws_connect_tag, str) else None},
                                    )
                                except Exception:
                                    pass
                                try:
                                    mark_root_control_up(
                                        summary="hub-root control session established",
                                        details={
                                            "server": log_server,
                                            "ws_tag": ws_connect_tag if isinstance(ws_connect_tag, str) else None,
                                        },
                                    )
                                except Exception:
                                    pass
                                try:
                                    asyncio.create_task(
                                        service._reset_hub_root_route_runtime(
                                            reason="nats_reconnected",
                                            notify_browser=True,
                                        )
                                    )
                                except Exception:
                                    pass
                                try:
                                    asyncio.create_task(report_control_lifecycle("subnet.nats.up"))
                                except Exception:
                                    pass
                                reported_down = False

                        def _ws_state(nc_for_diag: Any) -> tuple[Any, Any, Any, Any]:
                            try:
                                tr = getattr(nc_for_diag, "_transport", None)
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
                                return ws_closed, ws_close_code, ws_close_reason, ws_exc
                            except Exception:
                                return None, None, None, None

                        def _env_is_sensitive(name: str) -> bool:
                            try:
                                n = (name or "").upper()
                            except Exception:
                                return False
                            return any(x in n for x in ("PASS", "PASSWORD", "TOKEN", "SECRET", "KEY", "JWT", "AUTH"))

                        def _env_snapshot(keys: list[str]) -> str:
                            parts: list[str] = []
                            for k in keys:
                                try:
                                    v = os.getenv(k)
                                except Exception:
                                    v = None
                                if v is None:
                                    parts.append(f"{k}=<unset>")
                                    continue
                                vv = str(v)
                                if _env_is_sensitive(k):
                                    if not vv:
                                        parts.append(f"{k}=<empty>")
                                    else:
                                        parts.append(f"{k}=<set:{len(vv)}>")
                                else:
                                    # Avoid huge env values in logs.
                                    if len(vv) > 200:
                                        vv = vv[:200] + "…"
                                    parts.append(f"{k}={vv}")
                            return " ".join(parts)

                        diag_file_state: dict[str, float | None] = {"last_at": None}

                        def _nats_task_snapshot(task: Any, *, stack_limit: int = 6) -> dict[str, Any] | None:
                            if not isinstance(task, asyncio.Task):
                                return None
                            snap: dict[str, Any] = {
                                "done": bool(task.done()),
                                "cancelled": bool(task.cancelled()),
                            }
                            try:
                                exc = task.exception() if task.done() and not task.cancelled() else None
                                snap["exc"] = f"{type(exc).__name__}: {exc}" if exc is not None else None
                            except Exception as exc:
                                snap["exc"] = f"{type(exc).__name__}: {exc}"
                            frames: list[str] = []

                            def _frame_has_y_py_locals(frame: Any) -> bool:
                                locals_values: Any = None
                                try:
                                    locals_values = getattr(frame, "f_locals", {}) or {}
                                    for value in locals_values.values():
                                        try:
                                            if type(value).__module__.split(".", 1)[0] == "y_py":
                                                return True
                                        finally:
                                            del value
                                except Exception:
                                    return False
                                finally:
                                    del locals_values
                                return False

                            try:
                                for frame in task.get_stack(limit=max(1, int(stack_limit))):
                                    try:
                                        if _frame_has_y_py_locals(frame):
                                            frames.append("y_py_frame")
                                            break
                                        frames.append(
                                            f"{Path(frame.f_code.co_filename).name}:{int(frame.f_lineno)}:{frame.f_code.co_name}"
                                        )
                                    except Exception:
                                        continue
                                    finally:
                                        # Do not let diagnostics retain live
                                        # frame objects. Frames can keep
                                        # y_py YDoc/YMap locals alive and
                                        # later drop them on a different
                                        # thread during GC on Windows.
                                        del frame
                            except Exception as exc:
                                frames = [f"{type(exc).__name__}: {exc}"]
                            snap["stack"] = frames
                            return snap

                        def _write_nats_ws_diag_file(
                            nc_for_diag: Any,
                            *,
                            server: Any | None = None,
                            source: str | None = None,
                            task_name: str | None = None,
                            err: Exception | None = None,
                            force: bool = False,
                        ) -> None:
                            raw_path = str(os.getenv("HUB_NATS_WS_DIAG_FILE", "") or "").strip()
                            if not raw_path:
                                return
                            try:
                                every_s = float(os.getenv("HUB_NATS_WS_DIAG_EVERY_S", "2") or "2")
                            except Exception:
                                every_s = 2.0
                            if every_s <= 0.0:
                                every_s = 2.0
                            now_mono = time.monotonic()
                            last_at = diag_file_state.get("last_at")
                            if (
                                not force
                                and source == "periodic"
                                and isinstance(last_at, (int, float))
                                and (now_mono - float(last_at)) < max(0.5, every_s)
                            ):
                                return
                            diag_file_state["last_at"] = now_mono
                            try:
                                stack_limit = int(os.getenv("HUB_NATS_WS_DIAG_STACK_LIMIT", "6") or "6")
                            except Exception:
                                stack_limit = 6
                            try:
                                loop = asyncio.get_running_loop()
                            except RuntimeError:
                                loop = None
                            try:
                                policy = asyncio.get_event_loop_policy()
                            except Exception:
                                policy = None
                            tr = getattr(nc_for_diag, "_transport", None)
                            ws = getattr(tr, "_ws", None) if tr is not None else None

                            def _ago(attr: str) -> float | None:
                                try:
                                    value = getattr(tr, attr, None) if tr is not None else None
                                    if isinstance(value, (int, float)):
                                        return round(now_mono - float(value), 3)
                                except Exception:
                                    return None
                                return None

                            connected_attr = getattr(nc_for_diag, "is_connected", None)
                            closed_attr = getattr(nc_for_diag, "is_closed", None)
                            connect_url = server if server is not None else nats_last_server
                            snapshot: dict[str, Any] = {
                                "ts": round(time.time(), 3),
                                "source": source,
                                "task_name": task_name,
                                "server": connect_url,
                                "connect_url": connect_url,
                                "conn_tag": ws_connect_tag if isinstance(ws_connect_tag, str) else None,
                                "loop_policy": type(policy).__name__ if policy is not None else None,
                                "loop": type(loop).__name__ if loop is not None else None,
                                "nc_connected": connected_attr() if callable(connected_attr) else bool(connected_attr),
                                "nc_closed": closed_attr() if callable(closed_attr) else bool(closed_attr),
                                "transport": type(tr).__name__ if tr is not None else None,
                                "ws_url": getattr(tr, "_adaos_ws_url", None) if tr is not None else None,
                                "ws_tag": getattr(tr, "_adaos_ws_tag", None) if tr is not None else None,
                                "ws_proto": getattr(tr, "_adaos_ws_proto", None) if tr is not None else None,
                                "ws_closed": getattr(ws, "closed", None) if ws is not None else None,
                                "ws_close_code": getattr(ws, "close_code", None) if ws is not None else None,
                                "last_rx_ago_s": _ago("_adaos_last_rx_at"),
                                "last_tx_ago_s": _ago("_adaos_last_tx_at"),
                                "last_ping_rx_ago_s": _ago("_adaos_last_ping_rx_at"),
                                "last_pong_tx_ago_s": _ago("_adaos_last_pong_tx_at"),
                                "last_ws_ping_tx_ago_s": _ago("_adaos_last_ws_ping_tx_at"),
                                "ka_pings_rx": getattr(tr, "_adaos_pings_rx", None) if tr is not None else None,
                                "ka_pongs_tx": getattr(tr, "_adaos_pongs_tx", None) if tr is not None else None,
                                "ws_pings_tx": getattr(tr, "_adaos_ws_pings_tx", None) if tr is not None else None,
                                "ws_data_ping_s": getattr(tr, "_adaos_ws_data_ping", None) if tr is not None else None,
                                "data_pings_tx": getattr(tr, "_adaos_data_pings_tx", None) if tr is not None else None,
                                "last_data_ping_tx_ago_s": _ago("_adaos_last_data_ping_tx_at"),
                                "last_tx_kind": getattr(tr, "_adaos_last_tx_kind", None) if tr is not None else None,
                                "last_tx_subj": getattr(tr, "_adaos_last_tx_subj", None) if tr is not None else None,
                                "io_loop_ago_s": _ago("_adaos_io_loop_at"),
                                "io_sending": getattr(tr, "_io_sending", None) if tr is not None else None,
                                "current_send_kind": getattr(tr, "_adaos_current_send_kind", None) if tr is not None else None,
                                "current_send_subj": getattr(tr, "_adaos_current_send_subj", None) if tr is not None else None,
                                "current_send_len": getattr(tr, "_adaos_current_send_len", None) if tr is not None else None,
                                "current_send_ago_s": _ago("_adaos_current_send_started_at"),
                                "last_send_kind": getattr(tr, "_adaos_last_send_kind", None) if tr is not None else None,
                                "last_send_subj": getattr(tr, "_adaos_last_send_subj", None) if tr is not None else None,
                                "last_send_len": getattr(tr, "_adaos_last_send_len", None) if tr is not None else None,
                                "last_send_done_ago_s": _ago("_adaos_last_send_done_at"),
                                "last_send_duration_s": getattr(tr, "_adaos_last_send_duration_s", None) if tr is not None else None,
                                "send_count": getattr(tr, "_adaos_send_count", None) if tr is not None else None,
                                "send_fail_count": getattr(tr, "_adaos_send_fail_count", None) if tr is not None else None,
                                "last_send_error": (
                                    f"{type(getattr(tr, '_adaos_last_send_error', None)).__name__}: {getattr(tr, '_adaos_last_send_error', None)}"
                                    if tr is not None and getattr(tr, "_adaos_last_send_error", None) is not None
                                    else None
                                ),
                                "last_send_error_ago_s": _ago("_adaos_last_send_error_at"),
                                "transport_pending_hi_q": (
                                    getattr(getattr(tr, "_pending_hi", None), "qsize", lambda: None)()
                                    if tr is not None and getattr(tr, "_pending_hi", None) is not None
                                    else None
                                ),
                                "transport_pending_q": (
                                    getattr(getattr(tr, "_pending", None), "qsize", lambda: None)()
                                    if tr is not None and getattr(tr, "_pending", None) is not None
                                    else None
                                ),
                                "pending_data_size": getattr(nc_for_diag, "_pending_data_size", None),
                                "io_task": _nats_task_snapshot(getattr(tr, "_io_task", None), stack_limit=stack_limit)
                                if tr is not None
                                else None,
                                "data_ping_task": _nats_task_snapshot(
                                    getattr(tr, "_data_ping_task", None), stack_limit=stack_limit
                                )
                                if tr is not None
                                else None,
                                "reading_task": _nats_task_snapshot(getattr(nc_for_diag, "_reading_task", None), stack_limit=stack_limit),
                                "flusher_task": _nats_task_snapshot(getattr(nc_for_diag, "_flusher_task", None), stack_limit=stack_limit),
                                "ping_interval_task": _nats_task_snapshot(getattr(nc_for_diag, "_ping_interval_task", None), stack_limit=stack_limit),
                                "err": f"{type(err).__name__}: {err}" if err is not None else None,
                            }
                            try:
                                path = Path(raw_path)
                                if not path.is_absolute():
                                    path = Path.cwd() / path
                                path.parent.mkdir(parents=True, exist_ok=True)
                                with path.open("a", encoding="utf-8") as fh:
                                    fh.write(_json.dumps(snapshot, ensure_ascii=False) + "\n")
                            except Exception:
                                pass

                        def _log_nats_ws_diag(
                            nc_for_diag: Any,
                            *,
                            server: Any | None = None,
                            rate_key: str = "nats.ws_diag",
                            every_s: float = 1.0,
                            source: str | None = None,
                            task_name: str | None = None,
                            err: Exception | None = None,
                        ) -> tuple[Any, Any, Any, Any]:
                            ws_closed, ws_close_code, ws_close_reason, ws_exc = _ws_state(nc_for_diag)
                            try:
                                tr = getattr(nc_for_diag, "_transport", None)
                                ws = getattr(tr, "_ws", None) if tr is not None else None
                                last_rx_at = getattr(tr, "_adaos_last_rx_at", None)
                                last_rx_ago_s = None
                                try:
                                    if isinstance(last_rx_at, (int, float)):
                                        last_rx_ago_s = round(time.monotonic() - float(last_rx_at), 3)
                                except Exception:
                                    last_rx_ago_s = None
                                last_tx_ago_s = None
                                try:
                                    last_tx_at = getattr(tr, "_adaos_last_tx_at", None)
                                    if isinstance(last_tx_at, (int, float)):
                                        last_tx_ago_s = round(time.monotonic() - float(last_tx_at), 3)
                                except Exception:
                                    last_tx_ago_s = None
                                tx_connect_ago_s = None
                                try:
                                    tx_connect_at = getattr(tr, "_adaos_tx_connect_at", None) if tr is not None else None
                                    if isinstance(tx_connect_at, (int, float)):
                                        tx_connect_ago_s = round(time.monotonic() - float(tx_connect_at), 3)
                                except Exception:
                                    tx_connect_ago_s = None
                                rx_info_ago_s = None
                                try:
                                    rx_info_at = getattr(tr, "_adaos_rx_info_at", None) if tr is not None else None
                                    if isinstance(rx_info_at, (int, float)):
                                        rx_info_ago_s = round(time.monotonic() - float(rx_info_at), 3)
                                except Exception:
                                    rx_info_ago_s = None
                                max_payload = None
                                try:
                                    max_payload = getattr(tr, "_adaos_nats_max_payload", None) if tr is not None else None
                                except Exception:
                                    max_payload = None
                                pending_data_size = getattr(nc_for_diag, "_pending_data_size", None)
                                pings_outstanding = getattr(nc_for_diag, "_pings_outstanding", None)
                                pongs_q = None
                                try:
                                    pongs = getattr(nc_for_diag, "_pongs", None)
                                    if isinstance(pongs, list):
                                        pongs_q = len(pongs)
                                except Exception:
                                    pongs_q = None
                                tr_pending_q = None
                                try:
                                    q = getattr(tr, "_pending", None) if tr is not None else None
                                    if q is not None:
                                        tr_pending_q = q.qsize()
                                except Exception:
                                    tr_pending_q = None
                                tr_pending_hi_q = None
                                try:
                                    q_hi = getattr(tr, "_pending_hi", None) if tr is not None else None
                                    if q_hi is not None and callable(getattr(q_hi, "qsize", None)):
                                        tr_pending_hi_q = q_hi.qsize()
                                except Exception:
                                    tr_pending_hi_q = None
                                send_lock_locked = None
                                try:
                                    lk = getattr(tr, "_send_lock", None) if tr is not None else None
                                    if lk is not None and callable(getattr(lk, "locked", None)):
                                        send_lock_locked = bool(lk.locked())
                                except Exception:
                                    send_lock_locked = None
                                ka_pings_rx = None
                                ka_last_ping_rx_ago_s = None
                                try:
                                    ka_pings_rx = getattr(tr, "_adaos_pings_rx", None) if tr is not None else None
                                    ka_last_ping_rx_at = getattr(tr, "_adaos_last_ping_rx_at", None) if tr is not None else None
                                    if isinstance(ka_last_ping_rx_at, (int, float)):
                                        ka_last_ping_rx_ago_s = round(time.monotonic() - float(ka_last_ping_rx_at), 3)
                                except Exception:
                                    ka_pings_rx = ka_pings_rx or None
                                    ka_last_ping_rx_ago_s = ka_last_ping_rx_ago_s or None
                                ka_pongs_tx = None
                                ka_last_pong_tx_ago_s = None
                                ka_last_pong_wait_ms = None
                                ka_last_pong_send_ms = None
                                try:
                                    ka_pongs_tx = getattr(tr, "_adaos_pongs_tx", None) if tr is not None else None
                                    ka_last_pong_tx_at = getattr(tr, "_adaos_last_pong_tx_at", None) if tr is not None else None
                                    if isinstance(ka_last_pong_tx_at, (int, float)):
                                        ka_last_pong_tx_ago_s = round(time.monotonic() - float(ka_last_pong_tx_at), 3)
                                    w_s = getattr(tr, "_adaos_last_pong_tx_wait_s", None) if tr is not None else None
                                    if isinstance(w_s, (int, float)):
                                        ka_last_pong_wait_ms = round(float(w_s) * 1000.0, 3)
                                    s_s = getattr(tr, "_adaos_last_pong_tx_send_s", None) if tr is not None else None
                                    if isinstance(s_s, (int, float)):
                                        ka_last_pong_send_ms = round(float(s_s) * 1000.0, 3)
                                except Exception:
                                    ka_pongs_tx = ka_pongs_tx or None
                                    ka_last_pong_tx_ago_s = ka_last_pong_tx_ago_s or None
                                    ka_last_pong_wait_ms = ka_last_pong_wait_ms or None
                                    ka_last_pong_send_ms = ka_last_pong_send_ms or None
                                ws_tag = None
                                try:
                                    ws_tag = getattr(tr, "_adaos_ws_tag", None) if tr is not None else None
                                except Exception:
                                    ws_tag = None
                                if not ws_tag:
                                    try:
                                        ws_tag = ws_connect_tag if isinstance(ws_connect_tag, str) else None
                                    except Exception:
                                        ws_tag = None
                                ws_hb = None
                                try:
                                    ws_hb = getattr(tr, "_adaos_ws_heartbeat", None) if tr is not None else None
                                except Exception:
                                    ws_hb = None
                                ws_hb_mode = None
                                try:
                                    ws_hb_mode = getattr(tr, "_adaos_ws_heartbeat_mode", None) if tr is not None else None
                                except Exception:
                                    ws_hb_mode = None
                                ws_data_hb = None
                                try:
                                    ws_data_hb = getattr(tr, "_adaos_ws_data_heartbeat", None) if tr is not None else None
                                except Exception:
                                    ws_data_hb = None
                                ws_data_ping = None
                                data_pings_tx = None
                                data_last_ping_tx_ago_s = None
                                try:
                                    ws_data_ping = getattr(tr, "_adaos_ws_data_ping", None) if tr is not None else None
                                    data_pings_tx = getattr(tr, "_adaos_data_pings_tx", None) if tr is not None else None
                                    data_last_ping_tx_at = getattr(tr, "_adaos_last_data_ping_tx_at", None) if tr is not None else None
                                    if isinstance(data_last_ping_tx_at, (int, float)):
                                        data_last_ping_tx_ago_s = round(time.monotonic() - float(data_last_ping_tx_at), 3)
                                except Exception:
                                    ws_data_ping = ws_data_ping or None
                                    data_pings_tx = data_pings_tx or None
                                    data_last_ping_tx_ago_s = data_last_ping_tx_ago_s or None
                                ws_recv_timeout = None
                                try:
                                    ws_recv_timeout = getattr(tr, "_adaos_ws_recv_timeout", None) if tr is not None else None
                                except Exception:
                                    ws_recv_timeout = None
                                ws_url = None
                                try:
                                    ws_url = getattr(tr, "_adaos_ws_url", None) if tr is not None else None
                                except Exception:
                                    ws_url = None
                                ws_proto = None
                                try:
                                    ws_proto = getattr(tr, "_adaos_ws_proto", None) if tr is not None else None
                                except Exception:
                                    ws_proto = None
                                if not ws_proto:
                                    try:
                                        ws_proto = getattr(ws, "protocol", None) if ws is not None else None
                                    except Exception:
                                        ws_proto = None
                                if not ws_proto:
                                    try:
                                        ws_proto = getattr(ws, "_response", None).headers.get("Sec-WebSocket-Protocol") if ws is not None and getattr(ws, "_response", None) is not None else None
                                    except Exception:
                                        ws_proto = None
                                last_tx_kind = None
                                last_tx_subj = None
                                last_tx_len = None
                                try:
                                    last_tx_kind = getattr(tr, "_adaos_last_tx_kind", None) if tr is not None else None
                                    last_tx_subj = getattr(tr, "_adaos_last_tx_subj", None) if tr is not None else None
                                    last_tx_len = getattr(tr, "_adaos_last_tx_len", None) if tr is not None else None
                                except Exception:
                                    last_tx_kind = last_tx_kind or None
                                    last_tx_subj = last_tx_subj or None
                                    last_tx_len = last_tx_len or None
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
                                ws_pings_tx = None
                                ws_last_ping_tx_ago_s = None
                                ws_last_ping_wait_ms = None
                                ws_last_ping_send_ms = None
                                try:
                                    ws_pings_tx = getattr(tr, "_adaos_ws_pings_tx", None) if tr is not None else None
                                    ws_last_ping_tx_at = getattr(tr, "_adaos_last_ws_ping_tx_at", None) if tr is not None else None
                                    if isinstance(ws_last_ping_tx_at, (int, float)):
                                        ws_last_ping_tx_ago_s = round(time.monotonic() - float(ws_last_ping_tx_at), 3)
                                    ws_ping_wait_s = getattr(tr, "_adaos_last_ws_ping_tx_wait_s", None) if tr is not None else None
                                    if isinstance(ws_ping_wait_s, (int, float)):
                                        ws_last_ping_wait_ms = round(float(ws_ping_wait_s) * 1000.0, 3)
                                    ws_ping_send_s = getattr(tr, "_adaos_last_ws_ping_tx_send_s", None) if tr is not None else None
                                    if isinstance(ws_ping_send_s, (int, float)):
                                        ws_last_ping_send_ms = round(float(ws_ping_send_s) * 1000.0, 3)
                                except Exception:
                                    ws_pings_tx = ws_pings_tx or None
                                    ws_last_ping_tx_ago_s = ws_last_ping_tx_ago_s or None
                                    ws_last_ping_wait_ms = ws_last_ping_wait_ms or None
                                    ws_last_ping_send_ms = ws_last_ping_send_ms or None
                                io_loop_ago_s = None
                                current_send_kind = None
                                current_send_subj = None
                                current_send_len = None
                                current_send_ago_s = None
                                last_send_kind = None
                                last_send_subj = None
                                last_send_len = None
                                last_send_done_ago_s = None
                                last_send_duration_ms = None
                                send_count = None
                                send_fail_count = None
                                last_send_err = None
                                last_send_err_ago_s = None
                                try:
                                    io_loop_at = getattr(tr, "_adaos_io_loop_at", None) if tr is not None else None
                                    if isinstance(io_loop_at, (int, float)):
                                        io_loop_ago_s = round(time.monotonic() - float(io_loop_at), 3)
                                    current_send_kind = getattr(tr, "_adaos_current_send_kind", None) if tr is not None else None
                                    current_send_subj = getattr(tr, "_adaos_current_send_subj", None) if tr is not None else None
                                    current_send_len = getattr(tr, "_adaos_current_send_len", None) if tr is not None else None
                                    current_send_at = (
                                        getattr(tr, "_adaos_current_send_started_at", None) if tr is not None else None
                                    )
                                    if isinstance(current_send_at, (int, float)):
                                        current_send_ago_s = round(time.monotonic() - float(current_send_at), 3)
                                    last_send_kind = getattr(tr, "_adaos_last_send_kind", None) if tr is not None else None
                                    last_send_subj = getattr(tr, "_adaos_last_send_subj", None) if tr is not None else None
                                    last_send_len = getattr(tr, "_adaos_last_send_len", None) if tr is not None else None
                                    last_send_done_at = (
                                        getattr(tr, "_adaos_last_send_done_at", None) if tr is not None else None
                                    )
                                    if isinstance(last_send_done_at, (int, float)):
                                        last_send_done_ago_s = round(time.monotonic() - float(last_send_done_at), 3)
                                    last_send_duration_s = (
                                        getattr(tr, "_adaos_last_send_duration_s", None) if tr is not None else None
                                    )
                                    if isinstance(last_send_duration_s, (int, float)):
                                        last_send_duration_ms = round(float(last_send_duration_s) * 1000.0, 3)
                                    send_count = getattr(tr, "_adaos_send_count", None) if tr is not None else None
                                    send_fail_count = getattr(tr, "_adaos_send_fail_count", None) if tr is not None else None
                                    last_send_err = getattr(tr, "_adaos_last_send_error", None) if tr is not None else None
                                    last_send_err_at = (
                                        getattr(tr, "_adaos_last_send_error_at", None) if tr is not None else None
                                    )
                                    if isinstance(last_send_err_at, (int, float)):
                                        last_send_err_ago_s = round(time.monotonic() - float(last_send_err_at), 3)
                                except Exception:
                                    pass
                                server0 = _resolve_nats_log_server(
                                    server=server,
                                    current_attempt=nats_attempt_server,
                                    connected_server=nats_last_server,
                                )
                                extra_parts: list[str] = []
                                if source:
                                    extra_parts.append(f"source={source}")
                                if task_name:
                                    extra_parts.append(f"task={task_name}")
                                if err is not None:
                                    extra_parts.append(f"err={type(err).__name__}: {err}")
                                extra_suffix = (" " + " ".join(extra_parts)) if extra_parts else ""
                                _rl_log(
                                    rate_key,
                                    f"[hub-io] nats ws diag: tag={ws_tag} server={server0} ws_hb_s={ws_hb} ws_hb_mode={ws_hb_mode} ws_data_hb_s={ws_data_hb} ws_data_ping_s={ws_data_ping} data_pings_tx={data_pings_tx} data_last_ping_tx_ago_s={data_last_ping_tx_ago_s} ws_recv_timeout_s={ws_recv_timeout} ws_url={ws_url} closed={ws_closed} close_code={ws_close_code} close_reason={ws_close_reason} ws_exc={ws_exc} last_rx_ago_s={last_rx_ago_s} last_tx_ago_s={last_tx_ago_s} io_loop_ago_s={io_loop_ago_s} tx_connect_ago_s={tx_connect_ago_s} rx_info_ago_s={rx_info_ago_s} max_payload={max_payload} pending_data_size={pending_data_size} pings_outstanding={pings_outstanding} pongs_q={pongs_q} transport_pending_hi_q={tr_pending_hi_q} transport_pending_q={tr_pending_q} send_lock={send_lock_locked} current_send_kind={current_send_kind} current_send_subj={current_send_subj} current_send_len={current_send_len} current_send_ago_s={current_send_ago_s} last_send_kind={last_send_kind} last_send_subj={last_send_subj} last_send_len={last_send_len} last_send_done_ago_s={last_send_done_ago_s} last_send_duration_ms={last_send_duration_ms} send_count={send_count} send_fail_count={send_fail_count} last_send_err={type(last_send_err).__name__ if last_send_err is not None else None} last_send_err_ago_s={last_send_err_ago_s} ka_pings_rx={ka_pings_rx} ka_last_ping_rx_ago_s={ka_last_ping_rx_ago_s} ka_pongs_tx={ka_pongs_tx} ka_last_pong_tx_ago_s={ka_last_pong_tx_ago_s} ka_last_pong_wait_ms={ka_last_pong_wait_ms} ka_last_pong_send_ms={ka_last_pong_send_ms} ws_pings_tx={ws_pings_tx} ws_last_ping_tx_ago_s={ws_last_ping_tx_ago_s} ws_last_ping_wait_ms={ws_last_ping_wait_ms} ws_last_ping_send_ms={ws_last_ping_send_ms} ws_proto={ws_proto} last_tx_kind={last_tx_kind} last_tx_subj={last_tx_subj} last_tx_len={last_tx_len} last_recv_err={type(last_recv_err).__name__ if last_recv_err is not None else None} last_recv_err_ago_s={last_recv_err_ago_s}{extra_suffix}",
                                    every_s=every_s,
                                )
                            except Exception:
                                pass
                            return ws_closed, ws_close_code, ws_close_reason, ws_exc

                        async def _on_error_cb(e: Exception, *, nc_for_diag: Any | None = None) -> None:
                            # Best-effort; keep quiet unless explicitly verbose or useful
                            is_transient = is_transient_nats_error(e)
                            is_eof = type(e).__name__ == "UnexpectedEOF" or "unexpected eof" in str(e).lower()
                            if os.getenv("SILENCE_NATS_EOF", "0") == "1" and is_eof:
                                return
                            # Emit extra transport diagnostics to correlate client-side errors with root-side logs.
                            if nc_for_diag is not None and (is_eof or os.getenv("HUB_NATS_VERBOSE", "0") == "1" or os.getenv("HUB_NATS_TRACE", "0") == "1"):
                                try:
                                    ws_closed, ws_close_code, ws_close_reason, ws_exc = _log_nats_ws_diag(
                                        nc_for_diag,
                                        server=_resolve_nats_log_server(
                                            current_attempt=nats_attempt_server,
                                            connected_server=nats_last_server,
                                        ),
                                        rate_key="nats.ws_diag",
                                        every_s=1.0,
                                        source="error_cb",
                                        err=e,
                                    )
                                    _rl_log(
                                        "nats.ws_eof",
                                        f"[hub-io] nats ws eof: closed={ws_closed} close_code={ws_close_code} close_reason={ws_close_reason} ws_exc={ws_exc}",
                                        every_s=1.0,
                                    )
                                    await asyncio.to_thread(
                                        _write_nats_ws_diag_file,
                                        nc_for_diag,
                                        server=_resolve_nats_log_server(
                                            current_attempt=nats_attempt_server,
                                            connected_server=nats_last_server,
                                        ),
                                        source="error_cb",
                                        err=e,
                                        force=True,
                                    )
                                except Exception:
                                    pass
                            # Capture the effective env knobs around NATS-over-WS on errors to make log sharing actionable.
                            try:
                                _env = _env_snapshot(
                                    [
                                        "HUB_NATS_PING_INTERVAL_S",
                                        "HUB_NATS_MAX_OUTSTANDING_PINGS",
                                        "HUB_NATS_DISABLE_PING_INTERVAL_TASK",
                                        "HUB_NATS_RX_TIMEOUT_S",
                                        "HUB_NATS_WS_IMPL",
                                        "HUB_NATS_WS_MAX_MSG_SIZE",
                                        "HUB_NATS_WS_MAX_QUEUE",
                                        "HUB_NATS_WS_HEARTBEAT_S",
                                        "HUB_NATS_WS_DATA_HEARTBEAT_S",
                                        "HUB_NATS_WS_PROXY",
                                        "HUB_NATS_WS_TRACE",
                                        "HUB_NATS_WS_PATCH_AIOHTTP",
                                        "HUB_NATS_WIRETAP",
                                        "HUB_NATS_WIRETAP_MAX_BYTES",
                                        "HUB_NATS_WIRETAP_EVERY_N",
                                        "HUB_NATS_WIRETAP_SKIP",
                                        "HUB_NATS_TCP_KEEPALIVE",
                                        "HUB_NATS_TCP_KEEPALIVE_S",
                                        "HUB_NATS_TCP_KEEPALIVE_INTERVAL_S",
                                        "HUB_NATS_TCP_KEEPALIVE_PROBES",
                                        "HUB_NATS_RAW_KEEPALIVE",
                                        "HUB_NATS_RAW_KEEPALIVE_S",
                                        "HUB_NATS_CONNECT_TAG_QUERY",
                                        "HUB_TRACE",
                                        "WS_NATS_PROXY_WS_PING",
                                        "WS_NATS_PROXY_TERMINATE_CLIENT_PING",
                                        "WS_NATS_PROXY_KEEPALIVE_REQUIRE_HANDSHAKE",
                                        "WS_NATS_PROXY_WIRETAP",
                                    ]
                                )
                                if _env:
                                    _rl_log("nats.env", f"[hub-io] nats env: {_env}", every_s=30.0)
                            except Exception:
                                pass
                            try:
                                if type(e).__name__ == "SlowConsumerError":
                                    try:
                                        sub_sc = getattr(e, "sub", None)
                                        q_sc = getattr(sub_sc, "_pending_queue", None) if sub_sc is not None else None
                                        qsize_sc = q_sc.qsize() if q_sc is not None and callable(getattr(q_sc, "qsize", None)) else None
                                    except Exception:
                                        qsize_sc = None
                                    try:
                                        pending_size_sc = getattr(sub_sc, "_pending_size", None) if sub_sc is not None else None
                                    except Exception:
                                        pending_size_sc = None
                                    try:
                                        subject_sc = getattr(e, "subject", None)
                                    except Exception:
                                        subject_sc = None
                                    try:
                                        sid_sc = getattr(e, "sid", None)
                                    except Exception:
                                        sid_sc = None
                                    try:
                                        service._log.warning(
                                            "nats slow consumer hub_id=%s server=%s subject=%s sid=%s qsize=%s pending_size=%s",
                                            hub_id,
                                            nats_last_server,
                                            subject_sc,
                                            sid_sc,
                                            qsize_sc,
                                            pending_size_sc,
                                        )
                                    except Exception:
                                        pass
                                    try:
                                        _rl_log(
                                            "nats.slow_consumer",
                                            f"[hub-io] nats slow consumer subject={subject_sc} sid={sid_sc} qsize={qsize_sc} pending_size={pending_size_sc}",
                                            every_s=1.0,
                                        )
                                    except Exception:
                                        pass
                                error_summary = nats_error_summary(e)
                                log_method = service._log.info if is_transient else service._log.warning
                                log_method(
                                    "nats error_cb hub_id=%s server=%s transient=%s type=%s err=%s",
                                    hub_id,
                                    _resolve_nats_log_server(
                                        current_attempt=nats_attempt_server,
                                        connected_server=nats_last_server,
                                    ),
                                    is_transient,
                                    type(e).__name__,
                                    error_summary,
                                )
                            except Exception:
                                pass
                            try:
                                verbose = os.getenv("HUB_NATS_VERBOSE", "0") == "1"
                                quiet = os.getenv("HUB_NATS_QUIET", "1") == "1"
                                if quiet and not verbose and not is_eof:
                                    return
                                if type(e).__name__ == "WSServerHandshakeError" and not verbose:
                                    print("[hub-io] nats error_cb: WSServerHandshakeError (check nats.ws_url path: '/' vs '/nats')")
                                    return
                                if verbose:
                                    print(f"[hub-io] nats error_cb: {type(e).__name__}: {e!s}")
                                else:
                                    print(f"[hub-io] nats error_cb: {type(e).__name__}")
                            except Exception:
                                pass

                        async def _on_disconnected() -> None:
                            try:
                                service._log.warning(
                                    "nats disconnected hub_id=%s server=%s",
                                    hub_id,
                                    _resolve_nats_log_server(
                                        current_attempt=nats_attempt_server,
                                        connected_server=nats_last_server,
                                    ),
                                )
                            except Exception:
                                pass
                            _emit_down("disconnected", None)

                        async def _on_reconnected() -> None:
                            try:
                                service._log.info(
                                    "nats reconnected hub_id=%s server=%s",
                                    hub_id,
                                    _resolve_nats_log_server(
                                        current_attempt=nats_attempt_server,
                                        connected_server=nats_last_server,
                                    ),
                                )
                            except Exception:
                                pass
                            # Suppress restored chatter in dev if silenced
                            if os.getenv("SILENCE_NATS_EOF", "0") == "1":
                                try:
                                    service.ctx.bus.publish(Event(type="subnet.nats.up", payload={"ts": time.time()}, source="io.nats"))
                                except Exception:
                                    pass
                            else:
                                _emit_up()

                        # Coerce types to what nats-py expects
                        # For WS proxy auth, always identify as the canonical hub id regardless of any human-friendly alias
                        try:
                            is_ws_candidates = any(isinstance(s, str) and s.startswith("ws") for s in candidates)
                        except Exception:
                            is_ws_candidates = False
                        if is_ws_candidates or realtime_enabled:
                            resolved_hub_id, resolved_nats_user = _canonical_hub_nats_identity(
                                local_hub_id=hub_id,
                                nats_user=nuser,
                            )
                            if resolved_hub_id:
                                hub_id = resolved_hub_id
                                credentials.update_hub_id(resolved_hub_id)
                            if resolved_nats_user:
                                user = resolved_nats_user
                        hub_id_str = hub_id if isinstance(hub_id, str) else str(hub_id)
                        user_str = user if (user is None or isinstance(user, str)) else str(user)
                        pw_str = pw if (pw is None or isinstance(pw, str)) else str(pw)
                        if os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                            try:
                                print(
                                    f"[hub-io] nats connect opts: name={runtime_connect_name(prefix=f'hub-{hub_id_str!s}')} "
                                    f"user={type(user_str).__name__} pass={type(pw_str).__name__} "
                                    f"role={runtime_role} instance={runtime_instance} servers={candidates}"
                                )
                            except Exception:
                                pass

                        try:
                            install_transient_nats_log_filter()
                        except Exception:
                            pass

                        # NOTE: Connect to candidates sequentially. Some endpoints can hang the WS handshake
                        # (leading to "Authentication Timeout") while others work; trying one-by-one keeps
                        # failures isolated and helps cleanup transports.
                        async def _try_connect(server: str) -> Any:
                            # `nats` package does not expose Client at top-level; use nats.aio.client.Client.
                            nc_local = _nats.aio.client.Client()
                            async def _on_error_cb_local(e: Exception) -> None:
                                await _on_error_cb(
                                    e,
                                    nc_for_diag=nc_local,
                                )
                            try:
                                # New correlation id for this connect attempt (sent as WS header).
                                try:
                                    nonlocal ws_connect_tag
                                    ws_connect_tag = f"{hub_id_str}-{uuid.uuid4().hex[:10]}"
                                except Exception:
                                    ws_connect_tag = None
                                connect_server = str(server)
                                # Some transports do not reliably propagate custom WS headers.
                                # Optionally attach the correlation id as a query param to help root-side
                                # logs correlate abnormal closes (1006/EOF) to hub attempts.
                                try:
                                    if (
                                        connect_server.startswith("ws")
                                        and os.getenv("HUB_NATS_CONNECT_TAG_QUERY", "0") == "1"
                                        and isinstance(ws_connect_tag, str)
                                        and ws_connect_tag
                                    ):
                                        from urllib.parse import urlparse as _urlparse, urlunparse as _urlunparse, parse_qsl as _parse_qsl, urlencode as _urlencode
                                        u = _urlparse(connect_server)
                                        q = dict(_parse_qsl(u.query, keep_blank_values=True))
                                        q.setdefault("adaos_conn", ws_connect_tag)
                                        connect_server = _urlunparse(u._replace(query=_urlencode(q)))
                                except Exception:
                                    connect_server = str(server)
                                try:
                                    if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                        _rl_log(
                                            "nats.connect_try",
                                            f"[hub-io] NATS connect try server={connect_server} tag={ws_connect_tag}",
                                            every_s=1.0,
                                        )
                                except Exception:
                                    pass
                                try:
                                    configure_hub_root_transport_strategy(
                                        effective_transport=_hub_root_transport_kind(connect_server),
                                        selected_server=connect_server,
                                        current_ws_tag=ws_connect_tag if isinstance(ws_connect_tag, str) else None,
                                        hypothesis={
                                            "selector_loop": bool(os.name == "nt" and os.getenv("ADAOS_WIN_SELECTOR_LOOP", "0") == "1"),
                                            "ws_impl": str(os.getenv("HUB_NATS_WS_IMPL", "") or "").strip() or None,
                                            "raw_keepalive": _env_truthy(os.getenv("HUB_NATS_RAW_KEEPALIVE"), default=False),
                                        },
                                    )
                                    record_hub_root_transport_event(
                                        "attempt",
                                        transport=_hub_root_transport_kind(connect_server),
                                        server=connect_server,
                                        summary="hub-root connect attempt started",
                                        details={"ws_tag": ws_connect_tag if isinstance(ws_connect_tag, str) else None},
                                    )
                                except Exception:
                                    pass
                                # Keepalive:
                                # - Root's ws-nats-proxy sends NATS `PING\r\n` frames to the hub, but those
                                #   only keep the WS tunnel alive if the hub actually replies with `PONG\r\n`.
                                # - Some reverse proxies / LBs will still cut long-lived WS connections if the
                                #   client stays silent (observed as ~1000s / close 1006 + ECONNRESET on root).
                                #
                                # Therefore, for WS transports default to a small hub->root ping interval to
                                # guarantee outbound traffic even when the hub is otherwise idle.
                                # NOTE: Some NATS-over-WS proxies (observed on inimatic ws-nats-proxy) can
                                # flap with close 1006/UnexpectedEOF when the client sends periodic NATS PINGs.
                                # Root/proxy already sends server PINGs, so the hub still generates outbound
                                # traffic by replying with PONGs even if the client ping interval is conservative.
                                try:
                                    # Defaults:
                                    # - WS: keep the client ping interval conservative (root/proxy already produces traffic).
                                    # - TCP: use a small ping interval so we can detect half-open links faster and avoid
                                    #   long stalls on Windows (often observed as WinError 121 in the reader task).
                                    is_ws = bool(connect_server.startswith("ws"))
                                    if is_ws:
                                        ping_interval_default = "3600"
                                    else:
                                        try:
                                            is_windows = (os.name == "nt")
                                        except Exception:
                                            is_windows = False
                                        ping_interval_default = "15" if is_windows else "60"
                                    ping_interval = int(
                                        os.getenv("HUB_NATS_PING_INTERVAL_S", ping_interval_default)
                                        or ping_interval_default
                                    )
                                    # nats-py always starts the ping task; 0 would create a busy-loop.
                                    if ping_interval <= 0:
                                        ping_interval = int(ping_interval_default)
                                except Exception:
                                    ping_interval = 3600
                                try:
                                    max_out_default = "10" if is_ws else "2"
                                    max_outstanding_pings = int(os.getenv("HUB_NATS_MAX_OUTSTANDING_PINGS", max_out_default) or max_out_default)
                                except Exception:
                                    max_outstanding_pings = 10 if is_ws else 2
                                try:
                                    if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                        _rl_log(
                                            "nats.keepalive",
                                            f"[hub-io] nats keepalive ping_interval={ping_interval}s max_outstanding_pings={max_outstanding_pings}",
                                            every_s=60.0,
                                        )
                                except Exception:
                                    pass
                                try:
                                    configure_hub_root_transport_strategy(
                                        effective_transport=_hub_root_transport_kind(connect_server),
                                        selected_server=connect_server,
                                        current_ws_tag=ws_connect_tag if isinstance(ws_connect_tag, str) else None,
                                        hypothesis={
                                            "selector_loop": bool(os.name == "nt" and os.getenv("ADAOS_WIN_SELECTOR_LOOP", "0") == "1"),
                                            "ws_impl": str(os.getenv("HUB_NATS_WS_IMPL", "") or "").strip() or None,
                                            "raw_keepalive": _env_truthy(os.getenv("HUB_NATS_RAW_KEEPALIVE"), default=False),
                                            "ping_interval_s": ping_interval,
                                            "max_outstanding_pings": max_outstanding_pings,
                                        },
                                    )
                                except Exception:
                                    pass
                                await asyncio.wait_for(
                                    nc_local.connect(
                                        servers=[connect_server],
                                        user=user_str,
                                        password=pw_str,
                                        name=runtime_connect_name(prefix=f"hub-{hub_id_str}"),
                                        ws_connection_headers=(
                                            {
                                                "X-AdaOS-Nats-Conn": [ws_connect_tag],
                                                "X-AdaOS-Runtime-Instance": [runtime_instance],
                                                "X-AdaOS-Runtime-Role": [runtime_role],
                                            }
                                            if connect_server.startswith("ws") and isinstance(ws_connect_tag, str) and ws_connect_tag
                                            else None
                                        ),
                                        allow_reconnect=False,
                                        # Be tolerant to intermittent WS proxy hiccups: missed PONGs should not
                                        # tear down the whole hub IO bridge too aggressively.
                                        ping_interval=ping_interval,
                                        max_outstanding_pings=max_outstanding_pings,
                                        connect_timeout=5.0,
                                        error_cb=_on_error_cb_local,
                                        disconnected_cb=_on_disconnected,
                                        reconnected_cb=_on_reconnected,
                                    ),
                                    timeout=7.0,
                                )
                                try:
                                    tr = getattr(nc_local, "_transport", None)
                                    if tr is not None:
                                        try:
                                            setattr(tr, "_adaos_nc", nc_local)
                                        except Exception:
                                            pass
                                    if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                        hb = getattr(tr, "_adaos_ws_heartbeat", None) if tr else None
                                        hb_mode = getattr(tr, "_adaos_ws_heartbeat_mode", None) if tr else None
                                        if hb is not None:
                                            _rl_log(
                                                "nats.ws_hb",
                                                f"[hub-io] nats ws heartbeat: {hb!s}s mode={hb_mode}",
                                                every_s=60.0,
                                            )
                                        if isinstance(ws_connect_tag, str) and ws_connect_tag:
                                            _rl_log("nats.ws_tag", f"[hub-io] nats ws tag: {ws_connect_tag}", every_s=1.0)
                                        _rl_log(
                                            "nats.transport",
                                            f"[hub-io] nats transport kind: {type(tr).__name__ if tr is not None else None}",
                                            every_s=60.0,
                                        )
                                except Exception:
                                    pass
                                # Optionally disable periodic client PINGs on WS transports.
                                # Some proxies respond poorly to client-initiated PINGs and can force-close (1006/EOF).
                                # Default: disable for WS; can be re-enabled with HUB_NATS_DISABLE_PING_INTERVAL_TASK=0.
                                try:
                                    if connect_server.startswith("ws"):
                                        disable_env = os.getenv("HUB_NATS_DISABLE_PING_INTERVAL_TASK", "1")
                                        disable_ping_task = str(disable_env or "").strip() != "0"
                                        if disable_ping_task:
                                            pt = getattr(nc_local, "_ping_interval_task", None)
                                            if isinstance(pt, asyncio.Task):
                                                try:
                                                    if not pt.done():
                                                        pt.cancel()
                                                except Exception:
                                                    pass
                                                # Important: our own bridge watchdog treats core task termination as fatal.
                                                # When we intentionally disable the ping task, clear the reference so the
                                                # watchdog doesn't restart the whole bridge on a cancelled task.
                                                try:
                                                    setattr(nc_local, "_ping_interval_task", None)
                                                except Exception:
                                                    pass
                                                try:
                                                    setattr(nc_local, "_adaos_ping_interval_task_disabled", True)
                                                except Exception:
                                                    pass
                                                if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                                    _rl_log(
                                                        "nats.ping_task_off",
                                                        "[hub-io] nats ping interval task disabled for WS transport",
                                                        every_s=60.0,
                                                    )
                                except Exception:
                                    pass

                                return nc_local
                            except Exception as e:
                                # Extra diagnostics for flaky WS/NATS drops (e.g. UnexpectedEOF without close frame).
                                try:
                                    if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                        tr = getattr(nc_local, "_transport", None)
                                        ws = getattr(tr, "_ws", None) if tr else None
                                        ws_closed = getattr(ws, "closed", None) if ws is not None else None
                                        ws_close_code = getattr(ws, "close_code", None) if ws is not None else None
                                        ws_exc = None
                                        try:
                                            exf = getattr(ws, "exception", None)
                                            if callable(exf):
                                                ws_exc = exf()
                                        except Exception:
                                            ws_exc = None
                                        _rl_log(
                                            "nats.ws_diag",
                                            f"[hub-io] nats ws diag: tag={ws_connect_tag} server={locals().get('connect_server', None)} err={type(e).__name__} closed={ws_closed} close_code={ws_close_code} ws_exc={ws_exc}",
                                            every_s=2.0,
                                        )
                                except Exception:
                                    pass
                                try:
                                    record_hub_root_transport_event(
                                        "connect_failed",
                                        transport=_hub_root_transport_kind(locals().get("connect_server", None)),
                                        server=locals().get("connect_server", None),
                                        summary="hub-root connect attempt failed",
                                        error=str(e),
                                        details={"ws_tag": ws_connect_tag if isinstance(ws_connect_tag, str) else None},
                                    )
                                except Exception:
                                    pass

                                # Refresh credentials only when the transport has auth evidence.
                                try:
                                    refresh_evidence = _nats_credentials_refresh_evidence(
                                        e,
                                        server=locals().get("connect_server", None),
                                    )
                                    if refresh_evidence:
                                        if os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                                            try:
                                                print(
                                                    "[hub-io] NATS auth failure confirmed; refreshing credentials "
                                                    f"(evidence={refresh_evidence} err={type(e).__name__}: {e})"
                                                )
                                            except Exception:
                                                pass
                                        await _fetch_nats_credentials()
                                except Exception:
                                    pass
                                # Best-effort cleanup of partially created WS transport
                                await _run_bounded_async_cleanup(nc_local.close)
                                # Ensure WS transport is fully torn down if connect() was cancelled/timed out.
                                try:
                                    tr = getattr(nc_local, "_transport", None)
                                    if tr:
                                        ws = getattr(tr, "_ws", None)
                                        client = getattr(tr, "_client", None)
                                        if ws is not None:
                                            await _run_bounded_async_cleanup(ws.close)
                                        if client is not None:
                                            await _run_bounded_async_cleanup(client.close)
                                except Exception:
                                    pass
                                raise e

                        last_exc: Exception | None = None
                        nc = None
                        connected_server: str | None = None
                        for srv in [str(s) for s in candidates]:
                            try:
                                nats_attempt_server = srv
                                if os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                                    print(f"[hub-io] NATS connect try server={srv}")
                                elif trace:
                                    _rl_log("nats.try", f"[hub-io] nats connect try server={srv}", every_s=1.0)
                                nc = await _try_connect(srv)
                                last_exc = None
                                connected_server = srv
                                break
                            except Exception as e:
                                last_exc = e
                                if trace:
                                    _rl_log("nats.try_fail", f"[hub-io] nats connect failed server={srv} err={type(e).__name__}", every_s=1.0)
                                continue
                        if nc is None:
                            raise last_exc or RuntimeError("nats connect failed (no candidates)")
                        try:
                            nats_last_server = connected_server
                            nats_attempt_server = None
                        except Exception:
                            pass
                        try:
                            # Expose for external forced reconnect requests (debug/ops).
                            service._hub_root_nc = nc
                        except Exception:
                            pass

                        # Keepalive: periodically send a tiny NATS protocol frame from hub->root.
                        #
                        # Root's WS proxy already sends NATS `PING` frames to the hub, but the main purpose of that
                        # is to elicit outbound traffic hub->root (`PONG`) to keep some NAT/firewall mappings alive.
                        # In practice, hubs sometimes end up mostly silent and the WS gets closed abnormally (1006),
                        # then hub sees `UnexpectedEOF`. To reduce dependency on nats-py's internal ping futures and
                        # ensure regular outbound traffic, optionally send raw `PING` via `_send_command`+`_flush_pending`.
                        #
                        # This avoids using `flush()` and avoids creating `_pongs` futures which can later explode
                        # with `InvalidStateError` on late/cancelled PONGs.
                        try:
                            raw_keepalive_env = os.getenv("HUB_NATS_RAW_KEEPALIVE", "")
                            # Default OFF: this uses nats-py internals (`_send_command`/`_flush_pending`) from a
                            # separate task and can introduce hard-to-debug races. Root already sends NATS PINGs to
                            # elicit hub->root traffic (PONG), and nats-py also has its own ping interval.
                            raw_keepalive_enabled = raw_keepalive_env.strip() == "1"
                        except Exception:
                            raw_keepalive_enabled = False
                        if raw_keepalive_enabled:
                            try:
                                raw_keepalive_s = float(os.getenv("HUB_NATS_RAW_KEEPALIVE_S", "15") or "15")
                            except Exception:
                                raw_keepalive_s = 15.0
                            if raw_keepalive_s < 5.0:
                                raw_keepalive_s = 5.0

                            async def _raw_keepalive_loop() -> None:
                                ping_cmd = b"PING\r\n"
                                sent = 0
                                while True:
                                    await asyncio.sleep(raw_keepalive_s)
                                    try:
                                        is_closed_attr = getattr(nc, "is_closed", None)
                                        is_closed = is_closed_attr() if callable(is_closed_attr) else bool(is_closed_attr)
                                        if is_closed:
                                            return
                                    except Exception:
                                        pass
                                    try:
                                        sc = getattr(nc, "_send_command", None)
                                        fp = getattr(nc, "_flush_pending", None)
                                        if callable(sc) and callable(fp):
                                            await sc(ping_cmd)
                                            # Ensure the frame actually hits the wire; otherwise some proxies/LBs
                                            # may still consider the connection idle and close it (1006/EOF).
                                            try:
                                                await fp(force_flush=True)
                                            except TypeError:
                                                try:
                                                    await fp(True)
                                                except TypeError:
                                                    await fp()
                                        else:
                                            # Fallback: if internals changed, use public flush() to force outbound IO.
                                            flush = getattr(nc, "flush", None)
                                            if callable(flush):
                                                try:
                                                    await flush(timeout=1.0)
                                                except Exception:
                                                    pass
                                        sent += 1
                                        try:
                                            # Log early pings too: if we disconnect before reaching 10,
                                            # it is still useful to know whether we managed to send keepalives.
                                            if sent <= 3 and (os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace):
                                                _rl_log(
                                                    "nats.raw_keepalive_first",
                                                    f"[hub-io] nats raw keepalive sent={sent} every_s={raw_keepalive_s:.1f}",
                                                    every_s=0.5,
                                                )
                                            if (sent % 10) == 0 and (os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace):
                                                _rl_log(
                                                    "nats.raw_keepalive",
                                                    f"[hub-io] nats raw keepalive sent={sent} every_s={raw_keepalive_s:.1f}",
                                                    every_s=5.0,
                                                )
                                        except Exception:
                                            pass
                                    except Exception as e:
                                        try:
                                            if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                                _rl_log(
                                                    "nats.raw_keepalive_err",
                                                    f"[hub-io] nats raw keepalive failed err={type(e).__name__}: {e}",
                                                    every_s=1.0,
                                                )
                                        except Exception:
                                            pass
                                        # Keepalive is best-effort; connection supervisor will handle reconnects.
                                        pass

                            try:
                                raw_keepalive_task = asyncio.create_task(_raw_keepalive_loop(), name="adaos-nats-raw-keepalive")
                            except Exception:
                                raw_keepalive_task = None

                        # Track subscriptions explicitly. When the connection closes (or this task is cancelled),
                        # unsubscribing helps nats-py cancel internal `_wait_for_msgs()` tasks and avoids
                        # "Task was destroyed but it is pending!" warnings on reconnect/shutdown.
                        subs: list[Any] = []
                        sub_workers: list[asyncio.Task] = []
                        _route_dispatch_trace = (
                            os.getenv("HUB_ROUTE_DISPATCH_TRACE", "0") == "1"
                            or os.getenv("HUB_ROUTE_TRACE", "0") == "1"
                            or os.getenv("HUB_TRACE", "0") == "1"
                        )

                        def _route_dispatch_log(msg0: str) -> None:
                            if not _route_dispatch_trace:
                                return
                            try:
                                print(msg0)
                            except Exception:
                                pass

                        def _sub_qsize(sub0: Any) -> int | None:
                            try:
                                q0 = getattr(sub0, "_pending_queue", None)
                                if q0 is None:
                                    return None
                                qsize = getattr(q0, "qsize", None)
                                if callable(qsize):
                                    return int(qsize())
                            except Exception:
                                return None
                            return None

                        def _sub_pending_bytes(sub0: Any) -> int | None:
                            try:
                                pending_bytes = getattr(sub0, "pending_bytes", None)
                                if isinstance(pending_bytes, int):
                                    return int(pending_bytes)
                                if callable(pending_bytes):
                                    return int(pending_bytes())
                            except Exception:
                                return None
                            return None

                        async def _sub(subject: str, *, cb: Any):
                            traffic_class = hub_root_protocol_traffic_class(subject)
                            policy = hub_root_protocol_class_policy(traffic_class)
                            sub = await nc.subscribe(
                                subject,
                                pending_msgs_limit=int(policy.get("pending_msgs_limit") or 1),
                                pending_bytes_limit=int(policy.get("pending_bytes_limit") or 1024),
                            )
                            # `nats-py` queues SUB locally and may not push it to the server until
                            # some later flush / publish. That breaks Root->Hub routing because
                            # `route.to_hub.*` must be active before the first proxied request arrives.
                            fp = getattr(nc, "_flush_pending", None)
                            if callable(fp):
                                try:
                                    await asyncio.wait_for(fp(force_flush=True), timeout=2.0)
                                except TypeError:
                                    try:
                                        await asyncio.wait_for(fp(True), timeout=2.0)
                                    except TypeError:
                                        await asyncio.wait_for(fp(), timeout=2.0)
                            else:
                                await nc.flush(timeout=2.0)
                            subs.append(sub)
                            try:
                                observe_hub_root_protocol_subscription(
                                    subject,
                                    traffic_class=traffic_class,
                                    pending_msgs_limit=int(policy.get("pending_msgs_limit") or 0),
                                    pending_bytes_limit=int(policy.get("pending_bytes_limit") or 0),
                                    qsize=_sub_qsize(sub),
                                    pending_bytes=_sub_pending_bytes(sub),
                                )
                            except Exception:
                                pass

                            async def _runner() -> None:
                                try:
                                    pending_queue = getattr(sub, "_pending_queue", None)
                                    while True:
                                        if pending_queue is None:
                                            raise RuntimeError("subscription pending queue missing")
                                        msg = await pending_queue.get()
                                        try:
                                            msg_subject = ""
                                            msg_bytes = None
                                            started = None
                                            if _route_dispatch_trace and (
                                                subject == "route.to_hub.*"
                                                or subject.startswith("route.to_hub.")
                                                or subject.startswith("route.to_browser.")
                                            ):
                                                try:
                                                    msg_subject = str(getattr(msg, "subject", "") or "")
                                                except Exception:
                                                    msg_subject = ""
                                                try:
                                                    raw0 = bytes(getattr(msg, "data", b"") or b"")
                                                    msg_bytes = len(raw0)
                                                except Exception:
                                                    msg_bytes = None
                                                started = time.monotonic()
                                                _route_dispatch_log(
                                                    f"[hub-route:dispatch] start sub={subject} msg={msg_subject} qsize={_sub_qsize(sub)} bytes={msg_bytes}"
                                                )
                                            await cb(msg)
                                            try:
                                                observe_hub_root_protocol_subscription(
                                                    subject,
                                                    traffic_class=traffic_class,
                                                    qsize=_sub_qsize(sub),
                                                    pending_bytes=_sub_pending_bytes(sub),
                                                    dispatched=True,
                                                    message_bytes=msg_bytes,
                                                )
                                            except Exception:
                                                pass
                                            if started is not None:
                                                took_ms = (time.monotonic() - started) * 1000.0
                                                _route_dispatch_log(
                                                    f"[hub-route:dispatch] done sub={subject} msg={msg_subject} qsize={_sub_qsize(sub)} took_ms={took_ms:.1f}"
                                                )
                                        except asyncio.CancelledError:
                                            raise
                                        except Exception as e:
                                            try:
                                                observe_hub_root_protocol_subscription(
                                                    subject,
                                                    traffic_class=traffic_class,
                                                    qsize=_sub_qsize(sub),
                                                    pending_bytes=_sub_pending_bytes(sub),
                                                    handler_error=f"{type(e).__name__}: {e}",
                                                )
                                            except Exception:
                                                pass
                                            try:
                                                service._log.warning(
                                                    "nats subscription handler failed subject=%s type=%s err=%s",
                                                    subject,
                                                    type(e).__name__,
                                                    e,
                                                )
                                            except Exception:
                                                pass
                                        finally:
                                            try:
                                                pending_queue.task_done()
                                            except Exception:
                                                pass
                                            try:
                                                data0 = getattr(msg, "data", b"")
                                                if hasattr(data0, "__len__"):
                                                    sub._pending_size -= len(data0)
                                            except Exception:
                                                pass
                                except asyncio.CancelledError:
                                    return
                                except Exception as e:
                                    try:
                                        observe_hub_root_protocol_subscription(
                                            subject,
                                            traffic_class=traffic_class,
                                            qsize=_sub_qsize(sub),
                                            pending_bytes=_sub_pending_bytes(sub),
                                            handler_error=f"worker_stopped:{type(e).__name__}: {e}",
                                            worker_done=True,
                                        )
                                    except Exception:
                                        pass
                                    try:
                                        service._log.warning(
                                            "nats subscription worker stopped subject=%s type=%s err=%s",
                                            subject,
                                            type(e).__name__,
                                            e,
                                        )
                                    except Exception:
                                        pass
                                finally:
                                    try:
                                        observe_hub_root_protocol_subscription(
                                            subject,
                                            traffic_class=traffic_class,
                                            qsize=_sub_qsize(sub),
                                            pending_bytes=_sub_pending_bytes(sub),
                                            worker_done=True,
                                        )
                                    except Exception:
                                        pass

                            task = asyncio.create_task(_runner(), name=f"adaos-nats-sub-{subject}")
                            sub_workers.append(task)
                            return sub

                        # Outbound bridge: local bus -> root NATS.
                        # This lets skills/router publish `tg.output.<bot>.chat.<chat_id>` and have
                        # the backend deliver it to Telegram, without requiring TG_BOT_TOKEN on the hub.
                        try:
                            setattr(service, "_tg_output_nats_nc", nc)
                        except Exception:
                            pass

                        def _report_tg_outbox(
                            *,
                            drained: int = 0,
                            dropped: int = 0,
                            publish_ok: int = 0,
                            publish_fail: int = 0,
                            operation_key: str | None = None,
                            last_error: str | None = None,
                        ) -> None:
                            try:
                                q0 = getattr(service, "_tg_output_pending", None)
                                size0 = len(q0) if q0 is not None else 0
                            except Exception:
                                size0 = 0
                            try:
                                persist_path0 = getattr(service, "_tg_output_persist_path", None)
                                persist_path0 = str(persist_path0) if persist_path0 else ""
                            except Exception:
                                persist_path0 = ""
                            try:
                                max_outbox0 = int(os.getenv("HUB_TG_OUTBOX_MAX", "200") or "200")
                            except Exception:
                                max_outbox0 = 200
                            try:
                                observe_hub_root_integration_outbox(
                                    "telegram",
                                    size=size0,
                                    max_size=max_outbox0,
                                    durable_store=True,
                                    persist_path=persist_path0,
                                    persisted_size=size0,
                                    drained=drained,
                                    dropped=dropped,
                                    publish_ok=publish_ok,
                                    publish_fail=publish_fail,
                                    connected=bool(getattr(service, "_tg_output_nats_nc", None)),
                                    operation_key=operation_key,
                                    last_error=last_error,
                                )
                            except Exception:
                                pass

                        def _persist_tg_outbox() -> None:
                            try:
                                q0 = getattr(service, "_tg_output_pending", None)
                                if q0 is None:
                                    return
                                save_outbox_items("telegram", q0)
                            except Exception:
                                try:
                                    _report_tg_outbox(last_error="persist_failed")
                                except Exception:
                                    pass

                        def _tg_subject_protocol(subj0: str, payload0: Any) -> tuple[dict[str, Any], dict[str, Any] | None]:
                            try:
                                payload_dict = dict(payload0 or {}) if isinstance(payload0, dict) else {}
                            except Exception:
                                payload_dict = {}
                            existing = payload_dict.get("_protocol")
                            if isinstance(existing, dict) and str(existing.get("operation_key") or "").strip():
                                return payload_dict, existing
                            parts = str(subj0 or "").split(".")
                            bot_id = ""
                            chat_id = ""
                            if len(parts) >= 5 and parts[0] == "tg" and parts[1] == "output":
                                bot_id = str(parts[2] or "").strip()
                                if str(parts[3] or "").strip() == "chat":
                                    chat_id = ".".join(parts[4:]).strip()
                            target = payload_dict.get("target") if isinstance(payload_dict.get("target"), dict) else {}
                            bot_id = str(target.get("bot_id") or bot_id or "main-bot").strip() or "main-bot"
                            chat_id = str(target.get("chat_id") or chat_id).strip()
                            hub_ref = str(target.get("hub_id") or hub_id or "").strip() or "unknown_hub"
                            normalized = dict(payload_dict)
                            normalized.pop("_protocol", None)
                            try:
                                raw = _json.dumps(
                                    {"subject": subj0, "payload": normalized},
                                    ensure_ascii=False,
                                    sort_keys=True,
                                    separators=(",", ":"),
                                )
                            except Exception:
                                raw = _json.dumps({"subject": subj0, "repr": repr(normalized)}, ensure_ascii=False)
                            digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()
                            protocol = {
                                "flow_id": "hub_root.integration.telegram",
                                "message_type": "command",
                                "delivery_class": "must_not_lose",
                                "stream_id": f"hub-integration:telegram:{hub_ref}:{bot_id}:{chat_id or 'unknown_chat'}",
                                "message_id": f"tgmsg:{digest[:24]}",
                                "operation_key": f"tgop:{hub_ref}:{bot_id}:{chat_id or 'unknown_chat'}:{digest[:24]}",
                                "authority_epoch": f"hub:{hub_ref}",
                                "issued_at": time.time(),
                                "ttl_ms": 600_000,
                            }
                            payload_dict["_protocol"] = protocol
                            return payload_dict, protocol

                        def _split_tg_outbox_item(item: Any) -> tuple[str, bytes, dict[str, Any] | None]:
                            if isinstance(item, tuple):
                                if len(item) >= 3:
                                    subj0 = str(item[0] or "")
                                    data0 = bytes(item[1] or b"")
                                    meta0 = item[2] if isinstance(item[2], dict) else None
                                    return subj0, data0, meta0
                                if len(item) == 2:
                                    return str(item[0] or ""), bytes(item[1] or b""), None
                            return "", b"", None

                        # Drain outbox (replay replies produced while NATS was down/flapping).
                        try:
                            q = getattr(service, "_tg_output_pending", None)
                            if q:
                                drained = 0
                                max_drain = 200
                                try:
                                    max_drain = int(os.getenv("HUB_TG_OUTBOX_DRAIN_MAX", "200") or "200")
                                except Exception:
                                    max_drain = 200
                                while q and (max_drain <= 0 or drained < max_drain):
                                    try:
                                        subj0, data0, meta0 = _split_tg_outbox_item(q[0])
                                    except Exception:
                                        break
                                    try:
                                        await nc.publish(str(subj0), bytes(data0))
                                        fp = getattr(nc, "_flush_pending", None)
                                        if callable(fp):
                                            await fp(force_flush=True)
                                        try:
                                            q.popleft()
                                        except Exception:
                                            pass
                                        _persist_tg_outbox()
                                        drained += 1
                                        try:
                                            observe_hub_root_protocol_publish(
                                                str(subj0),
                                                ok=True,
                                                traffic_class="integration",
                                                payload_bytes=len(bytes(data0)),
                                            )
                                        except Exception:
                                            pass
                                        try:
                                            _report_tg_outbox(
                                                drained=1,
                                                operation_key=str((meta0 or {}).get("operation_key") or "").strip() or None,
                                            )
                                        except Exception:
                                            pass
                                    except Exception:
                                        try:
                                            observe_hub_root_protocol_publish(
                                                str(subj0),
                                                ok=False,
                                                traffic_class="integration",
                                                payload_bytes=len(bytes(data0)),
                                                error="drain_failed",
                                            )
                                        except Exception:
                                            pass
                                        break
                                if drained and (hub_nats_verbose or trace):
                                    _rl_log("nats.outbox", f"[hub-io] tg outbox drained={drained}", every_s=1.0)
                                if not drained:
                                    _report_tg_outbox()
                            else:
                                _report_tg_outbox()
                        except Exception:
                            try:
                                _report_tg_outbox(last_error="drain_failed")
                            except Exception:
                                pass

                        try:
                            if candidate_passive_mode:
                                pass
                            elif not bool(getattr(service, "_tg_output_bridge_hooked", False)):

                                def _on_local_output(ev: Event) -> None:
                                    try:
                                        subj = ev.type
                                        if not isinstance(subj, str) or not subj.startswith("tg.output."):
                                            return
                                        try:
                                            payload_dict, protocol_meta = _tg_subject_protocol(subj, ev.payload or {})
                                            data = _json.dumps(payload_dict, ensure_ascii=False).encode("utf-8")
                                        except Exception:
                                            protocol_meta = None
                                            data = b"{}"
                                        max_outbox = 200
                                        try:
                                            max_outbox = int(os.getenv("HUB_TG_OUTBOX_MAX", "200") or "200")
                                        except Exception:
                                            max_outbox = 200

                                        def _queue() -> None:
                                            dropped = 0
                                            last_op = str((protocol_meta or {}).get("operation_key") or "").strip() or None
                                            try:
                                                q = getattr(service, "_tg_output_pending", None)
                                                if q is None:
                                                    q = deque()
                                                    setattr(service, "_tg_output_pending", q)
                                                while max_outbox > 0 and len(q) >= max_outbox:
                                                    dropped_item = q.popleft()
                                                    _, _, dropped_meta = _split_tg_outbox_item(dropped_item)
                                                    dropped_op = str((dropped_meta or {}).get("operation_key") or "").strip() or None
                                                    if dropped_op and not last_op:
                                                        last_op = dropped_op
                                                    dropped += 1
                                                q.append((subj, data, protocol_meta))
                                                _persist_tg_outbox()
                                            except Exception:
                                                return
                                            try:
                                                _report_tg_outbox(dropped=dropped, operation_key=last_op)
                                            except Exception:
                                                pass

                                        nc2 = getattr(service, "_tg_output_nats_nc", None)
                                        if not nc2:
                                            _queue()
                                            return

                                        async def _publish_or_queue() -> None:
                                            try:
                                                await nc2.publish(subj, data)
                                                fp = getattr(nc2, "_flush_pending", None)
                                                if callable(fp):
                                                    await fp(force_flush=True)
                                                try:
                                                    observe_hub_root_protocol_publish(
                                                        subj,
                                                        ok=True,
                                                        traffic_class="integration",
                                                        payload_bytes=len(data),
                                                    )
                                                except Exception:
                                                    pass
                                                try:
                                                    _report_tg_outbox(
                                                        publish_ok=1,
                                                        operation_key=str((protocol_meta or {}).get("operation_key") or "").strip() or None,
                                                    )
                                                except Exception:
                                                    pass
                                            except Exception:
                                                try:
                                                    observe_hub_root_protocol_publish(
                                                        subj,
                                                        ok=False,
                                                        traffic_class="integration",
                                                        payload_bytes=len(data),
                                                        error="publish_failed",
                                                    )
                                                except Exception:
                                                    pass
                                                try:
                                                    _report_tg_outbox(
                                                        publish_fail=1,
                                                        operation_key=str((protocol_meta or {}).get("operation_key") or "").strip() or None,
                                                        last_error="publish_failed",
                                                    )
                                                except Exception:
                                                    pass
                                                _queue()

                                        try:
                                            loop = asyncio.get_running_loop()
                                            loop.create_task(_publish_or_queue())
                                        except RuntimeError:
                                            _queue()
                                    except Exception:
                                        return

                                # Prefix subscription on LocalEventBus works as "starts with".
                                core_bus.subscribe("tg.output.", _on_local_output)
                                setattr(service, "_tg_output_bridge_hooked", True)
                        except Exception:
                            pass
                        subj = f"tg.input.{hub_id}"
                        subj_legacy = f"io.tg.in.{hub_id}.text"
                        if candidate_passive_mode:
                            if hub_nats_verbose or not hub_nats_quiet:
                                print(
                                    f"[hub-io] NATS candidate runtime connected passively hub_id={hub_id} "
                                    f"instance={runtime_instance} role={runtime_role}"
                                )
                        elif hub_nats_verbose or not hub_nats_quiet:
                            print(f"[hub-io] NATS subscribe {subj} and legacy {subj_legacy}")
                        else:
                            # In quiet mode we still want a single signal that we are connected, because
                            # troubleshooting "TG stops responding" depends on correlating with NATS flaps.
                            _rl_log(
                                "nats.connected",
                                f"[hub-io] nats connected ({connected_server or 'unknown'})",
                                every_s=2.0,
                            )
                        try:
                            service._log.info(
                                "nats bridge connected server=%s hub_id=%s role=%s instance=%s passive=%s",
                                connected_server or "unknown",
                                hub_id,
                                runtime_role,
                                runtime_instance,
                                candidate_passive_mode,
                            )
                        except Exception:
                            pass
                        try:
                            if (
                                isinstance(established_ws_tag, str)
                                and established_ws_tag
                                and isinstance(ws_connect_tag, str)
                                and ws_connect_tag
                                and ws_connect_tag != established_ws_tag
                            ):
                                reconnect_payload = {
                                    "ts": time.time(),
                                    "server": connected_server or "unknown",
                                    "previous_ws_tag": established_ws_tag,
                                    "ws_tag": ws_connect_tag,
                                }
                                try:
                                    note_root_control_reconnect(
                                        summary="hub-root websocket session tag changed after reconnect",
                                        details=reconnect_payload,
                                    )
                                except Exception:
                                    pass
                                try:
                                    record_hub_root_transport_event(
                                        "reconnected",
                                        transport=_hub_root_transport_kind(connected_server),
                                        server=connected_server,
                                        summary="hub-root transport websocket tag changed after reconnect",
                                        details=reconnect_payload,
                                    )
                                except Exception:
                                    pass
                                try:
                                    service.ctx.bus.publish(
                                        Event(type="subnet.nats.reconnect", payload=reconnect_payload, source="io.nats")
                                    )
                                except Exception:
                                    pass
                            established_ws_tag = ws_connect_tag if isinstance(ws_connect_tag, str) and ws_connect_tag else established_ws_tag
                        except Exception:
                            pass
                        try:
                            configure_hub_root_transport_strategy(
                                effective_transport=_hub_root_transport_kind(connected_server),
                                selected_server=connected_server,
                                current_ws_tag=ws_connect_tag if isinstance(ws_connect_tag, str) else None,
                            )
                            record_hub_root_transport_event(
                                "connected",
                                transport=_hub_root_transport_kind(connected_server),
                                server=connected_server,
                                summary="hub-root control session established",
                                details={
                                    "phase": "initial_connect",
                                    "ws_tag": ws_connect_tag if isinstance(ws_connect_tag, str) else None,
                                },
                            )
                        except Exception:
                            pass
                        try:
                            mark_root_control_up(
                                summary="hub-root control session established",
                                details={
                                    "server": connected_server or "unknown",
                                    "phase": "initial_connect",
                                    "ws_tag": ws_connect_tag if isinstance(ws_connect_tag, str) else None,
                                },
                            )
                        except Exception:
                            pass
                        try:
                            asyncio.create_task(report_control_lifecycle("nats.initial_connect"))
                        except Exception:
                            pass
                        # First successful connect after failures
                        _emit_up()
                        try:
                            conf_local = getattr(service.ctx, "config", None)
                            if (
                                getattr(conf_local, "role", None) == "hub"
                                and bool(getattr(conf_local, "core_update_enabled", True))
                                and not candidate_passive_mode
                                and not _dev_api_serve_core_update_sync_disabled()
                            ):
                                async def _reconcile_core_release_after_connect() -> None:
                                    try:
                                        result = await asyncio.to_thread(reconcile_hub_core_update, conf_local)
                                        if isinstance(result, dict) and result.get("ok"):
                                            release = result.get("release") if isinstance(result.get("release"), dict) else {}
                                            set_integration_readiness(
                                                "github",
                                                status=ReadinessStatus.READY,
                                                summary="core update release probe succeeded through root",
                                                details={
                                                    "needs_update": bool(result.get("needs_update")),
                                                    "branch": str(release.get("branch") or result.get("branch") or ""),
                                                    "head_sha": str(release.get("head_sha") or ""),
                                                },
                                            )
                                        else:
                                            set_integration_readiness(
                                                "github",
                                                status=ReadinessStatus.DEGRADED,
                                                summary="core update release probe returned an unexpected response",
                                                details={"result_type": type(result).__name__},
                                            )
                                        if isinstance(result, dict) and result.get("needs_update"):
                                            try:
                                                service._log.info(
                                                    "core update reconcile scheduled hub_id=%s branch=%s release=%s",
                                                    hub_id,
                                                    result.get("branch") or "",
                                                    ((result.get("release") or {}) if isinstance(result.get("release"), dict) else {}).get("head_short_sha")
                                                    or ((result.get("release") or {}) if isinstance(result.get("release"), dict) else {}).get("head_sha")
                                                    or "",
                                                )
                                            except Exception:
                                                pass
                                    except Exception:
                                        try:
                                            set_integration_readiness(
                                                "github",
                                                status=ReadinessStatus.DEGRADED,
                                                summary="core update reconcile failed",
                                                details={"error": traceback.format_exc(limit=1).strip()},
                                            )
                                        except Exception:
                                            pass
                                        try:
                                            service._log.warning("core update reconcile failed hub_id=%s", hub_id, exc_info=True)
                                        except Exception:
                                            pass
                                loop.create_task(_reconcile_core_release_after_connect())
                        except Exception:
                            pass
                        nats_last_ok_at = time.monotonic()
                        # Baseline for RX watchdog (updated by patched WebSocketTransport.readline()).
                        try:
                            tr = getattr(nc, "_transport", None)
                            if tr is not None and not hasattr(tr, "_adaos_last_rx_at"):
                                setattr(tr, "_adaos_last_rx_at", time.monotonic())
                        except Exception:
                            pass

                        # Control channel: hub alias updates from backend
                        try:
                            ctl_alias = f"hub.control.{hub_id}.alias"

                            async def _ctl_alias_cb(msg):
                                try:
                                    data = _json.loads(msg.data.decode("utf-8"))
                                except Exception:
                                    data = {}
                                alias = (data or {}).get("alias")
                                if isinstance(alias, str) and alias:
                                    try:
                                        save_subnet_alias(alias, subnet_id=hub_id)
                                        try:
                                            service.ctx.bus.publish(Event(type="subnet.alias.changed", payload={"alias": alias, "subnet_id": hub_id}, source="io.nats"))
                                        except Exception:
                                            pass
                                        print(f"[hub-io] alias set via NATS: {alias}")
                                    except Exception:
                                        pass

                            await _sub(ctl_alias, cb=_ctl_alias_cb)
                            if hub_nats_verbose or not hub_nats_quiet:
                                print(f"[hub-io] NATS subscribe control {ctl_alias}")
                        except Exception:
                            pass
                        break
                    except Exception as e:
                        # Optionally print per-attempt diagnostics when verbose
                        try:
                            if os.getenv("HUB_NATS_VERBOSE", "0") == "1":
                                emsg = _explain_connect_error(e)
                                print(f"[hub-io] NATS connect failed: {emsg}")
                                try:
                                    tb = "".join(traceback.format_exception(type(e), e, e.__traceback__))
                                    print(tb.rstrip())
                                except Exception:
                                    pass
                            else:
                                if not (
                                    os.getenv("SILENCE_NATS_EOF", "0") == "1"
                                    and (type(e).__name__ == "UnexpectedEOF" or "unexpected eof" in str(e).lower())
                                ):
                                    # Minimal single-line failure for non-EOF issues
                                    print(f"[hub-io] NATS connect failed: {_explain_connect_error(e)}")
                        except Exception:
                            pass
                        # One-time down message and bus event while offline
                        try:
                            _emit_down("connect_error", e)
                        except Exception:
                            pass
                        # On failure, keep retrying with backoff; candidates are rebuilt each attempt.
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2.0, 30.0)

                async def cb(msg):
                    msg_subject = str(getattr(msg, "subject", "") or subj)
                    if trace:
                        try:
                            _rl_log("nats.msg", f"[hub-io] nats recv subject={msg_subject} bytes={len(getattr(msg, 'data', b'') or b'')}", every_s=0.2)
                        except Exception:
                            pass
                    try:
                        data = _json.loads(msg.data.decode("utf-8"))
                    except Exception:
                        data = {}
                    try:
                        # Media fetch: if event includes telegram media, download to local cache and annotate path
                        p = (data or {}).get("payload") or {}
                        typ = p.get("type") or (data.get("type") if isinstance(data.get("type"), str) else None)
                        bot_id = p.get("bot_id") or data.get("bot_id") or ""
                        file_id = p.get("file_id") if isinstance(p, dict) else None
                        if not file_id and isinstance(p, dict):
                            file_id = p.get("payload", {}).get("file_id") if isinstance(p.get("payload"), dict) else None
                        media_path = None
                        if isinstance(typ, str) and file_id and bot_id and typ in ("photo", "document", "audio", "voice"):
                            base = service.ctx.settings.api_base.rstrip("/")
                            token = os.getenv("ADAOS_TOKEN", "")
                            url = f"{base}/internal/tg/file?bot_id={bot_id}&file_id={file_id}"
                            cache_dir = service.ctx.paths.cache_dir()
                            cache_dir.mkdir(parents=True, exist_ok=True)
                            import urllib.request as _ureq
                            import uuid as _uuid
                            import mimetypes as _mtypes
                            # `urllib` is blocking; run the download and file write in a worker thread so it
                            # doesn't stall the hub event loop (and therefore NATS keepalives).
                            def _download() -> str:
                                req = _ureq.Request(url, headers={"X-AdaOS-Token": token})
                                with _ureq.urlopen(req, timeout=20) as resp:
                                    # Prefer filename from header; fallback to Content-Disposition; then use type
                                    fname = resp.headers.get("X-File-Name") or ""
                                    if not fname:
                                        cd = resp.headers.get("Content-Disposition") or ""
                                        try:
                                            import cgi as _cgi

                                            _val, _params = _cgi.parse_header(cd)
                                            fname = _params.get("filename") or ""
                                        except Exception:
                                            fname = ""
                                    if fname:
                                        import os as _os

                                        fname = _os.path.basename(fname)
                                    else:
                                        # fallback to type-based extension
                                        ctype = resp.headers.get("Content-Type") or "application/octet-stream"
                                        ext = _mtypes.guess_extension(ctype) or ""
                                        fname = f"tg_{_uuid.uuid4().hex}{ext}"
                                    dest = cache_dir / fname
                                    with open(dest, "wb") as out:
                                        out.write(resp.read())
                                return str(dest)

                            media_path = await asyncio.to_thread(_download)
                            # annotate
                            if isinstance(p, dict):
                                if isinstance(p.get("payload"), dict):
                                    p["payload"]["file_path"] = media_path
                                else:
                                    p["file_path"] = media_path
                            data["payload"] = p
                    except Exception:
                        pass
                    try:
                        service.ctx.bus.publish(Event(type=msg_subject, payload=data, source="io.nats", ts=time.time()))
                    except Exception:
                        pass

                if candidate_passive_mode:
                    try:
                        service._log.info(
                            "nats candidate runtime stays passive on root subjects hub_id=%s instance=%s",
                            hub_id,
                            runtime_instance,
                        )
                    except Exception:
                        pass
                else:
                    await _sub(subj, cb=cb)
                    receipt_subj = f"tg.receipt.{hub_id}"

                    async def _telegram_receipt_cb(msg):
                        try:
                            receipt_payload = _json.loads(msg.data.decode("utf-8"))
                        except Exception:
                            receipt_payload = {}
                        if not isinstance(receipt_payload, dict):
                            return
                        try:
                            service.ctx.bus.publish(
                                Event(
                                    type="tg.delivery.receipt",
                                    payload=receipt_payload,
                                    source="io.nats",
                                    ts=time.time(),
                                )
                            )
                        except Exception:
                            pass

                    await _sub(receipt_subj, cb=_telegram_receipt_cb)
                    try:
                        service._log.info(
                            "nats bridge subscribed subjects=%s,%s",
                            subj,
                            receipt_subj,
                        )
                    except Exception:
                        pass

                route_runtime = NatsRouteTunnelRuntime(
                    service,
                    rate_limited_log=_rl_log,
                    is_ready=is_ready,
                )
                await route_runtime.install(
                    nc=nc,
                    subscribe=_sub,
                    sub_workers=sub_workers,
                    hub_id=hub_id,
                    candidate_passive_mode=candidate_passive_mode,
                    runtime_instance=runtime_instance,
                    hub_nats_verbose=hub_nats_verbose,
                    hub_nats_quiet=hub_nats_quiet,
                )

                # Optional compatibility: also listen to additional hub aliases if explicitly configured
                if not candidate_passive_mode:
                    try:
                        aliases_env = os.getenv("HUB_INPUT_ALIASES", "")
                        aliases: List[str] = [a.strip() for a in aliases_env.split(",") if a.strip()]
                        seen = set([hub_id])
                        for aid in aliases:
                            if aid in seen:
                                continue
                            seen.add(aid)
                            alt = f"tg.input.{aid}"
                            if hub_nats_verbose or not hub_nats_quiet:
                                print(f"[hub-io] NATS subscribe (alias) {alt}")
                            await _sub(alt, cb=cb)
                            try:
                                service._log.info("nats bridge subscribed subject=%s", alt)
                            except Exception:
                                pass
                    except Exception:
                        pass

                # legacy text bridge -> wrap into minimal envelope and publish to same tg.input subject
                async def cb_legacy(msg):
                    try:
                        data = _json.loads(msg.data.decode("utf-8"))
                    except Exception:
                        data = {}
                    # transform into minimal io.input envelope compatible with downstream
                    try:
                        text = (data or {}).get("text") or ""
                        chat_id = str((data or {}).get("chat_id") or "")
                        tg_msg_id = (data or {}).get("tg_msg_id") or 0
                        env = {
                            "event_id": str(uuid.uuid4()).replace("-", ""),
                            "kind": "io.input",
                            "ts": datetime.utcnow().isoformat() + "Z",
                            "dedup_key": f"legacy:{chat_id}:{tg_msg_id}",
                            "payload": {
                                "type": "text",
                                "source": "telegram",
                                "bot_id": "",
                                "hub_id": hub_id,
                                "chat_id": chat_id,
                                "user_id": chat_id,
                                "update_id": str(tg_msg_id),
                                "payload": {"text": text, "meta": {"msg_id": tg_msg_id}},
                            },
                            "meta": {"hub_id": hub_id},
                        }
                    except Exception:
                        env = data
                    try:
                        service.ctx.bus.publish(Event(type=subj, payload=env, source="io.nats", ts=time.time()))
                    except Exception:
                        pass

                from datetime import datetime

                # Legacy classic path subscription only when explicitly enabled
                if not candidate_passive_mode:
                    try:
                        if os.getenv("HUB_LISTEN_LEGACY", "0") == "1":
                            await _sub(subj_legacy, cb=cb_legacy)
                            aliases_env = os.getenv("HUB_INPUT_ALIASES", "")
                            aliases: List[str] = [a.strip() for a in aliases_env.split(",") if a.strip()]
                            seen = set([hub_id])
                            for aid in aliases:
                                if aid in seen:
                                    continue
                                seen.add(aid)
                                alt_legacy = f"io.tg.in.{aid}.text"
                                if hub_nats_verbose or not hub_nats_quiet:
                                    print(f"[hub-io] NATS subscribe (alias legacy) {alt_legacy}")
                                await _sub(alt_legacy, cb=cb_legacy)
                                try:
                                    service._log.info("nats bridge subscribed subject=%s", alt_legacy)
                                except Exception:
                                    pass
                    except Exception:
                        pass
                # keep task alive
                try:
                    last_watchdog_tick_at = time.monotonic()
                    while True:
                        await asyncio.sleep(1.0)
                        now = time.monotonic()
                        tick_gap = now - last_watchdog_tick_at
                        last_watchdog_tick_at = now
                        try:
                            await asyncio.to_thread(
                                _write_nats_ws_diag_file,
                                nc,
                                server=nats_last_server,
                                source="periodic",
                            )
                        except Exception:
                            pass
                        try:
                            local_sidecar_url = realtime_sidecar_local_url()
                            sidecar_rx_watchdog_not_applicable = bool(
                                isinstance(nats_last_server, str)
                                and isinstance(local_sidecar_url, str)
                                and str(nats_last_server).strip() == str(local_sidecar_url).strip()
                            )
                        except Exception:
                            sidecar_rx_watchdog_not_applicable = False
                        skip_rx_watchdog = tick_gap > 5.0 or sidecar_rx_watchdog_not_applicable
                        if skip_rx_watchdog:
                            # If the event loop was stalled (e.g. a long sync handler), don't treat lack of RX
                            # during that window as a dead connection; refresh the baseline instead. When the
                            # client is connected to the local realtime sidecar, root-facing RX is owned by the
                            # sidecar process and is reflected in sidecar diagnostics, not this local transport.
                            try:
                                tr = getattr(nc, "_transport", None)
                                if tr is not None:
                                    setattr(tr, "_adaos_last_rx_at", now)
                            except Exception:
                                pass
                        # Watchdog: nats-py can silently lose its internal loops on unexpected WS/control frames
                        # (or other exceptions), leaving the socket open but the client effectively dead.
                        # If any core task terminates unexpectedly, restart the bridge.
                        try:
                            for _tname in ("_reading_task", "_flusher_task", "_ping_interval_task"):
                                if _tname == "_ping_interval_task" and bool(getattr(nc, "_adaos_ping_interval_task_disabled", False)):
                                    continue
                                _t = getattr(nc, _tname, None)
                                if isinstance(_t, asyncio.Task) and _t.done():
                                    _exc = None
                                    try:
                                        _exc = _t.exception()
                                    except asyncio.CancelledError:
                                        _exc = None
                                    # If the core task stopped without an exception, surface the last_error
                                    # so the supervisor can classify transient EOFs and quarantine the server.
                                    try:
                                        if _exc is None:
                                            _le = getattr(nc, "last_error", None)
                                            if isinstance(_le, Exception):
                                                _exc = _le
                                    except Exception:
                                        pass
                                    try:
                                        if _exc is None:
                                            tr = getattr(nc, "_transport", None)
                                            _le = getattr(tr, "_adaos_last_recv_error", None) if tr is not None else None
                                            if isinstance(_le, Exception):
                                                _exc = _le
                                    except Exception:
                                        pass
                                    # If task ended without exception, still restart - it should live forever.
                                    _msg = (
                                        f"[hub-io] nats watchdog: task={_tname} terminated exc={type(_exc).__name__}: {_exc}"
                                        if _exc
                                        else f"[hub-io] nats watchdog: task={_tname} terminated"
                                    )
                                    try:
                                        service._log.warning(_msg)
                                    except Exception:
                                        pass
                                    _rl_log("nats.watchdog", _msg, every_s=1.0)
                                    try:
                                        _log_nats_ws_diag(
                                            nc,
                                            server=nats_last_server,
                                            rate_key="nats.ws_diag.watchdog",
                                            every_s=1.0,
                                            source="watchdog",
                                            task_name=_tname,
                                            err=_exc if isinstance(_exc, Exception) else None,
                                        )
                                    except Exception:
                                        pass
                                    # This failure mode can happen without the NATS client's disconnected_cb firing
                                    # (for example, when `_reading_task` dies first). Emit a one-time DOWN signal so
                                    # readiness/stability reflect the incident immediately.
                                    try:
                                        _emit_down(kind=f"watchdog.{_tname}", err=_exc if isinstance(_exc, Exception) else None)
                                    except Exception:
                                        pass
                                    if _exc is not None:
                                        raise RuntimeError(_msg) from _exc
                                    raise RuntimeError(_msg)
                        except RuntimeError:
                            raise
                        except Exception:
                            pass
                        # RX watchdog: if we stop receiving WS frames (including keepalives) for too long,
                        # treat the connection as dead even if `nc.is_closed()` is still False.
                        try:
                            if skip_rx_watchdog:
                                raise StopIteration()
                            tr = getattr(nc, "_transport", None)
                            last_rx = getattr(tr, "_adaos_last_rx_at", None) if tr is not None else None
                            if isinstance(last_rx, (int, float)):
                                try:
                                    rx_timeout_s = float(os.getenv("HUB_NATS_RX_TIMEOUT_S", "90") or "90")
                                except Exception:
                                    rx_timeout_s = 90.0
                                if rx_timeout_s >= 10.0 and (time.monotonic() - float(last_rx)) > rx_timeout_s:
                                    _idle = time.monotonic() - float(last_rx)
                                    _msg = f"[hub-io] nats watchdog: no RX for {_idle:.1f}s (timeout={rx_timeout_s:.1f}s)"
                                    _rl_log("nats.watchdog", _msg, every_s=1.0)
                                    raise RuntimeError(_msg)
                        except StopIteration:
                            pass
                        except RuntimeError:
                            raise
                        except Exception:
                            pass

                        is_closed_attr = getattr(nc, "is_closed", None)
                        is_closed = is_closed_attr() if callable(is_closed_attr) else bool(is_closed_attr)
                        if is_closed:
                            # Extra WS diagnostics (close code/reason) for debugging UnexpectedEOF.
                            try:
                                if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                    tr = getattr(nc, "_transport", None)
                                    ws = getattr(tr, "_ws", None) if tr else None
                                    ws_closed = getattr(ws, "closed", None) if ws is not None else None
                                    ws_close_code = getattr(ws, "close_code", None) if ws is not None else None
                                    ws_exc = None
                                    try:
                                        exf = getattr(ws, "exception", None)
                                        if callable(exf):
                                            ws_exc = exf()
                                    except Exception:
                                        ws_exc = None
                                    _rl_log(
                                        "nats.ws_state",
                                        f"[hub-io] nats ws state: tag={getattr(tr, '_adaos_ws_tag', None) if tr is not None else None} server={nats_last_server} ws_url={getattr(tr, '_adaos_ws_url', None) if tr is not None else None} closed={ws_closed} close_code={ws_close_code} ws_exc={ws_exc}",
                                        every_s=1.0,
                                    )
                            except Exception:
                                pass
                            last_err = getattr(nc, "last_error", None)
                            details = f"{type(last_err).__name__}: {last_err}" if last_err else ""
                            try:
                                service._log.warning(
                                    "nats bridge closed server=%s hub_id=%s details=%s",
                                    nats_last_server,
                                    hub_id,
                                    details,
                                )
                            except Exception:
                                pass
                            raise RuntimeError(f"nats connection closed{(': ' + details) if details else ''}")
                finally:
                    try:
                        service._log.info("nats bridge finalizing hub_id=%s server=%s", hub_id, nats_last_server)
                    except Exception:
                        pass
                    def _keep_pending_task(task: asyncio.Task | None) -> None:
                        # asyncio keeps only weak refs to tasks; if we drop our references before a
                        # canceled task finishes, Python can emit "Task was destroyed but it is pending!".
                        try:
                            if not isinstance(task, asyncio.Task) or task.done():
                                return
                        except Exception:
                            return
                        try:
                            alive = getattr(service, "_nats_pending_cleanup_tasks", None)
                            if alive is None:
                                alive = set()
                                setattr(service, "_nats_pending_cleanup_tasks", alive)
                            alive.add(task)

                            def _drop(done: asyncio.Task) -> None:
                                try:
                                    alive.discard(done)
                                except Exception:
                                    pass

                            task.add_done_callback(_drop)
                        except Exception:
                            pass
                    try:
                        if raw_keepalive_task is not None:
                            try:
                                raw_keepalive_task.cancel()
                            except Exception:
                                pass
                            try:
                                _keep_pending_task(raw_keepalive_task)
                            except Exception:
                                pass
                            try:
                                await asyncio.wait_for(asyncio.gather(raw_keepalive_task, return_exceptions=True), timeout=1.0)
                            except Exception:
                                pass
                            raw_keepalive_task = None
                    except Exception:
                        pass
                    try:
                        if getattr(service, "_tg_output_nats_nc", None) is nc:
                            setattr(service, "_tg_output_nats_nc", None)
                    except Exception:
                        pass
                    try:
                        if getattr(service, "_hub_root_nc", None) is nc:
                            setattr(service, "_hub_root_nc", None)
                    except Exception:
                        pass
                    async def _force_close_ws_transport() -> None:
                        # WebSocket transports can leave client resources unclosed
                        # if the websocket is already None (close() becomes a no-op and wait_closed() hangs).
                        try:
                            tr = getattr(nc, "_transport", None)
                            if not tr:
                                return

                            ws = getattr(tr, "_ws", None)
                            close_task = getattr(tr, "_close_task", None)
                            client = getattr(tr, "_client", None)

                            try:
                                if ws is not None:
                                    await ws.close()
                            except Exception:
                                pass

                            # Unblock wait_closed() if it would otherwise await an unresolved Future.
                            try:
                                if close_task is not None and hasattr(close_task, "done") and not close_task.done():
                                    close_task.set_result(None)
                            except Exception:
                                pass

                            try:
                                if client is not None:
                                    await client.close()
                            except Exception:
                                pass

                            try:
                                setattr(tr, "_ws", None)
                                setattr(tr, "_client", None)
                            except Exception:
                                pass
                        except Exception:
                            pass

                    # Route tunnel state is owned and closed by its connection runtime.
                    try:
                        if route_runtime is not None:
                            await route_runtime.close()
                    except Exception:
                        pass
                    try:
                        for task in list(sub_workers):
                            try:
                                task.cancel()
                            except Exception:
                                pass
                            try:
                                _keep_pending_task(task if isinstance(task, asyncio.Task) else None)
                            except Exception:
                                pass
                        if sub_workers:
                            try:
                                await asyncio.wait_for(asyncio.gather(*sub_workers, return_exceptions=True), timeout=1.0)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        # Unsubscribe all subscriptions explicitly to ensure nats-py cancels
                        # internal subscription tasks before the next reconnect attempt.
                        for sub in list(subs):
                            try:
                                unsub = sub.unsubscribe()
                                if asyncio.iscoroutine(unsub):
                                    await unsub
                            except Exception:
                                pass

                        # Ensure internal subscription tasks are stopped even if the connection is already closed.
                        for sub in list(subs):
                            try:
                                stop = getattr(sub, "_stop_processing", None)
                                if callable(stop):
                                    stop()
                            except Exception:
                                pass

                        # Await/cancel internal subscription tasks, if present.
                        wait_tasks: list[asyncio.Task] = []
                        for sub in list(subs):
                            t = getattr(sub, "_wait_for_msgs_task", None)
                            if isinstance(t, asyncio.Task) and not t.done():
                                try:
                                    t.cancel()
                                except Exception:
                                    pass
                                try:
                                    _keep_pending_task(t)
                                except Exception:
                                    pass
                                wait_tasks.append(t)
                        if wait_tasks:
                            try:
                                await asyncio.wait_for(asyncio.gather(*wait_tasks, return_exceptions=True), timeout=1.0)
                            except Exception:
                                pass
                    except Exception:
                        pass
                    try:
                        await asyncio.wait_for(nc.drain(), timeout=2.0)
                    except asyncio.CancelledError:
                        if _current_async_task_is_cancelling():
                            raise
                    except Exception:
                        pass
                    await _run_bounded_async_cleanup(nc.close, timeout_s=2.0)
                    await _run_bounded_async_cleanup(_force_close_ws_transport, timeout_s=2.0)
                    # Give canceled subscription tasks a chance to finish to avoid
                    # "Task was destroyed but it is pending!" warnings.
                    try:
                        await asyncio.sleep(0)
                    except Exception:
                        pass

            async def _maybe_snapshot_root_logs(
                *,
                trace: bool,
                force: bool = False,
                tag_override: str | None = None,
                server_override: str | None = None,
            ) -> None:
                try:
                    if os.getenv("HUB_ROOT_LOG_SNAPSHOT", "0") != "1":
                        return
                    now = time.monotonic()
                    try:
                        snap_every_s = float(os.getenv("HUB_ROOT_LOG_SNAPSHOT_EVERY_S", "60") or "60")
                    except Exception:
                        snap_every_s = 60.0
                    if snap_every_s < 5.0:
                        snap_every_s = 5.0

                    nonlocal last_root_snapshot_at
                    if (not force) and last_root_snapshot_at is not None and (now - last_root_snapshot_at) < snap_every_s:
                        return
                    last_root_snapshot_at = now

                    base = None
                    try:
                        from urllib.parse import urlparse as _urlparse

                        u = _urlparse(str(server_override or nats_last_server or ""))
                        host = (u.hostname or "").strip()
                        if host:
                            # Dev endpoints (like /v1/dev/log_tail) live on the API host, not the NATS host.
                            # If we connected to `nats.<domain>`, try `api.<domain>` for snapshots.
                            if host.startswith("nats.") and host.count(".") >= 2:
                                host = "api." + host.split(".", 1)[1]
                            base = ("https://" if str(u.scheme).startswith("wss") else "http://") + host
                    except Exception:
                        base = None
                    if not base:
                        return

                    files = os.getenv("HUB_ROOT_LOG_SNAPSHOT_FILES", "reverse-proxy.log,nats.log,backend-b.log") or ""
                    want = [x.strip() for x in files.split(",") if x.strip()]
                    if not want:
                        return
                    try:
                        snapshot_lines = int(os.getenv("HUB_ROOT_LOG_SNAPSHOT_LINES", "250") or "250")
                    except Exception:
                        snapshot_lines = 250
                    if snapshot_lines < 50:
                        snapshot_lines = 50

                    out_dir = Path(".adaos") / "root_log_snapshots"
                    out_dir.mkdir(parents=True, exist_ok=True)

                    def _fetch_one(fname: str) -> tuple[str, str]:
                        import urllib.parse as _up
                        import urllib.request as _ureq

                        qs = _up.urlencode({"file": fname, "lines": str(snapshot_lines)})
                        url = f"{base}/v1/dev/log_tail?{qs}"
                        hdrs = {}
                        try:
                            # Root dev endpoints are protected by X-Root-Token.
                            tok = (os.getenv("HUB_ROOT_LOG_SNAPSHOT_ROOT_TOKEN", "") or "").strip()
                            if not tok:
                                tok = (os.getenv("ROOT_TOKEN", "") or "").strip()
                            if not tok:
                                tok = (os.getenv("ADAOS_ROOT_OWNER_TOKEN", "") or "").strip()
                            if not tok:
                                # Back-compat: previously this env existed and users sometimes set
                                # `Bearer <token>`; accept and normalize it.
                                tok = (os.getenv("HUB_ROOT_LOG_SNAPSHOT_AUTH", "") or "").strip()
                            if tok.lower().startswith("bearer "):
                                tok = tok.split(" ", 1)[1].strip()
                            if tok:
                                hdrs["X-Root-Token"] = tok
                        except Exception:
                            pass
                        req = _ureq.Request(url, headers=hdrs)
                        with _ureq.urlopen(req, timeout=10) as resp:
                            body = resp.read().decode("utf-8", errors="replace")
                        return url, body

                    def _extract_tag_lines(body: str, tag: str) -> str:
                        try:
                            if not tag:
                                return ""
                            import json as _json
                            import re as _re

                            obj = _json.loads(body)
                            lines0 = obj.get("lines", [])
                            if not isinstance(lines0, list):
                                return ""
                            tag_s = str(tag)
                            hub_prefix = tag_s.rsplit("-", 1)[0] if "-" in tag_s else tag_s
                            tag_hits = [str(s) for s in lines0 if isinstance(s, str) and tag_s in s]
                            conn_ids: set[str] = set()
                            for line0 in tag_hits:
                                try:
                                    for m0 in _re.finditer(r'"conn":"([^"]+)"', line0):
                                        conn_ids.add(str(m0.group(1)))
                                except Exception:
                                    continue
                            route_prefixes = (
                                f"route.to_browser.{hub_prefix}--",
                                f"route.to_hub.{hub_prefix}--",
                                # v2 subjects include hubId as a separate token: route.v2.to_browser.<hubId>.<key>
                                f"route.v2.to_browser.{hub_prefix}.{hub_prefix}--",
                                f"route.v2.to_hub.{hub_prefix}.{hub_prefix}--",
                            )
                            include_extra = str(os.getenv("HUB_ROOT_LOG_SNAPSHOT_EXTRACT_EXTRA", "0") or "0").strip() == "1"
                            extra_keywords = (
                                "http proxy failed",
                                "ws tunnel:",
                                "nats http route",
                                "nats keepalive pong missing",
                                "nats route chunk (client->proxy)",
                                "nats route upstream write",
                                "conn close",
                                "upstream close",
                                "upstream error",
                                "ws close 1006 diag",
                                "ws socket data after keepalive",
                                "ws socket readable after keepalive",
                                "ws socket pause",
                                "ws socket resume",
                                "ws socket end",
                                "ws socket close",
                                "ws socket error",
                                "ws error",
                                "ws upstream closed",
                                "closing superseded hub ws-nats connection",
                            )
                            hits: list[str] = []
                            for item in lines0:
                                if not isinstance(item, str):
                                    continue
                                line = str(item)
                                include = tag_s in line
                                if not include and conn_ids:
                                    try:
                                        include = any(cid and cid in line for cid in conn_ids)
                                    except Exception:
                                        include = False
                                if not include:
                                    try:
                                        include = any(pref in line for pref in route_prefixes)
                                    except Exception:
                                        include = False
                                if include_extra and (not include):
                                    try:
                                        include = any(kw in line for kw in extra_keywords)
                                    except Exception:
                                        include = False
                                if include:
                                    hits.append(line)
                            # Keep this file small and focused.
                            return "\n".join(hits[-1000:])
                        except Exception:
                            return ""

                    try:
                        if isinstance(tag_override, str) and tag_override.strip():
                            tag0 = tag_override.strip()
                        else:
                            tag0 = ws_connect_tag if isinstance(ws_connect_tag, str) else ""
                    except Exception:
                        tag0 = ws_connect_tag if isinstance(ws_connect_tag, str) else ""
                    ts = time.strftime("%Y%m%d_%H%M%SZ", time.gmtime())
                    for fname in want:
                        try:
                            url, body = await asyncio.to_thread(_fetch_one, fname)
                            fn = out_dir / f"{ts}__{(tag0 or 'no_tag')}__{fname.replace('/', '_')}"
                            fn.write_text(body, encoding="utf-8", errors="replace")
                            try:
                                ex = _extract_tag_lines(body, tag0)
                                if ex:
                                    fn2 = out_dir / f"{ts}__{(tag0 or 'no_tag')}__{fname.replace('/', '_')}__extract.log"
                                    fn2.write_text(ex, encoding="utf-8", errors="replace")
                                    try:
                                        if os.getenv("HUB_ROOT_LOG_SNAPSHOT_EXTRACT_PRINT", "0") == "1":
                                            try:
                                                tail_n = int(os.getenv("HUB_ROOT_LOG_SNAPSHOT_EXTRACT_TAIL", "40") or "40")
                                            except Exception:
                                                tail_n = 40
                                            if tail_n < 1:
                                                tail_n = 1
                                            tail_lines = ex.splitlines()
                                            tail = "\n".join(tail_lines[-tail_n:]) if tail_lines else ""
                                            if tail:
                                                # Include best-effort recency hint: extracted tails can be old if
                                                # the upstream service has been quiet (e.g. only a few errors in nats.log).
                                                try:
                                                    from datetime import datetime, timezone

                                                    newest_ts = None
                                                    for raw in reversed(tail_lines):
                                                        try:
                                                            token = (str(raw).strip().split(" ", 1)[0] or "").strip()
                                                            if not token:
                                                                continue
                                                            if token.endswith("Z"):
                                                                token = token[:-1] + "+00:00"
                                                            dt = datetime.fromisoformat(token)
                                                            if dt.tzinfo is None:
                                                                dt = dt.replace(tzinfo=timezone.utc)
                                                            newest_ts = dt.timestamp()
                                                            break
                                                        except Exception:
                                                            continue
                                                    age_s = None
                                                    if isinstance(newest_ts, (int, float)) and newest_ts > 0:
                                                        age_s = round(max(0.0, time.time() - float(newest_ts)), 3)
                                                except Exception:
                                                    newest_ts = None
                                                    age_s = None
                                                print(
                                                    f"[hub-io] root log extract tail file={fn2} lines={len(tail_lines)}"
                                                    + (f" newest_age_s={age_s}" if age_s is not None else "")
                                                )
                                                try:
                                                    extract_log = logging.getLogger("adaos.hub-io.root-log-extract")
                                                    interesting_lines: list[str] = []
                                                    interesting_markers = (
                                                        "http route: timeout",
                                                        "http proxy failed",
                                                        "ws route: open ack fallback elapsed",
                                                        "ws route: open ack received",
                                                        "closing superseded hub ws-nats connection",
                                                        "hub ws-nats auth ok",
                                                        "upstream close",
                                                        "nats route downstream send failed",
                                                        "nats route upstream write missing",
                                                    )
                                                    for tail_line in tail_lines:
                                                        text = str(tail_line)
                                                        if any(marker in text for marker in interesting_markers):
                                                            interesting_lines.append(text)
                                                    interesting_lines = interesting_lines[-12:]
                                                    extract_log.warning(
                                                        "root log extract tail file=%s lines=%s newest_age_s=%s interesting_lines=%s",
                                                        fn2,
                                                        len(tail_lines),
                                                        age_s,
                                                        len(interesting_lines),
                                                    )
                                                    for tail_line in interesting_lines:
                                                        extract_log.warning(
                                                            "root log extract line file=%s line=%s",
                                                            fn2,
                                                            tail_line,
                                                        )
                                                    if (
                                                        os.getenv("HUB_ROOT_LOG_EXTRACT_VERBOSE", "0") == "1"
                                                        or trace
                                                    ):
                                                        for tail_line in tail_lines:
                                                            extract_log.debug(
                                                                "root log extract raw file=%s line=%s",
                                                                fn2,
                                                                str(tail_line),
                                                            )
                                                except Exception:
                                                    pass
                                                if (
                                                    os.getenv("HUB_ROOT_LOG_EXTRACT_VERBOSE", "0") == "1"
                                                    or trace
                                                ):
                                                    print(tail)
                                    except Exception:
                                        pass
                            except Exception:
                                pass
                            if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                _rl_log("root.snap", f"[hub-io] saved root log snapshot {fn} (from {url})", every_s=1.0)
                        except Exception as _se:
                            if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or trace:
                                _rl_log("root.snap_fail", f"[hub-io] root log snapshot failed file={fname} err={type(_se).__name__}: {_se}", every_s=1.0)
                except Exception:
                    return

            # Supervisor wrapper: never crash on unhandled errors; restart with backoff
            async def _nats_bridge_supervisor() -> None:
                delay = 1.0
                while True:
                    started_at = time.monotonic()
                    trace = os.getenv("HUB_NATS_TRACE", "0") == "1"
                    try:
                        _rl_log("nats.supervisor.start", "[hub-io] nats supervisor: start bridge", every_s=5.0)
                        await _nats_bridge()
                        service._log.warning("nats bridge returned without error; restarting hub_id=%s", hub_id)
                        await asyncio.sleep(delay)
                        delay = min(max(delay, 0.5), 2.0)
                    except asyncio.CancelledError:
                        if _current_async_task_is_cancelling():
                            try:
                                service._log.info(
                                    "nats supervisor cancelled hub_id=%s server=%s",
                                    hub_id,
                                    _resolve_nats_log_server(
                                        current_attempt=nats_attempt_server,
                                        connected_server=nats_last_server,
                                    ),
                                )
                            except Exception:
                                pass
                            raise
                        # An awaited transport/cleanup coroutine cancelled
                        # itself.  Treat that as a failed attempt, not as an
                        # instruction to permanently stop the supervisor.
                        service._log.warning(
                            "nats bridge surfaced child cancellation; restarting hub_id=%s server=%s",
                            hub_id,
                            _resolve_nats_log_server(
                                current_attempt=nats_attempt_server,
                                connected_server=nats_last_server,
                            ),
                        )
                        try:
                            record_hub_root_transport_event(
                                "child_cancellation_recovered",
                                server=_resolve_nats_log_server(
                                    current_attempt=nats_attempt_server,
                                    connected_server=nats_last_server,
                                ),
                                summary="hub-root supervisor recovered a child transport cancellation",
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(0.5)
                        delay = min(max(delay, 0.5), 2.0)
                        continue
                    except Exception as e:
                        ran_for_s = time.monotonic() - started_at
                        is_transient = is_transient_nats_error(e)
                        error_summary = nats_error_summary(e)
                        try:
                            if is_transient:
                                service._log.warning(
                                    "nats transient disconnect hub_id=%s server=%s type=%s err=%s ran_for_s=%.1f",
                                    hub_id,
                                    _resolve_nats_log_server(
                                        current_attempt=nats_attempt_server,
                                        connected_server=nats_last_server,
                                    ),
                                    type(e).__name__,
                                    error_summary,
                                    ran_for_s,
                                )
                            else:
                                service._log.warning(
                                    "nats supervisor error hub_id=%s server=%s type=%s err=%s",
                                    hub_id,
                                    _resolve_nats_log_server(
                                        current_attempt=nats_attempt_server,
                                        connected_server=nats_last_server,
                                    ),
                                    type(e).__name__,
                                    error_summary,
                                )
                        except Exception:
                            pass
                        try:
                            if not is_transient:
                                service._log.warning(
                                    "nats encountered error hub_id=%s server=%s type=%s err=%s",
                                    hub_id,
                                    _resolve_nats_log_server(
                                        current_attempt=nats_attempt_server,
                                        connected_server=nats_last_server,
                                    ),
                                    type(e).__name__,
                                    error_summary,
                                )
                        except Exception:
                            pass
                        try:
                            if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or not is_transient:
                                print(f"[hub-io] nats: encountered error: {error_summary}")
                        except Exception:
                            pass
                        try:
                            local_sidecar_url = realtime_sidecar_local_url()
                            error_server = _resolve_nats_log_server(
                                current_attempt=nats_attempt_server,
                                connected_server=nats_last_server,
                            )
                            using_sidecar = bool(
                                isinstance(error_server, str)
                                and isinstance(local_sidecar_url, str)
                                and str(error_server).strip() == str(local_sidecar_url).strip()
                            )
                        except Exception:
                            using_sidecar = False
                        try:
                            if using_sidecar:
                                async def _print_sidecar_tail() -> None:
                                    def _tail(path: Path, lines: int) -> tuple[Path, list[str]]:
                                        return path, _read_sidecar_tail_lines(path, lines=lines)

                                    try:
                                        log_path, log_tail = await asyncio.to_thread(_tail, realtime_sidecar_log_path(), 40)
                                        if log_tail:
                                            try:
                                                sidecar_log = logging.getLogger("adaos.hub-io.sidecar")
                                                sidecar_log.warning(
                                                    "adaos-realtime log tail file=%s lines=%s last=%s",
                                                    log_path,
                                                    len(log_tail),
                                                    _sidecar_tail_summary(log_tail),
                                                )
                                                if _sidecar_tail_log_each_enabled():
                                                    for line in log_tail:
                                                        sidecar_log.debug(
                                                            "adaos-realtime log line file=%s line=%s",
                                                            log_path,
                                                            line,
                                                        )
                                            except Exception:
                                                pass
                                            if _sidecar_tail_log_each_enabled():
                                                print("\n".join(log_tail))
                                    except Exception:
                                        pass
                                    try:
                                        diag_path, diag_tail = await asyncio.to_thread(_tail, realtime_sidecar_diag_path(), 10)
                                        if diag_tail:
                                            try:
                                                sidecar_log = logging.getLogger("adaos.hub-io.sidecar")
                                                sidecar_log.warning(
                                                    "adaos-realtime diag tail file=%s lines=%s last=%s",
                                                    diag_path,
                                                    len(diag_tail),
                                                    _sidecar_tail_summary(diag_tail),
                                                )
                                                if _sidecar_tail_log_each_enabled():
                                                    for line in diag_tail:
                                                        sidecar_log.debug(
                                                            "adaos-realtime diag line file=%s line=%s",
                                                            diag_path,
                                                            line,
                                                        )
                                            except Exception:
                                                pass
                                            if _sidecar_tail_log_each_enabled():
                                                print("\n".join(diag_tail))
                                    except Exception:
                                        pass

                                asyncio.create_task(_print_sidecar_tail(), name="adaos-realtime-log-tail")
                        except Exception:
                            pass
                        # Optional delayed snapshot: root-side logs (ECONNRESET/conn close) can be emitted
                        # slightly after the hub notices EOF. A second tail a few seconds later often captures it.
                        try:
                            # `HUB_ROOT_LOG_SNAPSHOT_AFTER_ERR_S` accepts a comma list of delays in seconds.
                            # Set it to empty to disable follow-up snapshots entirely.
                            after_env = os.getenv("HUB_ROOT_LOG_SNAPSHOT_AFTER_ERR_S")
                            if after_env is None:
                                after_env = "0,3"
                        except Exception:
                            after_env = "0,3"
                        delays: list[float] = []
                        try:
                            if str(after_env or "").strip():
                                for part in str(after_env).split(","):
                                    p = str(part).strip()
                                    if not p:
                                        continue
                                    try:
                                        v = float(p)
                                    except Exception:
                                        continue
                                    if v >= 0:
                                        delays.append(v)
                            else:
                                delays = []
                        except Exception:
                            delays = []
                        # Schedule snapshots in the background so reconnect is not delayed by HTTP tailing.
                        try:
                            if delays and os.getenv("HUB_ROOT_LOG_SNAPSHOT", "0") == "1":
                                tag0 = ws_connect_tag if isinstance(ws_connect_tag, str) else None
                                srv0 = _resolve_nats_log_server(
                                    current_attempt=nats_attempt_server,
                                    connected_server=nats_last_server,
                                )

                                async def _snap_later(delay_s: float) -> None:
                                    try:
                                        if delay_s > 0:
                                            await asyncio.sleep(min(30.0, max(0.1, float(delay_s))))
                                    except Exception:
                                        pass
                                    try:
                                        await _maybe_snapshot_root_logs(
                                            trace=trace,
                                            force=True,
                                            tag_override=tag0,
                                            server_override=srv0,
                                        )
                                    except Exception:
                                        pass

                                for after_s in delays[:8]:
                                    try:
                                        asyncio.create_task(_snap_later(float(after_s)), name="adaos-root-log-snapshot")
                                    except Exception:
                                        pass
                        except Exception:
                            pass
                        # No blocking snapshots here: supervisor keeps retrying promptly.
                        try:
                            delays = []
                        except Exception:
                            pass

                        try:
                            auto_env = os.getenv("HUB_NATS_WS_AUTO_FALLBACK")
                            if auto_env is None:
                                auto_fallback = False
                            else:
                                auto_fallback = str(auto_env).strip().lower() not in ("0", "false", "off", "no")
                            if (
                                auto_fallback
                                and os.name == "nt"
                                and (last_ws_transport or "").lower() == "websockets"
                                and is_transient
                            ):
                                if os.getenv("HUB_NATS_WS_IMPL", "").lower() != "aiohttp":
                                    os.environ["HUB_NATS_WS_IMPL"] = "aiohttp"
                                    try:
                                        service._log.warning(
                                            "nats ws auto-fallback: switching to aiohttp transport after %s",
                                            type(e).__name__,
                                        )
                                    except Exception:
                                        pass
                                    try:
                                        print("[hub-io] nats ws auto-fallback -> aiohttp (HUB_NATS_WS_AUTO_FALLBACK=1)")
                                    except Exception:
                                        pass
                                    try:
                                        configure_hub_root_transport_strategy(
                                            hypothesis={
                                                "selector_loop": bool(os.name == "nt" and os.getenv("ADAOS_WIN_SELECTOR_LOOP", "0") == "1"),
                                                "ws_impl": "aiohttp",
                                                "raw_keepalive": _env_truthy(os.getenv("HUB_NATS_RAW_KEEPALIVE"), default=False),
                                            }
                                        )
                                        record_hub_root_transport_event(
                                            "auto_fallback",
                                            transport="ws",
                                            server=nats_last_server,
                                            summary="hub-root WS client implementation switched to aiohttp after transient failure",
                                            error=str(e),
                                        )
                                    except Exception:
                                        pass
                        except Exception:
                            pass

                        try:
                            q_min_uptime_s = float(os.getenv("HUB_NATS_QUARANTINE_MIN_UPTIME_S", "90") or "90")
                        except Exception:
                            q_min_uptime_s = 90.0
                        try:
                            q_for_s = float(os.getenv("HUB_NATS_QUARANTINE_S", "300") or "300")
                        except Exception:
                            q_for_s = 300.0
                        try:
                            if is_transient and ran_for_s < q_min_uptime_s and isinstance(nats_last_server, str) and nats_last_server:
                                q_seconds = max(30.0, q_for_s)
                                local_sidecar_url = realtime_sidecar_local_url()
                                if _should_quarantine_nats_candidate(
                                    nats_last_server,
                                    local_sidecar_url=local_sidecar_url,
                                ):
                                    nats_server_quarantine_until[nats_last_server] = time.monotonic() + q_seconds
                                    _rl_log(
                                        "nats.supervisor.quarantine",
                                        f"[hub-io] nats supervisor: quarantine server={nats_last_server} for {q_seconds:.0f}s (ran_for={ran_for_s:.1f}s)",
                                        every_s=1.0,
                                    )
                                else:
                                    _rl_log(
                                        "nats.supervisor.quarantine.skip",
                                        f"[hub-io] nats supervisor: skip quarantine for local sidecar={nats_last_server} (ran_for={ran_for_s:.1f}s)",
                                        every_s=1.0,
                                    )
                        except Exception:
                            pass

                        try:
                            if is_transient and using_sidecar and _hub_nats_sidecar_failover_on_transient():
                                local_sidecar_url = realtime_sidecar_local_url()
                                if isinstance(local_sidecar_url, str) and local_sidecar_url:
                                    q_seconds = _hub_nats_sidecar_quarantine_s()
                                    nats_server_quarantine_until[local_sidecar_url] = time.monotonic() + q_seconds
                                    _rl_log(
                                        "nats.supervisor.sidecar_quarantine",
                                        (
                                            f"[hub-io] nats supervisor: sidecar failover quarantine "
                                            f"server={local_sidecar_url} for {q_seconds:.0f}s "
                                            f"(ran_for={ran_for_s:.1f}s transient={is_transient})"
                                        ),
                                        every_s=1.0,
                                    )
                        except Exception:
                            pass

                        if ran_for_s >= 10.0 or is_transient:
                            delay = 0.5
                        try:
                            ok_ago = None
                            if nats_last_ok_at is not None:
                                ok_ago = time.monotonic() - nats_last_ok_at
                            _rl_log(
                                "nats.supervisor.retry",
                                f"[hub-io] nats supervisor: retry in {delay:.1f}s (ran_for={ran_for_s:.1f}s ok_ago={ok_ago:.1f}s transient={is_transient})",
                                every_s=1.0,
                            )
                        except Exception:
                            pass
                        await asyncio.sleep(delay)
                        if ran_for_s < 10.0 and not is_transient:
                            delay = min(delay * 2.0, 30.0)
                        else:
                            delay = min(max(delay, 0.5), 2.0)

            candidate_transport_deferred = _hub_root_candidate_passive_mode()
            service._start_hub_root_bridge_task(
                _nats_bridge_supervisor,
                start_immediately=not candidate_transport_deferred,
            )
            service._start_boot_task_once(
                service._hub_root_bridge_watchdog_task_name,
                service._hub_root_bridge_watchdog,
            )
            if candidate_transport_deferred:
                service._log.info(
                    "nats candidate transport deferred until promotion instance=%s",
                    str(runtime_identity_snapshot().get("runtime_instance_id") or ""),
                )
    except Exception:
        try:
            if os.getenv("HUB_NATS_VERBOSE", "0") == "1" or os.getenv("ADAOS_CLI_DEBUG", "0") == "1":
                print("[hub-io] nats init failed")
                try:
                    tb = "".join(traceback.format_exception(*__import__("sys").exc_info()))
                    print(tb.rstrip())
                except Exception:
                    pass
        except Exception:
            pass


class NatsRootTransportRuntime:
    """Own one composed hub-root transport lifecycle."""

    def __init__(
        self,
        service: Any,
        *,
        core_bus: Any,
        startup_stage_mark: Any,
        report_control_lifecycle: Any,
    ) -> None:
        self._service = service
        self._core_bus = core_bus
        self._startup_stage_mark = startup_stage_mark
        self._report_control_lifecycle = report_control_lifecycle

    async def run(self) -> None:
        await _run_nats_root_transport(
            self._service,
            core_bus=self._core_bus,
            startup_stage_mark=self._startup_stage_mark,
            report_control_lifecycle=self._report_control_lifecycle,
        )
