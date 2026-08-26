from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import sys
import time
import urllib.parse
from typing import Any, Callable

import requests
import websockets  # type: ignore
import y_py as Y

from adaos.apps.cli.active_control import resolve_control_token
from adaos.build_info import BUILD_INFO
from adaos.domain import Event as DomainEvent
from adaos.domain.node_identity import node_identities_match
from adaos.adapters.db import SqliteSkillRegistry
from adaos.services.agent_context import get_ctx
from adaos.services.core_slots import active_slot_manifest, slot_status
from adaos.services.core_update import read_last_result as read_core_update_last_result
from adaos.services.core_update import read_status as read_core_update_status
from adaos.services.core_update_policy import core_update_reactions_disabled_reason
from adaos.services.env_policy import env_bool
from adaos.services.node_config import load_config, normalize_node_names, set_node_names as persist_node_names
from adaos.services.node_runtime_state import save_node_runtime_state
from adaos.services.node_runtime_state import load_member_hub_token
from adaos.services.capacity import get_local_capacity
from adaos.services.runtime_lifecycle import is_accepting_new_work, runtime_lifecycle_snapshot
from adaos.services.subnet.rpc_errors import member_rpc_error_payload, rpc_error_code
from adaos.services.runtime_topology import (
    DEFAULT_RUNTIME_PORT,
    http_base,
    runtime_fallback_http_bases,
    supervisor_base_candidates_from_env,
)
from adaos.services.skill.manager import SkillManager
from adaos.services.skill.tool_contract import declared_tool_side_effects, side_effects_are_read_only
from adaos.services.yjs.doc import apply_update_to_live_room
from adaos.services.yjs.store import add_ystore_write_listener, get_ystore_for_webspace, suppress_ystore_write_notifications

_log = logging.getLogger("adaos.subnet.client")


def _deployment_inventory_payload() -> dict[str, Any]:
    try:
        from adaos.services.project_deployment.default_runtime import (
            deployment_runtime_inventory_payload,
        )

        value = deployment_runtime_inventory_payload()
    except Exception:
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _service_supervisor_runtime_payload() -> dict[str, Any]:
    try:
        from adaos.services.skill.service_supervisor import (
            service_supervisor_runtime_summary,
        )

        value = service_supervisor_runtime_summary()
    except Exception:
        return {
            "schema": "adaos.skill_service_supervisor.runtime.v1",
            "state": "unavailable",
            "initialized": False,
            "distributed": [],
        }
    return dict(value) if isinstance(value, dict) else {}


def _bounded_json_size(value: Any, *, limit: int) -> tuple[int, bool]:
    """Estimate encoded JSON bytes and stop before copying an oversized value."""

    budget = max(1, int(limit))
    total = 0
    stack: list[tuple[str, Any]] = [("value", value)]
    seen: set[int] = set()
    visited = 0
    while stack:
        kind, item = stack.pop()
        if kind == "exit":
            seen.discard(int(item))
            continue
        visited += 1
        if visited > 200_000:
            return total, True
        if item is None:
            total += 4
        elif isinstance(item, bool):
            total += 4 if item else 5
        elif isinstance(item, (int, float)):
            total += len(str(item))
        elif isinstance(item, str):
            remaining = budget - total
            if len(item) > remaining:
                return total + len(item), True
            total += len(json.dumps(item).encode("utf-8"))
        elif isinstance(item, dict):
            marker = id(item)
            if marker in seen:
                return total, True
            seen.add(marker)
            stack.append(("exit", marker))
            total += 2 + (2 * len(item)) + (2 * max(0, len(item) - 1))
            for key, child in item.items():
                stack.append(("value", child))
                stack.append(("value", str(key)))
        elif isinstance(item, (list, tuple)):
            marker = id(item)
            if marker in seen:
                return total, True
            seen.add(marker)
            stack.append(("exit", marker))
            total += 2 + (2 * max(0, len(item) - 1))
            stack.extend(("value", child) for child in item)
        else:
            text = str(item)
            remaining = budget - total
            if len(text) > remaining:
                return total + len(text), True
            total += len(json.dumps(text).encode("utf-8"))
        if total > budget:
            return total, True
    return total, False


def _resolve_member_hub_token(conf) -> str:
    token = load_member_hub_token()
    if token:
        return token
    return str(getattr(conf, "token", "") or "dev-local-token").strip() or "dev-local-token"


def _jwt_exp_unverified(token: str) -> float | None:
    """Read expiry only for refresh scheduling; Root still verifies the signature."""
    parts = str(token or "").split(".")
    if len(parts) != 3:
        return None
    try:
        raw = parts[1] + ("=" * (-len(parts[1]) % 4))
        payload = json.loads(base64.urlsafe_b64decode(raw.encode("ascii")).decode("utf-8"))
        value = float(payload.get("exp") or 0.0) if isinstance(payload, dict) else 0.0
        return value if value > 0.0 else None
    except Exception:
        return None


def _routed_root_base(hub_url: str) -> str:
    parsed = urllib.parse.urlparse(str(hub_url or "").strip())
    marker = "/hubs/"
    if parsed.scheme not in {"http", "https"} or marker not in parsed.path:
        return ""
    return urllib.parse.urlunparse((parsed.scheme, parsed.netloc, "", "", "", "")).rstrip("/")


def _to_ws_url(http_base: str, path: str) -> str:
    u = urllib.parse.urlparse(str(http_base or "").strip())
    if u.scheme in ("http", "https"):
        scheme = "wss" if u.scheme == "https" else "ws"
        netloc = u.netloc
        base_path = u.path
    else:
        # tolerate bare host:port or host
        scheme = "ws"
        netloc = u.path
        base_path = ""
    full_path = (base_path.rstrip("/") + "/" + path.lstrip("/")).rstrip("/")
    return urllib.parse.urlunparse((scheme, netloc, full_path, "", "", ""))


def _env_flag_default_enabled(name: str) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return True
    return str(raw).strip().lower() not in {"0", "false", "no", "off", "none", "disabled"}


def _member_link_ws_compression() -> str | None:
    raw = str(os.getenv("ADAOS_SUBNET_LINK_WS_COMPRESSION") or "").strip()
    if not raw or raw.lower() in {"0", "false", "no", "off", "none", "disabled"}:
        return None
    if raw.lower() in {"1", "true", "yes", "on", "deflate"}:
        return "deflate"
    return raw


def _core_version_label(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    public, _sep, _local = text.partition("+")
    return public.strip() or text


def _core_version_label_is_semver(value: object) -> bool:
    label = _core_version_label(value)
    parts = label.split(".")
    return len(parts) >= 3 and all(part.isdigit() for part in parts[:3])


def _core_non_default_version_label(*values: object) -> str:
    for value in values:
        label = _core_version_label(value)
        if label and label != "0.1.0" and _core_version_label_is_semver(label):
            return label
    return ""


def _core_build_version_with_label(build_version: object, label: object) -> str:
    build = str(build_version or "").strip()
    public = _core_version_label(label)
    if not build or not public:
        return build
    _old_public, sep, local = build.partition("+")
    if not sep:
        return public
    return f"{public}+{local}" if local else public


def _effective_core_build_version(manifest: dict[str, Any], build_version: object) -> str:
    manifest_version = str(manifest.get("build_version") or "").strip()
    runtime_version = str(build_version or "").strip()
    if _core_version_label(manifest_version) == "0.1.0":
        replacement = _core_non_default_version_label(manifest.get("base_version"), runtime_version)
        if replacement:
            return _core_build_version_with_label(manifest_version, replacement)
    return manifest_version or runtime_version


def _subnet_link_malloc_trim_min_interval_s() -> float:
    raw = str(os.getenv("ADAOS_SUBNET_LINK_MALLOC_TRIM_MIN_INTERVAL_S") or "").strip()
    try:
        value = float(raw or 5.0)
    except Exception:
        value = 5.0
    return max(0.0, min(value, 3600.0))


def _trim_allocator_after_member_link_cycle() -> bool:
    if not _env_flag_default_enabled("ADAOS_SUBNET_LINK_MALLOC_TRIM"):
        return False
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6")
        trim = getattr(libc, "malloc_trim", None)
        if not callable(trim):
            return False
        return bool(trim(0))
    except Exception:
        return False


def _member_link_transition_snapshot() -> dict[str, Any]:
    update_status = read_core_update_status() or {}
    lifecycle = runtime_lifecycle_snapshot()
    status = update_status if isinstance(update_status, dict) else {}
    runtime = lifecycle if isinstance(lifecycle, dict) else {}
    state = str(status.get("state") or "").strip().lower()
    phase = str(status.get("phase") or "").strip().lower()
    node_state = str(runtime.get("node_state") or "").strip().lower()
    lifecycle_reason = str(runtime.get("reason") or "").strip().lower()
    draining = bool(runtime.get("draining"))
    transition_state = "ready"
    reason = "none"
    if state in {"preparing", "countdown", "draining", "stopping", "applying"}:
        transition_state = "paused_for_update"
        reason = state
    elif state == "restarting" or phase in {"launch", "root_promoted"}:
        transition_state = "restarting"
        reason = state or phase or "restarting"
    elif state == "validated" and phase == "root_promotion_pending":
        transition_state = "waiting_restart"
        reason = "root_promotion_pending"
    elif draining or node_state in {"stopping", "stopped", "restarting"}:
        transition_state = "waiting_restart"
        reason = lifecycle_reason or node_state or "draining"
    return {
        "transition_state": transition_state,
        "reason": reason,
        "update_state": state or None,
        "update_phase": phase or None,
    }


def _target_version_matches(left: Any, right: Any) -> bool:
    a = str(left or "").strip()
    b = str(right or "").strip()
    if not a or not b:
        return False
    if a == b:
        return True
    return len(a) >= 7 and len(b) >= 7 and (a.startswith(b) or b.startswith(a))


def _manifest_matches_target_version(manifest: dict[str, Any] | None, target_version: str) -> bool:
    expected = str(target_version or "").strip()
    if not expected:
        return False
    data = manifest if isinstance(manifest, dict) else {}
    for key in ("target_version", "build_version", "git_commit", "git_short_commit"):
        if _target_version_matches(expected, data.get(key)):
            return True
    return False


def _payload_source_node_id(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    return str(
        payload.get("node_id")
        or payload.get("source_node_id")
        or meta.get("node_id")
        or meta.get("source_node_id")
        or ""
    ).strip()


def _is_node_qualified_webio_stream_event(event_type: str) -> bool:
    parts = str(event_type or "").strip().split(".")
    if len(parts) < 4 or parts[0] != "webio" or parts[1] != "stream":
        return False
    return parts[2] == "nodes" or (len(parts) >= 5 and parts[3] == "nodes")


def _is_unqualified_webio_stream_data_event(event_type: str) -> bool:
    topic = str(event_type or "").strip()
    if topic in {
        "webio.stream.snapshot.requested",
        "webio.stream.subscription.changed",
    }:
        return False
    parts = topic.split(".")
    if len(parts) < 4 or parts[0] != "webio" or parts[1] != "stream":
        return False
    return not _is_node_qualified_webio_stream_event(topic)


def _should_forward_member_bus_event(event_type: str, payload: Any) -> bool:
    if str(event_type or "").strip() in {
        "core.update.status",
        "hub.core_update.status",
        "supervisor.update.status.raw",
    }:
        # These retained topics describe the node that owns the local bus.
        # Member update state is propagated in the member runtime snapshot.
        return False
    if (
        _is_unqualified_webio_stream_data_event(event_type)
        and _payload_source_node_id(payload)
    ):
        return False
    return True


class MemberLinkClient:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._connected = asyncio.Event()
        self._out_q: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=5000)
        self._task: asyncio.Task | None = None
        self._remove_ystore_listener: Callable[[], None] | None = None
        self._bus_subscribed = False
        self._yjs_enabled = os.getenv("ADAOS_SUBNET_YJS_REPLICATION", "1").strip().lower() not in ("0", "false", "no")
        self._bus_prefixes = self._parse_bus_prefixes(os.getenv("ADAOS_SUBNET_BUS_FORWARD_PREFIXES", "io.out.,ui."))
        self._connected_at = 0.0
        self._last_message_at = 0.0
        self._last_pong_at = 0.0
        self._hello_ack_ok = False
        self._hello_ack_at = 0.0
        self._last_hello_ack_error = ""
        self._ws_url = ""
        self._hub_node_id = ""
        self._last_hub_event_type = ""
        self._last_hub_event_at = 0.0
        self._last_hub_core_update: dict[str, Any] = {}
        self._last_follow_key = ""
        self._last_follow_result: dict[str, Any] = {}
        self._last_follow_error = ""
        self._last_follow_at = 0.0
        self._last_control_request: dict[str, Any] = {}
        self._last_control_result: dict[str, Any] = {}
        self._last_control_error = ""
        self._last_control_requested_at = 0.0
        self._last_control_completed_at = 0.0
        self._last_forced_snapshot_at = 0.0
        self._last_yjs_write_snapshot_at = 0.0
        self._yjs_write_seen_total = 0
        self._yjs_write_queued_total = 0
        self._yjs_write_drop_disconnected_total = 0
        self._yjs_write_drop_encode_total = 0
        self._yjs_write_drop_queue_total = 0
        self._yjs_sent_total = 0
        self._yjs_send_failed_total = 0
        self._yjs_received_total = 0
        self._yjs_received_bytes = 0
        self._yjs_snapshot_queued_total = 0
        self._yjs_snapshot_failed_total = 0
        self._yjs_snapshot_bytes = 0
        self._last_yjs_write_at = 0.0
        self._last_yjs_sent_at = 0.0
        self._last_yjs_received_at = 0.0
        self._last_yjs_snapshot_at = 0.0
        self._last_yjs_write_webspace_id = ""
        self._last_yjs_write_bytes = 0
        self._last_yjs_snapshot_webspace_id = ""
        self._last_yjs_snapshot_reason = ""
        self._last_yjs_queue_size = 0
        self._last_yjs_node_state_timeout_at = 0.0
        self._loop: asyncio.AbstractEventLoop | None = None
        self._snapshot_task: asyncio.Task | None = None
        self._yjs_node_state_tasks: dict[str, asyncio.Task] = {}
        self._yjs_node_state_reasons: dict[str, str] = {}
        self._yjs_node_state_cache: dict[str, dict[str, Any]] = {}
        self._yjs_node_state_dirty: set[str] = set()
        self._yjs_node_state_full_read_total = 0
        self._yjs_node_state_cache_hit_total = 0
        self._last_connect_full_snapshot_at = 0.0
        self._last_connect_yjs_state_at = 0.0
        self._link_session_end_total = 0
        self._last_link_session_end_log_at = 0.0
        self._ws_control_ping_interval_s_last: float | None = None
        self._ws_control_ping_timeout_s_last: float | None = None
        self._ws_compression_last: str | None = None
        self._last_ws_close_code: int | None = None
        self._last_ws_close_reason = ""
        self._last_ws_close_error = ""
        self._last_allocator_trim_attempt_at = 0.0
        self._last_allocator_trim_at = 0.0
        self._last_allocator_trim_reason = ""
        self._allocator_trim_total = 0
        self._member_session_refresh_attempted_at = 0.0
        self._member_session_refresh_succeeded_at = 0.0
        self._member_session_refresh_error = ""
        self._member_session_expires_at = 0.0
        self._outbound_queue_high_watermark = 0
        self._outbound_enqueued_total = 0
        self._outbound_drop_total = 0
        self._outbound_drop_by_type: dict[str, int] = {}
        self._outbound_rejected_total = 0
        self._outbound_rejected_by_type: dict[str, int] = {}
        self._outbound_last_rejected: dict[str, Any] = {}
        self._outbound_send_seq = 0
        self._outbound_send_active: dict[int, dict[str, Any]] = {}
        self._outbound_send_total = 0
        self._outbound_send_failed_total = 0
        self._outbound_send_timeout_total = 0
        self._outbound_sent_bytes = 0
        self._outbound_last_send: dict[str, Any] = {}
        self._outbound_max_send: dict[str, Any] = {}
        self._semantic_ping_send_total = 0
        self._semantic_ping_send_failed_total = 0
        self._semantic_ping_send_timeout_total = 0
        self._semantic_ping_last_send: dict[str, Any] = {}
        self._semantic_ping_max_send: dict[str, Any] = {}
        self._rpc_tasks: set[asyncio.Task[Any]] = set()
        self._rpc_started_total = 0
        self._rpc_completed_total = 0
        self._rpc_failed_total = 0
        self._rpc_rejected_total = 0
        self._rpc_last_result: dict[str, Any] = {}

    @staticmethod
    def _pong_stale_after_s() -> float:
        raw = str(os.getenv("ADAOS_SUBNET_PONG_STALE_AFTER_S") or "").strip()
        try:
            value = float(raw or 35.0)
        except Exception:
            value = 35.0
        return max(15.0, value)

    @staticmethod
    def _ws_control_ping_interval_s() -> float | None:
        raw = str(os.getenv("ADAOS_SUBNET_WS_PING_INTERVAL_S") or "").strip()
        if not raw or raw.lower() in {"0", "false", "no", "off", "none", "disabled"}:
            return None
        try:
            value = float(raw)
        except Exception:
            return None
        if value <= 0.0:
            return None
        return max(5.0, value)

    @staticmethod
    def _ws_control_ping_timeout_s(ping_interval_s: float | None = None) -> float | None:
        if ping_interval_s is None:
            return None
        raw = str(os.getenv("ADAOS_SUBNET_WS_PING_TIMEOUT_S") or "").strip()
        if raw.lower() in {"0", "false", "no", "off", "none", "disabled"}:
            return None
        try:
            value = float(raw) if raw else max(20.0, ping_interval_s * 4.0)
        except Exception:
            value = max(20.0, ping_interval_s * 4.0)
        if value <= 0.0:
            return None
        return max(5.0, value)

    @staticmethod
    def _ws_semantic_ping_send_timeout_s() -> float | None:
        raw = str(os.getenv("ADAOS_SUBNET_WS_SEMANTIC_PING_SEND_TIMEOUT_S") or "").strip()
        if raw.lower() in {"0", "false", "no", "off", "none", "disabled"}:
            return None
        try:
            value = float(raw or 10.0)
        except Exception:
            value = 10.0
        if value <= 0.0:
            return None
        return max(1.0, min(value, 60.0))

    @staticmethod
    def _yjs_node_state_timeout_s() -> float | None:
        raw = str(os.getenv("ADAOS_SUBNET_YJS_NODE_STATE_TIMEOUT_S") or "").strip()
        if raw.lower() in {"0", "false", "no", "off", "none", "disabled"}:
            return None
        try:
            value = float(raw or 5.0)
        except Exception:
            value = 5.0
        if value <= 0.0:
            return None
        return max(0.25, min(value, 60.0))

    @staticmethod
    def _yjs_node_state_debounce_s() -> float:
        raw = str(os.getenv("ADAOS_SUBNET_YJS_NODE_STATE_DEBOUNCE_S") or "").strip()
        try:
            value = float(raw or 0.75)
        except Exception:
            value = 0.75
        return max(0.0, min(value, 10.0))

    @staticmethod
    def _parse_bus_prefixes(raw: str | None) -> list[str] | None:
        txt = str(raw or "").strip()
        if not txt:
            return ["io.out.", "ui."]
        if txt in ("*", "all"):
            return None
        parts = [p.strip() for p in txt.split(",") if p.strip()]
        return parts or ["io.out.", "ui."]

    def is_connected(self) -> bool:
        if not self._connected.is_set():
            return False
        if not self._hello_ack_ok or self._hello_ack_at <= 0.0:
            return False
        last_activity_at = max(
            float(self._last_pong_at or 0.0),
            float(self._last_message_at or 0.0),
            float(self._hello_ack_at or 0.0),
        )
        if last_activity_at <= 0.0:
            return False
        try:
            stale_after_s = self._pong_stale_after_s()
        except Exception:
            stale_after_s = 35.0
        return (time.time() - last_activity_at) <= max(15.0, stale_after_s)

    @staticmethod
    def _hello_ack_failure_reason(exc: BaseException, *, fallback: str = "") -> str:
        reason = str(getattr(exc, "reason", "") or fallback or type(exc).__name__).strip()
        return reason or type(exc).__name__

    @staticmethod
    def _outbound_message_identity(message: Any, *, fallback_type: str = "unknown") -> tuple[str, str | None]:
        if not isinstance(message, dict):
            return str(fallback_type or "unknown"), None
        message_type = str(message.get("t") or fallback_type or "unknown").strip() or "unknown"
        source: str | None = None
        if message_type == "bus.emit":
            event = message.get("event") if isinstance(message.get("event"), dict) else {}
            event_type = str(event.get("type") or "unknown").strip() or "unknown"
            message_type = f"bus.emit:{event_type}"
            source = str(event.get("source") or "").strip() or None
        return message_type[:192], source[:128] if source else None

    @staticmethod
    def _outbound_event_max_bytes() -> int:
        try:
            value = int(str(os.getenv("ADAOS_SUBNET_MEMBER_EVENT_MAX_BYTES", str(256 * 1024)) or "").strip())
        except Exception:
            value = 256 * 1024
        return max(1024, min(value, 16 * 1024 * 1024))

    @staticmethod
    def _outbound_frame_max_bytes() -> int:
        try:
            value = int(str(os.getenv("ADAOS_SUBNET_MEMBER_FRAME_MAX_BYTES", str(2 * 1024 * 1024)) or "").strip())
        except Exception:
            value = 2 * 1024 * 1024
        return max(16 * 1024, min(value, 64 * 1024 * 1024))

    def _record_outbound_pressure(
        self,
        *,
        signal: str,
        message_type: str,
        source: str | None,
        payload_bytes: int,
    ) -> None:
        try:
            from adaos.services.incident_registry import incident_domain_from_owner, record_incident

            domain = incident_domain_from_owner(source, fallback="core.subnet")
            record_incident(
                incident_class="subnet_channel_pressure",
                signal=signal,
                severity="warning",
                domain=domain,
                component="member_link_client",
                source=source or "subnet.link_client",
                summary=f"member-hub outbound {signal}",
                evidence={
                    "message_type": message_type,
                    "source": source,
                    "payload_bytes": int(payload_bytes),
                    "queue_size": int(self._out_q.qsize()),
                },
                fingerprint_parts=("subnet_channel_pressure", signal, domain, message_type),
                tags=("subnet", "channel", "backpressure"),
            )
        except Exception:
            _log.debug("failed to record member-hub outbound pressure", exc_info=True)

    def _validate_outbound_size(self, message: dict[str, Any]) -> tuple[str, str | None, int]:
        message_type, source = self._outbound_message_identity(message)
        base_type = str(message.get("t") or "unknown").strip() or "unknown"
        limit = self._outbound_event_max_bytes() if base_type == "bus.emit" else self._outbound_frame_max_bytes()
        estimated_bytes, exceeded = _bounded_json_size(message, limit=limit)
        if exceeded:
            signal = "event_payload_too_large" if base_type == "bus.emit" else "frame_payload_too_large"
            self._outbound_rejected_total += 1
            self._outbound_rejected_by_type[message_type] = (
                int(self._outbound_rejected_by_type.get(message_type) or 0) + 1
            )
            self._outbound_last_rejected = {
                "message_type": message_type,
                "source": source,
                "estimated_bytes": int(estimated_bytes),
                "limit_bytes": int(limit),
                "signal": signal,
                "rejected_at": time.time(),
            }
            self._record_outbound_pressure(
                signal=signal,
                message_type=message_type,
                source=source,
                payload_bytes=estimated_bytes,
            )
            raise RuntimeError(f"member-hub outbound rejected: {signal}")
        return message_type, source, estimated_bytes

    def _queue_outbound(self, message: dict[str, Any]) -> None:
        message_type, _source, _estimated_bytes = self._validate_outbound_size(message)
        try:
            self._out_q.put_nowait(message)
        except asyncio.QueueFull:
            self._outbound_drop_total += 1
            self._outbound_drop_by_type[message_type] = int(self._outbound_drop_by_type.get(message_type) or 0) + 1
            raise
        self._outbound_enqueued_total += 1
        self._outbound_queue_high_watermark = max(
            int(self._outbound_queue_high_watermark),
            int(self._out_q.qsize()),
        )

    async def _send_ws_message(
        self,
        ws: Any,
        message: dict[str, Any],
        *,
        lane: str,
        timeout_s: float | None = None,
    ) -> None:
        self._validate_outbound_size(message)
        encoded = json.dumps(message)
        encoded_bytes = len(encoded.encode("utf-8"))
        message_type, source = self._outbound_message_identity(message)
        self._outbound_send_seq += 1
        token = int(self._outbound_send_seq)
        started_at = time.time()
        started_mono = time.monotonic()
        active = {
            "token": token,
            "lane": str(lane or "unknown"),
            "message_type": message_type,
            "source": source,
            "bytes": encoded_bytes,
            "started_at": started_at,
        }
        self._outbound_send_active[token] = active
        if lane == "semantic_ping":
            self._semantic_ping_send_total += 1
        error: str | None = None
        timed_out = False
        succeeded = False
        try:
            send_result = ws.send(encoded)
            if timeout_s is None:
                await send_result
            else:
                await asyncio.wait_for(send_result, timeout=timeout_s)
            succeeded = True
            self._outbound_send_total += 1
            self._outbound_sent_bytes += encoded_bytes
        except asyncio.TimeoutError:
            timed_out = True
            error = "TimeoutError"
            self._outbound_send_timeout_total += 1
            self._outbound_send_failed_total += 1
            if lane == "semantic_ping":
                self._semantic_ping_send_timeout_total += 1
                self._semantic_ping_send_failed_total += 1
            raise
        except asyncio.CancelledError:
            error = "CancelledError"
            raise
        except Exception as exc:
            error = type(exc).__name__
            self._outbound_send_failed_total += 1
            if lane == "semantic_ping":
                self._semantic_ping_send_failed_total += 1
            raise
        finally:
            duration_s = max(0.0, time.monotonic() - started_mono)
            completed = {
                "lane": str(lane or "unknown"),
                "message_type": message_type,
                "source": source,
                "bytes": encoded_bytes,
                "duration_s": round(duration_s, 6),
                "started_at": started_at,
                "finished_at": time.time(),
                "succeeded": succeeded,
                "timed_out": timed_out,
                "error": error,
            }
            self._outbound_send_active.pop(token, None)
            self._outbound_last_send = completed
            if duration_s >= float(self._outbound_max_send.get("duration_s") or 0.0):
                self._outbound_max_send = dict(completed)
            if lane == "semantic_ping":
                self._semantic_ping_last_send = dict(completed)
                if duration_s >= float(self._semantic_ping_max_send.get("duration_s") or 0.0):
                    self._semantic_ping_max_send = dict(completed)

    def snapshot(self) -> dict[str, Any]:
        now = time.time()
        active_sends: list[dict[str, Any]] = []
        for item in list(self._outbound_send_active.values()):
            row = dict(item)
            row.pop("token", None)
            row["age_s"] = round(max(0.0, now - float(row.get("started_at") or now)), 6)
            active_sends.append(row)
        active_sends.sort(key=lambda item: -float(item.get("age_s") or 0.0))
        last_hub_core_update = (
            dict(self._last_hub_core_update)
            if isinstance(self._last_hub_core_update, dict)
            else {}
        )
        transition = _member_link_transition_snapshot()
        return {
            "role": "member",
            "connected": self.is_connected(),
            "ws_url": self._ws_url,
            "hub_node_id": self._hub_node_id,
            "connected_ago_s": round(max(0.0, now - self._connected_at), 3) if self._connected_at else None,
            "hello_ack_ok": bool(self._hello_ack_ok),
            "hello_ack_ago_s": round(max(0.0, now - self._hello_ack_at), 3) if self._hello_ack_at else None,
            "last_hello_ack_error": self._last_hello_ack_error or None,
            "last_message_ago_s": round(max(0.0, now - self._last_message_at), 3) if self._last_message_at else None,
            "last_pong_ago_s": round(max(0.0, now - self._last_pong_at), 3) if self._last_pong_at else None,
            "last_hub_event_type": self._last_hub_event_type,
            "last_hub_event_ago_s": round(max(0.0, now - self._last_hub_event_at), 3) if self._last_hub_event_at else None,
            "ws_control_ping_interval_s": self._ws_control_ping_interval_s_last,
            "ws_control_ping_timeout_s": self._ws_control_ping_timeout_s_last,
            "ws_compression": self._ws_compression_last,
            "last_ws_close_code": self._last_ws_close_code,
            "last_ws_close_reason": self._last_ws_close_reason or None,
            "last_ws_close_error": self._last_ws_close_error or None,
            "outbound": {
                "queue_size": int(self._out_q.qsize()),
                "queue_capacity": int(self._out_q.maxsize),
                "queue_high_watermark": int(self._outbound_queue_high_watermark),
                "enqueued_total": int(self._outbound_enqueued_total),
                "drop_total": int(self._outbound_drop_total),
                "drop_by_type": dict(self._outbound_drop_by_type),
                "rejected_total": int(self._outbound_rejected_total),
                "rejected_by_type": dict(self._outbound_rejected_by_type),
                "last_rejected": dict(self._outbound_last_rejected),
                "send_total": int(self._outbound_send_total),
                "send_failed_total": int(self._outbound_send_failed_total),
                "send_timeout_total": int(self._outbound_send_timeout_total),
                "sent_bytes": int(self._outbound_sent_bytes),
                "active_sends": active_sends[:8],
                "last_send": dict(self._outbound_last_send),
                "max_send": dict(self._outbound_max_send),
                "semantic_ping": {
                    "send_total": int(self._semantic_ping_send_total),
                    "send_failed_total": int(self._semantic_ping_send_failed_total),
                    "send_timeout_total": int(self._semantic_ping_send_timeout_total),
                    "last_send": dict(self._semantic_ping_last_send),
                    "max_send": dict(self._semantic_ping_max_send),
                },
            },
            "rpc": {
                "active": len(self._rpc_tasks),
                "max_concurrency": self._rpc_max_concurrency(),
                "started_total": int(self._rpc_started_total),
                "completed_total": int(self._rpc_completed_total),
                "failed_total": int(self._rpc_failed_total),
                "rejected_total": int(self._rpc_rejected_total),
                "last_result": dict(self._rpc_last_result),
            },
            "member_session": {
                "expires_at": self._member_session_expires_at or None,
                "refresh_attempt_ago_s": (
                    round(max(0.0, now - self._member_session_refresh_attempted_at), 3)
                    if self._member_session_refresh_attempted_at
                    else None
                ),
                "refresh_success_ago_s": (
                    round(max(0.0, now - self._member_session_refresh_succeeded_at), 3)
                    if self._member_session_refresh_succeeded_at
                    else None
                ),
                "refresh_error": self._member_session_refresh_error or None,
            },
            "allocator_trim": {
                "total": int(self._allocator_trim_total),
                "last_attempt_ago_s": (
                    round(max(0.0, now - self._last_allocator_trim_attempt_at), 3)
                    if self._last_allocator_trim_attempt_at
                    else None
                ),
                "last_success_ago_s": (
                    round(max(0.0, now - self._last_allocator_trim_at), 3)
                    if self._last_allocator_trim_at
                    else None
                ),
                "last_reason": self._last_allocator_trim_reason or None,
            },
            "last_hub_core_update": last_hub_core_update,
            "last_follow_key": self._last_follow_key or None,
            "last_follow_result": dict(self._last_follow_result) if isinstance(self._last_follow_result, dict) else {},
            "last_follow_error": self._last_follow_error or None,
            "last_follow_ago_s": round(max(0.0, now - self._last_follow_at), 3) if self._last_follow_at else None,
            "last_control_request": dict(self._last_control_request) if isinstance(self._last_control_request, dict) else {},
            "last_control_result": dict(self._last_control_result) if isinstance(self._last_control_result, dict) else {},
            "last_control_error": self._last_control_error or None,
            "last_control_request_ago_s": round(max(0.0, now - self._last_control_requested_at), 3) if self._last_control_requested_at else None,
            "last_control_result_ago_s": round(max(0.0, now - self._last_control_completed_at), 3) if self._last_control_completed_at else None,
            "yjs_replication": {
                "enabled": bool(self._yjs_enabled),
                "write_seen_total": int(self._yjs_write_seen_total),
                "write_queued_total": int(self._yjs_write_queued_total),
                "write_drop_disconnected_total": int(self._yjs_write_drop_disconnected_total),
                "write_drop_encode_total": int(self._yjs_write_drop_encode_total),
                "write_drop_queue_total": int(self._yjs_write_drop_queue_total),
                "sent_total": int(self._yjs_sent_total),
                "send_failed_total": int(self._yjs_send_failed_total),
                "received_total": int(self._yjs_received_total),
                "received_bytes": int(self._yjs_received_bytes),
                "snapshot_queued_total": int(self._yjs_snapshot_queued_total),
                "snapshot_failed_total": int(self._yjs_snapshot_failed_total),
                "snapshot_bytes": int(self._yjs_snapshot_bytes),
                "last_write_ago_s": round(max(0.0, now - self._last_yjs_write_at), 3) if self._last_yjs_write_at else None,
                "last_sent_ago_s": round(max(0.0, now - self._last_yjs_sent_at), 3) if self._last_yjs_sent_at else None,
                "last_received_ago_s": round(max(0.0, now - self._last_yjs_received_at), 3) if self._last_yjs_received_at else None,
                "last_snapshot_ago_s": round(max(0.0, now - self._last_yjs_snapshot_at), 3) if self._last_yjs_snapshot_at else None,
                "last_write_webspace_id": self._last_yjs_write_webspace_id or None,
                "last_write_bytes": int(self._last_yjs_write_bytes),
                "last_snapshot_webspace_id": self._last_yjs_snapshot_webspace_id or None,
                "last_snapshot_reason": self._last_yjs_snapshot_reason or None,
                "last_queue_size": int(self._last_yjs_queue_size),
                "last_node_state_timeout_ago_s": (
                    round(max(0.0, now - self._last_yjs_node_state_timeout_at), 3)
                    if self._last_yjs_node_state_timeout_at
                    else None
                ),
                "node_state_cache_entries": len(self._yjs_node_state_cache),
                "node_state_dirty_webspaces": sorted(self._yjs_node_state_dirty)[:10],
                "node_state_full_read_total": int(self._yjs_node_state_full_read_total),
                "node_state_cache_hit_total": int(self._yjs_node_state_cache_hit_total),
            },
            "transition_state": str(transition.get("transition_state") or "ready"),
            "transition_reason": str(transition.get("reason") or "none"),
            "updated_at": now,
        }

    def _compose_local_node_snapshot(
        self,
        *,
        desktop_catalog: dict[str, Any] | None = None,
        include_capacity: bool = True,
    ) -> dict[str, Any]:
        conf = get_ctx().config
        lifecycle = runtime_lifecycle_snapshot()
        update_status = read_core_update_status() or {}
        transition = _member_link_transition_snapshot()
        last_result = read_core_update_last_result() or {}
        slots = slot_status() or {}
        active_manifest = active_slot_manifest() or {}
        runtime_build_version = _effective_core_build_version(active_manifest, BUILD_INFO.version)
        runtime_base_version = str(active_manifest.get("base_version") or "")
        node_names = normalize_node_names(getattr(getattr(conf, "node_settings", None), "node_names", []))
        now = time.time()
        node_state = str(lifecycle.get("node_state") or "ready")
        try:
            from adaos.services.voice_runtime import listening_service_projection

            voice_listening = listening_service_projection()
        except Exception:
            voice_listening = {}
        snapshot = {
            "captured_at": now,
            "node_id": str(getattr(conf, "node_id", "") or ""),
            "subnet_id": str(getattr(conf, "subnet_id", "") or ""),
            "role": str(getattr(conf, "role", "") or ""),
            "node_names": list(node_names),
            "primary_node_name": str(getattr(conf, "primary_node_name", "") or ""),
            "ready": bool(node_state == "ready" and not bool(lifecycle.get("draining"))),
            "node_state": node_state,
            "reason": str(lifecycle.get("reason") or ""),
            "draining": bool(lifecycle.get("draining")),
            "route_mode": "ws" if self.is_connected() else "none",
            "connected_to_subnet": bool(self.is_connected()),
            "connected_to_hub": bool(self.is_connected()),
            "member_link_transition": transition,
            "environment": {
                "platform": sys.platform,
                "voice": {
                    "listening": voice_listening,
                    "stt": "endpoint_audio",
                    "tts": "native_or_browser",
                },
            },
            "services": {
                "voice_listening": voice_listening,
                "skill_supervisor": _service_supervisor_runtime_payload(),
            },
            "deployment": _deployment_inventory_payload(),
            "build": {
                "version": str(BUILD_INFO.version or ""),
                "build_date": str(BUILD_INFO.build_date or ""),
                "runtime_version": str(
                    runtime_build_version
                    or runtime_base_version
                    or active_manifest.get("target_version")
                    or ""
                ),
                "runtime_base_version": runtime_base_version,
                "runtime_build_version": runtime_build_version,
                "runtime_target_version": str(active_manifest.get("target_version") or ""),
                "runtime_git_commit": str(active_manifest.get("git_commit") or ""),
                "runtime_git_short_commit": str(active_manifest.get("git_short_commit") or ""),
                "runtime_git_branch": str(active_manifest.get("git_branch") or active_manifest.get("target_rev") or ""),
                "runtime_git_subject": str(active_manifest.get("git_subject") or ""),
            },
            "update_status": {
                "state": str(update_status.get("state") or ""),
                "phase": str(update_status.get("phase") or ""),
                "action": str(update_status.get("action") or ""),
                "message": str(update_status.get("message") or ""),
                "reason": str(update_status.get("reason") or ""),
                "target_rev": str(update_status.get("target_rev") or ""),
                "target_version": str(update_status.get("target_version") or ""),
                "target_slot": str(update_status.get("target_slot") or ""),
                "scheduled_for": update_status.get("scheduled_for"),
                "updated_at": update_status.get("updated_at"),
                "finished_at": update_status.get("finished_at"),
            },
            "last_result": {
                "state": str(last_result.get("state") or ""),
                "phase": str(last_result.get("phase") or ""),
                "message": str(last_result.get("message") or last_result.get("validation_error_summary") or ""),
                "target_slot": str(last_result.get("target_slot") or ""),
                "finished_at": last_result.get("finished_at"),
                "validated_at": last_result.get("validated_at"),
            },
            "slots": {
                "active_slot": str(slots.get("active_slot") or ""),
                "previous_slot": str(slots.get("previous_slot") or ""),
                "active_manifest": {
                    "slot": str(active_manifest.get("slot") or ""),
                    "target_rev": str(active_manifest.get("target_rev") or ""),
                    "target_version": str(active_manifest.get("target_version") or ""),
                    "base_version": str(active_manifest.get("base_version") or ""),
                    "build_version": str(active_manifest.get("build_version") or ""),
                    "build_date": str(active_manifest.get("build_date") or ""),
                    "git_commit": str(active_manifest.get("git_commit") or ""),
                    "git_short_commit": str(active_manifest.get("git_short_commit") or ""),
                    "git_branch": str(active_manifest.get("git_branch") or ""),
                    "git_subject": str(active_manifest.get("git_subject") or ""),
                },
            },
            "hub_control_request": {
                "request": dict(self._last_control_request) if isinstance(self._last_control_request, dict) else {},
                "result": dict(self._last_control_result) if isinstance(self._last_control_result, dict) else {},
                "error": self._last_control_error or "",
                "requested_at": self._last_control_requested_at or None,
                "completed_at": self._last_control_completed_at or None,
            },
        }
        if include_capacity:
            snapshot["capacity"] = get_local_capacity()
        if desktop_catalog is not None:
            snapshot["desktop_catalog"] = desktop_catalog
        return snapshot

    def _local_node_snapshot(self) -> dict[str, Any]:
        try:
            from adaos.services.scenario.webspace_runtime import build_local_desktop_catalog_snapshot

            desktop_catalog = build_local_desktop_catalog_snapshot(mode="workspace", include_remote=False)
        except Exception:
            _log.debug("failed to build local desktop catalog snapshot; sending empty catalog", exc_info=True)
            desktop_catalog = {"apps": [], "widgets": []}
        return self._compose_local_node_snapshot(desktop_catalog=desktop_catalog)

    async def _local_node_snapshot_async(self) -> dict[str, Any]:
        try:
            from adaos.services.scenario.webspace_runtime import build_local_desktop_catalog_snapshot_async

            desktop_catalog = await build_local_desktop_catalog_snapshot_async(mode="workspace", include_remote=False)
        except Exception:
            _log.debug("failed to build async local desktop catalog snapshot; sending empty catalog", exc_info=True)
            desktop_catalog = {"apps": [], "widgets": []}
        return self._compose_local_node_snapshot(desktop_catalog=desktop_catalog)

    def _local_node_snapshot_heartbeat(self) -> dict[str, Any]:
        return self._compose_local_node_snapshot(
            desktop_catalog=None,
            include_capacity=False,
        )

    def _local_node_status(self, *, include_capacity: bool = False) -> dict[str, Any]:
        return self._compose_local_node_snapshot(
            desktop_catalog=None,
            include_capacity=include_capacity,
        )

    def _queue_node_status(self, *, include_capacity: bool = False) -> None:
        try:
            self._queue_outbound(
                {
                    "t": "node.status",
                    "status": self._local_node_status(include_capacity=include_capacity),
                    "ts": time.time(),
                }
            )
        except Exception:
            return

    def _queue_node_snapshot_heartbeat(self) -> None:
        self._queue_node_status(include_capacity=False)

    def _queue_node_snapshot(self) -> None:
        self._last_forced_snapshot_at = time.time()
        self._queue_node_status(include_capacity=True)

    def _queue_node_catalog_snapshot(self) -> None:
        self._last_forced_snapshot_at = time.time()
        loop = self._loop
        if loop and loop.is_running():
            try:
                loop.call_soon_threadsafe(self._ensure_snapshot_task)
                return
            except Exception:
                pass
        try:
            self._queue_outbound(
                {
                    "t": "node.catalog",
                    "snapshot": self._local_node_snapshot(),
                    "ts": time.time(),
                }
            )
        except Exception:
            return

    def request_refresh(self, *, reason: str = "member_link_refresh") -> dict[str, Any]:
        snapshot = self.snapshot()
        if not self.is_connected():
            return {
                "ok": True,
                "accepted": False,
                "reason": "member_hub_not_connected",
                "link": snapshot,
            }
        self._queue_node_status(include_capacity=True)
        self._queue_node_catalog_snapshot()
        return {
            "ok": True,
            "accepted": True,
            "reason": str(reason or "member_link_refresh"),
            "link": self.snapshot(),
        }

    @staticmethod
    def _yjs_write_needs_full_node_snapshot(meta: dict[str, Any] | None) -> bool:
        metadata = dict(meta or {})
        source = str(metadata.get("source") or "").strip().lower()
        channel = str(metadata.get("channel") or "").strip().lower()
        # Skill/subnet data projections are already replicated through the
        # lightweight yjs.node_state message below. Forcing a full node snapshot
        # for every such write creates an infrastate/catalog rebuild loop on the
        # hub and can starve the member link under pressure.
        if source in {"projection_service", "async_get_ydoc", "yjs.gateway_ws"}:
            return False
        if source.startswith("projection_service") or channel.startswith("projection."):
            return False
        # Catalog/scenario mutations are structural desktop changes; send a
        # bounded structural catalog refresh, not a full periodic state snapshot.
        structural_tokens = (
            "catalog",
            "desktop_catalog",
            "scenario",
            "webspace_runtime",
            "webui",
            "installed",
        )
        return any(token in source or token in channel for token in structural_tokens)

    def _queue_node_snapshot_from_yjs_write(self, *, webspace_id: str | None, meta: dict[str, Any] | None = None) -> None:
        token = str(webspace_id or "").strip() or "default"
        # Keep desktop/subnet projections warm without turning every Yjs write
        # into a snapshot storm. The shared desktop only needs a quick bounded
        # pulse after the first write in a short burst.
        if token not in {"default", "desktop"}:
            return
        if not self._yjs_write_needs_full_node_snapshot(meta):
            return
        # Source-side suppression: regular Yjs replication already sends the
        # lightweight yjs.node_state frame. Rebuilding and publishing a full
        # node snapshot from the Yjs write callback is too expensive for idle
        # member runtimes and can become a self-sustaining catalog rebuild loop.
        # Keep the old path only as an explicit debug escape hatch.
        raw_enabled = str(os.getenv("ADAOS_SUBNET_FULL_SNAPSHOT_ON_YJS_WRITE") or "").strip().lower()
        if raw_enabled not in {"1", "true", "yes", "on"}:
            return
        now = time.time()
        min_interval = 15.0
        if now - float(self._last_yjs_write_snapshot_at or 0.0) < min_interval:
            return
        self._last_yjs_write_snapshot_at = now
        self._queue_node_catalog_snapshot()

    def _ensure_snapshot_task(self) -> None:
        if self._snapshot_task is not None and not self._snapshot_task.done():
            return
        self._snapshot_task = asyncio.create_task(
            self._enqueue_node_snapshot_async(),
            name="subnet-link-node-snapshot",
        )

    async def _enqueue_node_snapshot_async(self) -> None:
        try:
            snapshot = await self._local_node_snapshot_async()
        except Exception:
            try:
                snapshot = self._local_node_snapshot()
            except Exception:
                return
        try:
            self._queue_outbound(
                {
                    "t": "node.catalog",
                    "snapshot": snapshot,
                    "ts": time.time(),
                }
            )
        except Exception:
            return

    @staticmethod
    def _forced_snapshot_min_interval_s() -> float:
        raw = str(os.getenv("ADAOS_SUBNET_FORCED_SNAPSHOT_MIN_INTERVAL_S") or "").strip()
        try:
            value = float(raw or 5.0)
        except Exception:
            value = 5.0
        return max(1.0, min(60.0, value))

    @staticmethod
    def _connect_full_snapshot_min_interval_s() -> float:
        raw = str(os.getenv("ADAOS_SUBNET_CONNECT_FULL_SNAPSHOT_MIN_INTERVAL_S") or "").strip()
        try:
            value = float(raw or 3600.0)
        except Exception:
            value = 3600.0
        return max(15.0, min(3600.0, value))

    @staticmethod
    def _connect_yjs_state_min_interval_s() -> float:
        raw = str(os.getenv("ADAOS_SUBNET_CONNECT_YJS_STATE_MIN_INTERVAL_S") or "").strip()
        try:
            value = float(raw or 60.0)
        except Exception:
            value = 60.0
        return max(5.0, min(3600.0, value))

    def _request_local_snapshot_sync(self, *, webspace_id: str | None = None, reason: str = "subnet_sync") -> None:
        now = time.time()
        if self._last_forced_snapshot_at and (now - self._last_forced_snapshot_at) < self._forced_snapshot_min_interval_s():
            return
        try:
            get_ctx().bus.publish(
                DomainEvent(
                    type="infrastate.refresh",
                    payload={
                        "webspace_id": str(webspace_id or "").strip() or None,
                        "reason": str(reason or "subnet_sync"),
                    },
                    source="subnet.link_client",
                    ts=now,
                )
            )
        except Exception:
            pass
        self._queue_node_snapshot()

    async def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="subnet-link-client")

    async def stop(self) -> None:
        self._stop.set()
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass
        self._task = None
        self._connected.clear()
        self._connected_at = 0.0
        self._hello_ack_ok = False
        self._hello_ack_at = 0.0
        self._loop = None
        if self._snapshot_task and not self._snapshot_task.done():
            self._snapshot_task.cancel()
            try:
                await self._snapshot_task
            except asyncio.CancelledError:
                pass
            except BaseException:
                pass
        self._snapshot_task = None
        for task in list(self._rpc_tasks):
            if not task.done():
                task.cancel()
        if self._rpc_tasks:
            try:
                await asyncio.gather(*self._rpc_tasks, return_exceptions=True)
            except Exception:
                pass
        self._rpc_tasks.clear()
        for task in list(self._yjs_node_state_tasks.values()):
            if task and not task.done():
                task.cancel()
        if self._yjs_node_state_tasks:
            try:
                await asyncio.gather(*self._yjs_node_state_tasks.values(), return_exceptions=True)
            except Exception:
                pass
        self._yjs_node_state_tasks.clear()
        self._yjs_node_state_reasons.clear()
        try:
            if self._remove_ystore_listener:
                self._remove_ystore_listener()
        except Exception:
            pass
        self._maybe_trim_allocator_after_link_cycle(reason="client_stop")

    def _install_ystore_listener(self) -> None:
        if not self._yjs_enabled:
            return
        if self._remove_ystore_listener:
            return

        def _on_write(webspace_id: str, update: bytes, meta: dict[str, Any] | None = None) -> None:
            if not update:
                return
            self._yjs_write_seen_total += 1
            self._last_yjs_write_at = time.time()
            self._last_yjs_write_webspace_id = str(webspace_id or "default")
            self._last_yjs_write_bytes = len(update)
            self._yjs_node_state_dirty.add(str(webspace_id or "default"))
            if not self._connected.is_set():
                self._yjs_write_drop_disconnected_total += 1
                return
            try:
                loop = self._loop or asyncio.get_running_loop()
            except Exception:
                self._yjs_write_drop_queue_total += 1
                return
            try:
                loop.call_soon_threadsafe(
                    lambda: self._schedule_yjs_node_state(
                        webspace_id=webspace_id or "default",
                        reason="ystore_write",
                    )
                )
            except Exception:
                self._yjs_write_drop_queue_total += 1
                return
            self._queue_node_snapshot_from_yjs_write(webspace_id=webspace_id, meta=meta if isinstance(meta, dict) else None)

        self._remove_ystore_listener = add_ystore_write_listener(_on_write)

    @staticmethod
    def _yjs_snapshot_webspaces() -> list[str]:
        raw = str(os.getenv("ADAOS_SUBNET_YJS_REPLICATION_WEBSPACES") or "desktop").strip()
        out: list[str] = []
        for item in raw.split(","):
            token = str(item or "").strip()
            if token and token not in out:
                out.append(token)
        return out or ["desktop"]

    async def _queue_yjs_node_state(self, *, webspace_id: str, reason: str) -> None:
        if not self._yjs_enabled:
            return
        ws_id = str(webspace_id or "").strip() or "default"
        try:
            local_node_id = str(get_ctx().config.node_id or "").strip()
        except Exception:
            local_node_id = ""
        if not local_node_id:
            return

        async def _read_node_state() -> dict[str, Any] | None:
            ydoc = Y.YDoc()
            store = get_ystore_for_webspace(ws_id)
            try:
                await store.start()
                await store.apply_updates(ydoc)
                data_map = ydoc.get_map("data")
                data = data_map.to_json() if hasattr(data_map, "to_json") else {}
                if isinstance(data, str):
                    data = json.loads(data)
                nodes = data.get("nodes") if isinstance(data, dict) else {}
                node_state = nodes.get(local_node_id) if isinstance(nodes, dict) else None
                return node_state if isinstance(node_state, dict) else None
            finally:
                try:
                    store.stop()
                except Exception:
                    pass
                try:
                    del ydoc
                except Exception:
                    pass

        try:
            cache_allowed = (
                str(reason or "").strip() == "member_link_connected"
                and ws_id in self._yjs_node_state_cache
                and ws_id not in self._yjs_node_state_dirty
            )
            if cache_allowed:
                cached = self._yjs_node_state_cache.get(ws_id)
                node_state = json.loads(json.dumps(cached)) if isinstance(cached, dict) else None
                self._yjs_node_state_cache_hit_total += 1
            else:
                timeout_s = self._yjs_node_state_timeout_s()
                if timeout_s is None:
                    node_state = await _read_node_state()
                else:
                    node_state = await asyncio.wait_for(_read_node_state(), timeout=timeout_s)
                self._yjs_node_state_full_read_total += 1
                if isinstance(node_state, dict):
                    self._yjs_node_state_cache[ws_id] = json.loads(json.dumps(node_state))
                    self._yjs_node_state_dirty.discard(ws_id)
            if not node_state:
                return
            msg = {
                "t": "yjs.node_state",
                "webspace_id": ws_id,
                "node_id": local_node_id,
                "state": node_state,
                "reason": str(reason or "member_link_snapshot"),
                "ts": time.time(),
            }
            self._queue_outbound(msg)
            self._yjs_snapshot_queued_total += 1
            self._yjs_write_queued_total += 1
            self._yjs_snapshot_bytes += len(json.dumps(node_state, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
            self._last_yjs_snapshot_at = time.time()
            self._last_yjs_snapshot_webspace_id = ws_id
            self._last_yjs_snapshot_reason = str(reason or "member_link_snapshot")
            self._last_yjs_queue_size = int(self._out_q.qsize())
        except asyncio.TimeoutError:
            self._yjs_snapshot_failed_total += 1
            self._last_yjs_node_state_timeout_at = time.time()
            _log.warning(
                "member-link Yjs node-state snapshot timed out webspace=%s reason=%s timeout_s=%.3f",
                ws_id,
                reason,
                self._yjs_node_state_timeout_s() or 0.0,
            )
        except Exception:
            self._yjs_snapshot_failed_total += 1
            _log.debug("failed to queue member-link Yjs state snapshot webspace=%s", ws_id, exc_info=True)

    def _schedule_yjs_node_state(self, *, webspace_id: str, reason: str) -> bool:
        if not self._yjs_enabled:
            return False
        ws_id = str(webspace_id or "").strip() or "default"
        reason_token = str(reason or "member_link_snapshot").strip() or "member_link_snapshot"
        try:
            loop = self._loop or asyncio.get_running_loop()
        except Exception:
            self._yjs_write_drop_queue_total += 1
            return False
        existing = self._yjs_node_state_tasks.get(ws_id)
        if existing is not None and not existing.done():
            self._yjs_node_state_reasons[ws_id] = reason_token
            return True

        async def _coalesced_node_state() -> None:
            try:
                delay_s = self._yjs_node_state_debounce_s()
                if delay_s > 0.0:
                    await asyncio.sleep(delay_s)
                queued_reason = self._yjs_node_state_reasons.pop(ws_id, reason_token)
                await self._queue_yjs_node_state(webspace_id=ws_id, reason=queued_reason)
            finally:
                current = self._yjs_node_state_tasks.get(ws_id)
                if current is asyncio.current_task():
                    self._yjs_node_state_tasks.pop(ws_id, None)
                    self._yjs_node_state_reasons.pop(ws_id, None)

        try:
            self._yjs_node_state_reasons[ws_id] = reason_token
            self._yjs_node_state_tasks[ws_id] = loop.create_task(
                _coalesced_node_state(),
                name=f"member-link-yjs-node-state:{ws_id}",
            )
            return True
        except Exception:
            self._yjs_node_state_tasks.pop(ws_id, None)
            self._yjs_node_state_reasons.pop(ws_id, None)
            self._yjs_write_drop_queue_total += 1
            return False

    def _ensure_bus_subscription(self) -> None:
        if self._bus_subscribed:
            return

        def _on_ev(ev: Any) -> None:
            # Forward only a small subset; expand via env later if needed.
            try:
                if not self._connected.is_set():
                    return
                typ = getattr(ev, "type", None) or (ev.get("type") if isinstance(ev, dict) else None)
                if not isinstance(typ, str) or not typ:
                    return
                if typ in {
                    "sys.ready",
                    "subnet.stopping",
                    "subnet.stopped",
                    "core.update.status",
                    "node.names.changed",
                    "subnet.nats.up",
                    "subnet.nats.down",
                    "subnet.nats.reconnect",
                }:
                    self._queue_node_snapshot()
                platform_report = typ == "distributed.service.membership.reported"
                if (
                    not platform_report
                    and self._bus_prefixes is not None
                    and not any(typ.startswith(p) for p in self._bus_prefixes)
                ):
                    return
                payload = getattr(ev, "payload", None) if hasattr(ev, "payload") else (ev.get("payload") if isinstance(ev, dict) else None)
                payload_dict = payload if isinstance(payload, dict) else {"value": payload}
                meta = payload_dict.get("_meta") if isinstance(payload_dict, dict) else None
                if isinstance(meta, dict) and (
                    bool(meta.get("subnet_hub_mirrored")) or bool(meta.get("subnet_origin_node_id"))
                ):
                    return
                if not _should_forward_member_bus_event(typ, payload_dict):
                    return
                source = getattr(ev, "source", None) if hasattr(ev, "source") else (ev.get("source") if isinstance(ev, dict) else None)
                ts = getattr(ev, "ts", None) if hasattr(ev, "ts") else (ev.get("ts") if isinstance(ev, dict) else None)
                msg = {
                    "t": "bus.emit",
                    "event": {
                        "type": typ,
                        "payload": payload_dict,
                        "source": str(source or "member"),
                        "ts": float(ts or time.time()),
                    },
                }
                self._queue_outbound(msg)
            except Exception:
                return

        try:
            get_ctx().bus.subscribe("*", _on_ev)
            self._bus_subscribed = True
        except Exception:
            pass

    @staticmethod
    def _member_session_refresh_retry_s() -> float:
        raw = str(os.getenv("ADAOS_MEMBER_SESSION_REFRESH_RETRY_S") or "").strip()
        try:
            value = float(raw or 900.0)
        except Exception:
            value = 900.0
        return max(60.0, min(value, 86400.0))

    @staticmethod
    def _member_session_refresh_ahead_s() -> float:
        raw = str(os.getenv("ADAOS_MEMBER_SESSION_REFRESH_AHEAD_S") or "").strip()
        try:
            value = float(raw or (7 * 24 * 60 * 60))
        except Exception:
            value = float(7 * 24 * 60 * 60)
        return max(3600.0, min(value, 30 * 24 * 60 * 60.0))

    def _member_session_refresh_due(self, token: str, *, now: float | None = None) -> bool:
        now0 = float(now if now is not None else time.time())
        expires_at = _jwt_exp_unverified(token)
        self._member_session_expires_at = float(expires_at or 0.0)
        if self._member_session_refresh_attempted_at and (
            now0 - self._member_session_refresh_attempted_at
        ) < self._member_session_refresh_retry_s():
            return False
        # Legacy opaque credentials are offered to the refresh endpoint once
        # per retry window so a deployment can migrate them to signed tokens.
        if expires_at is None:
            return True
        return (expires_at - now0) <= self._member_session_refresh_ahead_s()

    @staticmethod
    def _request_member_session_refresh(*, root_base: str, token: str, node_id: str) -> dict[str, Any]:
        with requests.Session() as sess:
            try:
                sess.trust_env = False
            except Exception:
                pass
            response = sess.post(
                root_base.rstrip("/") + "/v1/subnets/session/refresh",
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                json={"node_id": node_id},
                timeout=8.0,
            )
            if response.status_code != 200:
                raise RuntimeError(f"member_session_refresh_http_{response.status_code}")
            payload = response.json()
        if not isinstance(payload, dict) or payload.get("ok") is not True:
            raise RuntimeError("member_session_refresh_invalid_response")
        next_token = str(payload.get("token") or "").strip()
        if not next_token:
            raise RuntimeError("member_session_refresh_missing_token")
        return payload

    async def _maybe_refresh_member_session(self, conf) -> None:
        root_base = _routed_root_base(str(getattr(conf, "hub_url", "") or ""))
        if not root_base:
            return
        token = _resolve_member_hub_token(conf)
        if not token or not self._member_session_refresh_due(token):
            return
        self._member_session_refresh_attempted_at = time.time()
        try:
            payload = await asyncio.to_thread(
                self._request_member_session_refresh,
                root_base=root_base,
                token=token,
                node_id=str(getattr(conf, "node_id", "") or "").strip(),
            )
            next_token = str(payload.get("token") or "").strip()
            save_node_runtime_state(member_hub_token=next_token)
            self._member_session_refresh_succeeded_at = time.time()
            self._member_session_refresh_error = ""
            self._member_session_expires_at = float(_jwt_exp_unverified(next_token) or 0.0)
            _log.info("routed member session refreshed")
        except Exception as exc:
            self._member_session_refresh_error = f"{type(exc).__name__}: {str(exc)[:160]}"
            _log.warning("routed member session refresh failed: %s", self._member_session_refresh_error)

    async def _run(self) -> None:
        conf = get_ctx().config
        if conf.role != "member":
            return
        if not conf.hub_url:
            _log.warning("subnet link: hub_url is not set for member")
            return
        self._loop = asyncio.get_running_loop()

        self._install_ystore_listener()
        self._ensure_bus_subscription()

        ws_url = _to_ws_url(conf.hub_url, "/ws/subnet")
        self._ws_url = ws_url

        backoff = 1.0
        while not self._stop.is_set():
            sender_t: asyncio.Task | None = None
            receiver_t: asyncio.Task | None = None
            ping_t: asyncio.Task | None = None
            status_t: asyncio.Task | None = None
            snapshot_t: asyncio.Task | None = None
            try:
                await self._maybe_refresh_member_session(conf)
                headers = [("X-AdaOS-Token", _resolve_member_hub_token(conf))]
                ws_ping_interval_s = self._ws_control_ping_interval_s()
                ws_ping_timeout_s = self._ws_control_ping_timeout_s(ws_ping_interval_s)
                ws_compression = _member_link_ws_compression()
                self._ws_control_ping_interval_s_last = ws_ping_interval_s
                self._ws_control_ping_timeout_s_last = ws_ping_timeout_s
                self._ws_compression_last = ws_compression
                self._last_ws_close_code = None
                self._last_ws_close_reason = ""
                self._last_ws_close_error = ""
                self._hello_ack_ok = False
                self._hello_ack_at = 0.0
                self._hub_node_id = ""
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    max_size=None,
                    ping_interval=ws_ping_interval_s,
                    ping_timeout=ws_ping_timeout_s,
                    compression=ws_compression,
                ) as ws:
                    hello = {
                        "t": "hello",
                        "node_id": conf.node_id,
                        "subnet_id": conf.subnet_id,
                        "hostname": None,
                        "roles": ["member"],
                        "node_names": normalize_node_names(getattr(getattr(conf, "node_settings", None), "node_names", [])),
                        "base_url": None,
                        "capacity": get_local_capacity(),
                    }
                    await self._send_ws_message(ws, hello, lane="handshake")
                    try:
                        raw_ack = await asyncio.wait_for(ws.recv(), timeout=5.0)
                        try:
                            ack = json.loads(raw_ack)
                        except Exception:
                            ack = {}
                    except asyncio.TimeoutError:
                        self._last_hello_ack_error = "hello_ack_timeout"
                        self._last_ws_close_reason = self._last_hello_ack_error
                        with contextlib.suppress(Exception):
                            await ws.close(code=1011, reason=self._last_hello_ack_error)
                        raise RuntimeError(self._last_hello_ack_error)
                    except Exception as exc:
                        self._remember_ws_close(exc)
                        reason = self._hello_ack_failure_reason(exc, fallback=self._last_ws_close_reason)
                        self._last_hello_ack_error = reason
                        self._last_ws_close_error = type(exc).__name__
                        self._last_ws_close_reason = reason
                        with contextlib.suppress(Exception):
                            await ws.close(code=1011, reason=reason[:120])
                        raise RuntimeError(reason)
                    if not isinstance(ack, dict) or ack.get("t") != "hello.ack" or ack.get("ok") is not True:
                        error = "hello_ack_rejected"
                        if isinstance(ack, dict):
                            error = str(ack.get("error") or error).strip() or error
                        self._last_hello_ack_error = error
                        self._last_ws_close_reason = error
                        with contextlib.suppress(Exception):
                            await ws.close(code=1011, reason=error[:120])
                        raise RuntimeError(error)
                    self._hub_node_id = str(ack.get("hub_node_id") or "").strip()
                    self._hello_ack_ok = True
                    self._hello_ack_at = time.time()
                    self._last_hello_ack_error = ""
                    self._connected.set()
                    self._connected_at = self._hello_ack_at
                    self._last_message_at = self._hello_ack_at
                    backoff = 1.0
                    try:
                        now = time.time()
                        await self._send_ws_message(
                            ws,
                            {
                                "t": "node.status",
                                "status": self._local_node_status(include_capacity=True),
                                "ts": now,
                            },
                            lane="session_bootstrap",
                        )
                    except Exception:
                        pass
                    try:
                        now = time.time()
                        min_full_interval = self._connect_full_snapshot_min_interval_s()
                        send_catalog = (
                            self._last_connect_full_snapshot_at <= 0.0
                            or (now - self._last_connect_full_snapshot_at) >= min_full_interval
                        )
                        if send_catalog:
                            self._last_connect_full_snapshot_at = now
                            catalog_snapshot = await self._local_node_snapshot_async()
                            await self._send_ws_message(
                                ws,
                                {
                                    "t": "node.catalog",
                                    "snapshot": catalog_snapshot,
                                    "ts": now,
                                },
                                lane="session_bootstrap",
                            )
                    except Exception:
                        _log.debug("failed to send member desktop catalog on connect", exc_info=True)
                    now = time.time()
                    min_yjs_interval = self._connect_yjs_state_min_interval_s()
                    if (
                        self._last_connect_yjs_state_at <= 0.0
                        or (now - self._last_connect_yjs_state_at) >= min_yjs_interval
                    ):
                        self._last_connect_yjs_state_at = now
                        for ws_id in self._yjs_snapshot_webspaces():
                            self._schedule_yjs_node_state(
                                webspace_id=ws_id,
                                reason="member_link_connected",
                            )

                    async def _sender() -> None:
                        while True:
                            msg = await self._out_q.get()
                            try:
                                await self._send_ws_message(ws, msg, lane="queue")
                                if isinstance(msg, dict) and msg.get("t") in {"yjs.update", "yjs.node_state"}:
                                    self._yjs_sent_total += 1
                                    self._last_yjs_sent_at = time.time()
                                    self._last_yjs_queue_size = int(self._out_q.qsize())
                            except asyncio.CancelledError:
                                raise
                            except Exception:
                                if isinstance(msg, dict) and msg.get("t") in {"yjs.update", "yjs.node_state"}:
                                    self._yjs_send_failed_total += 1
                                return

                    async def _receiver() -> None:
                        while True:
                            try:
                                raw = await ws.recv()
                            except asyncio.CancelledError:
                                raise
                            except websockets.exceptions.ConnectionClosedOK as exc:
                                self._remember_ws_close(exc)
                                return
                            except websockets.exceptions.ConnectionClosedError as exc:
                                self._remember_ws_close(exc)
                                return
                            try:
                                msg = json.loads(raw)
                            except Exception:
                                continue
                            if not isinstance(msg, dict):
                                continue
                            self._last_message_at = time.time()
                            t = msg.get("t")
                            if t == "pong":
                                self._last_pong_at = time.time()
                                continue
                            if t == "yjs.update":
                                if self._yjs_enabled:
                                    await self._on_yjs_update(msg)
                                continue
                            if t == "hub.event":
                                await self._on_hub_event(msg)
                                continue
                            if t == "node.status.request":
                                self._queue_node_status(include_capacity=True)
                                continue
                            if t in {"node.catalog.request", "node.snapshot.request"}:
                                self._queue_node_catalog_snapshot()
                                continue
                            if t == "node.display.assignment":
                                await self._on_node_display_assignment(msg)
                                continue
                            if t == "core.update.request":
                                await self._on_core_update_request(ws, msg)
                                continue
                            if t == "node.names.set":
                                await self._on_node_names_set(msg)
                                continue
                            if t == "rpc.req":
                                await self._schedule_rpc(ws, msg)
                                continue

                    async def _status_loop() -> None:
                        interval_raw = str(
                            os.getenv("ADAOS_SUBNET_STATUS_INTERVAL_S")
                            or os.getenv("ADAOS_SUBNET_SNAPSHOT_INTERVAL_S")
                            or ""
                        ).strip()
                        try:
                            interval = max(5.0, min(120.0, float(interval_raw or 20.0)))
                        except Exception:
                            interval = 20.0
                        while True:
                            await asyncio.sleep(interval)
                            await self._maybe_refresh_member_session(conf)
                            self._queue_node_status(include_capacity=False)

                    sender_t = asyncio.create_task(_sender(), name="subnet-link-sender")
                    receiver_t = asyncio.create_task(_receiver(), name="subnet-link-receiver")
                    ping_t = asyncio.create_task(self._ping_loop(ws), name="subnet-link-ping")
                    status_t = asyncio.create_task(_status_loop(), name="subnet-link-status")
                    tasks = [sender_t, receiver_t, ping_t, status_t]
                    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
                    for p in pending:
                        p.cancel()
                    # Ensure task exceptions are retrieved so shutdown doesn't spam logs.
                    _ = await asyncio.gather(*pending, return_exceptions=True)
                    done_results = await asyncio.gather(*done, return_exceptions=True)
                    done_diag: list[str] = []
                    for task, result in zip(done, done_results):
                        name = task.get_name() if hasattr(task, "get_name") else str(task)
                        if isinstance(result, BaseException):
                            done_diag.append(f"{name}:{type(result).__name__}:{result}")
                        else:
                            done_diag.append(f"{name}:ok")
                    now = time.time()
                    close_code = self._last_ws_close_code
                    if close_code is None:
                        close_code = getattr(ws, "close_code", None)
                    close_reason = self._last_ws_close_reason or str(getattr(ws, "close_reason", "") or "")
                    self._link_session_end_total += 1
                    log_fn = _log.debug
                    if now - float(self._last_link_session_end_log_at or 0.0) >= 60.0:
                        self._last_link_session_end_log_at = now
                        log_fn = _log.warning
                    log_fn(
                        "subnet link session ended ws=%s done=%s connected_for_s=%.3f last_message_ago_s=%.3f last_pong_ago_s=%.3f queue=%d close_code=%s close_reason=%s close_error=%s ws_ping_interval=%s ws_ping_timeout=%s ws_compression=%s",
                        ws_url,
                        ",".join(done_diag) or "-",
                        max(0.0, now - float(self._connected_at or 0.0)),
                        max(0.0, now - float(self._last_message_at or 0.0)) if self._last_message_at else -1.0,
                        max(0.0, now - float(self._last_pong_at or 0.0)) if self._last_pong_at else -1.0,
                        int(self._out_q.qsize()),
                        close_code,
                        close_reason or "-",
                        self._last_ws_close_error or "-",
                        ws_ping_interval_s,
                        ws_ping_timeout_s,
                        ws_compression or "disabled",
                    )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _log.debug("subnet link connect failed ws=%s err=%s", ws_url, exc)
            finally:
                had_session = self._connected_at > 0.0
                for t in (sender_t, receiver_t, ping_t, status_t, snapshot_t):
                    if t and not t.done():
                        t.cancel()
                try:
                    await asyncio.gather(
                        *(t for t in (sender_t, receiver_t, ping_t, status_t, snapshot_t) if t),
                        return_exceptions=True,
                    )
                except Exception:
                    pass
                for task in list(self._rpc_tasks):
                    if not task.done():
                        task.cancel()
                if self._rpc_tasks:
                    try:
                        await asyncio.gather(*self._rpc_tasks, return_exceptions=True)
                    except Exception:
                        pass
                self._rpc_tasks.clear()
                self._connected.clear()
                self._connected_at = 0.0
                self._hello_ack_ok = False
                self._hello_ack_at = 0.0
                if had_session:
                    self._maybe_trim_allocator_after_link_cycle(reason="session_end")

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2.0, 15.0)

    def _maybe_trim_allocator_after_link_cycle(self, *, reason: str) -> bool:
        now = time.time()
        min_interval_s = _subnet_link_malloc_trim_min_interval_s()
        last_attempt = float(self._last_allocator_trim_attempt_at or 0.0)
        if last_attempt > 0.0 and (now - last_attempt) < min_interval_s:
            return False
        self._last_allocator_trim_attempt_at = now
        trimmed = _trim_allocator_after_member_link_cycle()
        if trimmed:
            self._last_allocator_trim_at = now
            self._last_allocator_trim_reason = str(reason or "")
            self._allocator_trim_total += 1
            _log.info(
                "subnet link allocator trim completed reason=%s total=%d min_interval_s=%.3f",
                self._last_allocator_trim_reason or "-",
                self._allocator_trim_total,
                min_interval_s,
            )
        else:
            _log.debug("subnet link allocator trim skipped reason=%s min_interval_s=%.3f", reason, min_interval_s)
        return trimmed

    def _remember_ws_close(self, exc: BaseException) -> None:
        self._last_ws_close_error = type(exc).__name__
        code = getattr(exc, "code", None)
        try:
            self._last_ws_close_code = int(code) if code is not None else None
        except Exception:
            self._last_ws_close_code = None
        self._last_ws_close_reason = str(getattr(exc, "reason", "") or "")

    async def _ping_loop(self, ws) -> None:
        pong_stale_after_s = self._pong_stale_after_s()
        while True:
            await asyncio.sleep(3.0)
            now = time.time()
            last_activity_at = max(
                float(self._last_pong_at or 0.0),
                float(self._last_message_at or 0.0),
                float(self._hello_ack_at or 0.0),
            )
            if last_activity_at > 0.0 and (now - last_activity_at) > pong_stale_after_s:
                _log.warning(
                    "subnet link activity watchdog expired ws=%s age_s=%.3f threshold_s=%.3f",
                    self._ws_url,
                    now - last_activity_at,
                    pong_stale_after_s,
                )
                return
            try:
                ping_payload = {"t": "ping", "ts": time.time()}
                send_timeout_s = self._ws_semantic_ping_send_timeout_s()
                await self._send_ws_message(
                    ws,
                    ping_payload,
                    lane="semantic_ping",
                    timeout_s=send_timeout_s,
                )
            except asyncio.TimeoutError:
                _log.warning(
                    "subnet link semantic ping send timed out ws=%s timeout_s=%.3f",
                    self._ws_url,
                    self._ws_semantic_ping_send_timeout_s() or 0.0,
                )
                return
            except Exception:
                return

    async def _on_yjs_update(self, msg: dict[str, Any]) -> None:
        try:
            ws_id = str(msg.get("webspace_id") or "default")
            b64 = str(msg.get("update_b64") or "")
            if not b64:
                return
            upd = base64.b64decode(b64.encode("ascii"), validate=False)
            self._yjs_received_total += 1
            self._yjs_received_bytes += len(upd)
            self._last_yjs_received_at = time.time()
            store = get_ystore_for_webspace(ws_id)
            async with suppress_ystore_write_notifications():
                await store.write(upd)
            apply_update_to_live_room(
                ws_id,
                upd,
                root_names=["data", "ui"],
                source="subnet.link_client",
                owner="core:subnet_link_client",
                channel="core.subnet.link.update",
            )
        except Exception:
            return

    async def _on_rpc(self, ws, msg: dict[str, Any]) -> None:
        rid = msg.get("id")
        method = msg.get("method")
        params = msg.get("params") or {}
        if not isinstance(rid, str) or not rid:
            return
        allowed_methods = {
            "tools.call",
            "skills.runtime.status",
            "project.deployment.phase",
            "distributed.topology.phase",
            "distributed.topology.transfer",
            "distributed.service.invoke",
        }
        if method not in allowed_methods:
            await self._send_ws_message(
                ws,
                {"t": "rpc.res", "id": rid, "ok": False, "error": "unknown_method"},
                lane="rpc_response",
            )
            return

        tool = (params or {}).get("tool")
        arguments = (params or {}).get("arguments") or {}
        timeout = (params or {}).get("timeout")
        dev = bool((params or {}).get("dev", False))
        intent = str((params or {}).get("intent") or "").strip().lower()
        if method == "tools.call" and (not isinstance(tool, str) or ":" not in tool):
            await self._send_ws_message(
                ws,
                {"t": "rpc.res", "id": rid, "ok": False, "error": "invalid_tool"},
                lane="rpc_response",
            )
            return

        rpc_name = str(tool or method)
        started_at = time.time()
        self._rpc_started_total += 1
        try:
            result = await asyncio.to_thread(
                self._run_rpc,
                method,
                dict(params),
                timeout,
                dev,
                intent,
            )
            duration_s = max(0.0, time.time() - started_at)
            self._rpc_completed_total += 1
            self._rpc_last_result = {
                "tool": rpc_name,
                "method": method,
                "ok": True,
                "duration_s": round(duration_s, 6),
                "finished_at": time.time(),
            }
            if duration_s >= 1.0:
                _log.warning(
                    "member rpc tool slow tool=%s duration_s=%.3f argument_keys=%s",
                    rpc_name,
                    duration_s,
                    sorted(str(key) for key in arguments.keys()) if isinstance(arguments, dict) else [],
                )
            await self._send_ws_message(
                ws,
                {"t": "rpc.res", "id": rid, "ok": True, "result": result},
                lane="rpc_response",
            )
        except Exception as exc:
            duration_s = max(0.0, time.time() - started_at)
            error_code = rpc_error_code(exc)
            self._rpc_failed_total += 1
            self._rpc_last_result = {
                "tool": rpc_name,
                "method": method,
                "ok": False,
                "duration_s": round(duration_s, 6),
                "error_type": type(exc).__name__,
                "error_code": error_code,
                "finished_at": time.time(),
            }
            _log.warning(
                "member rpc tool failed tool=%s duration_s=%.3f error_type=%s error_code=%s argument_keys=%s",
                rpc_name,
                duration_s,
                type(exc).__name__,
                error_code,
                sorted(str(key) for key in arguments.keys()) if isinstance(arguments, dict) else [],
            )
            await self._send_ws_message(
                ws,
                {
                    "t": "rpc.res",
                    "id": rid,
                    "ok": False,
                    "error": member_rpc_error_payload(exc),
                },
                lane="rpc_response",
            )

    @staticmethod
    def _rpc_max_concurrency() -> int:
        try:
            value = int(str(os.getenv("ADAOS_SUBNET_RPC_MAX_CONCURRENCY", "4") or "").strip())
        except Exception:
            value = 4
        return max(1, min(value, 32))

    async def _schedule_rpc(self, ws: Any, msg: dict[str, Any]) -> None:
        active = {task for task in self._rpc_tasks if not task.done()}
        self._rpc_tasks = active
        if len(active) >= self._rpc_max_concurrency():
            self._rpc_rejected_total += 1
            rid = msg.get("id")
            if isinstance(rid, str) and rid:
                await self._send_ws_message(
                    ws,
                    {"t": "rpc.res", "id": rid, "ok": False, "error": "member_rpc_busy"},
                    lane="rpc_response",
                )
            _log.warning(
                "member rpc rejected: concurrency limit active=%d limit=%d",
                len(active),
                self._rpc_max_concurrency(),
            )
            return
        params = (msg.get("params") or {}) if isinstance(msg.get("params"), dict) else {}
        rpc_name = str(params.get("tool") or msg.get("method") or "unknown")
        task = asyncio.create_task(self._on_rpc(ws, msg), name=f"subnet-rpc:{rpc_name[:80]}")
        self._rpc_tasks.add(task)
        task.add_done_callback(self._rpc_tasks.discard)

    def _run_rpc(
        self,
        method: str,
        params: dict[str, Any],
        timeout: Any,
        dev: bool,
        intent: str = "",
    ) -> Any:
        if method == "tools.call":
            return self._run_tool(
                str(params.get("tool") or ""),
                dict(params.get("arguments") or {}),
                timeout,
                dev,
                intent,
            )
        if method == "skills.runtime.status":
            name = str(params.get("name") or "").strip()
            if (
                not name
                or len(name) > 128
                or any(character not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for character in name)
            ):
                raise ValueError("skill_name_invalid")
            return self._skill_manager().runtime_status(name)
        if method == "project.deployment.phase":
            from adaos.services.project_deployment.transport import (
                execute_remote_component_phase,
            )

            return execute_remote_component_phase(params)
        if method == "distributed.topology.phase":
            from adaos.services.distributed_runtime.adapters import (
                execute_registered_topology_phase,
            )

            return execute_registered_topology_phase(params)
        if method == "distributed.topology.transfer":
            from adaos.services.distributed_runtime.adapters import (
                execute_registered_topology_transfer,
            )

            return execute_registered_topology_transfer(params)
        if method == "distributed.service.invoke":
            from adaos.services.distributed_runtime.service_invocation import (
                execute_registered_service_invocation,
            )

            return execute_registered_service_invocation(params)
        raise PermissionError("member_rpc_method_not_allowed")

    @staticmethod
    def _skill_manager() -> SkillManager:
        ctx = get_ctx()
        return SkillManager(
            repo=ctx.skills_repo,
            registry=SqliteSkillRegistry(ctx.sql),
            git=ctx.git,
            paths=ctx.paths,
            bus=getattr(ctx, "bus", None),
            caps=ctx.caps,
            settings=ctx.settings,
        )

    @staticmethod
    def _run_tool(
        tool: str,
        arguments: dict[str, Any],
        timeout: Any,
        dev: bool,
        intent: str = "",
    ) -> Any:
        skill_name, public_tool = tool.split(":", 1)
        mgr = MemberLinkClient._skill_manager()
        accepting_new_work = is_accepting_new_work()
        declared_side_effects = (
            declared_tool_side_effects(
                mgr,
                skill_name=skill_name,
                public_tool=public_tool,
                dev=dev,
            )
            if intent == "read" or not accepting_new_work
            else ""
        )
        trusted_read_only = side_effects_are_read_only(declared_side_effects)
        if intent == "read" and not trusted_read_only:
            raise PermissionError(
                f"tool_intent_mismatch:{tool}:declared_side_effects={declared_side_effects or 'undeclared'}"
            )
        if not accepting_new_work and not trusted_read_only:
            raise RuntimeError(f"node_draining:{tool}")
        if dev:
            return mgr.run_dev_tool(skill_name, public_tool, arguments or {}, timeout=timeout)
        return mgr.run_tool(skill_name, public_tool, arguments or {}, timeout=timeout)

    async def _on_hub_event(self, msg: dict[str, Any]) -> None:
        event = msg.get("event")
        if not isinstance(event, dict):
            return
        event_type = str(event.get("type") or "").strip()
        if not event_type:
            return
        payload = event.get("payload")
        if not isinstance(payload, dict):
            payload = {"value": payload}
        source = str(event.get("source") or "hub").strip() or "hub"
        mirrored_payload = dict(payload)
        meta = mirrored_payload.get("_meta")
        if isinstance(meta, dict):
            meta = dict(meta)
        else:
            meta = {}
        meta["subnet_hub_mirrored"] = True
        if self._hub_node_id:
            meta.setdefault("subnet_hub_node_id", self._hub_node_id)
        target_node_id = str(
            mirrored_payload.get("target_node_id")
            or meta.get("target_node_id")
            or meta.get("node_target_id")
            or ""
        ).strip()
        local_node_id = str(getattr(get_ctx().config, "node_id", "") or "").strip()
        if target_node_id and local_node_id and not node_identities_match(target_node_id, local_node_id):
            return
        mirrored_payload["_meta"] = meta
        self._last_hub_event_type = event_type
        self._last_hub_event_at = time.time()
        try:
            get_ctx().bus.publish(
                DomainEvent(
                    type=event_type if event_type != "core.update.status" else "hub.core_update.status",
                    payload=mirrored_payload,
                    source=source,
                    ts=float(event.get("ts") or time.time()),
                )
            )
        except Exception:
            _log.debug("failed to publish mirrored hub event type=%s", event_type, exc_info=True)
        if event_type == "core.update.status":
            self._last_hub_core_update = dict(payload)
            await self._follow_hub_core_update(payload)
        if event_type in {"desktop.webspace.reload", "desktop.webspace.reloaded", "desktop.webspace.reset"}:
            self._request_local_snapshot_sync(
                webspace_id=str(payload.get("webspace_id") or "").strip() or None,
                reason=event_type,
            )

    async def _on_node_display_assignment(self, msg: dict[str, Any]) -> None:
        payload = msg.get("node_display")
        if not isinstance(payload, dict):
            return
        try:
            save_node_runtime_state(
                node_display={
                    "display_index": payload.get("node_index"),
                    "accent_index": payload.get("node_color_index"),
                    "node_label": str(payload.get("node_label") or "").strip(),
                    "node_compact_label": str(payload.get("node_compact_label") or "").strip(),
                    "node_color": str(payload.get("node_color") or "").strip(),
                }
            )
        except Exception:
            _log.debug("failed to persist node display assignment", exc_info=True)

    async def _follow_hub_core_update(self, payload: dict[str, Any]) -> None:
        if str(os.getenv("ADAOS_MEMBER_FOLLOW_HUB_UPDATE", "1")).strip().lower() in {"0", "false", "no", "off"}:
            return
        try:
            conf = getattr(get_ctx(), "config", None) or load_config()
        except Exception:
            conf = None
        if conf is not None and not bool(getattr(conf, "core_update_enabled", True)):
            return
        disabled_reason = core_update_reactions_disabled_reason()
        if disabled_reason:
            _log.info("hub core update follow skipped reason=%s", disabled_reason)
            return
        state = str(payload.get("state") or "").strip().lower()
        action = str(payload.get("action") or "update").strip().lower()
        manifest = payload.get("manifest") if isinstance(payload.get("manifest"), dict) else {}
        target_rev = str(payload.get("target_rev") or manifest.get("target_rev") or "").strip()
        target_version = str(
            payload.get("target_version")
            or manifest.get("target_version")
            or manifest.get("git_commit")
            or manifest.get("git_short_commit")
            or ""
        ).strip()
        scheduled_for = payload.get("scheduled_for")
        follow_key = f"{action}:{target_rev}:{target_version}:{scheduled_for}:{state}"
        if follow_key == self._last_follow_key and self._last_follow_at > 0:
            return
        if action not in {"update", "rollback"}:
            return
        if action == "update" and state != "cancelled" and not (target_rev or target_version):
            return
        from adaos.services.core_update import read_status as read_core_update_status

        local_status = read_core_update_status()
        local_state = str(local_status.get("state") or "").strip().lower()
        local_active_manifest = active_slot_manifest()
        if state not in {"countdown", "draining", "stopping", "cancelled"}:
            if not (
                action == "update"
                and state in {"succeeded", "validated"}
                and target_version
                and not _manifest_matches_target_version(local_active_manifest, target_version)
            ):
                return
        if state == "cancelled":
            if local_state not in {"countdown", "draining", "stopping"}:
                return
            path = "/api/admin/update/cancel"
            body = {"reason": "hub.member_follow.cancel"}
        elif action == "rollback":
            if local_state in {"preparing", "countdown", "draining", "stopping", "restarting", "applying"}:
                return
            body = {
                "reason": "hub.member_follow.rollback",
                "countdown_sec": self._remaining_countdown_s(scheduled_for, default=12.0),
                "drain_timeout_sec": float(payload.get("drain_timeout_sec") or 10.0),
                "signal_delay_sec": float(payload.get("signal_delay_sec") or 0.25),
            }
            path = "/api/admin/update/rollback"
        else:
            if _manifest_matches_target_version(local_active_manifest, target_version):
                return
            if local_state in {"preparing", "countdown", "draining", "stopping", "restarting", "applying"}:
                return
            reason = "hub.member_follow.update"
            countdown_default = 15.0
            if state in {"succeeded", "validated"}:
                reason = "hub.member_follow.catchup"
                countdown_default = 30.0
            body = {
                "reason": reason,
                "target_rev": target_rev,
                "target_version": target_version,
                "countdown_sec": self._remaining_countdown_s(scheduled_for, default=countdown_default),
                "drain_timeout_sec": float(payload.get("drain_timeout_sec") or 10.0),
                "signal_delay_sec": float(payload.get("signal_delay_sec") or 0.25),
            }
            path = "/api/admin/update/start"
        self._last_follow_key = follow_key
        self._last_follow_at = time.time()
        try:
            result = await asyncio.to_thread(self._post_local_admin, path, body)
            self._last_follow_result = result if isinstance(result, dict) else {"ok": True}
            self._last_follow_error = ""
        except Exception as exc:
            self._last_follow_error = f"{type(exc).__name__}: {exc}"
            self._last_follow_result = {"ok": False, "error": self._last_follow_error}
        self._queue_node_snapshot()

    @staticmethod
    def _remaining_countdown_s(scheduled_for: Any, *, default: float) -> float:
        try:
            value = float(scheduled_for or 0.0)
        except Exception:
            value = 0.0
        if value <= 0.0:
            return default
        remaining = max(5.0, min(120.0, value - time.time()))
        return round(remaining, 3)

    @staticmethod
    def _post_local_admin(path: str, body: dict[str, Any]) -> dict[str, Any]:
        supervisor_path = MemberLinkClient._supervisor_update_path(path)
        if supervisor_path:
            supervisor_bases = MemberLinkClient._local_supervisor_bases()
            supervisor_attempts: list[str] = []
            for supervisor_base in supervisor_bases:
                sess = requests.Session()
                try:
                    token = str(resolve_control_token(base_url=supervisor_base) or "dev-local-token")
                    headers = {"X-AdaOS-Token": token, "Accept": "application/json"}
                    try:
                        sess.trust_env = False
                    except Exception:
                        pass
                    with sess.post(
                        supervisor_base.rstrip("/") + supervisor_path,
                        headers=headers,
                        json=body,
                        timeout=8.0,
                    ) as response:
                        response.raise_for_status()
                        data = response.json()
                    return data if isinstance(data, dict) else {"ok": True}
                except Exception as exc:
                    supervisor_attempts.append(f"{supervisor_base}: {type(exc).__name__}: {str(exc)[:160]}")
                    continue
                finally:
                    with contextlib.suppress(Exception):
                        sess.close()
            if supervisor_bases:
                raise RuntimeError(
                    "supervisor_update_route_unavailable: "
                    + "; ".join(supervisor_attempts[-4:] or supervisor_bases[-4:])
                )

        base = MemberLinkClient._resolve_local_control_base()
        # Re-resolve the control token against the selected local control base because
        # the active runtime may be serving with a newer supervisor/env token than the
        # persisted node config still knows about.
        token = str(resolve_control_token(base_url=base) or "dev-local-token")
        headers = {"X-AdaOS-Token": token, "Accept": "application/json"}
        with requests.Session() as sess:
            try:
                sess.trust_env = False
            except Exception:
                pass
            with sess.post(base.rstrip("/") + path, headers=headers, json=body, timeout=8.0) as response:
                response.raise_for_status()
                data = response.json()
            return data if isinstance(data, dict) else {"ok": True}

    @staticmethod
    def _supervisor_update_path(path: str) -> str:
        text = str(path or "").strip()
        mapping = {
            "/api/admin/update/start": "/api/supervisor/update/start",
            "/api/admin/update/cancel": "/api/supervisor/update/cancel",
            "/api/admin/update/rollback": "/api/supervisor/update/rollback",
        }
        return mapping.get(text, "")

    @staticmethod
    def _is_ambiguous_supervisor_submission_error(exc: BaseException) -> bool:
        text = f"{type(exc).__name__}: {exc}".strip().lower()
        return "supervisor_update_route_unavailable" in text and (
            "readtimeout" in text or "read timed out" in text
        )

    @staticmethod
    def _local_supervisor_bases() -> list[str]:
        return supervisor_base_candidates_from_env(require_signal=True)

    @staticmethod
    def _resolve_local_control_base() -> str:
        candidates: list[str] = []
        env_type = str(os.getenv("ENV_TYPE") or "").strip().lower()
        supervisor_enabled = env_bool("ADAOS_SUPERVISOR_ENABLED")
        autostart_managed = env_bool("ADAOS_AUTOSTART_MANAGED")
        allow_supervisor_probe = bool(supervisor_enabled or autostart_managed or env_type != "dev")
        supervisor_candidates = []
        if allow_supervisor_probe:
            supervisor_candidates.extend(
                supervisor_base_candidates_from_env(
                    require_signal=False,
                    include_localhost=True,
                )
            )
        for raw in (
            os.getenv("ADAOS_SELF_BASE_URL"),
            os.getenv("ADAOS_CONTROL_URL"),
            os.getenv("ADAOS_CONTROL_BASE"),
        ):
            text = str(raw or "").strip().rstrip("/")
            if not text or text in candidates:
                continue
            candidates.append(text)
        for raw in runtime_fallback_http_bases(include_localhost=True, order="host"):
            if raw not in candidates:
                candidates.append(raw)
        with requests.Session() as sess:
            try:
                sess.trust_env = False
            except Exception:
                pass
            for supervisor_base in supervisor_candidates:
                if not supervisor_base:
                    continue
                try:
                    with sess.get(
                        supervisor_base + "/api/supervisor/public/update-status",
                        headers={"Accept": "application/json"},
                        timeout=0.6,
                    ) as resp:
                        if int(resp.status_code) != 200:
                            continue
                        payload = resp.json()
                    runtime = payload.get("runtime") if isinstance(payload, dict) else {}
                    runtime_url = str((runtime or {}).get("runtime_url") or "").strip().rstrip("/")
                    if runtime_url and runtime_url not in candidates:
                        candidates.insert(0, runtime_url)
                except Exception:
                    continue
            for base in candidates:
                try:
                    with sess.get(base + "/api/ping", headers={"Accept": "application/json"}, timeout=0.5) as resp:
                        if int(resp.status_code) != 200:
                            continue
                        payload = resp.json()
                    runtime = payload.get("runtime") if isinstance(payload, dict) else {}
                    transition_role = str((runtime or {}).get("transition_role") or "").strip().lower()
                    if transition_role == "candidate":
                        continue
                    if isinstance(runtime, dict) and runtime.get("admin_mutation_allowed") is False:
                        continue
                    return base
                except Exception:
                    continue
        return candidates[0] if candidates else http_base(port=DEFAULT_RUNTIME_PORT)

    async def _on_core_update_request(self, ws, msg: dict[str, Any]) -> None:
        action = str(msg.get("action") or "").strip().lower()
        if action == "start":
            action = "update"
        request_id = str(msg.get("request_id") or "").strip()
        reason = str(msg.get("reason") or "hub.member_control").strip() or "hub.member_control"
        target_rev = str(msg.get("target_rev") or "").strip()
        target_version = str(msg.get("target_version") or "").strip()
        try:
            countdown_sec = float(msg.get("countdown_sec") or (15.0 if action == "update" else 12.0))
        except Exception:
            countdown_sec = 15.0 if action == "update" else 12.0
        try:
            drain_timeout_sec = float(msg.get("drain_timeout_sec") or 10.0)
        except Exception:
            drain_timeout_sec = 10.0
        try:
            signal_delay_sec = float(msg.get("signal_delay_sec") or 0.25)
        except Exception:
            signal_delay_sec = 0.25
        self._last_control_requested_at = time.time()
        self._last_control_completed_at = 0.0
        self._last_control_error = ""
        self._last_control_request = {
            "request_id": request_id,
            "action": action,
            "reason": reason,
            "target_rev": target_rev,
            "target_version": target_version,
            "countdown_sec": countdown_sec,
            "drain_timeout_sec": drain_timeout_sec,
            "signal_delay_sec": signal_delay_sec,
            "state": "requested",
        }
        if action not in {"update", "cancel", "rollback", "drain"}:
            self._last_control_error = "invalid_action"
            result = {
                "ok": False,
                "request_id": request_id,
                "action": action,
                "error": "invalid_action",
            }
        else:
            if action == "cancel":
                path = "/api/admin/update/cancel"
                body = {"reason": reason}
            elif action == "rollback":
                path = "/api/admin/update/rollback"
                body = {
                    "reason": reason,
                    "countdown_sec": countdown_sec,
                    "drain_timeout_sec": drain_timeout_sec,
                    "signal_delay_sec": signal_delay_sec,
                }
            elif action == "drain":
                path = "/api/admin/drain"
                body = {
                    "reason": reason,
                    "drain_timeout_sec": drain_timeout_sec,
                }
            else:
                path = "/api/admin/update/start"
                body = {
                    "reason": reason,
                    "target_rev": target_rev,
                    "target_version": target_version,
                    "countdown_sec": countdown_sec,
                    "drain_timeout_sec": drain_timeout_sec,
                    "signal_delay_sec": signal_delay_sec,
                }
            try:
                admin_result = await asyncio.to_thread(self._post_local_admin, path, body)
                result = {
                    "ok": True,
                    "request_id": request_id,
                    "action": action,
                    "response": admin_result if isinstance(admin_result, dict) else {"ok": True},
                }
            except Exception as exc:
                self._last_control_error = f"{type(exc).__name__}: {exc}"
                if action in {"update", "rollback"} and self._is_ambiguous_supervisor_submission_error(exc):
                    # A supervisor POST read timeout does not prove rejection: the
                    # transition may already be persisted and running. Keep the
                    # command pending until the regular member snapshot confirms
                    # its update state instead of publishing a false hard failure.
                    result = {
                        "ok": None,
                        "accepted": None,
                        "pending": True,
                        "state": "submission_unconfirmed",
                        "request_id": request_id,
                        "action": action,
                        "error": self._last_control_error,
                    }
                else:
                    result = {
                        "ok": False,
                        "request_id": request_id,
                        "action": action,
                        "error": self._last_control_error,
                    }
        self._last_control_completed_at = time.time()
        self._last_control_result = dict(result)
        pending_confirmation = bool(result.get("pending"))
        self._last_control_request["state"] = "submission_unconfirmed" if pending_confirmation else "completed"
        self._last_control_request["ok"] = None if pending_confirmation else bool(result.get("ok"))
        if not result.get("ok") and result.get("error"):
            self._last_control_request["error"] = str(result.get("error"))
        self._queue_node_snapshot()
        try:
            await self._send_ws_message(
                ws,
                {"t": "core.update.result", "result": result},
                lane="control_response",
            )
        except Exception:
            pass

    async def _on_node_names_set(self, msg: dict[str, Any]) -> None:
        node_names = normalize_node_names(msg.get("node_names"))
        conf = persist_node_names(node_names)
        try:
            self._queue_outbound(
                {
                    "t": "node.meta",
                    "node_names": list(getattr(conf, "node_names", []) or []),
                    "ts": time.time(),
                }
            )
        except Exception:
            pass
        self._queue_node_snapshot()
        try:
            get_ctx().bus.publish(
                DomainEvent(
                    type="node.names.changed",
                    payload={
                        "node_id": str(getattr(conf, "node_id", "") or ""),
                        "node_names": list(getattr(conf, "node_names", []) or []),
                    },
                    source="subnet.member",
                    ts=time.time(),
                )
            )
        except Exception:
            pass


_MEMBER_CLIENT: MemberLinkClient | None = None


def get_member_link_client() -> MemberLinkClient:
    global _MEMBER_CLIENT
    if _MEMBER_CLIENT is None:
        _MEMBER_CLIENT = MemberLinkClient()
    return _MEMBER_CLIENT


def member_link_client_snapshot() -> dict[str, Any]:
    return get_member_link_client().snapshot()
