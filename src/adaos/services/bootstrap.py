# src\adaos\services\bootstrap.py
from __future__ import annotations

import asyncio
import base64
import hashlib
import json as _json
import logging
import math
import os
import re
import socket
import sys
import tempfile
import threading
import time
import traceback
import uuid
from collections import deque
from pathlib import Path
from typing import Any, Awaitable, Callable, List, Optional, Sequence
from urllib.parse import urlparse

import nats as _nats

from adaos.adapters.db.sqlite_schema import ensure_schema
from adaos.adapters.scenarios.git_repo import GitScenarioRepository
from adaos.adapters.skills.git_repo import GitSkillRepository
from adaos.domain import Event
from adaos.ports.heartbeat import HeartbeatPort
from adaos.ports.skills_loader import SkillsLoaderPort
from adaos.ports.subnet_registry import SubnetRegistryPort
from adaos.sdk.core.decorators import register_subscriptions
from adaos.sdk.data import bus
from adaos.services import yjs as _y_store  # ensure YStore subscriptions are registered
from adaos.services.agent_context import AgentContext, get_ctx
from adaos.services.chat_io import telemetry as tm
from adaos.services.chat_io.interfaces import ChatOutputEvent, ChatOutputMessage
from adaos.services.chat_io.nlu_bridge import register_chat_nlu_bridge  # chat->NLU bridge
from adaos.services.eventbus import LocalEventBus
from adaos.services.io_bus.http_fallback import HttpFallbackBus
from adaos.services.io_bus.local_bus import LocalIoBus
from adaos.services.nats_config import (
    PUBLIC_NATS_WS_API,
    PUBLIC_NATS_WS_DEDICATED,
    normalize_nats_ws_url,
    nats_url_uses_websocket,
    order_nats_ws_candidates,
    public_nats_ws_api,
    public_nats_tcp_candidates,
    public_nats_ws_candidates,
)
from adaos.services.reliability import (
    ReadinessStatus,
    configure_hub_root_transport_strategy,
    hub_root_protocol_class_policy,
    hub_root_protocol_traffic_class,
    hub_root_transport_strategy_snapshot,
    mark_root_control_down,
    note_root_control_reconnect,
    mark_root_control_up,
    mark_route_degraded,
    mark_route_ready,
    note_route_incident,
    observe_route_e2e,
    observe_hub_root_integration_outbox,
    observe_hub_root_protocol_publish,
    observe_hub_root_protocol_subscription,
    observe_hub_root_route_flow,
    observe_hub_root_route_runtime,
    record_hub_root_transport_event,
    set_integration_readiness,
)
from adaos.services.realtime_sidecar import (
    probe_realtime_sidecar_ready,
    realtime_sidecar_diag_path,
    realtime_sidecar_enabled,
    realtime_sidecar_host,
    realtime_sidecar_log_path,
    realtime_sidecar_local_url,
    realtime_sidecar_port,
    realtime_sidecar_route_tunnel_ws_bases,
    resolve_realtime_remote_candidates,
)
from adaos.services.node_config import NodeConfig, generate_provisional_subnet_id, load_config, set_role as cfg_set_role
from adaos.services.node_runtime_state import (
    load_member_hub_token,
    load_nats_runtime_config,
    migrate_legacy_nats_runtime_config,
    save_nats_runtime_config,
)
from adaos.services.nats_errors import (
    install_transient_nats_log_filter,
    is_transient_nats_error,
    nats_error_summary,
)
from adaos.services.hub_root_outbox_store import load_outbox_items, outbox_store_path, save_outbox_items
from adaos.services.root.control_lifecycle_sync import report_hub_control_lifecycle_state
from adaos.services.root.core_update_sync import reconcile_hub_core_update
from adaos.services.runtime_identity import (
    runtime_connect_name,
    runtime_identity_snapshot,
    runtime_instance_id,
    runtime_transition_role,
)
from adaos.services.scheduler import start_scheduler, stop_scheduler
from adaos.services.scenario import (
    webspace_runtime as _scenario_ws_runtime,  # ensure core scenario subscriptions
)
from adaos.services.scenario import workflow_runtime as _scenario_workflow_runtime  # ensure scenario workflow subscriptions
from adaos.services import weather as _weather_services  # ensure weather observers
from adaos.services import nlu as _nlu_services  # ensure NLU dispatcher subscriptions
from adaos.services import named_entity_projection as _named_entity_projection  # ensure named-entity projection subscriptions
from adaos.services import pending_actions as _pending_actions  # ensure Pending Actions subscriptions
from adaos.services.bounded_io import bounded_text_tail_lines
from adaos.services.bootstrap_runtime import (
    BootstrapLifecycleCoordinator,
    BootstrapStatusWatchdogService,
    HubRouteProxyPolicy,
    NatsBridgePolicy,
    RootTransportService,
)
from adaos.services.bootstrap_runtime import core_update_convergence as _core_update_convergence
from adaos.services.bootstrap_runtime import hub_route_proxy as _hub_route_proxy
from adaos.services.bootstrap_runtime import nats_bridge as _nats_bridge
from adaos.services.bootstrap_runtime import status_policy as _status_policy
from adaos.services.bootstrap_runtime import transport_cleanup as _transport_cleanup
from adaos.services.bootstrap_runtime.nats_root_runtime import start_nats_root_transport
from adaos.services.skill import runtime_shutdown_runtime as _runtime_shutdown_runtime  # ensure skill shutdown subscriptions
from adaos.services.skill import service_supervisor_runtime as _service_supervisor_runtime  # ensure service supervisor subscriptions
from adaos.services.skill.service_supervisor import get_service_supervisor
from adaos.services.zone_hosts import DEFAULT_PUBLIC_ROOT_BASE_URL, canonical_zone_id, zone_public_base_url
from adaos.services.subnet_alias import save_subnet_alias
from adaos.integrations.telegram.sender import TelegramSender


_BOOTSTRAP_RUNTIME_ORIGINALS: dict[str, dict[str, Any]] = {
    "_status_policy": {name: getattr(_status_policy, name) for name in ('_bounded_interval_seconds', '_hub_root_bridge_watchdog_interval_s', '_should_forward_node_status_to_members', '_webio_control_target_node_id', '_should_forward_webio_control_to_members', '_node_status_dedupe_window_s', '_node_status_emit_fingerprint', '_should_emit_node_status', '_env_truthy', '_loop_hang_watchdog_enabled_from_env', '_hub_channel_console_trace_enabled', '_hub_channel_console_allow_rl')},
    "_transport_cleanup": {name: getattr(_transport_cleanup, name) for name in ('_run_bounded_async_cleanup', '_close_route_tunnels_bounded', '_current_async_task_is_cancelling')},
    "_hub_route_proxy": {name: getattr(_hub_route_proxy, name) for name in ('_hub_route_max_chunk_raw_bytes', '_hub_route_normalize_resend_chunk_indexes', '_hub_route_path_token', '_hub_route_semantic_flow_for_path', '_hub_route_should_shed_sync_frame', '_hub_route_sync_frame_force_flush_enabled', '_hub_route_should_force_flush_reply', '_hub_route_subnet_sync_payload_type', '_hub_route_should_drop_subnet_sync_frame', '_is_local_http_base', '_hub_route_prefers_supervisor_public_status', '_dev_without_supervisor', '_read_json_file_silent', '_hub_route_node_status_supervisor_runtime', '_dev_api_serve_core_update_sync_disabled', '_supervisor_local_bases', '_route_local_base_cache_ttl_s', '_runtime_port_local_http_base', '_runtime_port_probe_candidates', '_route_state_dir_from_ctx', '_route_state_dir_fallback', '_active_runtime_state_local_http_bases', '_append_local_http_base', '_hub_route_local_http_timeout', '_hub_route_tools_call_has_idempotency', '_hub_route_should_retry_http_upstream_error', '_hub_route_parse_resend_delays', '_hub_route_should_resend_http_resp', '_probe_runtime_http_base', '_observe_route_local_base_diag', '_note_route_local_base_shortcut', '_discover_active_runtime_local_base', '_build_hub_route_http_bases', '_http_base_to_ws_base', '_build_hub_route_ws_bases', '_hub_route_force_close_no_upstream_s')},
    "_nats_bridge": {name: getattr(_nats_bridge, name) for name in ('_read_sidecar_tail_lines', '_nats_credentials_refresh_evidence', '_should_refresh_nats_credentials', '_hub_root_transport_kind', '_hub_nats_prefer_dedicated', '_normalize_hub_nats_ws_url', '_hub_public_ws_candidates', '_hub_public_tcp_candidates', '_runtime_candidate_mode', '_hub_root_candidate_passive_mode', '_nats_url_needs_public_ws_refresh', '_build_realtime_sidecar_fallback_candidates', '_should_quarantine_nats_candidate', '_hub_nats_sidecar_failover_on_transient', '_hub_nats_sidecar_quarantine_s', '_resolve_nats_log_server', '_hub_id_from_nats_user', '_canonical_hub_nats_identity')},
    "_core_update_convergence": {name: getattr(_core_update_convergence, name) for name in ('_core_update_status_fingerprint', '_core_update_waits_for_supervisor_convergence', '_watch_supervisor_core_update_convergence')},
}
_BOOTSTRAP_RUNTIME_WRAPPERS: dict[str, dict[str, Any]] = {
    "_status_policy": {},
    "_transport_cleanup": {},
    "_hub_route_proxy": {},
    "_nats_bridge": {},
    "_core_update_convergence": {},
}

def _sync_bootstrap_runtime_helpers(module_alias: str) -> None:
    module = globals()[module_alias]
    originals = _BOOTSTRAP_RUNTIME_ORIGINALS[module_alias]
    wrappers = _BOOTSTRAP_RUNTIME_WRAPPERS[module_alias]
    for helper_name, original in originals.items():
        current = globals().get(helper_name, original)
        wrapper = wrappers.get(helper_name)
        setattr(module, helper_name, original if wrapper is not None and current is wrapper else current)

    if module_alias == "_hub_route_proxy":
        module.realtime_sidecar_route_tunnel_ws_bases = realtime_sidecar_route_tunnel_ws_bases
        module.observe_hub_root_route_runtime = observe_hub_root_route_runtime
    elif module_alias == "_nats_bridge":
        module.runtime_transition_role = runtime_transition_role
        module.realtime_sidecar_local_url = realtime_sidecar_local_url
        module.realtime_sidecar_diag_path = realtime_sidecar_diag_path
        module.normalize_nats_ws_url = normalize_nats_ws_url
        module.nats_url_uses_websocket = nats_url_uses_websocket
        module.public_nats_ws_api = public_nats_ws_api
        module.public_nats_ws_candidates = public_nats_ws_candidates
        module.public_nats_tcp_candidates = public_nats_tcp_candidates

def _bounded_interval_seconds(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._bounded_interval_seconds(*args, **kwargs)
def _hub_root_bridge_watchdog_interval_s(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._hub_root_bridge_watchdog_interval_s(*args, **kwargs)
def _should_forward_node_status_to_members(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._should_forward_node_status_to_members(*args, **kwargs)
def _webio_control_target_node_id(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._webio_control_target_node_id(*args, **kwargs)
def _should_forward_webio_control_to_members(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._should_forward_webio_control_to_members(*args, **kwargs)
def _node_status_dedupe_window_s(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._node_status_dedupe_window_s(*args, **kwargs)
def _node_status_emit_fingerprint(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._node_status_emit_fingerprint(*args, **kwargs)
def _should_emit_node_status(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._should_emit_node_status(*args, **kwargs)
def _env_truthy(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._env_truthy(*args, **kwargs)
def _loop_hang_watchdog_enabled_from_env(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._loop_hang_watchdog_enabled_from_env(*args, **kwargs)
def _hub_channel_console_trace_enabled(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._hub_channel_console_trace_enabled(*args, **kwargs)
def _hub_channel_console_allow_rl(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_status_policy")
    return _status_policy._hub_channel_console_allow_rl(*args, **kwargs)
async def _run_bounded_async_cleanup(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_transport_cleanup")
    return await _transport_cleanup._run_bounded_async_cleanup(*args, **kwargs)
async def _close_route_tunnels_bounded(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_transport_cleanup")
    return await _transport_cleanup._close_route_tunnels_bounded(*args, **kwargs)
def _current_async_task_is_cancelling(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_transport_cleanup")
    return _transport_cleanup._current_async_task_is_cancelling(*args, **kwargs)
def _hub_route_max_chunk_raw_bytes(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_max_chunk_raw_bytes(*args, **kwargs)
def _hub_route_normalize_resend_chunk_indexes(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_normalize_resend_chunk_indexes(*args, **kwargs)
def _hub_route_path_token(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_path_token(*args, **kwargs)
def _hub_route_semantic_flow_for_path(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_semantic_flow_for_path(*args, **kwargs)
def _hub_route_should_shed_sync_frame(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_should_shed_sync_frame(*args, **kwargs)
def _hub_route_sync_frame_force_flush_enabled(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_sync_frame_force_flush_enabled(*args, **kwargs)
def _hub_route_should_force_flush_reply(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_should_force_flush_reply(*args, **kwargs)
def _hub_route_subnet_sync_payload_type(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_subnet_sync_payload_type(*args, **kwargs)
def _hub_route_should_drop_subnet_sync_frame(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_should_drop_subnet_sync_frame(*args, **kwargs)
def _is_local_http_base(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._is_local_http_base(*args, **kwargs)
def _hub_route_prefers_supervisor_public_status(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_prefers_supervisor_public_status(*args, **kwargs)
def _dev_without_supervisor(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._dev_without_supervisor(*args, **kwargs)
def _read_json_file_silent(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._read_json_file_silent(*args, **kwargs)
def _hub_route_node_status_supervisor_runtime(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_node_status_supervisor_runtime(*args, **kwargs)
def _dev_api_serve_core_update_sync_disabled(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._dev_api_serve_core_update_sync_disabled(*args, **kwargs)
def _supervisor_local_bases(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._supervisor_local_bases(*args, **kwargs)
def _route_local_base_cache_ttl_s(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._route_local_base_cache_ttl_s(*args, **kwargs)
def _runtime_port_local_http_base(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._runtime_port_local_http_base(*args, **kwargs)
def _runtime_port_probe_candidates(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._runtime_port_probe_candidates(*args, **kwargs)
def _route_state_dir_from_ctx(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._route_state_dir_from_ctx(*args, **kwargs)
def _route_state_dir_fallback(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._route_state_dir_fallback(*args, **kwargs)
def _active_runtime_state_local_http_bases(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._active_runtime_state_local_http_bases(*args, **kwargs)
def _append_local_http_base(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._append_local_http_base(*args, **kwargs)
def _hub_route_local_http_timeout(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_local_http_timeout(*args, **kwargs)
def _hub_route_tools_call_has_idempotency(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_tools_call_has_idempotency(*args, **kwargs)
def _hub_route_should_retry_http_upstream_error(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_should_retry_http_upstream_error(*args, **kwargs)
def _hub_route_parse_resend_delays(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_parse_resend_delays(*args, **kwargs)
def _hub_route_should_resend_http_resp(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_should_resend_http_resp(*args, **kwargs)
def _probe_runtime_http_base(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._probe_runtime_http_base(*args, **kwargs)
def _observe_route_local_base_diag(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._observe_route_local_base_diag(*args, **kwargs)
def _note_route_local_base_shortcut(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._note_route_local_base_shortcut(*args, **kwargs)
def _discover_active_runtime_local_base(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._discover_active_runtime_local_base(*args, **kwargs)
def _build_hub_route_http_bases(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._build_hub_route_http_bases(*args, **kwargs)
def _http_base_to_ws_base(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._http_base_to_ws_base(*args, **kwargs)
def _build_hub_route_ws_bases(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._build_hub_route_ws_bases(*args, **kwargs)
def _hub_route_force_close_no_upstream_s(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_hub_route_proxy")
    return _hub_route_proxy._hub_route_force_close_no_upstream_s(*args, **kwargs)
def _read_sidecar_tail_lines(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._read_sidecar_tail_lines(*args, **kwargs)
def _nats_credentials_refresh_evidence(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._nats_credentials_refresh_evidence(*args, **kwargs)
def _should_refresh_nats_credentials(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._should_refresh_nats_credentials(*args, **kwargs)
def _hub_root_transport_kind(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_root_transport_kind(*args, **kwargs)
def _hub_nats_prefer_dedicated(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_nats_prefer_dedicated(*args, **kwargs)
def _normalize_hub_nats_ws_url(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._normalize_hub_nats_ws_url(*args, **kwargs)
def _hub_public_ws_candidates(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_public_ws_candidates(*args, **kwargs)
def _hub_public_tcp_candidates(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_public_tcp_candidates(*args, **kwargs)
def _runtime_candidate_mode(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._runtime_candidate_mode(*args, **kwargs)
def _hub_root_candidate_passive_mode(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_root_candidate_passive_mode(*args, **kwargs)
def _nats_url_needs_public_ws_refresh(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._nats_url_needs_public_ws_refresh(*args, **kwargs)
def _build_realtime_sidecar_fallback_candidates(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._build_realtime_sidecar_fallback_candidates(*args, **kwargs)
def _should_quarantine_nats_candidate(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._should_quarantine_nats_candidate(*args, **kwargs)
def _hub_nats_sidecar_failover_on_transient(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_nats_sidecar_failover_on_transient(*args, **kwargs)
def _hub_nats_sidecar_quarantine_s(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_nats_sidecar_quarantine_s(*args, **kwargs)
def _resolve_nats_log_server(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._resolve_nats_log_server(*args, **kwargs)
def _hub_id_from_nats_user(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._hub_id_from_nats_user(*args, **kwargs)
def _canonical_hub_nats_identity(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_nats_bridge")
    return _nats_bridge._canonical_hub_nats_identity(*args, **kwargs)
def _core_update_status_fingerprint(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_core_update_convergence")
    return _core_update_convergence._core_update_status_fingerprint(*args, **kwargs)
def _core_update_waits_for_supervisor_convergence(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_core_update_convergence")
    return _core_update_convergence._core_update_waits_for_supervisor_convergence(*args, **kwargs)
async def _watch_supervisor_core_update_convergence(*args: Any, **kwargs: Any) -> Any:
    _sync_bootstrap_runtime_helpers("_core_update_convergence")
    return await _core_update_convergence._watch_supervisor_core_update_convergence(*args, **kwargs)

_BOOTSTRAP_RUNTIME_WRAPPERS["_status_policy"] = {
    "_bounded_interval_seconds": _bounded_interval_seconds,
    "_hub_root_bridge_watchdog_interval_s": _hub_root_bridge_watchdog_interval_s,
    "_should_forward_node_status_to_members": _should_forward_node_status_to_members,
    "_webio_control_target_node_id": _webio_control_target_node_id,
    "_should_forward_webio_control_to_members": _should_forward_webio_control_to_members,
    "_node_status_dedupe_window_s": _node_status_dedupe_window_s,
    "_node_status_emit_fingerprint": _node_status_emit_fingerprint,
    "_should_emit_node_status": _should_emit_node_status,
    "_env_truthy": _env_truthy,
    "_loop_hang_watchdog_enabled_from_env": _loop_hang_watchdog_enabled_from_env,
    "_hub_channel_console_trace_enabled": _hub_channel_console_trace_enabled,
    "_hub_channel_console_allow_rl": _hub_channel_console_allow_rl,
}
_BOOTSTRAP_RUNTIME_WRAPPERS["_transport_cleanup"] = {
    "_run_bounded_async_cleanup": _run_bounded_async_cleanup,
    "_close_route_tunnels_bounded": _close_route_tunnels_bounded,
    "_current_async_task_is_cancelling": _current_async_task_is_cancelling,
}
_BOOTSTRAP_RUNTIME_WRAPPERS["_hub_route_proxy"] = {
    "_hub_route_max_chunk_raw_bytes": _hub_route_max_chunk_raw_bytes,
    "_hub_route_normalize_resend_chunk_indexes": _hub_route_normalize_resend_chunk_indexes,
    "_hub_route_path_token": _hub_route_path_token,
    "_hub_route_semantic_flow_for_path": _hub_route_semantic_flow_for_path,
    "_hub_route_should_shed_sync_frame": _hub_route_should_shed_sync_frame,
    "_hub_route_sync_frame_force_flush_enabled": _hub_route_sync_frame_force_flush_enabled,
    "_hub_route_should_force_flush_reply": _hub_route_should_force_flush_reply,
    "_hub_route_subnet_sync_payload_type": _hub_route_subnet_sync_payload_type,
    "_hub_route_should_drop_subnet_sync_frame": _hub_route_should_drop_subnet_sync_frame,
    "_is_local_http_base": _is_local_http_base,
    "_hub_route_prefers_supervisor_public_status": _hub_route_prefers_supervisor_public_status,
    "_dev_without_supervisor": _dev_without_supervisor,
    "_read_json_file_silent": _read_json_file_silent,
    "_hub_route_node_status_supervisor_runtime": _hub_route_node_status_supervisor_runtime,
    "_dev_api_serve_core_update_sync_disabled": _dev_api_serve_core_update_sync_disabled,
    "_supervisor_local_bases": _supervisor_local_bases,
    "_route_local_base_cache_ttl_s": _route_local_base_cache_ttl_s,
    "_runtime_port_local_http_base": _runtime_port_local_http_base,
    "_runtime_port_probe_candidates": _runtime_port_probe_candidates,
    "_route_state_dir_from_ctx": _route_state_dir_from_ctx,
    "_route_state_dir_fallback": _route_state_dir_fallback,
    "_active_runtime_state_local_http_bases": _active_runtime_state_local_http_bases,
    "_append_local_http_base": _append_local_http_base,
    "_hub_route_local_http_timeout": _hub_route_local_http_timeout,
    "_hub_route_tools_call_has_idempotency": _hub_route_tools_call_has_idempotency,
    "_hub_route_should_retry_http_upstream_error": _hub_route_should_retry_http_upstream_error,
    "_hub_route_parse_resend_delays": _hub_route_parse_resend_delays,
    "_hub_route_should_resend_http_resp": _hub_route_should_resend_http_resp,
    "_probe_runtime_http_base": _probe_runtime_http_base,
    "_observe_route_local_base_diag": _observe_route_local_base_diag,
    "_note_route_local_base_shortcut": _note_route_local_base_shortcut,
    "_discover_active_runtime_local_base": _discover_active_runtime_local_base,
    "_build_hub_route_http_bases": _build_hub_route_http_bases,
    "_http_base_to_ws_base": _http_base_to_ws_base,
    "_build_hub_route_ws_bases": _build_hub_route_ws_bases,
    "_hub_route_force_close_no_upstream_s": _hub_route_force_close_no_upstream_s,
}
_BOOTSTRAP_RUNTIME_WRAPPERS["_nats_bridge"] = {
    "_read_sidecar_tail_lines": _read_sidecar_tail_lines,
    "_nats_credentials_refresh_evidence": _nats_credentials_refresh_evidence,
    "_should_refresh_nats_credentials": _should_refresh_nats_credentials,
    "_hub_root_transport_kind": _hub_root_transport_kind,
    "_hub_nats_prefer_dedicated": _hub_nats_prefer_dedicated,
    "_normalize_hub_nats_ws_url": _normalize_hub_nats_ws_url,
    "_hub_public_ws_candidates": _hub_public_ws_candidates,
    "_hub_public_tcp_candidates": _hub_public_tcp_candidates,
    "_runtime_candidate_mode": _runtime_candidate_mode,
    "_hub_root_candidate_passive_mode": _hub_root_candidate_passive_mode,
    "_nats_url_needs_public_ws_refresh": _nats_url_needs_public_ws_refresh,
    "_build_realtime_sidecar_fallback_candidates": _build_realtime_sidecar_fallback_candidates,
    "_should_quarantine_nats_candidate": _should_quarantine_nats_candidate,
    "_hub_nats_sidecar_failover_on_transient": _hub_nats_sidecar_failover_on_transient,
    "_hub_nats_sidecar_quarantine_s": _hub_nats_sidecar_quarantine_s,
    "_resolve_nats_log_server": _resolve_nats_log_server,
    "_hub_id_from_nats_user": _hub_id_from_nats_user,
    "_canonical_hub_nats_identity": _canonical_hub_nats_identity,
}
_BOOTSTRAP_RUNTIME_WRAPPERS["_core_update_convergence"] = {
    "_core_update_status_fingerprint": _core_update_status_fingerprint,
    "_core_update_waits_for_supervisor_convergence": _core_update_waits_for_supervisor_convergence,
    "_watch_supervisor_core_update_convergence": _watch_supervisor_core_update_convergence,
}








def _ensure_managed_nlu_service_skills(log: logging.Logger) -> None:
    try:
        from adaos.services.nlu.rasa_skill_installer import ensure_rasa_service_skill_installed, is_rasa_nlu_enabled

        if is_rasa_nlu_enabled():
            ensure_rasa_service_skill_installed()
    except Exception:
        log.warning("failed to ensure managed NLU service skills", exc_info=True)














































































































































class BootstrapService:
    def __init__(
        self,
        ctx: AgentContext,
        *,
        heartbeat: HeartbeatPort,
        skills_loader: SkillsLoaderPort,
        subnet_registry: SubnetRegistryPort,
    ) -> None:
        self.ctx = ctx
        self.heartbeat = heartbeat
        self.skills_loader = skills_loader
        self.subnet_registry = subnet_registry
        self._lifecycle = BootstrapLifecycleCoordinator()
        self._nats_policy = NatsBridgePolicy()
        self._route_policy = HubRouteProxyPolicy()
        self._io_bus: Any = None
        self._log = logging.getLogger("adaos.hub-io")
        self._root_transport = RootTransportService(
            lifecycle=self._lifecycle,
            role=lambda: str(getattr(self.ctx.config, "role", "") or ""),
            candidate_passive=self._nats_policy.candidate_passive_mode,
            reconnect=lambda **kwargs: self.request_hub_root_reconnect(**kwargs),
            watchdog_interval=lambda: _hub_root_bridge_watchdog_interval_s(),
            record_event=lambda *args, **kwargs: record_hub_root_transport_event(*args, **kwargs),
            logger=self._log,
        )

    # Compatibility facades for callers and tests that still inspect the
    # historical BootstrapService lifecycle attributes directly.
    @property
    def _boot_tasks(self) -> list[asyncio.Task[Any]]:
        return self._lifecycle.boot_tasks

    @_boot_tasks.setter
    def _boot_tasks(self, value: list[asyncio.Task[Any]]) -> None:
        self._lifecycle.boot_tasks = value

    @property
    def _boot_lock(self) -> asyncio.Lock:
        return self._lifecycle.boot_lock

    @property
    def _boot_done(self) -> asyncio.Event:
        return self._lifecycle.boot_done

    @property
    def _boot_in_progress(self) -> bool:
        return self._lifecycle.boot_in_progress

    @_boot_in_progress.setter
    def _boot_in_progress(self, value: bool) -> None:
        self._lifecycle.boot_in_progress = bool(value)

    @property
    def _ready(self) -> asyncio.Event:
        return self._lifecycle.ready

    @property
    def _booted(self) -> bool:
        return self._lifecycle.booted

    @_booted.setter
    def _booted(self, value: bool) -> None:
        self._lifecycle.booted = bool(value)

    @property
    def _app(self) -> Any:
        return self._lifecycle.app

    @_app.setter
    def _app(self, value: Any) -> None:
        self._lifecycle.app = value

    @property
    def _member_ready_callback(self) -> Callable[[], Awaitable[None]] | None:
        return self._lifecycle.member_ready_callback

    @_member_ready_callback.setter
    def _member_ready_callback(self, value: Callable[[], Awaitable[None]] | None) -> None:
        self._lifecycle.member_ready_callback = value

    @property
    def _hub_root_nc(self) -> Any:
        return self._root_transport.nats_client

    @_hub_root_nc.setter
    def _hub_root_nc(self, value: Any) -> None:
        self._root_transport.nats_client = value

    @property
    def _hub_root_route_reset(self) -> Any:
        return self._root_transport.route_reset

    @_hub_root_route_reset.setter
    def _hub_root_route_reset(self, value: Any) -> None:
        self._root_transport.route_reset = value

    @property
    def _hub_root_bridge_task_name(self) -> str:
        return self._root_transport.bridge_task_name

    @property
    def _hub_root_bridge_watchdog_task_name(self) -> str:
        return self._root_transport.bridge_watchdog_task_name

    @property
    def _hub_root_bridge_factory(self) -> Callable[[], Awaitable[Any]] | None:
        return self._root_transport.bridge_factory

    @_hub_root_bridge_factory.setter
    def _hub_root_bridge_factory(self, value: Callable[[], Awaitable[Any]] | None) -> None:
        self._root_transport.bridge_factory = value

    @property
    def _hub_root_bridge_watchdog_rearm_total(self) -> int:
        return self._root_transport.bridge_watchdog_rearm_total

    @_hub_root_bridge_watchdog_rearm_total.setter
    def _hub_root_bridge_watchdog_rearm_total(self, value: int) -> None:
        self._root_transport.bridge_watchdog_rearm_total = int(value)

    @property
    def _hub_root_authority_waiters(self) -> set[asyncio.Event]:
        return self._root_transport.authority_waiters

    @property
    def _hub_root_authority_ready_at(self) -> float | None:
        return self._root_transport.authority_ready_at

    @_hub_root_authority_ready_at.setter
    def _hub_root_authority_ready_at(self, value: float | None) -> None:
        self._root_transport.authority_ready_at = value

    def _mark_hub_root_authority_ready(self) -> None:
        """Release cutover waiters only after the active Root route subscription is flushed."""
        self._root_transport.mark_authority_ready()

    def _find_live_boot_task(self, task_name: str) -> asyncio.Task | None:
        return self._lifecycle.find_live_task(task_name)

    def _start_boot_task_once(self, task_name: str, coro_factory: Callable[[], Awaitable[Any]]) -> asyncio.Task:
        return self._lifecycle.start_task_once(task_name, coro_factory)

    def _start_hub_root_bridge_task(
        self,
        coro_factory: Callable[[], Awaitable[Any]],
        *,
        start_immediately: bool = True,
    ) -> asyncio.Task | None:
        return self._root_transport.start_bridge_task(
            coro_factory,
            start_immediately=start_immediately,
        )

    def _hub_root_bridge_required(self) -> bool:
        return self._root_transport.bridge_required()

    async def _repair_missing_hub_root_bridge(self, *, reason: str) -> dict[str, Any]:
        return await self._root_transport.repair_missing_bridge(reason=reason)

    async def _hub_root_bridge_watchdog(self) -> None:
        await self._root_transport.watchdog()

    def _ensure_hub_root_bridge_task(
        self,
        *,
        force_rearm: bool = False,
        reason: str | None = None,
    ) -> dict[str, Any]:
        return self._root_transport.ensure_bridge_task(
            force_rearm=force_rearm,
            reason=reason,
        )

    def is_ready(self) -> bool:
        return self._lifecycle.is_ready()

    async def _reset_hub_root_route_runtime(
        self,
        *,
        reason: str,
        notify_browser: bool,
    ) -> dict[str, Any]:
        return await self._root_transport.reset_route_runtime(
            reason=reason,
            notify_browser=notify_browser,
        )

    async def request_hub_root_reconnect(
        self,
        *,
        transport: str | None = None,
        url_override: str | None = None,
        wait_for_authority: bool = False,
        _reason: str = "manual_reconnect",
    ) -> dict[str, Any]:
        """
        Force hub-root transport reconnect.

        This is a debugging/ops hook: update env-like overrides and proactively close the current
        NATS connection so the supervisor reconnects using new settings.
        """
        tr = str(transport or "").strip().lower() or None
        override = str(url_override or "").strip() or None
        reconnect_reason = str(_reason or "manual_reconnect").strip() or "manual_reconnect"
        close_diag: dict[str, Any] = {"attempted": False, "timeout": False, "forced_ws_close": False}
        bridge_diag: dict[str, Any] = {"attempted": False, "started": False}
        authority_waiter = asyncio.Event() if wait_for_authority else None
        authority_diag: dict[str, Any] = {
            "required": bool(wait_for_authority),
            "ready": None if not wait_for_authority else False,
        }
        if authority_waiter is not None:
            self._hub_root_authority_waiters.add(authority_waiter)
            current_task = asyncio.current_task()
            if current_task is not None:
                current_task.add_done_callback(
                    lambda _task, waiter=authority_waiter: self._hub_root_authority_waiters.discard(waiter)
                )

        def _finish(payload: dict[str, Any]) -> dict[str, Any]:
            if authority_waiter is not None:
                self._hub_root_authority_waiters.discard(authority_waiter)
            payload["authority"] = dict(authority_diag)
            return payload

        def _safe_strategy() -> dict[str, Any]:
            try:
                return hub_root_transport_strategy_snapshot()
            except Exception:
                return {}

        try:
            if tr is not None:
                os.environ["HUB_NATS_TRANSPORT"] = tr
            if override is not None:
                os.environ["HUB_NATS_URL_OVERRIDE"] = override
            elif url_override is not None:
                # Explicit empty override clears it.
                os.environ.pop("HUB_NATS_URL_OVERRIDE", None)
            try:
                strategy_update: dict[str, Any] = {}
                if transport is not None:
                    strategy_update["requested_transport"] = tr
                if url_override is not None:
                    strategy_update["url_override"] = override
                if strategy_update:
                    configure_hub_root_transport_strategy(**strategy_update)
                record_hub_root_transport_event(
                    "reconnect_requested",
                    transport=tr,
                    server=override,
                    summary=f"hub-root reconnect requested ({reconnect_reason})",
                    details={
                        "requested_transport": tr,
                        "url_override": override,
                        "reason": reconnect_reason,
                    },
                )
            except Exception:
                pass
            try:
                close_diag["route_reset"] = await self._reset_hub_root_route_runtime(
                    reason=reconnect_reason,
                    notify_browser=True,
                )
            except Exception:
                pass
            # Trigger reconnect by closing the active connection if present.
            nc = getattr(self, "_hub_root_nc", None)
            if nc is not None:
                try:
                    close = getattr(nc, "close", None)
                    if callable(close):
                        close_diag["attempted"] = True
                        try:
                            close_timeout_s = float(os.getenv("HUB_ROOT_RECONNECT_CLOSE_TIMEOUT_S", "1.5") or "1.5")
                        except Exception:
                            close_timeout_s = 1.5
                        if close_timeout_s < 0.2:
                            close_timeout_s = 0.2

                        # NOTE: asyncio.wait_for() can itself hang if the close coroutine ignores cancellation.
                        # Use asyncio.wait() with timeout to ensure the HTTP request returns promptly.
                        try:
                            task = asyncio.create_task(close())
                            _done, pending = await asyncio.wait({task}, timeout=close_timeout_s)
                            if pending:
                                close_diag["timeout"] = True
                                try:
                                    task.cancel()
                                except Exception:
                                    pass
                                # Best-effort: force-close websocket transport internals if present to avoid a stuck close().
                                try:
                                    tr_obj = getattr(nc, "_transport", None)
                                    ws = getattr(tr_obj, "_ws", None) if tr_obj else None
                                    close_task = getattr(tr_obj, "_close_task", None) if tr_obj else None
                                    client = getattr(tr_obj, "_client", None) if tr_obj else None
                                    try:
                                        if ws is not None:
                                            t = asyncio.create_task(ws.close())
                                            await asyncio.wait({t}, timeout=0.5)
                                            if not t.done():
                                                try:
                                                    t.cancel()
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    try:
                                        if close_task is not None and hasattr(close_task, "done") and not close_task.done():
                                            close_task.set_result(None)
                                    except Exception:
                                        pass
                                    try:
                                        if client is not None:
                                            t = asyncio.create_task(client.close())
                                            await asyncio.wait({t}, timeout=0.5)
                                            if not t.done():
                                                try:
                                                    t.cancel()
                                                except Exception:
                                                    pass
                                    except Exception:
                                        pass
                                    close_diag["forced_ws_close"] = True
                                except Exception:
                                    pass
                        except Exception:
                            pass
                except Exception:
                    pass
            try:
                force_bridge_rearm = nc is None or bool(close_diag.get("timeout"))
                bridge_diag = self._ensure_hub_root_bridge_task(
                    force_rearm=force_bridge_rearm,
                    reason=(
                        f"{reconnect_reason}_without_active_nats"
                        if nc is None
                        else f"{reconnect_reason}_close_timeout"
                    ),
                )
                if bridge_diag.get("started"):
                    try:
                        record_hub_root_transport_event(
                            "bridge_rearmed",
                            transport=tr,
                            server=override,
                            summary=f"hub-root reconnect rearmed bridge task ({reconnect_reason})",
                            details=dict(bridge_diag),
                        )
                    except Exception:
                        pass
            except Exception as exc:
                bridge_diag = {
                    "attempted": True,
                    "started": False,
                    "state": "failed",
                    "error": f"{type(exc).__name__}: {exc}",
                }
            if authority_waiter is not None:
                try:
                    authority_timeout_s = float(
                        os.getenv("HUB_ROOT_RECONNECT_AUTHORITY_TIMEOUT_S", "8.0") or "8.0"
                    )
                except Exception:
                    authority_timeout_s = 8.0
                authority_timeout_s = max(0.25, min(authority_timeout_s, 30.0))
                authority_started_at = time.monotonic()
                try:
                    await asyncio.wait_for(authority_waiter.wait(), timeout=authority_timeout_s)
                    authority_diag.update(
                        {
                            "ready": True,
                            "wait_sec": round(max(0.0, time.monotonic() - authority_started_at), 3),
                            "ready_at": self._hub_root_authority_ready_at,
                        }
                    )
                except asyncio.TimeoutError:
                    authority_diag.update(
                        {
                            "ready": False,
                            "wait_sec": round(max(0.0, time.monotonic() - authority_started_at), 3),
                            "timeout_sec": authority_timeout_s,
                            "error": "hub_root_authority_timeout",
                        }
                    )
            return _finish({
                "ok": not bool(wait_for_authority) or bool(authority_diag.get("ready")),
                "requested": {"transport": tr, "url_override": override},
                "strategy": _safe_strategy(),
                "close": close_diag,
                "bridge": bridge_diag,
            })
        except Exception as exc:
            return _finish({
                "ok": False,
                "requested": {"transport": tr, "url_override": override},
                "strategy": _safe_strategy(),
                "close": close_diag,
                "bridge": bridge_diag,
                "error": f"{type(exc).__name__}: {exc}",
            })

    def _member_hub_transition_snapshot(self) -> dict[str, Any]:
        try:
            from adaos.services.core_update import read_status as _read_core_update_status
        except Exception:
            _read_core_update_status = None
        try:
            from adaos.services.runtime_lifecycle import runtime_lifecycle_snapshot as _runtime_lifecycle_snapshot
        except Exception:
            _runtime_lifecycle_snapshot = None

        update_status = _read_core_update_status() if callable(_read_core_update_status) else {}
        lifecycle = _runtime_lifecycle_snapshot() if callable(_runtime_lifecycle_snapshot) else {}
        status = update_status if isinstance(update_status, dict) else {}
        runtime = lifecycle if isinstance(lifecycle, dict) else {}
        state = str(status.get("state") or "").strip().lower()
        phase = str(status.get("phase") or "").strip().lower()
        node_state = str(runtime.get("node_state") or "").strip().lower()
        lifecycle_reason = str(runtime.get("reason") or "").strip().lower()
        draining = bool(runtime.get("draining"))

        transition_state = "ready"
        reason = "none"
        recovery_blocked = False
        if state in {"preparing", "countdown", "draining", "stopping", "applying"}:
            transition_state = "paused_for_update"
            reason = state
            recovery_blocked = True
        elif state == "restarting" or phase in {"launch", "root_promoted"}:
            transition_state = "restarting"
            reason = state or phase or "restarting"
            recovery_blocked = True
        elif state == "validated" and phase == "root_promotion_pending":
            transition_state = "waiting_restart"
            reason = "root_promotion_pending"
            recovery_blocked = True
        elif draining or node_state in {"stopping", "stopped", "restarting"}:
            transition_state = "waiting_restart"
            reason = lifecycle_reason or node_state or "draining"
            recovery_blocked = True
        return {
            "transition_state": transition_state,
            "reason": reason,
            "recovery_blocked": recovery_blocked,
            "update_state": state or None,
            "update_phase": phase or None,
            "node_state": node_state or None,
            "draining": draining,
        }

    async def request_member_hub_reconnect(self, *, force: bool = False) -> dict[str, Any]:
        conf = load_config(ctx=self.ctx)
        transition = self._member_hub_transition_snapshot()
        if str(getattr(conf, "role", "") or "").strip().lower() != "member":
            return {
                "ok": False,
                "accepted": False,
                "error": "role_not_member",
                "role": str(getattr(conf, "role", "") or "").strip().lower() or None,
                "transition": transition,
            }
        hub_url = str(getattr(conf, "hub_url", "") or "").strip()
        if not hub_url:
            return {
                "ok": False,
                "accepted": False,
                "error": "hub_url_missing",
                "transition": transition,
            }
        member_hub_token = str(load_member_hub_token() or getattr(conf, "token", "") or "").strip()
        if not member_hub_token:
            return {
                "ok": False,
                "accepted": False,
                "error": "member_hub_token_missing",
                "transition": transition,
            }
        if transition.get("recovery_blocked") and not force:
            return {
                "ok": True,
                "accepted": False,
                "transition": transition,
                "reason": "transition_in_progress",
            }

        from adaos.services.subnet.link_client import get_member_link_client

        existing = self._find_live_boot_task("adaos-heartbeat")
        if existing is not None:
            existing.cancel()
            try:
                await existing
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass
        try:
            await get_member_link_client().stop()
        except Exception:
            pass

        ready_callback = self._member_ready_callback if callable(self._member_ready_callback) else None
        heartbeat_task = await self._member_register_and_heartbeat(conf, on_registered=ready_callback)
        if heartbeat_task is not None:
            self._lifecycle.track_task(heartbeat_task)
        try:
            await get_member_link_client().start()
        except Exception as exc:
            return {
                "ok": False,
                "accepted": False,
                "error": f"{type(exc).__name__}: {exc}",
                "transition": transition,
            }
        return {
            "ok": True,
            "accepted": True,
            "transition": transition,
            "role": "member",
            "hub_url": hub_url,
            "started": {
                "heartbeat": heartbeat_task is not None,
                "link_client": True,
            },
        }

    async def request_member_hub_refresh(self, *, reason: str = "member_hub_refresh") -> dict[str, Any]:
        conf = load_config(ctx=self.ctx)
        transition = self._member_hub_transition_snapshot()
        if str(getattr(conf, "role", "") or "").strip().lower() != "member":
            return {
                "ok": False,
                "accepted": False,
                "error": "role_not_member",
                "role": str(getattr(conf, "role", "") or "").strip().lower() or None,
                "transition": transition,
            }
        if transition.get("recovery_blocked"):
            return {
                "ok": True,
                "accepted": False,
                "transition": transition,
                "reason": "transition_in_progress",
            }

        from adaos.services.subnet.link_client import get_member_link_client

        result = get_member_link_client().request_refresh(
            reason=str(reason or "member_hub_refresh"),
        )
        if isinstance(result, dict):
            result.setdefault("transition", transition)
            return result
        return {"ok": True, "accepted": False, "transition": transition}

    async def request_hub_root_route_reset(
        self,
        *,
        reason: str,
        notify_browser: bool = True,
    ) -> dict[str, Any]:
        return await self._reset_hub_root_route_runtime(
            reason=str(reason or "").strip() or "route_reset",
            notify_browser=bool(notify_browser),
        )

    def _prepare_environment(self) -> None:
        """
        Гарантированная подготовка окружения:
          - создаёт каталоги (skills, scenarios, state, cache, logs)
          - инициализирует схему БД (skills/scenarios)
          - при наличии URL монорепо — клонирует репозитории без установки
        """
        ctx = self.ctx

        # каталоги (учитываем, что в paths могут быть callables)
        def _resolve(x):
            return x() if callable(x) else x

        skills_root = Path(_resolve(getattr(ctx.paths, "skills_dir", "")))
        scenarios_root = Path(_resolve(getattr(ctx.paths, "scenarios_dir", "")))
        state_root = Path(_resolve(getattr(ctx.paths, "state_dir", "")))
        cache_root = Path(_resolve(getattr(ctx.paths, "cache_dir", "")))
        logs_root = Path(_resolve(getattr(ctx.paths, "logs_dir", "")))

        for p in (skills_root, scenarios_root, state_root, cache_root, logs_root):
            if p:
                p.mkdir(parents=True, exist_ok=True)

        # схема БД (единая функция, не через побочный эффект конкретного реестра)
        ensure_schema(ctx.sql)

        # в тестах — не трогаем удалённые репозитории/сеть
        if os.getenv("ADAOS_TESTING") == "1":
            return

        # Default routing rules for RouterService (stdout + telegram broadcast).
        # This file is a runtime config (often ignored by git) but must exist for
        # system notifications (subnet.started/stopped, greet_on_boot, etc).
        try:
            base_dir = getattr(ctx.paths, "base_dir", None)
            base_dir = base_dir() if callable(base_dir) else base_dir
            if base_dir:
                rules_path = Path(base_dir) / "route_rules.yaml"
                if not rules_path.exists():
                    rules_path.write_text(
                        "rules:\n"
                        "  - priority: 60\n"
                        "    match: {}\n"
                        "    target: {node_id: this, kind: io_type, io_type: stdout}\n"
                        "  - priority: 50\n"
                        "    match: {}\n"
                        "    target: {node_id: this, kind: io_type, io_type: telegram}\n",
                        encoding="utf-8",
                    )
        except Exception:
            pass

        # монорепо навыков
        try:
            if ctx.settings.skills_monorepo_url and not (skills_root / ".git").exists():
                GitSkillRepository(
                    paths=ctx.paths,
                    git=ctx.git,
                    monorepo_url=getattr(ctx.settings, "skills_monorepo_url", None),
                    monorepo_branch=getattr(ctx.settings, "skills_monorepo_branch", None),
                ).ensure()
        except Exception:
            # не блокируем бут при сбое ensure; логирование можно добавить позже
            pass

        # монорепо сценариев (поддержим оба возможных конструктора)
        try:
            if ctx.settings.scenarios_monorepo_url and not (scenarios_root / ".git").exists():
                GitScenarioRepository(
                    paths=ctx.paths,
                    git=ctx.git,
                    url=getattr(ctx.settings, "scenarios_monorepo_url", None),
                    branch=getattr(ctx.settings, "scenarios_monorepo_branch", None),
                ).ensure()

        except Exception:
            pass

    async def _member_register_and_heartbeat(
        self,
        conf: NodeConfig,
        *,
        on_registered: Callable[[], Awaitable[None]] | None = None,
    ) -> Optional[asyncio.Task]:
        hub_url = str(conf.hub_url or "").strip()
        if not hub_url:
            await bus.emit("net.subnet.register.error", {"status": "hub_url_missing"}, source="lifecycle", actor="system")
            return None
        member_hub_token = str(load_member_hub_token() or conf.token or "").strip()
        register_retry_s = _bounded_interval_seconds(
            os.getenv("ADAOS_MEMBER_REGISTER_RETRY_INITIAL_S", "1"),
            default=1.0,
            minimum=0.05,
        )

        async def _try_register() -> bool:
            try:
                ok = await self.heartbeat.register(
                    hub_url,
                    member_hub_token,
                    node_id=conf.node_id,
                    subnet_id=conf.subnet_id,
                    hostname=socket.gethostname(),
                    roles=["member"],
                )
            except Exception as exc:
                await bus.emit("net.subnet.register.error", {"error": str(exc)}, source="lifecycle", actor="system")
                return False
            if not ok:
                await bus.emit("net.subnet.register.error", {"status": "non-200"}, source="lifecycle", actor="system")
                return False
            await bus.emit("net.subnet.registered", {"hub": conf.hub_url}, source="lifecycle", actor="system")
            return True

        async def loop() -> None:
            registered = False
            registered_notified = False
            backoff = register_retry_s
            while True:
                if not registered:
                    registered = await _try_register()
                    if not registered:
                        await asyncio.sleep(backoff)
                        backoff = min(backoff * 2, 30)
                        continue
                    backoff = 1
                    if on_registered is not None and not registered_notified:
                        registered_notified = True
                        await on_registered()
                try:
                    ok_hb = await self.heartbeat.heartbeat(
                        hub_url,
                        member_hub_token,
                        node_id=conf.node_id,
                        base_url=str(os.getenv("ADAOS_SELF_BASE_URL") or "").strip() or None,
                    )
                    if ok_hb:
                        backoff = 1
                    else:
                        await bus.emit("net.subnet.heartbeat.warn", {"status": "non-200"}, source="lifecycle", actor="system")
                        backoff = min(backoff * 2, 30)
                except Exception as e:
                    await bus.emit("net.subnet.heartbeat.error", {"error": str(e)}, source="lifecycle", actor="system")
                    backoff = min(backoff * 2, 30)
                await asyncio.sleep(backoff if backoff > 1 else 5)

        return asyncio.create_task(loop(), name="adaos-heartbeat")

    async def run_boot_sequence(self, app: Any) -> None:
        await self._lifecycle.run_once(app, self._run_boot_sequence_impl)

    async def _run_boot_sequence_impl(self, app: Any) -> None:
        if self._booted:
            return
        self._lifecycle.bind_app(app)
        # Unified deep-trace switch for WS/NATS/route debugging.
        try:
            if os.getenv("HUB_TRACE", "0") == "1":
                for k in (
                    "HUB_NATS_TRACE",
                    "HUB_NATS_VERBOSE",
                    "HUB_NATS_WS_TRACE",
                    "HUB_NATS_WIRETAP",
                    "HUB_NATS_WS_PATCH_AIOHTTP",
                    "HUB_ROUTE_TRACE",
                    "HUB_ROUTE_FRAME_VERBOSE",
                    "HUB_ROUTE_TX_VERBOSE",
                    "HUB_ROUTE_DIAG",
                    "HUB_WS_TRACE",
                    "HUB_ROOT_LOG_SNAPSHOT",
                    "HUB_ROOT_LOG_SNAPSHOT_EXTRACT_PRINT",
                ):
                    os.environ.setdefault(k, "1")
                os.environ.setdefault("HUB_NATS_WIRETAP_MAX_BYTES", "200")
                os.environ.setdefault("HUB_ROOT_LOG_SNAPSHOT_LINES", "2000")
                os.environ.setdefault("ADAOS_LOOP_LAG_MONITOR", "1")
                try:
                    print("[hub-io] HUB_TRACE=1 -> enabling deep WS/NATS/route tracing")
                except Exception:
                    pass
        except Exception:
            pass
        conf = getattr(self.ctx, "config", None) or load_config(ctx=self.ctx)
        async def _run_release_validation_autorun(trigger: str) -> None:
            try:
                from adaos.services.release_validation_autorun import (
                    autonomous_release_validation_delay_s,
                    run_autonomous_release_validation,
                )

                await asyncio.sleep(autonomous_release_validation_delay_s())
                report = await asyncio.to_thread(
                    run_autonomous_release_validation,
                    conf,
                    trigger=trigger,
                )
                if not isinstance(report, dict):
                    return
                await bus.emit(
                    "release_validation.autonomous.finished",
                    report,
                    source="release_validation.autorun",
                    actor="system",
                )
                state = str(report.get("state") or "unknown").upper()
                await bus.emit(
                    "ui.notify",
                    {
                        "text": (
                            f"AdaOS autonomous validation {state}: "
                            f"{report.get('build_identity') or 'unknown build'}\n"
                            f"{report.get('reason') or 'no result reason'}"
                        ),
                        "_meta": {
                            "source": "release_validation.autorun",
                            "report_id": report.get("report_id"),
                            "severity": "info" if report.get("state") == "passed" else "critical",
                        },
                    },
                    source="release_validation.autorun",
                    actor="system",
                )
                if str(getattr(conf, "role", "") or "").strip().lower() == "hub":
                    from adaos.services.root.core_update_sync import report_hub_core_update_state

                    await asyncio.to_thread(report_hub_core_update_state, conf)
            except Exception:
                self._log.warning("autonomous release validation failed trigger=%s", trigger, exc_info=True)

        def _schedule_release_validation_autorun(trigger: str) -> None:
            try:
                from adaos.services.release_validation_autorun import autonomous_release_validation_enabled

                if autonomous_release_validation_enabled():
                    self._start_boot_task_once(
                        "adaos-release-validation-autorun",
                        lambda: _run_release_validation_autorun(trigger),
                    )
            except Exception:
                self._log.debug("failed to schedule autonomous release validation", exc_info=True)

        try:
            from adaos.services.system_model.service import (
                current_node_status_push_payload as _current_node_status_push_payload,
                node_status_push_heartbeat_s as _node_status_push_heartbeat_s,
            )
        except Exception:
            _current_node_status_push_payload = None
            _node_status_push_heartbeat_s = None

        self._status_watchdog = BootstrapStatusWatchdogService.from_environment(
            config=conf,
            logger=self._log,
            report_control=lambda config: report_hub_control_lifecycle_state(config),
            node_status_payload=_current_node_status_push_payload,
            node_status_heartbeat_s=(
                _node_status_push_heartbeat_s() if callable(_node_status_push_heartbeat_s) else None
            ),
            should_emit_node_status=lambda **kwargs: _should_emit_node_status(**kwargs),
            emit_event=lambda *args, **kwargs: bus.emit(*args, **kwargs),
        )
        _report_control_lifecycle = self._status_watchdog.report_control_lifecycle
        _emit_node_status = self._status_watchdog.emit_node_status

        self._prepare_environment()
        # local adapter over LocalEventBus
        core_bus = self.ctx.bus if isinstance(self.ctx.bus, LocalEventBus) else LocalEventBus()
        io_bus: Any = LocalIoBus(core=core_bus)
        await io_bus.connect()
        print("[bootstrap] IO bus: LocalEventBus")
        self._io_bus = io_bus
        # Attach chat IO -> NLU bridge (e.g. Telegram text -> nlp.intent.detect.request)
        try:
            register_chat_nlu_bridge(core_bus)
        except Exception:
            self._log.warning("failed to register chat_io NLU bridge", exc_info=True)
        # expose in app.state
        try:
            setattr(app.state, "bus", io_bus)
        except Exception:
            pass
        await bus.emit("sys.boot.start", {"role": conf.role, "node_id": conf.node_id, "subnet_id": conf.subnet_id}, source="lifecycle", actor="system")
        if not self._nats_policy.runtime_candidate_mode():
            await asyncio.to_thread(_ensure_managed_nlu_service_skills, self._log)
        await self.skills_loader.import_all_handlers(self.ctx.paths.skills_dir())
        # Start service-type skills (external processes).
        if self._nats_policy.runtime_candidate_mode():
            self._log.info("skipping service skill startup for candidate runtime prewarm")
        else:
            try:
                await get_service_supervisor().start_all()
            except Exception:
                self._log.warning("failed to start service skills", exc_info=True)
        await register_subscriptions()
        if str(getattr(conf, "role", "") or "").strip().lower() == "hub":
            try:
                from adaos.services.subnet.link_manager import get_hub_link_manager as _get_hub_link_manager

                def _forward_core_update_status_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type="core.update.status",
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        self._log.debug("failed to mirror core.update.status to members", exc_info=True)

                def _forward_supervisor_update_status_raw_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type="supervisor.update.status.raw",
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        self._log.debug("failed to mirror supervisor.update.status.raw to members", exc_info=True)

                def _forward_node_status_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    if not _should_forward_node_status_to_members(payload):
                        return
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type="node.status",
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        self._log.debug("failed to mirror node.status to members", exc_info=True)

                def _forward_desktop_reload_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type=str(ev.type or "desktop.webspace.reload"),
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        self._log.debug("failed to mirror desktop reload event=%s to members", str(ev.type or ""), exc_info=True)

                def _forward_webio_stream_control_to_members(ev: Event) -> None:
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    if not _should_forward_webio_control_to_members(payload):
                        return
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type=str(ev.type or ""),
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        self._log.debug("failed to mirror webio stream control event=%s to members", str(ev.type or ""), exc_info=True)

                def _forward_targeted_event_to_members(ev: Event) -> None:
                    event_type = str(ev.type or "").strip()
                    if not event_type or event_type.startswith("desktop."):
                        return
                    if event_type in {
                        "webio.stream.snapshot.requested",
                        "webio.stream.subscription.changed",
                        "webio.yjs.snapshot.requested",
                        "webio.yjs.subscription.changed",
                    }:
                        return
                    payload = ev.payload if isinstance(ev.payload, dict) else {}
                    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
                    if bool(meta.get("subnet_origin_node_id")) or bool(meta.get("subnet_hub_mirrored")):
                        return
                    target_node_id = str(
                        payload.get("target_node_id")
                        or payload.get("node_target_id")
                        or meta.get("target_node_id")
                        or meta.get("node_target_id")
                        or ""
                    ).strip()
                    if not target_node_id or target_node_id == str(getattr(conf, "node_id", "") or "").strip():
                        return
                    try:
                        asyncio.get_running_loop().create_task(
                            _get_hub_link_manager().broadcast_event(
                                event_type=event_type,
                                payload=payload,
                                source=str(ev.source or "hub"),
                            )
                        )
                    except Exception:
                        self._log.debug("failed to mirror node-targeted event=%s to members", event_type, exc_info=True)

                core_bus.subscribe("core.update.status", _forward_core_update_status_to_members)
                core_bus.subscribe("supervisor.update.status.raw", _forward_supervisor_update_status_raw_to_members)
                core_bus.subscribe("node.status", _forward_node_status_to_members)
                core_bus.subscribe("desktop.webspace.reload", _forward_desktop_reload_to_members)
                core_bus.subscribe("desktop.webspace.reloaded", _forward_desktop_reload_to_members)
                core_bus.subscribe("desktop.webspace.reset", _forward_desktop_reload_to_members)
                core_bus.subscribe("webio.stream.snapshot.requested", _forward_webio_stream_control_to_members)
                core_bus.subscribe("webio.stream.subscription.changed", _forward_webio_stream_control_to_members)
                core_bus.subscribe("webio.yjs.snapshot.requested", _forward_webio_stream_control_to_members)
                core_bus.subscribe("webio.yjs.subscription.changed", _forward_webio_stream_control_to_members)
                core_bus.subscribe("*", _forward_targeted_event_to_members)
            except Exception:
                self._log.debug(
                    "failed to install member status forwarders",
                    exc_info=True,
                )
        try:
            from adaos.services.core_update import (
                finalize_runtime_boot_status as _finalize_runtime_boot_status,
                read_public_update_status as _read_public_update_status,
                read_status as _read_core_update_status,
            )

            initial_core_update_status = _read_core_update_status()
            await bus.emit(
                "core.update.status",
                initial_core_update_status,
                source="lifecycle",
                actor="system",
            )
            await bus.emit(
                "supervisor.update.status.raw",
                _read_public_update_status(),
                source="lifecycle",
                actor="system",
            )
            if _core_update_waits_for_supervisor_convergence(initial_core_update_status):
                self._start_boot_task_once(
                    "adaos-core-update-supervisor-convergence",
                    lambda: _watch_supervisor_core_update_convergence(
                        bus,
                        read_status=_read_core_update_status,
                        initial_status=initial_core_update_status,
                    ),
                )
        except Exception:
            _finalize_runtime_boot_status = None
            self._log.debug("failed to emit initial core.update.status", exc_info=True)
        await _emit_node_status("boot")
        await bus.emit("sys.bus.ready", {}, source="lifecycle", actor="system")
        # Start in-process scheduler after the bus is ready.
        try:
            await start_scheduler()
        except Exception:
            self._log.warning("failed to start scheduler", exc_info=True)

        # Optional: monitor asyncio event loop lag to catch blocking handlers (which can manifest as
        # WebSocket stalls/timeouts and cascading disconnects).
        try:
            if os.getenv("ADAOS_LOOP_LAG_MONITOR", "0") == "1":
                try:
                    interval_s = float(os.getenv("ADAOS_LOOP_LAG_INTERVAL_S", "0.5") or "0.5")
                except Exception:
                    interval_s = 0.5
                if interval_s < 0.05:
                    interval_s = 0.05
                try:
                    # Keep normal runs readable: sub-second drift is useful for
                    # targeted diagnostics, but too noisy under browser attach.
                    warn_ms = float(os.getenv("ADAOS_LOOP_LAG_WARN_MS", "1000") or "1000")
                except Exception:
                    warn_ms = 1000.0
                try:
                    dump_ms = float(os.getenv("ADAOS_LOOP_LAG_DUMP_MS", "2000") or "2000")
                except Exception:
                    dump_ms = 2000.0
                try:
                    dump_top = int(os.getenv("ADAOS_LOOP_LAG_DUMP_TOP", "10") or "10")
                except Exception:
                    dump_top = 10
                if dump_top < 1:
                    dump_top = 1
                if dump_top > 50:
                    dump_top = 50

                async def _loop_lag_monitor() -> None:
                    # Measure *per-interval* overshoot (do not accumulate drift), so we can distinguish
                    # a single stall from a slow-but-steady loop.
                    last_tick = time.monotonic()
                    last_log = 0.0
                    last_dump = 0.0
                    while True:
                        await asyncio.sleep(interval_s)
                        now = time.monotonic()
                        drift_s = (now - last_tick) - interval_s
                        last_tick = now
                        if drift_s < 0:
                            drift_s = 0.0
                        drift_ms = drift_s * 1000.0
                        if drift_ms >= warn_ms:
                            try:
                                # Local rate-limit (do not depend on hub-io _rl_log).
                                if now - last_log >= 1.0:
                                    last_log = now
                                    msg = (
                                        f"[diag] event loop lag {drift_ms:.0f}ms (interval={interval_s:.2f}s warn={warn_ms:.0f}ms dump={dump_ms:.0f}ms)"
                                    )
                                    print(msg)
                                    diag_log.warning(
                                        "event loop lag drift_ms=%.0f interval_s=%.2f warn_ms=%.0f dump_ms=%.0f",
                                        drift_ms,
                                        interval_s,
                                        warn_ms,
                                        dump_ms,
                                    )
                            except Exception:
                                pass
                        if drift_ms >= dump_ms and (now - last_dump) >= max(5.0, interval_s):
                            last_dump = now
                            try:
                                tasks = list(asyncio.all_tasks())
                                # Keep deterministic ordering for repeated dumps.
                                tasks.sort(key=lambda t: (0 if t is asyncio.current_task() else 1, t.get_name()))
                                lines: list[str] = []
                                for t in tasks[:dump_top]:
                                    frames = None
                                    top = None
                                    try:
                                        frames = t.get_stack(limit=1)
                                        top = frames[-1] if frames else None
                                        loc = None
                                        if top is not None:
                                            try:
                                                loc = f"{top.f_code.co_filename}:{top.f_lineno}"
                                            except Exception:
                                                loc = None
                                        lines.append(f"- task={t.get_name()} done={t.done()} cancelled={t.cancelled()} at={loc}")
                                    except Exception:
                                        continue
                                    finally:
                                        # Do not keep frame objects in the lag
                                        # monitor coroutine. Frames can retain
                                        # y_py locals and later release them from
                                        # an unrelated thread during GC.
                                        del top
                                        del frames
                                del tasks
                                try:
                                    backlog_fn = getattr(core_bus, "backlog_snapshot", None)
                                    backlog = backlog_fn() if callable(backlog_fn) else {}
                                    active_bounded = (
                                        backlog.get("top_active_bounded_handlers")
                                        if isinstance(backlog, dict)
                                        else None
                                    )
                                    if isinstance(active_bounded, list):
                                        for item in active_bounded[:dump_top]:
                                            if not isinstance(item, dict):
                                                continue
                                            lines.append(
                                                "- eventbus.active_bounded "
                                                f"type={item.get('event_type')} "
                                                f"handler={item.get('handler')} "
                                                f"receiver={item.get('receiver')} "
                                                f"webspace={item.get('webspace_id')} "
                                                f"age={item.get('age_s')}s"
                                            )
                                    active_tasks = backlog.get("top_active_tasks") if isinstance(backlog, dict) else None
                                    if isinstance(active_tasks, list):
                                        for item in active_tasks[:dump_top]:
                                            if not isinstance(item, dict):
                                                continue
                                            lines.append(
                                                "- eventbus.active_task "
                                                f"type={item.get('event_type')} "
                                                f"handler={item.get('handler')} "
                                                f"age={item.get('age_s')}s"
                                            )
                                except Exception:
                                    pass
                                try:
                                    from adaos.services.yjs.doc import (
                                        live_room_command_diagnostics_snapshot,
                                    )

                                    command_diag = live_room_command_diagnostics_snapshot()
                                    last_command = command_diag.get("last_result")
                                    if isinstance(last_command, dict):
                                        lines.append(
                                            "- yjs.live_room_command "
                                            f"source={last_command.get('source')} "
                                            f"webspace={last_command.get('webspace_id')} "
                                            f"reason={last_command.get('reason')} "
                                            f"handoff={last_command.get('handoff')} "
                                            f"queue={last_command.get('queue_ms')}ms "
                                            f"apply={last_command.get('apply_ms')}ms "
                                            f"bytes={last_command.get('update_bytes')}"
                                        )
                                except Exception:
                                    pass
                                try:
                                    from adaos.services.named_entity_projection import (
                                        named_entity_projection_diagnostics_snapshot,
                                    )

                                    projection_diag = named_entity_projection_diagnostics_snapshot()
                                    last_timings = projection_diag.get("last_timings_ms")
                                    lines.append(
                                        "- named_entities.projection "
                                        f"webspace={projection_diag.get('last_webspace_id')} "
                                        f"outcome={projection_diag.get('last_outcome')} "
                                        f"payload={projection_diag.get('last_payload_bytes')}B "
                                        f"timings={last_timings}"
                                    )
                                except Exception:
                                    pass
                                if lines:
                                    dump = "\n".join(lines)
                                    print("[diag] loop lag dump:\n" + dump)
                                    diag_log.warning("event loop lag dump\n%s", dump)
                            except Exception:
                                pass

                self._start_boot_task_once("adaos-loop-lag-monitor", _loop_lag_monitor)
        except Exception:
            pass

        # Optional: hang watchdog (thread-based) to capture the main thread stack during prolonged
        # event loop stalls. This catches cases where asyncio tasks show "await" positions only.
        try:
            # Keep thread-based frame capture behind an explicit unsafe opt-in.
            if _loop_hang_watchdog_enabled_from_env():
                try:
                    import threading as _threading
                    import sys as _sys
                    import traceback as _traceback
                except Exception:
                    _threading = None  # type: ignore[assignment]
                    _sys = None  # type: ignore[assignment]
                    _traceback = None  # type: ignore[assignment]
                if _threading and _sys and _traceback:
                    try:
                        hang_ms = float(
                            os.getenv("ADAOS_LOOP_HANG_MS")
                            or os.getenv("ADAOS_LOOP_LAG_DUMP_MS")
                            or "3000"
                        )
                    except Exception:
                        hang_ms = 3000.0
                    try:
                        every_s = float(os.getenv("ADAOS_LOOP_HANG_EVERY_S", "10") or "10")
                    except Exception:
                        every_s = 10.0
                    try:
                        stack_limit = int(os.getenv("ADAOS_LOOP_HANG_STACK", "40") or "40")
                    except Exception:
                        stack_limit = 40
                    if stack_limit < 5:
                        stack_limit = 5
                    if stack_limit > 200:
                        stack_limit = 200
                    if hang_ms < 200:
                        hang_ms = 200.0
                    if every_s < 1:
                        every_s = 1.0

                    main_tid = _threading.get_ident()
                    last_tick_box = {"t": time.monotonic()}

                    async def _tick() -> None:
                        while True:
                            last_tick_box["t"] = time.monotonic()
                            await asyncio.sleep(0.2)

                    self._start_boot_task_once("adaos-loop-tick", _tick)

                    def _is_idle_event_loop_wait(stack_text: str) -> bool:
                        try:
                            st = stack_text.replace("\\", "/")
                            if "asyncio/base_events.py" not in st or "in _run_once" not in st:
                                return False
                            if "selectors.py" in st and "select.select(" in st:
                                return True
                            if "asyncio/windows_events.py" in st and "_overlapped.GetQueuedCompletionStatus" in st:
                                return True
                        except Exception:
                            return False
                        return False

                    def _safe_thread_stack(frame: Any, *, limit: int) -> tuple[str | None, str | None]:
                        try:
                            frames: list[str] = []
                            cur = frame
                            remaining = max(1, int(limit))
                            while cur is not None and remaining > 0:
                                code = getattr(cur, "f_code", None)
                                filename = str(getattr(code, "co_filename", "") or "")
                                func = str(getattr(code, "co_name", "") or "")
                                lineno = int(getattr(cur, "f_lineno", 0) or 0)
                                norm = filename.replace("\\", "/")
                                if "y_py" in norm or "site-packages/y_py" in norm:
                                    return None, "y_py_frame"
                                frames.append(f'  File "{filename}", line {lineno}, in {func}')
                                cur = getattr(cur, "f_back", None)
                                remaining -= 1
                            return "\n".join(reversed(frames)), None
                        except Exception as exc:
                            return None, f"{type(exc).__name__}: {exc}"

                    def _watch() -> None:
                        last_dump = 0.0
                        while True:
                            time.sleep(0.25)
                            now = time.monotonic()
                            dt_ms = (now - float(last_tick_box.get("t", now))) * 1000.0
                            if dt_ms < hang_ms:
                                continue
                            if now - last_dump < every_s:
                                continue
                            last_dump = now
                            try:
                                fr = _sys._current_frames().get(main_tid)  # type: ignore[attr-defined]
                                if fr is None:
                                    print(f"[diag] event loop hang {dt_ms:.0f}ms (no frame)")
                                    diag_log.warning("event loop hang dt_ms=%.0f frame=none", dt_ms)
                                    continue
                                st, stack_error = _safe_thread_stack(fr, limit=stack_limit)
                                if stack_error:
                                    print(f"[diag] event loop hang {dt_ms:.0f}ms stack unavailable: {stack_error}")
                                    diag_log.warning(
                                        "event loop hang dt_ms=%.0f stack_unavailable=%s",
                                        dt_ms,
                                        stack_error,
                                    )
                                    continue
                                st = st or ""
                                if _is_idle_event_loop_wait(st):
                                    diag_log.debug("event loop hang suppressed idle wait dt_ms=%.0f", dt_ms)
                                    continue
                                print(f"[diag] event loop hang {dt_ms:.0f}ms stack:\n{st.rstrip()}")
                                diag_log.warning("event loop hang dt_ms=%.0f stack:\n%s", dt_ms, st.rstrip())
                            except Exception:
                                continue

                    t = _threading.Thread(target=_watch, name="adaos-loop-hang-watchdog", daemon=True)
                    t.start()
        except Exception:
            pass
        diag_log = logging.getLogger("adaos.diagnostics")
        startup_log = logging.getLogger("adaos.startup")
        startup_stage_logs_enabled = str(os.getenv("ADAOS_STARTUP_STAGE_LOGS") or "").strip().lower() in {"1", "true", "yes", "on"}

        def _startup_stage_mark(stage: str, *, started: float | None = None, failed: Exception | None = None) -> float:
            now = time.perf_counter()
            if started is None:
                if startup_stage_logs_enabled:
                    startup_log.info("startup stage start stage=%s", stage)
                return now
            duration = now - started
            if failed is None:
                if startup_stage_logs_enabled:
                    startup_log.info("startup stage done stage=%s duration_s=%.3f", stage, duration)
            else:
                startup_log.warning(
                    "startup stage failed stage=%s duration_s=%.3f error=%s",
                    stage,
                    duration,
                    type(failed).__name__,
                )
            return now

        try:
            from adaos.services.agent_context import get_ctx as _get_ctx
            from adaos.services.workspace_sync import reconcile_workspace_db_to_materialized as _reconcile_workspace_db_to_materialized

            _reconcile_started = _startup_stage_mark("bootstrap_reconcile_workspace_registry")
            _reconcile_workspace_db_to_materialized(_get_ctx())
            _startup_stage_mark("bootstrap_reconcile_workspace_registry", started=_reconcile_started)
        except Exception:
            self._log.debug("failed to reconcile workspace sqlite registry on boot", exc_info=True)
        if conf.role == "hub":
            _hub_ready_started = _startup_stage_mark("bootstrap_emit_net_subnet_hub_ready")
            await bus.emit("net.subnet.hub.ready", {"subnet_id": conf.subnet_id}, source="lifecycle", actor="system")
            _startup_stage_mark("bootstrap_emit_net_subnet_hub_ready", started=_hub_ready_started)

            async def lease_monitor() -> None:
                while True:
                    for info in self.subnet_registry.mark_down_if_expired():
                        await bus.emit("net.subnet.node.down", {"node_id": getattr(info, "node_id", None)}, source="lifecycle", actor="system")
                    await asyncio.sleep(5)

            self._start_boot_task_once("adaos-lease-monitor", lease_monitor)
            self._lifecycle.mark_ready()
            _sys_ready_started = _startup_stage_mark("bootstrap_emit_sys_ready")
            await bus.emit("sys.ready", {"ts": time.time()}, source="lifecycle", actor="system")
            _startup_stage_mark("bootstrap_emit_sys_ready", started=_sys_ready_started)
            _node_status_started = _startup_stage_mark("bootstrap_emit_node_status")
            await _emit_node_status("sys.ready")
            _startup_stage_mark("bootstrap_emit_node_status", started=_node_status_started)
            try:
                if callable(_finalize_runtime_boot_status):
                    _finalize_runtime_boot_status()
            except Exception:
                self._log.debug("failed to finalize core.update.status after sys.ready", exc_info=True)
            _schedule_release_validation_autorun("sys.ready")
            _control_started = _startup_stage_mark("bootstrap_report_control_lifecycle")
            await _report_control_lifecycle("sys.ready")
            _startup_stage_mark("bootstrap_report_control_lifecycle", started=_control_started)
            self._status_watchdog.start_heartbeats(self._lifecycle)
        else:
            member_ready_announced = False

            async def _announce_member_ready() -> None:
                nonlocal member_ready_announced
                if member_ready_announced:
                    return
                member_ready_announced = True
                try:
                    from adaos.services.subnet.link_client import get_member_link_client

                    await get_member_link_client().start()
                except Exception:
                    self._log.warning("failed to start member hub websocket link after registration", exc_info=True)
                self._lifecycle.signal_ready()
                _sys_ready_started = _startup_stage_mark("bootstrap_emit_sys_ready")
                await bus.emit("sys.ready", {"ts": time.time()}, source="lifecycle", actor="system")
                _startup_stage_mark("bootstrap_emit_sys_ready", started=_sys_ready_started)
                _node_status_started = _startup_stage_mark("bootstrap_emit_node_status")
                await _emit_node_status("sys.ready")
                _startup_stage_mark("bootstrap_emit_node_status", started=_node_status_started)
                try:
                    if callable(_finalize_runtime_boot_status):
                        _finalize_runtime_boot_status()
                except Exception:
                    self._log.debug("failed to finalize core.update.status after sys.ready", exc_info=True)
                _schedule_release_validation_autorun("sys.ready")

            # Keep the original boot-generation callback available to an
            # explicit member reconnect. If startup registration failed (for
            # example because a legacy routed token expired), a later
            # successful rejoin must complete the same readiness/sys.ready
            # transition instead of leaving the otherwise connected node
            # permanently at ready=false.
            self._lifecycle.set_member_ready_callback(_announce_member_ready)
            task = await self._member_register_and_heartbeat(conf, on_registered=_announce_member_ready)
            if task:
                self._lifecycle.track_task(task)
                self._lifecycle.mark_booted()

        # After IO bus is ready, wire outbound subscriber for Telegram if NATS/local
        _post_ready_started = _startup_stage_mark("bootstrap_post_ready_tail")
        try:
            if hasattr(self._io_bus, "subscribe_output"):
                _subscribe_output_started = _startup_stage_mark("bootstrap_subscribe_output")

                # Subscribe to all bot ids ("tg.output.*") and use the single configured TG_BOT_TOKEN.
                sender = TelegramSender("any-bot")

                async def _handler(subject: str, data: bytes) -> None:
                    try:
                        payload = _json.loads(data.decode("utf-8"))
                        # payload may already match ChatOutputEvent schema
                        messages = [ChatOutputMessage(**m) for m in payload.get("messages", [])]
                        out = ChatOutputEvent(target=payload.get("target", {}), messages=messages, options=payload.get("options"))
                        await sender.send(out)
                        for m in messages:
                            tm.record_event("outbound_total", {"type": m.type})
                    except Exception as e:
                        # On error, emit DLQ if possible
                        try:
                            dlq_env = {"error": str(e), "subject": subject, "data": payload if "payload" in locals() else None}
                            if hasattr(self._io_bus, "publish_dlq"):
                                await self._io_bus.publish_dlq("output", dlq_env)
                        except Exception:
                            pass

                await self._io_bus.subscribe_output("*", _handler)
                _startup_stage_mark("bootstrap_subscribe_output", started=_subscribe_output_started)
        except Exception:
            pass

        # Inbound bridge from root NATS -> local event bus (tg.input.<hub_id>)
        await start_nats_root_transport(
            self,
            core_bus=core_bus,
            startup_stage_mark=_startup_stage_mark,
            report_control_lifecycle=_report_control_lifecycle,
        )

    async def shutdown(self) -> None:
        await bus.emit("sys.stopping", {}, source="lifecycle", actor="system")
        try:
            conf = getattr(self.ctx, "config", None) or load_config(ctx=self.ctx)
            if getattr(conf, "role", None) == "hub":
                await asyncio.to_thread(report_hub_control_lifecycle_state, conf)
        except Exception:
            self._log.debug("control lifecycle report failed trigger=sys.stopping", exc_info=True)
        try:
            await get_service_supervisor().shutdown()
        except Exception as e:
            self._log.debug("service supervisor shutdown failed", exc_info=True)
            pass
        try:
            await stop_scheduler()
        except Exception:
            pass
        await self._lifecycle.stop()
        await bus.emit("sys.stopped", {}, source="lifecycle", actor="system")

    async def switch_role(self, app: Any, role: str, *, hub_url: str | None = None, subnet_id: str | None = None) -> NodeConfig:
        prev = getattr(self.ctx, "config", None) or load_config(ctx=self.ctx)
        await self.shutdown()
        if prev.role == "member" and role.lower().strip() == "hub" and prev.hub_url:
            try:
                await self.heartbeat.deregister(prev.hub_url, prev.token or "", node_id=prev.node_id)
            except Exception:
                pass
            subnet_id = subnet_id or generate_provisional_subnet_id()
        conf = cfg_set_role(role, hub_url=hub_url, subnet_id=subnet_id, ctx=self.ctx)
        if str(role or "").strip().lower() == "hub":
            self._last_role_switch_root_init = self._ensure_hub_root_bootstrap_for_role_switch(conf)
            if not bool(self._last_role_switch_root_init.get("ok")):
                raise RuntimeError(str(self._last_role_switch_root_init.get("error") or "hub Root bootstrap failed"))
            conf = load_config(ctx=self.ctx)
        else:
            self._last_role_switch_root_init = {"attempted": False, "reason": "role_not_hub"}
        await self.run_boot_sequence(app or self._app)
        return conf

    def _ensure_hub_root_bootstrap_for_role_switch(self, conf: NodeConfig) -> dict[str, Any]:
        """
        Ensure a switched hub has Root-issued mTLS material before booting.

        `node role switch --role hub` can be called from a member runtime. In that
        path the config role changes immediately, but the hub still needs
        Root-issued hub cert/key/CA before the hub-root NATS tunnel can obtain
        credentials and become visible to routed browsers.
        """
        if str(getattr(conf, "role", "") or "").strip().lower() != "hub":
            return {"attempted": False, "reason": "role_not_hub"}
        if str(os.getenv("ADAOS_ROLE_SWITCH_HUB_ROOT_INIT", "1") or "1").strip().lower() in {"0", "false", "no", "off"}:
            return {"attempted": False, "ok": True, "reason": "disabled_by_env"}
        try:
            from adaos.services.root.service import RootDeveloperService

            result = RootDeveloperService().init(preferred_subnet_id=str(getattr(conf, "subnet_id", "") or "").strip() or None)
            return {
                "attempted": True,
                "ok": True,
                "subnet_id": result.subnet_id,
                "reused": bool(result.reused),
                "hub_key_path": str(result.hub_key_path),
                "hub_cert_path": str(result.hub_cert_path),
                "ca_cert_path": str(result.ca_cert_path) if result.ca_cert_path else None,
                "workspace_path": str(result.workspace_path),
            }
        except Exception as exc:
            return {
                "attempted": True,
                "ok": False,
                "subnet_id": str(getattr(conf, "subnet_id", "") or "") or None,
                "error": f"{type(exc).__name__}: {exc}",
            }


# --- модульные фасады (синглтон) ---
from adaos.services.heartbeat_requests import RequestsHeartbeat
from adaos.services.skills_loader_importlib import ImportlibSkillsLoader
from adaos.services.subnet_registry_mem import get_subnet_registry

_SERVICE: BootstrapService | None = None


def _svc() -> BootstrapService:
    global _SERVICE
    if _SERVICE is None:
        ctx = get_ctx()
        _SERVICE = BootstrapService(ctx, heartbeat=RequestsHeartbeat(), skills_loader=ImportlibSkillsLoader(), subnet_registry=get_subnet_registry())
    return _SERVICE


def is_ready() -> bool:
    return _svc().is_ready()


async def request_hub_root_reconnect(
    *,
    transport: str | None = None,
    url_override: str | None = None,
    wait_for_authority: bool = False,
) -> dict[str, Any]:
    return await _svc().request_hub_root_reconnect(
        transport=transport,
        url_override=url_override,
        wait_for_authority=bool(wait_for_authority),
    )


async def request_member_hub_reconnect(*, force: bool = False) -> dict[str, Any]:
    return await _svc().request_member_hub_reconnect(force=bool(force))


async def request_member_hub_refresh(*, reason: str = "member_hub_refresh") -> dict[str, Any]:
    return await _svc().request_member_hub_refresh(reason=str(reason or "member_hub_refresh"))


async def request_hub_root_route_reset(*, reason: str, notify_browser: bool = True) -> dict[str, Any]:
    return await _svc().request_hub_root_route_reset(
        reason=str(reason or "").strip() or "route_reset",
        notify_browser=bool(notify_browser),
    )


async def run_boot_sequence(app: Any) -> None:
    await _svc().run_boot_sequence(app)


async def shutdown() -> None:
    await _svc().shutdown()


async def switch_role(app: Any, role: str, *, hub_url: str | None = None, subnet_id: str | None = None) -> NodeConfig:
    return await _svc().switch_role(app, role, hub_url=hub_url, subnet_id=subnet_id)
