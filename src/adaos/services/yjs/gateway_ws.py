from __future__ import annotations

"""
Yjs websocket gateway implementation (service layer).
"""

import asyncio
import contextlib
import contextvars
from collections import deque
import hashlib
import inspect
import json
import struct
import subprocess
import sys
import time
import logging
import threading
import os
from enum import IntEnum
from typing import TYPE_CHECKING, Dict, Any, Mapping

if TYPE_CHECKING:
    from typing import Awaitable, Callable

from fastapi import APIRouter, WebSocket
from fastapi.websockets import WebSocketDisconnect

try:
    from anyio import create_task_group
    from ypy_websocket.websocket import Websocket as YWebsocket
    from ypy_websocket.websocket_server import WebsocketServer
    from ypy_websocket.yroom import YRoom
    from ypy_websocket import yutils as _ypy_yutils
except ImportError as exc:  # pragma: no cover - import guard for dev envs
    raise RuntimeError("ypy_websocket is required for AdaOS realtime collaboration. " "Install dependencies via `pip install -e .[dev]` or `pip install ypy-websocket`.") from exc

create_update_message = _ypy_yutils.create_update_message
process_sync_message = getattr(_ypy_yutils, "process_sync_message", None)
read_sync_message = getattr(_ypy_yutils, "read_message", None)
sync = getattr(_ypy_yutils, "sync", None)
YMessageType = getattr(_ypy_yutils, "YMessageType", None)
YSyncMessageType = getattr(_ypy_yutils, "YSyncMessageType", None)
if YMessageType is None:
    class YMessageType(IntEnum):
        SYNC = 0
        AWARENESS = 1
if YSyncMessageType is None:
    class YSyncMessageType(IntEnum):
        SYNC_STEP1 = 0
        SYNC_STEP2 = 1
        SYNC_UPDATE = 2

from adaos.services.workspaces import ensure_workspace, get_workspace, workspace_catalog_version
from adaos.services.yjs.bootstrap import ensure_webspace_seeded_from_scenario, write_runtime_bootstrap_state
from adaos.services.yjs.doc import invalidate_live_map_value_cache
from adaos.services.yjs.observers import attach_room_observers, forget_room_observers
from adaos.services.yjs.store import (
    current_ystore_write_metadata,
    evict_ystore_for_webspace,
    get_ystore_for_webspace,
    ystore_write_metadata_sync,
)
from adaos.services.yjs.store import ystore_write_metadata
from adaos.services.yjs.update_origin import consume_backend_room_update, mark_backend_room_update
from adaos.services.yjs.webspace import default_webspace_id
from adaos.services.scheduler import get_scheduler
from adaos.services.webio_snapshot_demand import request_snapshot_event, snapshot_demand_snapshot
from adaos.domain import Event as DomainEvent
from adaos.services.agent_context import get_ctx as get_agent_ctx

router = APIRouter()
_log = logging.getLogger("adaos.events_ws")
_ylog = logging.getLogger("adaos.yjs.gateway")


def _is_control_flow_base_exception(exc: BaseException) -> bool:
    return isinstance(exc, (asyncio.CancelledError, KeyboardInterrupt, SystemExit, GeneratorExit))


_TRANSPORT_LOCK = threading.RLock()
_ACTIVE_YWS_LOCK = threading.RLock()
_ACTIVE_EVENTS_WS_LOCK = threading.RLock()
_YWS_STORM_LOCK = threading.RLock()
_YWS_ATTEMPT_LOCK = threading.RLock()
_WEBIO_CONTROL_DEDUPE_LOCK = threading.RLock()
_WEBIO_CONTROL_DEDUPE_TTL_S = max(0.0, float(os.getenv("ADAOS_WEBIO_CONTROL_DEDUPE_TTL_S", "1.5") or "1.5"))
_WEBIO_CONTROL_DEDUPE_MAX = max(32, int(os.getenv("ADAOS_WEBIO_CONTROL_DEDUPE_MAX", "512") or "512"))
_WEBIO_CONTROL_DEDUPE_LOG_INTERVAL_S = max(
    0.0,
    float(os.getenv("ADAOS_WEBIO_CONTROL_DEDUPE_LOG_INTERVAL_S", "30") or "30"),
)
_WEBIO_CONTROL_DEDUPE_RECENT: dict[str, float] = {}
_WEBIO_CONTROL_DEDUPE_LOG_RECENT: dict[str, tuple[float, int]] = {}
_WEBIO_CONTROL_EVENT_TYPES = {
    "webio.stream.snapshot.requested",
    "webio.stream.subscription.changed",
    "webio.yjs.snapshot.requested",
    "webio.yjs.subscription.changed",
}
_TRANSPORT_STATE: dict[str, dict[str, Any]] = {
    "ws": {
        "active_connections": 0,
        "open_total": 0,
        "close_total": 0,
        "last_open_at": 0.0,
        "last_close_at": 0.0,
    },
    "yws": {
        "active_connections": 0,
        "open_total": 0,
        "close_total": 0,
        "last_open_at": 0.0,
        "last_close_at": 0.0,
    },
}
_ACTIVE_YWS_CONNECTIONS: dict[str, list[WebSocket]] = {}
_ACTIVE_YWS_CLIENTS: dict[str, dict[str, int]] = {}
_ACTIVE_EVENTS_WS_WEBSPACES: dict[int, str] = {}
_YWS_OPEN_HISTORY: deque[float] = deque(maxlen=512)
_YWS_CLIENT_OPEN_HISTORY: dict[str, deque[float]] = {}
_YWS_ATTEMPT_HISTORY: deque[float] = deque(maxlen=1024)
_YWS_CLIENT_ATTEMPT_HISTORY: dict[str, deque[float]] = {}
_YWS_CLIENT_SHORT_SESSION_HISTORY: dict[str, deque[float]] = {}
_YWS_GUARD_QUARANTINE_UNTIL: dict[str, float] = {}
_YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL: dict[str, float] = {}
_YWS_GUARD_LAST_LOG_AT: dict[str, float] = {}
_YWS_GUARD_LAST_NOTIFY_AT: dict[str, float] = {}
_YWS_GUARD_INCIDENTS: dict[str, dict[str, float]] = {}
_YWS_GUARD_DIAG: dict[str, Any] = {
    "reject_total": 0,
    "last_reject_at": 0.0,
    "last_reject_reason": "",
    "last_reject_webspace_id": "",
    "last_reject_dev_id": "",
}
_YWS_ATTEMPT_SEQ = 0
_CURRENT_YWS_ATTEMPT_ID = contextvars.ContextVar("adaos_yws_attempt_id", default="")
_ROOM_BOOTSTRAP_MATERIALIZATION = contextvars.ContextVar(
    "adaos_room_bootstrap_materialization",
    default=None,
)
_YWS_ATTEMPT_DIAG: dict[str, Any] = {
    "last_attempt_id": "",
    "last_attempt_at": 0.0,
    "last_attempt_webspace_id": "",
    "last_attempt_dev_id": "",
    "last_open_attempt_id": "",
    "last_open_at": 0.0,
    "last_close_attempt_id": "",
    "last_close_at": 0.0,
    "last_close_code": None,
    "last_close_reason": "",
    "last_guard_reject_attempt_id": "",
    "last_room_timeout_attempt_id": "",
}
_YROOM_LIFECYCLE_LOCK = threading.RLock()
_YROOM_BOOTSTRAP_ATTEMPT_SEQ = 0
_YROOM_LIFECYCLE: dict[str, dict[str, Any]] = {}
_GATEWAY_SNAPSHOT_OWNER_LOCK = threading.RLock()
_GATEWAY_SNAPSHOT_OWNER_THREAD_ID: int | None = None
_GATEWAY_SNAPSHOT_OWNER_LOOP: asyncio.AbstractEventLoop | None = None
_GATEWAY_SNAPSHOT_CACHE: dict[str, Any] = {}
_WS_EVENT_SUBSCRIPTIONS_LOCK = threading.RLock()
_WS_EVENT_SUBSCRIBERS: dict[int, dict[str, Any]] = {}
_WS_EVENT_FORWARDER_INSTALLED = False
_WS_EVENT_SEND_LOCK = threading.RLock()
_WS_EVENT_SEND_STATES: dict[int, dict[str, Any]] = {}
_WS_EVENT_SEND_DIAG: dict[str, Any] = {
    "queued_total": 0,
    "sent_total": 0,
    "dropped_total": 0,
    "coalesced_total": 0,
    "last_drop_at": 0.0,
    "last_drop_kind": "",
    "last_coalesced_at": 0.0,
    "last_coalesced_kind": "",
}
_COMMAND_TRACE_LOCK = threading.RLock()
_COMMAND_TRACE_HISTORY: deque[dict[str, Any]] = deque(maxlen=128)
_COMMAND_TRACE_STATS: dict[str, int] = {
    "reload_total": 0,
    "reload_duplicate_total": 0,
    "reset_total": 0,
    "reset_duplicate_total": 0,
}
_COMMAND_TRACE_SEQ = 0
_IDLE_ROOM_RESET_TASKS: dict[str, asyncio.Task[None]] = {}


def _env_float(name: str, default: float, *, minimum: float = 0.0) -> float:
    try:
        value = float(os.getenv(name, str(default)) or str(default))
    except Exception:
        value = float(default)
    return max(float(minimum), value)


def _coerce_gateway_webspace_id(value: Any) -> str:
    raw = str(value or "").strip()
    default_id = default_webspace_id()
    # Older browser builds persisted "default"; route them to the runtime default.
    if not raw or raw == "default":
        return default_id
    return raw


def _clean_browser_metadata_value(value: Any, *, max_len: int = 256) -> str | None:
    token = str(value or "").strip()
    if not token:
        return None
    return token[:max_len]


def _browser_metadata_param(params: Mapping[str, Any], *keys: str) -> tuple[Any, bool]:
    for key in keys:
        if key in params:
            return params.get(key), True
    return None, False


def _clean_signaling_device_id(value: Any) -> str | None:
    return _clean_browser_metadata_value(value, max_len=128)


def _browser_session_metadata(params: Dict[str, str]) -> dict[str, str]:
    raw: dict[str, tuple[Any, bool]] = {
        "browser_family": _browser_metadata_param(params, "browser_family", "browserFamily", "browser"),
        "device_display_name": _browser_metadata_param(params, "device_display_name", "deviceDisplayName", "device_name", "deviceName"),
        "endpoint_display_name": _browser_metadata_param(params, "endpoint_display_name", "endpointDisplayName", "endpoint_name", "endpointName"),
        "client_build_id": _browser_metadata_param(params, "client_build_id", "clientBuildId", "build_id", "buildId"),
        "client_build_version": _browser_metadata_param(params, "client_build_version", "clientBuildVersion", "build_version", "buildVersion"),
        "os_name": _browser_metadata_param(params, "os_name", "osName", "os", "platform"),
        "form_factor": _browser_metadata_param(params, "form_factor", "formFactor", "form"),
        "user_agent": _browser_metadata_param(params, "user_agent", "userAgent", "ua"),
        "media_audio_input_device_id": _browser_metadata_param(params, "media_audio_input_device_id", "mediaAudioInputDeviceId"),
        "media_audio_input_label": _browser_metadata_param(params, "media_audio_input_label", "mediaAudioInputLabel"),
        "media_audio_output_device_id": _browser_metadata_param(params, "media_audio_output_device_id", "mediaAudioOutputDeviceId"),
        "media_audio_output_label": _browser_metadata_param(params, "media_audio_output_label", "mediaAudioOutputLabel"),
        "media_volume": _browser_metadata_param(params, "media_volume", "mediaVolume"),
        "media_muted": _browser_metadata_param(params, "media_muted", "mediaMuted"),
        "media_audio_input_supported": _browser_metadata_param(params, "media_audio_input_supported", "mediaAudioInputSupported"),
        "media_audio_output_supported": _browser_metadata_param(params, "media_audio_output_supported", "mediaAudioOutputSupported"),
        "media_audio_output_selection_supported": _browser_metadata_param(params, "media_audio_output_selection_supported", "mediaAudioOutputSelectionSupported"),
        "media_route_status_level": _browser_metadata_param(params, "media_route_status_level", "mediaRouteStatusLevel"),
        "media_route_status_state": _browser_metadata_param(params, "media_route_status_state", "mediaRouteStatusState"),
        "media_route_status_reason": _browser_metadata_param(params, "media_route_status_reason", "mediaRouteStatusReason"),
        "media_route_status_detail": _browser_metadata_param(params, "media_route_status_detail", "mediaRouteStatusDetail"),
        "media_route_checked_at": _browser_metadata_param(params, "media_route_checked_at", "mediaRouteCheckedAt"),
        "media_route_recent_device_change": _browser_metadata_param(params, "media_route_recent_device_change", "mediaRouteRecentDeviceChange"),
        "media_route_bluetooth_profile_hint": _browser_metadata_param(params, "media_route_bluetooth_profile_hint", "mediaRouteBluetoothProfileHint"),
        "media_route_output_routed": _browser_metadata_param(params, "media_route_output_routed", "mediaRouteOutputRouted"),
        "media_route_input_applied": _browser_metadata_param(params, "media_route_input_applied", "mediaRouteInputApplied"),
    }
    clearable_media_keys = {
        "media_audio_input_device_id",
        "media_audio_input_label",
        "media_audio_output_device_id",
        "media_audio_output_label",
    }
    out: dict[str, str] = {}
    for key, (value, present) in raw.items():
        cleaned = _clean_browser_metadata_value(
            value,
            max_len=512 if key == "user_agent" else (256 if key in {"media_audio_input_device_id", "media_audio_output_device_id"} else (160 if key == "media_route_status_detail" else (128 if key in {"client_build_version", "device_display_name", "endpoint_display_name", "media_audio_input_label", "media_audio_output_label"} else 96))),
        )
        if cleaned:
            out[key] = cleaned
        elif present and key in clearable_media_keys:
            out[key] = ""
    return out


def _parse_client_build_version(value: Any) -> tuple[int, int, int] | None:
    token = str(value or "").strip()
    if not token:
        return None
    core = token.split("+", 1)[0].split("-", 1)[0].strip()
    parts = core.split(".")
    if not 1 <= len(parts) <= 3:
        return None
    parsed: list[int] = []
    for part in parts:
        if not part.isdigit():
            return None
        parsed.append(int(part))
    while len(parsed) < 3:
        parsed.append(0)
    return parsed[0], parsed[1], parsed[2]


def _browser_env_rejected_reason(dev_id: str, browser_metadata: dict[str, Any]) -> str | None:
    revoked_raw = os.getenv("ADAOS_BROWSER_REVOKED_DEVICE_IDS", "") or ""
    revoked = {item.strip() for item in revoked_raw.replace(";", ",").split(",") if item.strip()}
    if str(dev_id or "").strip() in revoked:
        return "revoked"
    minimum = _parse_client_build_version(os.getenv("ADAOS_BROWSER_MIN_CLIENT_BUILD_VERSION", ""))
    if minimum is None:
        return None
    current = _parse_client_build_version(browser_metadata.get("client_build_version"))
    if current is None or current < minimum:
        return "client_version_unsupported"
    return None


def _browser_env_rejected_yws_close(reason: str | None) -> tuple[int, str]:
    reason_token = str(reason or "denied").strip().lower() or "denied"
    if reason_token == "client_version_unsupported":
        # This is not an auth failure: the browser must reload its frontend bundle.
        # Older clients already hard-reset on inbound_yws_update_payload_blocked,
        # so keep that token for backward-compatible upgrade recovery.
        return 1013, "inbound_yws_update_payload_blocked:client_version_unsupported"
    return 1008, f"device_{reason_token}"


def _yws_direct_transport_enabled() -> bool:
    return _env_flag("ADAOS_YWS_DIRECT_TRANSPORT_ENABLED", True)


def _yws_disabled_reject_hold_sec() -> float:
    return _env_float("ADAOS_YWS_DISABLED_REJECT_HOLD_SEC", 20.0, minimum=0.0)


def _yws_client_limit_key(
    dev_id: str | None,
    *,
    browser_page_id: str | None = None,
    browser_session_id: str | None = None,
    client_attempt_id: str | None = None,
) -> str:
    device_key = _clean_browser_metadata_value(dev_id, max_len=128) or "unknown"
    page_key = _clean_browser_metadata_value(browser_page_id, max_len=128)
    session_key = _clean_browser_metadata_value(browser_session_id, max_len=128)
    attempt_key = _clean_browser_metadata_value(client_attempt_id, max_len=128)
    # browser_session_id is persisted in sessionStorage and can be copied by a
    # browser when a tab is duplicated. Prefer the non-persisted page id so two
    # live tabs do not replace each other's reconnecting Yjs connection.
    scoped_key = page_key or session_key or attempt_key
    return f"{device_key}::{scoped_key}" if scoped_key else device_key


def _split_yws_client_limit_key(value: str) -> tuple[str, str | None]:
    token = str(value or "").strip()
    if "::" not in token:
        return token or "unknown", None
    device_key, _, scoped_key = token.partition("::")
    return device_key or "unknown", scoped_key or None


def _websocket_yws_client_limit_key(websocket: WebSocket, *, fallback_device_id: str | None = None) -> str:
    try:
        params = getattr(websocket, "query_params", {}) or {}
    except Exception:
        params = {}
    dev_id = _websocket_device_id(websocket) if websocket is not None else fallback_device_id
    if not dev_id or dev_id == "unknown":
        dev_id = fallback_device_id or dev_id
    return _yws_client_limit_key(
        dev_id,
        browser_page_id=params.get("browser_page_id") or params.get("browserPageId"),
        browser_session_id=(
            params.get("browser_session_id")
            or params.get("browserSessionId")
            or params.get("client_session_id")
            or params.get("clientSessionId")
        ),
        client_attempt_id=params.get("client_yws_attempt_id") or params.get("client_attempt_id"),
    )


def _browser_auth_response_payload(
    *,
    dev_id: str,
    webspace_id: str,
    allowed: bool,
    reason: str | None,
) -> dict[str, Any]:
    reason_token = str(reason or "").strip().lower() or None
    payload: dict[str, Any] = {
        "ok": True,
        "kind": "browser",
        "device_id": str(dev_id or "").strip(),
        "webspace_id": _coerce_gateway_webspace_id(webspace_id),
        "allowed": bool(allowed),
        "reason": reason_token,
        "next": "continue" if allowed else "login",
        "terminal": not bool(allowed),
    }
    if reason_token:
        payload["connection_state"] = reason_token
    return payload


def _env_int(name: str, default: int, *, minimum: int = 0) -> int:
    try:
        value = int(os.getenv(name, str(default)) or str(default))
    except Exception:
        value = int(default)
    return max(int(minimum), value)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


_IDLE_ROOM_EVICT_SEC = _env_float("ADAOS_YJS_IDLE_ROOM_EVICT_SEC", 60.0, minimum=0.0)
_YROOM_DIAG_ENABLED = _env_flag("ADAOS_YJS_ROOM_DIAG_ENABLED", True)
_YWS_ROOM_READY_TIMEOUT_S = _env_float("ADAOS_YWS_ROOM_READY_TIMEOUT_S", 12.0, minimum=0.0)
_YWS_ROOM_READY_MAX_S = _env_float("ADAOS_YWS_ROOM_READY_MAX_S", 45.0, minimum=0.0)
_YWS_ROOM_READY_POLL_S = _env_float("ADAOS_YWS_ROOM_READY_POLL_S", 1.0, minimum=0.25)
_YWS_ROOM_BOOTSTRAP_STEP_TIMEOUT_S = _env_float("ADAOS_YWS_ROOM_BOOTSTRAP_STEP_TIMEOUT_S", 20.0, minimum=0.0)
_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_BYTES = _env_int(
    "ADAOS_YSTORE_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_BYTES",
    1024 * 1024,
    minimum=0,
)
_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_DELAY_SEC = _env_float(
    "ADAOS_YSTORE_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_DELAY_SEC",
    1.5,
    minimum=0.0,
)
_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_QUIET_SEC = _env_float(
    "ADAOS_YSTORE_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_QUIET_SEC",
    1.0,
    minimum=0.0,
)
_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_COOLDOWN_SEC = _env_float(
    "ADAOS_YSTORE_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_COOLDOWN_SEC",
    30.0,
    minimum=0.0,
)
_GATEWAY_LIVE_PERSIST_COMPACTION_LOCK = threading.RLock()
_GATEWAY_LIVE_PERSIST_COMPACTION_NEXT_AT: dict[str, float] = {}
# Materialization mutates the authoritative YDoc synchronously, while YRoom
# fans the resulting update out on its async observer task. Keep the wait
# bounded, but enabled by default so a successful preview switch also means
# that every currently connected browser has received the update.
_LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS = _env_float(
    "ADAOS_YJS_LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS",
    250.0,
    minimum=0.0,
)
_LIVE_ROOM_REFRESH_DIAG_TTL_SEC = _env_float("ADAOS_YJS_LIVE_ROOM_REFRESH_DIAG_TTL_SEC", 60.0, minimum=1.0)
_LIVE_ROOM_REFRESH_DIAG_MAX = _env_int("ADAOS_YJS_LIVE_ROOM_REFRESH_DIAG_MAX", 128, minimum=16)
_YWS_ROOM_STALE_RECOVERY_TIMEOUT_S = _env_float("ADAOS_YWS_ROOM_STALE_RECOVERY_TIMEOUT_S", 3.0, minimum=0.25)
_YWS_ROOM_RESTART_RECOMMEND_TIMEOUTS = _env_int("ADAOS_YWS_ROOM_RESTART_RECOMMEND_TIMEOUTS", 3, minimum=1)
_YWS_FIRST_MESSAGE_TIMEOUT_S = _env_float("ADAOS_YWS_FIRST_MESSAGE_TIMEOUT_S", 12.0, minimum=0.0)
_YWS_MAX_ACTIVE_PER_WEBSPACE = _env_int("ADAOS_YWS_MAX_ACTIVE_PER_WEBSPACE", 6, minimum=1)
_YWS_MAX_ACTIVE_PER_CLIENT = _env_int("ADAOS_YWS_MAX_ACTIVE_PER_CLIENT", 2, minimum=1)
_YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS = _env_flag("ADAOS_YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS", True)
_YWS_GUARD_RECENT_OPEN_10S = _env_int("ADAOS_YWS_GUARD_RECENT_OPEN_10S", 8, minimum=1)
_YWS_GUARD_CLIENT_OPEN_15S = _env_int("ADAOS_YWS_GUARD_CLIENT_OPEN_15S", 4, minimum=1)
_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S = _env_int("ADAOS_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S", 3, minimum=1)
_YWS_GUARD_COOLDOWN_S = _env_float("ADAOS_YWS_GUARD_COOLDOWN_S", 300.0, minimum=0.0)
_YWS_GUARD_MAX_COOLDOWN_S = _env_float("ADAOS_YWS_GUARD_MAX_COOLDOWN_S", 1800.0, minimum=0.0)
_YWS_GUARD_ESCALATION_WINDOW_S = _env_float("ADAOS_YWS_GUARD_ESCALATION_WINDOW_S", 3600.0, minimum=1.0)
_YWS_GUARD_NOTIFY_INTERVAL_S = _env_float("ADAOS_YWS_GUARD_NOTIFY_INTERVAL_S", 30.0, minimum=1.0)
_YWS_GUARD_REJECT_HOLD_MAX_SEC = _env_float("ADAOS_YWS_GUARD_REJECT_HOLD_MAX_SEC", 0.0, minimum=0.0)
_YWS_GUARD_REJECT_HOLD_STEP_SEC = _env_float("ADAOS_YWS_GUARD_REJECT_HOLD_STEP_SEC", 1.0, minimum=0.05)
_YWS_GUARD_RECOVERY_IN_PROGRESS_S = _env_float("ADAOS_YWS_GUARD_RECOVERY_IN_PROGRESS_S", 10.0, minimum=1.0)
_YWS_GUARD_MIN_STABLE_SESSION_S = _env_float("ADAOS_YWS_GUARD_MIN_STABLE_SESSION_S", 20.0, minimum=0.0)
_YWS_GUARD_SHORT_SESSION_WINDOW_S = _env_float("ADAOS_YWS_GUARD_SHORT_SESSION_WINDOW_S", 60.0, minimum=1.0)
_YWS_GUARD_SHORT_SESSION_LIMIT = _env_int("ADAOS_YWS_GUARD_SHORT_SESSION_LIMIT", 3, minimum=1)
_YWS_GUARD_ROUTE_DEPENDENCY_RECOVERY = _env_flag("ADAOS_YWS_GUARD_ROUTE_DEPENDENCY_RECOVERY", True)
_YWS_GUARD_ROUTE_PROBE_FRESH_S = _env_float("ADAOS_YWS_GUARD_ROUTE_PROBE_FRESH_S", 30.0, minimum=1.0)
_YWS_GUARD_PLANNED_TRANSITION_GRACE_S = _env_float(
    "ADAOS_YWS_GUARD_PLANNED_TRANSITION_GRACE_S",
    120.0,
    minimum=0.0,
)
_YWS_GUARD_PLANNED_TRANSITION_MAX_AGE_S = _env_float(
    "ADAOS_YWS_GUARD_PLANNED_TRANSITION_MAX_AGE_S",
    900.0,
    minimum=1.0,
)
_WS_EVENT_SEND_QUEUE_LIMIT = _env_int("ADAOS_WS_EVENT_SEND_QUEUE_LIMIT", 64, minimum=1)
_WS_EVENT_SEND_LOG_INTERVAL_S = _env_float("ADAOS_WS_EVENT_SEND_LOG_INTERVAL_S", 10.0, minimum=0.0)
_YROOM_DIAG_LOG_INTERVAL_SEC = _env_float("ADAOS_YJS_ROOM_DIAG_LOG_INTERVAL_SEC", 5.0, minimum=0.0)
_YROOM_DIAG_BUFFER_WARN = _env_int("ADAOS_YJS_ROOM_DIAG_BUFFER_WARN", 32, minimum=1)
_YROOM_DIAG_PENDING_WARN = _env_int("ADAOS_YJS_ROOM_DIAG_PENDING_WARN", 32, minimum=1)
_YROOM_DIAG_UPDATE_WARN_BYTES = _env_int("ADAOS_YJS_ROOM_DIAG_UPDATE_WARN_BYTES", 256 * 1024, minimum=1)
_YROOM_INBOUND_GUARD_BLOCK_BYTES = _env_int("ADAOS_YJS_ROOM_INBOUND_GUARD_BLOCK_BYTES", 16 * 1024 * 1024, minimum=1)
_YROOM_INBOUND_GUARD_RESET_COOLDOWN_SEC = _env_float("ADAOS_YJS_ROOM_INBOUND_GUARD_RESET_COOLDOWN_SEC", 5.0, minimum=0.0)
_YROOM_NATIVE_PREFLIGHT_ENABLED = _env_flag("ADAOS_YJS_ROOM_NATIVE_PREFLIGHT_ENABLED", True)
_YROOM_NATIVE_PREFLIGHT_THRESHOLD_BYTES = _env_int(
    "ADAOS_YJS_ROOM_NATIVE_PREFLIGHT_THRESHOLD_BYTES",
    256 * 1024,
    minimum=1,
)
_YROOM_NATIVE_PREFLIGHT_TIMEOUT_SEC = _env_float(
    "ADAOS_YJS_ROOM_NATIVE_PREFLIGHT_TIMEOUT_SEC",
    5.0,
    minimum=0.25,
)
_YROOM_SERVER_AUTHORITATIVE_INITIAL_SYNC = _env_flag(
    "ADAOS_YJS_SERVER_AUTHORITATIVE_INITIAL_SYNC",
    True,
)
_YROOM_DIAG_INCLUDE_YSTORE = _env_flag("ADAOS_YJS_ROOM_DIAG_INCLUDE_YSTORE", False)
_YROOM_EFFECTIVE_GUARD_FULL_CHECK_INTERVAL_SEC = _env_float("ADAOS_YJS_EFFECTIVE_GUARD_FULL_CHECK_INTERVAL_SEC", 120.0, minimum=0.0)
_YROOM_EFFECTIVE_GUARD_FULL_CHECK_BYTES = _env_int("ADAOS_YJS_EFFECTIVE_GUARD_FULL_CHECK_BYTES", 64 * 1024 * 1024, minimum=1)
_YROOM_EFFECTIVE_GUARD_MIN_CHECK_INTERVAL_SEC = _env_float("ADAOS_YJS_EFFECTIVE_GUARD_MIN_CHECK_INTERVAL_SEC", 1.0, minimum=0.0)
_YROOM_EFFECTIVE_GUARD_TOP_LEVEL_CHECKS = _env_flag("ADAOS_YJS_EFFECTIVE_GUARD_TOP_LEVEL_CHECKS", True)
_YROOM_EFFECTIVE_GUARD_SNAPSHOT_HASHES = _env_flag("ADAOS_YJS_EFFECTIVE_GUARD_SNAPSHOT_HASHES", False)
_YROOM_EFFECTIVE_GUARD_SNAPSHOT_DETAILS = _env_flag("ADAOS_YJS_EFFECTIVE_GUARD_SNAPSHOT_DETAILS", False)
_YJS_MATERIALIZED_PAYLOAD_TRUST_APPLY_SUMMARY = _env_flag(
    "ADAOS_YJS_MATERIALIZED_PAYLOAD_TRUST_APPLY_SUMMARY",
    True,
)
_YROOM_EFFECTIVE_DEFAULT_REQUIRED_BRANCHES = (
    "ui.application",
    "data.catalog",
    "data.installed",
    "data.desktop",
    "data.webio",
    "data.routing",
    "registry.merged",
)


_YROOM_NATIVE_PREFLIGHT_SCRIPT = (
    "import struct, sys\n"
    "import y_py as Y\n"
    "payload = sys.stdin.buffer.read()\n"
    "if len(payload) < 17:\n"
    "    raise SystemExit(2)\n"
    "sync_type = payload[0]\n"
    "current_size, message_size = struct.unpack('>QQ', payload[1:17])\n"
    "if len(payload) != 17 + current_size + message_size:\n"
    "    raise SystemExit(3)\n"
    "current = payload[17:17 + current_size]\n"
    "message = payload[17 + current_size:]\n"
    "doc = Y.YDoc()\n"
    "if current:\n"
    "    Y.apply_update(doc, current)\n"
    "if sync_type == 0:\n"
    "    Y.encode_state_as_update(doc, message)\n"
    "elif sync_type in (1, 2):\n"
    "    if message != b'\\x00\\x00':\n"
    "        Y.apply_update(doc, message)\n"
    "else:\n"
    "    raise SystemExit(4)\n"
    "Y.encode_state_vector(doc)\n"
)


def _extract_inbound_y_sync_payload(message: bytes) -> tuple[int | None, bytes | None]:
    if (
        not message
        or read_sync_message is None
        or int(message[0]) != int(YMessageType.SYNC)
    ):
        return None, None
    if len(message) < 2:
        return -1, None
    sync_payload = bytes(message[1:])
    sync_type = int(sync_payload[0])
    if sync_type not in {
        int(YSyncMessageType.SYNC_STEP1),
        int(YSyncMessageType.SYNC_STEP2),
        int(YSyncMessageType.SYNC_UPDATE),
    }:
        return sync_type, None
    try:
        return sync_type, bytes(read_sync_message(sync_payload[1:]) or b"")
    except Exception:
        return sync_type, None


def _preflight_inbound_y_sync_payload(
    current: bytes,
    payload: bytes,
    *,
    sync_type: int,
) -> tuple[bool, str]:
    if not _YROOM_NATIVE_PREFLIGHT_ENABLED:
        return True, "disabled"
    if len(payload) >= int(_YROOM_INBOUND_GUARD_BLOCK_BYTES):
        return False, "sync_payload_too_large"
    framed = (
        bytes([int(sync_type)])
        + struct.pack(">QQ", len(current), len(payload))
        + current
        + payload
    )
    try:
        completed = subprocess.run(
            [sys.executable, "-c", _YROOM_NATIVE_PREFLIGHT_SCRIPT],
            input=framed,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            timeout=float(_YROOM_NATIVE_PREFLIGHT_TIMEOUT_SEC),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, "timeout"
    except Exception as exc:
        return False, f"preflight_error:{type(exc).__name__}"
    if completed.returncode == 0:
        return True, "ok"
    stderr = (completed.stderr or b"")[:500].decode("utf-8", errors="replace").strip()
    reason = f"returncode={completed.returncode}"
    if stderr:
        reason += f" stderr={stderr}"
    return False, reason


def _yroom_effective_env_required_branches() -> tuple[str, ...]:
    raw_branches = str(os.getenv("ADAOS_YJS_EFFECTIVE_REQUIRED_BRANCHES", "") or "").strip()
    if raw_branches:
        return _normalize_required_branch_list(raw_branches.split(","))
    raw_data_keys = str(
        os.getenv(
            "ADAOS_YJS_EFFECTIVE_REQUIRED_DATA_KEYS",
            "",
        )
        or ""
    ).strip()
    if raw_data_keys:
        return _normalize_required_branch_list(f"data.{key.strip()}" for key in raw_data_keys.split(","))
    return tuple(_YROOM_EFFECTIVE_DEFAULT_REQUIRED_BRANCHES)


def _normalize_required_branch_list(raw_items: Any) -> tuple[str, ...]:
    if isinstance(raw_items, str):
        raw_items = raw_items.split(",")
    if isinstance(raw_items, (bytes, bytearray)) or raw_items is None:
        return ()
    try:
        items = list(raw_items)
    except Exception:
        return ()
    allowed_roots = {"ui", "data", "registry", "runtime"}
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in items:
        token = str(raw or "").strip().replace("/", ".")
        parts = [part.strip() for part in token.split(".") if part.strip()]
        if len(parts) < 2 or parts[0] not in allowed_roots:
            continue
        path = ".".join(parts)
        if path in seen:
            continue
        seen.add(path)
        normalized.append(path)
    return tuple(normalized)


def _yws_single_client_reconnect_escalation_limit() -> int:
    return max(_YWS_GUARD_CLIENT_OPEN_15S + 1, _YWS_GUARD_CLIENT_OPEN_15S * 2)


def _yws_single_client_short_session_escalation_limit() -> int:
    return max(_YWS_GUARD_SHORT_SESSION_LIMIT + 1, _YWS_GUARD_SHORT_SESSION_LIMIT * 2)
_YROOM_EFFECTIVE_GUARD_STRICT_FULL_CHECKS = _env_flag("ADAOS_YJS_EFFECTIVE_GUARD_STRICT_FULL_CHECKS", False)
_YROOM_EFFECTIVE_GUARD_INITIAL_FULL_CHECK_UPDATES = _env_int(
    "ADAOS_YJS_EFFECTIVE_GUARD_INITIAL_FULL_CHECK_UPDATES",
    3,
    minimum=0,
)
_YROOM_EFFECTIVE_GUARD_REPAIR_INITIAL_UPDATES = _env_int(
    "ADAOS_YJS_EFFECTIVE_GUARD_REPAIR_INITIAL_UPDATES",
    0,
    minimum=0,
)
_YROOM_EFFECTIVE_GUARD_REPAIR_COOLDOWN_SEC = _env_float(
    "ADAOS_YJS_EFFECTIVE_GUARD_REPAIR_COOLDOWN_SEC",
    0.25,
    minimum=0.0,
)
_YROOM_EFFECTIVE_REPAIR_REPLAY_TTL_SEC = _env_float(
    "ADAOS_YJS_EFFECTIVE_REPAIR_REPLAY_TTL_SEC",
    30.0,
    minimum=0.0,
)
_YROOM_EFFECTIVE_REPAIR_REPLAY_MAX_UPDATES = _env_int(
    "ADAOS_YJS_EFFECTIVE_REPAIR_REPLAY_MAX_UPDATES",
    8,
    minimum=1,
)
_YROOM_EFFECTIVE_REPAIR_REPLAY_FLUSH_SEC = _env_float(
    "ADAOS_YJS_EFFECTIVE_REPAIR_REPLAY_FLUSH_SEC",
    6.0,
    minimum=0.0,
)
_YROOM_EFFECTIVE_REPAIR_REPLAY_INTERVAL_SEC = _env_float(
    "ADAOS_YJS_EFFECTIVE_REPAIR_REPLAY_INTERVAL_SEC",
    0.1,
    minimum=0.05,
)
_YROOM_EFFECTIVE_INITIAL_REPLAY = _env_flag("ADAOS_YJS_EFFECTIVE_INITIAL_REPLAY", True)
_YROOM_EFFECTIVE_INITIAL_REPLAY_MAX_BYTES = _env_int(
    "ADAOS_YJS_EFFECTIVE_INITIAL_REPLAY_MAX_BYTES",
    8 * 1024 * 1024,
    minimum=1,
)
_YROOM_AUTHORITATIVE_SELECTOR_LEASE_SEC = _env_float(
    "ADAOS_YJS_AUTHORITATIVE_SELECTOR_LEASE_SEC",
    30.0,
    minimum=0.0,
)
_EMPTY_Y_UPDATE = b"\x00\x00"
_YROOM_INBOUND_GUARD_RESET_AT: dict[str, float] = {}


def _shorten_webspace_id(value: str | None) -> str:
    raw = str(value or "").strip()
    return raw if raw else "default"


def _reserve_inbound_guard_reset(webspace_id: str, now_mono: float) -> bool:
    key = _coerce_gateway_webspace_id(webspace_id)
    with _YROOM_LIFECYCLE_LOCK:
        previous = float(_YROOM_INBOUND_GUARD_RESET_AT.get(key) or 0.0)
        if previous > 0.0 and now_mono - previous < _YROOM_INBOUND_GUARD_RESET_COOLDOWN_SEC:
            return False
        _YROOM_INBOUND_GUARD_RESET_AT[key] = now_mono
        return True


def _is_empty_y_update(update: bytes | bytearray | memoryview | None) -> bool:
    return bytes(update or b"") == _EMPTY_Y_UPDATE


def _is_websocket_accept_race(exc: BaseException) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    return (
        "websocket.accept" in text
        and "websocket.close" in text
    ) or "close message has been sent" in text


def _is_websocket_receive_disconnect_race(exc: BaseException) -> bool:
    text = str(exc or "").strip().lower()
    if not text:
        return False
    return (
        "websocket is not connected" in text
        or "need to call \"accept\" first" in text
        or "disconnect message has been received" in text
        or "close message has been sent" in text
    )


async def _stop_ystore_maybe_async(ystore: Any) -> None:
    try:
        result = ystore.stop()
    except Exception:
        return
    if inspect.isawaitable(result):
        try:
            await result
        except Exception:
            return


def _seconds_ago(value: Any, now: float) -> float | None:
    if not isinstance(value, (int, float)):
        return None
    stamp = float(value)
    if stamp <= 0.0:
        return None
    return round(max(0.0, now - stamp), 3)


def _memory_stream_statistics(stream: Any) -> dict[str, Any]:
    stats = getattr(stream, "statistics", None)
    if not callable(stats):
        return {}
    try:
        snapshot = stats()
    except Exception:
        return {}
    return {
        "current_buffer_used": int(getattr(snapshot, "current_buffer_used", 0) or 0),
        "max_buffer_size": int(getattr(snapshot, "max_buffer_size", 0) or 0),
        "open_send_streams": int(getattr(snapshot, "open_send_streams", 0) or 0),
        "open_receive_streams": int(getattr(snapshot, "open_receive_streams", 0) or 0),
        "tasks_waiting_send": int(getattr(snapshot, "tasks_waiting_send", 0) or 0),
        "tasks_waiting_receive": int(getattr(snapshot, "tasks_waiting_receive", 0) or 0),
    }


_YROOM_PRESSURE_STATE: dict[str, dict[str, Any]] = {}
_AUTHORITATIVE_SCENARIO_LEASES: dict[str, dict[str, Any]] = {}
_LIVE_ROOM_REFRESH_DIAG_LOCK = threading.RLock()
_LIVE_ROOM_REFRESH_PENDING: dict[tuple[str, int, str], dict[str, Any]] = {}
_LIVE_ROOM_REFRESH_RECENT: deque[dict[str, Any]] = deque(maxlen=_LIVE_ROOM_REFRESH_DIAG_MAX)


def _elapsed_ms_since(started: float) -> float:
    return round(max(0.0, time.perf_counter() - float(started or 0.0)) * 1000.0, 3)


def _live_refresh_update_key(webspace_id: str, update: bytes | bytearray | memoryview | None) -> tuple[str, int, str] | None:
    if not update:
        return None
    payload = bytes(update or b"")
    if not payload:
        return None
    return (_coerce_gateway_webspace_id(webspace_id), len(payload), hashlib.sha1(payload).hexdigest())


def _live_refresh_public_key(key: tuple[str, int, str]) -> dict[str, Any]:
    return {"webspace_id": key[0], "bytes": key[1], "sha1": key[2]}


def _prune_live_refresh_diag_locked(now_mono: float) -> None:
    cutoff = now_mono - float(_LIVE_ROOM_REFRESH_DIAG_TTL_SEC)
    stale = [
        key
        for key, entry in _LIVE_ROOM_REFRESH_PENDING.items()
        if float(entry.get("registered_at_mono") or 0.0) < cutoff
    ]
    for key in stale:
        _LIVE_ROOM_REFRESH_PENDING.pop(key, None)
    while len(_LIVE_ROOM_REFRESH_PENDING) > int(_LIVE_ROOM_REFRESH_DIAG_MAX):
        oldest_key = min(
            _LIVE_ROOM_REFRESH_PENDING,
            key=lambda item: float(_LIVE_ROOM_REFRESH_PENDING[item].get("registered_at_mono") or 0.0),
        )
        _LIVE_ROOM_REFRESH_PENDING.pop(oldest_key, None)


def _snapshot_live_refresh_entry(entry: Mapping[str, Any] | None) -> dict[str, Any]:
    if not isinstance(entry, Mapping):
        return {}
    phase_timings = entry.get("phase_timings_ms") if isinstance(entry.get("phase_timings_ms"), Mapping) else {}
    return {
        "webspace_id": str(entry.get("webspace_id") or "").strip() or "default",
        "reason": str(entry.get("reason") or "").strip() or None,
        "bytes": int(entry.get("bytes") or 0),
        "sha1": str(entry.get("sha1") or "").strip() or None,
        "registered_at": float(entry.get("registered_at") or 0.0),
        "observer_broadcast_seen": bool(entry.get("observer_broadcast_seen")),
        "observer_exact_update_match": entry.get("observer_exact_update_match"),
        "observer_update_bytes": entry.get("observer_update_bytes"),
        "observer_update_sha1": entry.get("observer_update_sha1"),
        "client_sync_done": bool(entry.get("client_sync_done")),
        "client_count": int(entry.get("client_count") or 0),
        "client_send_done_count": int(entry.get("client_send_done_count") or 0),
        "last_client_send_ms": entry.get("last_client_send_ms"),
        "client_sync_reason": str(entry.get("client_sync_reason") or "").strip() or None,
        "phase_timings_ms": {
            str(key): float(value)
            for key, value in dict(phase_timings).items()
            if isinstance(value, (int, float))
        },
        "timed_out": bool(entry.get("timed_out")),
    }


def _register_live_refresh_update(
    webspace_id: str,
    update: bytes | bytearray | memoryview | None,
    *,
    reason: str,
    phase_timings_ms: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    key = _live_refresh_update_key(webspace_id, update)
    if key is None:
        return {}
    now_mono = time.monotonic()
    phase_timings = {
        str(name): float(value)
        for name, value in dict(phase_timings_ms or {}).items()
        if isinstance(value, (int, float))
    }
    entry: dict[str, Any] = {
        "webspace_id": key[0],
        "bytes": key[1],
        "sha1": key[2],
        "reason": str(reason or "").strip() or "live_room_refresh",
        "registered_at_mono": now_mono,
        "registered_at": time.time(),
        "phase_timings_ms": phase_timings,
        "observer_broadcast_seen": False,
        "client_sync_done": False,
        "client_count": 0,
        "client_send_done_count": 0,
    }
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        _prune_live_refresh_diag_locked(now_mono)
        _LIVE_ROOM_REFRESH_PENDING[key] = entry
    marker = _snapshot_live_refresh_entry(entry)
    marker["key"] = _live_refresh_public_key(key)
    return marker


def _live_refresh_snapshot_by_key(key: tuple[str, int, str] | None) -> dict[str, Any]:
    if key is None:
        return {}
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        entry = _LIVE_ROOM_REFRESH_PENDING.get(key)
        if entry is None:
            for recent in reversed(_LIVE_ROOM_REFRESH_RECENT):
                if (
                    str(recent.get("webspace_id") or "") == key[0]
                    and int(recent.get("bytes") or 0) == key[1]
                    and str(recent.get("sha1") or "") == key[2]
                ):
                    entry = recent
                    break
        snapshot = _snapshot_live_refresh_entry(entry)
    if snapshot:
        snapshot["key"] = _live_refresh_public_key(key)
    return snapshot


def _live_refresh_recent_snapshot(webspace_id: str | None = None, *, limit: int = 5) -> list[dict[str, Any]]:
    key = _coerce_gateway_webspace_id(webspace_id) if webspace_id is not None else None
    out: list[dict[str, Any]] = []
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        pending = [
            _snapshot_live_refresh_entry(entry)
            for entry in _LIVE_ROOM_REFRESH_PENDING.values()
            if key is None or str(entry.get("webspace_id") or "") == key
        ]
        recent = [
            _snapshot_live_refresh_entry(entry)
            for entry in _LIVE_ROOM_REFRESH_RECENT
            if key is None or str(entry.get("webspace_id") or "") == key
        ]
    for item in [*pending, *recent]:
        if item:
            out.append(item)
    out.sort(key=lambda item: float(item.get("registered_at") or 0.0), reverse=True)
    return out[: max(0, int(limit))]


def _compact_materialized_payload_for_room_history(payload: Mapping[str, Any]) -> dict[str, Any]:
    compact = {
        str(key): value
        for key, value in dict(payload or {}).items()
        if str(key) != "skill_decls"
    }
    try:
        return json.loads(json.dumps(compact, ensure_ascii=False))
    except Exception:
        return dict(compact)


def _compact_materialized_payload_apply_result_for_log(value: Any) -> dict[str, Any]:
    source = value if isinstance(value, Mapping) else {}
    apply_summary = source.get("apply_summary") if isinstance(source.get("apply_summary"), Mapping) else {}
    return {
        "ok": source.get("ok"),
        "ready": source.get("ready"),
        "error": source.get("error"),
        "broadcast_update_bytes": source.get("broadcast_update_bytes"),
        "full_state_update_bytes": source.get("full_state_update_bytes"),
        "force_full_state_update": source.get("force_full_state_update"),
        "full_state_snapshot_persisted": source.get("full_state_snapshot_persisted"),
        "phase_timings_ms": {
            str(key): float(raw)
            for key, raw in dict(source.get("phase_timings_ms") or {}).items()
            if isinstance(raw, (int, float))
        },
        "apply_summary": {
            key: apply_summary.get(key)
            for key in (
                "changed_branches",
                "unchanged_branches",
                "failed_branches",
                "diff_applied_branches",
                "patch_applied_branches",
                "trusted_fingerprint_unchanged_branches",
                "trusted_previous_fingerprint_patch_branches",
                "fingerprint_unchanged_branches",
                "stale_fingerprint_branches",
            )
            if apply_summary.get(key) is not None
        },
    }


def _record_live_refresh_observer_broadcast_for_key(
    key: tuple[str, int, str] | None,
    *,
    update: bytes | bytearray | memoryview | None,
    client_count: int,
    exact_update_match: bool,
) -> tuple[str, int, str] | None:
    if key is None:
        return None
    observed = bytes(update or b"")
    now_mono = time.monotonic()
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        entry = _LIVE_ROOM_REFRESH_PENDING.get(key)
        if entry is None:
            return None
        phase_timings = entry.setdefault("phase_timings_ms", {})
        if isinstance(phase_timings, dict):
            phase_timings["observer_broadcast"] = round(
                max(0.0, now_mono - float(entry.get("registered_at_mono") or now_mono)) * 1000.0,
                3,
            )
        entry["observer_broadcast_seen"] = True
        entry["observer_exact_update_match"] = bool(exact_update_match)
        entry["observer_update_bytes"] = len(observed)
        entry["observer_update_sha1"] = hashlib.sha1(observed).hexdigest() if observed else None
        entry["client_count"] = max(0, int(client_count or 0))
        if int(client_count or 0) <= 0:
            entry["client_sync_done"] = True
            if isinstance(phase_timings, dict):
                phase_timings["client_sync"] = phase_timings.get("observer_broadcast", 0.0)
        _LIVE_ROOM_REFRESH_PENDING[key] = entry
    return key


def _record_live_refresh_observer_broadcast(
    webspace_id: str,
    update: bytes | bytearray | memoryview | None,
    *,
    client_count: int,
) -> tuple[str, int, str] | None:
    return _record_live_refresh_observer_broadcast_for_key(
        _live_refresh_update_key(webspace_id, update),
        update=update,
        client_count=client_count,
        exact_update_match=True,
    )


def _record_live_refresh_message_create(key: tuple[str, int, str] | None, elapsed_ms: float) -> None:
    if key is None:
        return
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        entry = _LIVE_ROOM_REFRESH_PENDING.get(key)
        if entry is None:
            return
        phase_timings = entry.setdefault("phase_timings_ms", {})
        if isinstance(phase_timings, dict):
            phase_timings["observer_message_create"] = round(max(0.0, float(elapsed_ms or 0.0)), 3)


def _record_live_refresh_client_send(
    key: tuple[str, int, str] | None,
    *,
    elapsed_ms: float,
) -> None:
    if key is None:
        return
    now_mono = time.monotonic()
    done_snapshot: dict[str, Any] | None = None
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        entry = _LIVE_ROOM_REFRESH_PENDING.get(key)
        if entry is None:
            return
        done = int(entry.get("client_send_done_count") or 0) + 1
        entry["client_send_done_count"] = done
        entry["last_client_send_ms"] = round(max(0.0, float(elapsed_ms or 0.0)), 3)
        client_count = int(entry.get("client_count") or 0)
        if client_count <= 0 or done >= client_count:
            phase_timings = entry.setdefault("phase_timings_ms", {})
            if isinstance(phase_timings, dict):
                phase_timings["client_sync"] = round(
                    max(0.0, now_mono - float(entry.get("registered_at_mono") or now_mono)) * 1000.0,
                    3,
                )
            entry["client_sync_done"] = True
            done_snapshot = _snapshot_live_refresh_entry(entry)
            _LIVE_ROOM_REFRESH_RECENT.append(done_snapshot)
            _LIVE_ROOM_REFRESH_PENDING.pop(key, None)
        else:
            _LIVE_ROOM_REFRESH_PENDING[key] = entry


def _mark_live_refresh_wait_timeout(key: tuple[str, int, str] | None) -> dict[str, Any]:
    if key is None:
        return {}
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        entry = _LIVE_ROOM_REFRESH_PENDING.get(key)
        if entry is not None:
            entry["timed_out"] = True
            _LIVE_ROOM_REFRESH_PENDING[key] = entry
        snapshot = _snapshot_live_refresh_entry(entry)
    if snapshot:
        snapshot["key"] = _live_refresh_public_key(key)
    return snapshot


def _mark_live_refresh_no_clients(key: tuple[str, int, str] | None) -> dict[str, Any]:
    if key is None:
        return {}
    done_snapshot: dict[str, Any] | None = None
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        entry = _LIVE_ROOM_REFRESH_PENDING.get(key)
        if entry is None:
            snapshot = _snapshot_live_refresh_entry(None)
        else:
            now_mono = time.monotonic()
            phase_timings = entry.setdefault("phase_timings_ms", {})
            if isinstance(phase_timings, dict):
                phase_timings["client_sync"] = round(
                    max(0.0, now_mono - float(entry.get("registered_at_mono") or now_mono)) * 1000.0,
                    3,
                )
            entry["client_count"] = 0
            entry["client_sync_done"] = True
            entry["client_sync_reason"] = "no_clients"
            done_snapshot = _snapshot_live_refresh_entry(entry)
            _LIVE_ROOM_REFRESH_RECENT.append(done_snapshot)
            _LIVE_ROOM_REFRESH_PENDING.pop(key, None)
            snapshot = done_snapshot
    if snapshot:
        snapshot["key"] = _live_refresh_public_key(key)
    return snapshot


def _mark_live_refresh_wait_skipped(
    key: tuple[str, int, str] | None,
    *,
    client_count: int,
    reason: str,
) -> dict[str, Any]:
    if key is None:
        return {}
    with _LIVE_ROOM_REFRESH_DIAG_LOCK:
        entry = _LIVE_ROOM_REFRESH_PENDING.get(key)
        if entry is not None:
            entry["client_count"] = max(0, int(client_count or 0))
            entry["client_sync_reason"] = str(reason or "wait_skipped")
            _LIVE_ROOM_REFRESH_PENDING[key] = entry
        snapshot = _snapshot_live_refresh_entry(entry)
    if snapshot:
        snapshot["key"] = _live_refresh_public_key(key)
    return snapshot


async def _wait_live_refresh_client_sync(
    key: tuple[str, int, str] | None,
    *,
    timeout_ms: float,
) -> dict[str, Any]:
    if key is None:
        return {}
    deadline = time.perf_counter() + max(0.0, float(timeout_ms or 0.0)) / 1000.0
    while True:
        snapshot = _live_refresh_snapshot_by_key(key)
        if snapshot.get("client_sync_done"):
            return snapshot
        if time.perf_counter() >= deadline:
            return _mark_live_refresh_wait_timeout(key) or _live_refresh_snapshot_by_key(key)
        await asyncio.sleep(0.002)


def note_authoritative_current_scenario(webspace_id: str, scenario_id: str, *, reason: str = "scenario_switch") -> None:
    key = _coerce_gateway_webspace_id(webspace_id)
    scenario = str(scenario_id or "").strip()
    if not key or not scenario or _YROOM_AUTHORITATIVE_SELECTOR_LEASE_SEC <= 0.0:
        return
    _AUTHORITATIVE_SCENARIO_LEASES[key] = {
        "scenario_id": scenario,
        "reason": str(reason or "").strip() or "scenario_switch",
        "expires_mono": time.monotonic() + float(_YROOM_AUTHORITATIVE_SELECTOR_LEASE_SEC),
        "updated_at": time.time(),
    }


def _clear_authoritative_current_scenario(webspace_id: str, *, reason: str = "stale") -> None:
    key = _coerce_gateway_webspace_id(webspace_id)
    if not key:
        return
    previous = _AUTHORITATIVE_SCENARIO_LEASES.pop(key, None)
    if previous:
        _ylog.info(
            "cleared authoritative current_scenario lease webspace=%s reason=%s previous=%s previous_reason=%s",
            key,
            str(reason or "").strip() or "stale",
            previous.get("scenario_id"),
            previous.get("reason"),
        )


def _authoritative_current_scenario(webspace_id: str) -> str | None:
    key = _coerce_gateway_webspace_id(webspace_id)
    lease = dict(_AUTHORITATIVE_SCENARIO_LEASES.get(key) or {})
    scenario = str(lease.get("scenario_id") or "").strip()
    expires_mono = float(lease.get("expires_mono") or 0.0)
    if not scenario or expires_mono <= 0.0:
        _AUTHORITATIVE_SCENARIO_LEASES.pop(key, None)
        return None
    if time.monotonic() > expires_mono:
        _AUTHORITATIVE_SCENARIO_LEASES.pop(key, None)
        return None
    return scenario


def yjs_pressure_snapshot(webspace_id: str | None = None) -> dict[str, Any]:
    now = time.monotonic()
    if webspace_id is None:
        active = 0
        rooms: list[dict[str, Any]] = []
        for key, raw in list(_YROOM_PRESSURE_STATE.items()):
            item = dict(raw or {})
            if bool(item.get("active")):
                active += 1
            since_at = float(item.get("since_mono") or 0.0)
            item["age_s"] = round(max(0.0, now - since_at), 3) if bool(item.get("active")) and since_at > 0.0 else 0.0
            item["webspace_id"] = str(item.get("webspace_id") or key or "default").strip() or "default"
            rooms.append(item)
        rooms.sort(key=lambda item: (0 if bool(item.get("active")) else 1, -float(item.get("age_s") or 0.0), str(item.get("webspace_id") or "")))
        return {
            "active_room_total": active,
            "room_total": len(rooms),
            "rooms": rooms,
        }
    key = _coerce_gateway_webspace_id(webspace_id)
    raw = dict(_YROOM_PRESSURE_STATE.get(key) or {})
    if not raw:
        return {
            "webspace_id": key,
            "active": False,
            "reason": "",
            "age_s": 0.0,
            "pending_send_tasks": 0,
            "pending_store_tasks": 0,
            "buffer_used": 0,
            "waiting_send": 0,
            "waiting_receive": 0,
            "update_bytes": 0,
            "message_bytes": 0,
        }
    since_at = float(raw.get("since_mono") or 0.0)
    raw["age_s"] = round(max(0.0, now - since_at), 3) if bool(raw.get("active")) and since_at > 0.0 else 0.0
    raw["webspace_id"] = key
    return raw


def _request_gateway_live_persist_compaction(
    ystore: Any,
    webspace_id: str,
    *,
    update_bytes: int,
    source: str,
    channel: str,
) -> bool:
    threshold = int(_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_BYTES)
    observed = max(0, int(update_bytes or 0))
    if threshold <= 0 or observed < threshold:
        return False
    requester = getattr(ystore, "request_runtime_compaction", None)
    if not callable(requester):
        return False
    key = str(webspace_id or "").strip() or "default"
    delay = float(_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_DELAY_SEC)
    quiet = float(_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_QUIET_SEC)
    cooldown = float(_GATEWAY_LIVE_PERSIST_AUTOCOMPACT_COOLDOWN_SEC)
    if cooldown > 0.0:
        now = time.monotonic()
        with _GATEWAY_LIVE_PERSIST_COMPACTION_LOCK:
            next_at = float(_GATEWAY_LIVE_PERSIST_COMPACTION_NEXT_AT.get(key) or 0.0)
            if now < next_at:
                return False
            _GATEWAY_LIVE_PERSIST_COMPACTION_NEXT_AT[key] = now + cooldown

    async def _runner() -> None:
        try:
            if delay > 0.0:
                await asyncio.sleep(delay)
            requested = bool(await requester(reason="gateway_live_room_persist", min_quiet_sec=quiet))
            if requested:
                _ylog.warning(
                    "YStore compaction requested after large gateway live-room persist webspace=%s bytes=%s source=%s channel=%s cooldown_s=%.3f",
                    key,
                    observed,
                    source,
                    channel,
                    cooldown,
                )
            else:
                _ylog.debug(
                    "YStore compaction skipped after large gateway live-room persist webspace=%s bytes=%s source=%s channel=%s",
                    key,
                    observed,
                    source,
                    channel,
                )
        except Exception:
            if cooldown > 0.0:
                with _GATEWAY_LIVE_PERSIST_COMPACTION_LOCK:
                    if float(_GATEWAY_LIVE_PERSIST_COMPACTION_NEXT_AT.get(key) or 0.0) > time.monotonic():
                        _GATEWAY_LIVE_PERSIST_COMPACTION_NEXT_AT.pop(key, None)
            _ylog.debug(
                "failed to request YStore compaction after gateway live-room persist webspace=%s",
                key,
                exc_info=True,
            )

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False
    loop.create_task(_runner())
    return True


class DiagnosticYRoom(YRoom):
    """
    Thin YRoom wrapper that logs pressure signals without changing semantics.

    The goal is to surface whether memory growth comes from queued Y updates
    and fanout tasks, not to alter delivery or persistence behavior yet.
    """

    def __init__(self, ready: bool = True, ystore: Any | None = None, log: logging.Logger | None = None):
        super().__init__(ready=ready, ystore=ystore, log=log)
        self._diag_pending_send_tasks = 0
        self._diag_pending_store_tasks = 0
        self._diag_peak_buffer_used = 0
        self._diag_peak_pending_send_tasks = 0
        self._diag_peak_pending_store_tasks = 0
        self._diag_update_total = 0
        self._diag_update_bytes_total = 0
        self._diag_empty_update_skip_total = 0
        self._diag_empty_update_skip_bytes = 0
        self._diag_backend_persist_skip_total = 0
        self._diag_backend_persist_skip_bytes = 0
        self._diag_destructive_update_block_total = 0
        self._diag_destructive_update_block_bytes = 0
        self._diag_inbound_guard_block_total = 0
        self._diag_inbound_guard_block_bytes = 0
        self._diag_inbound_guard_last_bytes = 0
        self._diag_inbound_guard_last_block_bytes = int(_YROOM_INBOUND_GUARD_BLOCK_BYTES)
        self._diag_inbound_guard_last_at = 0.0
        self._diag_inbound_guard_last_reset_reserved = False
        self._diag_native_preflight_total = 0
        self._diag_native_preflight_block_total = 0
        self._diag_native_preflight_block_bytes = 0
        self._diag_native_preflight_last_reason = ""
        self._diag_authoritative_initial_skip_total = 0
        self._diag_authoritative_initial_skip_bytes = 0
        self._diag_authoritative_initial_last_sync_type = ""
        self._diag_effective_repair_total = 0
        self._diag_effective_repair_bytes = 0
        self._diag_effective_initial_replay_total = 0
        self._diag_effective_initial_replay_bytes = 0
        self._diag_effective_initial_replay_skip_total = 0
        self._diag_effective_initial_replay_dedupe_total = 0
        self._diag_effective_initial_replay_last_reason = ""
        self._diag_effective_branch_snapshot: dict[str, Any] = {"ready": False, "error": "not_observed"}
        self._diag_effective_last_full_check_mono = time.monotonic()
        self._diag_effective_last_repair_mono = 0.0
        self._diag_effective_repair_replay_updates: deque[dict[str, Any]] = deque()
        self._diag_last_log_mono = 0.0
        self._diag_pressure_active = False
        self._diag_pressure_reason = ""
        self._diag_pressure_since_mono = 0.0
        self._diag_pressure_activation_total = 0
        self._diag_pressure_clear_total = 0

    def _diag_room_id(self) -> str:
        return str(getattr(self, "_webspace_id", "") or "default").strip() or "default"

    def _diag_ystore_snapshot(self) -> dict[str, Any]:
        ystore = getattr(self, "ystore", None)
        runtime_snapshot = getattr(ystore, "runtime_snapshot", None)
        if callable(runtime_snapshot):
            try:
                raw = runtime_snapshot()
                if isinstance(raw, dict):
                    return {
                        "update_log_entries": int(raw.get("update_log_entries") or 0),
                        "update_log_bytes": int(raw.get("update_log_bytes") or 0),
                        "replay_window_bytes": int(raw.get("replay_window_bytes") or 0),
                        "last_update_bytes": int(raw.get("last_update_bytes") or 0),
                    }
            except Exception:
                return {}
        return {}

    def _diag_snapshot(self, *, include_ystore: bool = False) -> dict[str, Any]:
        send_stats = _memory_stream_statistics(getattr(self, "_update_send_stream", None))
        recv_stats = _memory_stream_statistics(getattr(self, "_update_receive_stream", None))
        now_mono = time.monotonic()
        pressure_age_s = (
            round(max(0.0, now_mono - float(self._diag_pressure_since_mono or 0.0)), 3)
            if self._diag_pressure_active and float(self._diag_pressure_since_mono or 0.0) > 0.0
            else 0.0
        )
        return {
            "webspace_id": self._diag_room_id(),
            "client_total": len(getattr(self, "clients", []) or []),
            "send_stream": send_stats,
            "receive_stream": recv_stats,
            "pending_send_tasks": int(self._diag_pending_send_tasks),
            "pending_store_tasks": int(self._diag_pending_store_tasks),
            "update_total": int(self._diag_update_total),
            "update_bytes_total": int(self._diag_update_bytes_total),
            "empty_update_skip_total": int(self._diag_empty_update_skip_total),
            "empty_update_skip_bytes": int(self._diag_empty_update_skip_bytes),
            "backend_persist_skip_total": int(self._diag_backend_persist_skip_total),
            "backend_persist_skip_bytes": int(self._diag_backend_persist_skip_bytes),
            "destructive_update_block_total": int(self._diag_destructive_update_block_total),
            "destructive_update_block_bytes": int(self._diag_destructive_update_block_bytes),
            "inbound_guard_block_total": int(self._diag_inbound_guard_block_total),
            "inbound_guard_block_bytes": int(self._diag_inbound_guard_block_bytes),
            "inbound_guard_last_bytes": int(self._diag_inbound_guard_last_bytes),
            "inbound_guard_last_block_bytes": int(self._diag_inbound_guard_last_block_bytes),
            "inbound_guard_last_at": float(self._diag_inbound_guard_last_at or 0.0),
            "inbound_guard_last_ago_s": _seconds_ago(
                self._diag_inbound_guard_last_at or None,
                time.time(),
            ),
            "inbound_guard_last_reset_reserved": bool(self._diag_inbound_guard_last_reset_reserved),
            "native_preflight_total": int(self._diag_native_preflight_total),
            "native_preflight_block_total": int(self._diag_native_preflight_block_total),
            "native_preflight_block_bytes": int(self._diag_native_preflight_block_bytes),
            "native_preflight_last_reason": str(self._diag_native_preflight_last_reason or ""),
            "authoritative_initial_skip_total": int(self._diag_authoritative_initial_skip_total),
            "authoritative_initial_skip_bytes": int(self._diag_authoritative_initial_skip_bytes),
            "authoritative_initial_last_sync_type": str(
                self._diag_authoritative_initial_last_sync_type or ""
            ),
            "effective_repair_total": int(self._diag_effective_repair_total),
            "effective_repair_bytes": int(self._diag_effective_repair_bytes),
            "effective_repair_replay_pending": len(self._effective_repair_replay_entries()),
            "effective_initial_replay_total": int(self._diag_effective_initial_replay_total),
            "effective_initial_replay_bytes": int(self._diag_effective_initial_replay_bytes),
            "effective_initial_replay_skip_total": int(self._diag_effective_initial_replay_skip_total),
            "effective_initial_replay_dedupe_total": int(
                self._diag_effective_initial_replay_dedupe_total
            ),
            "effective_initial_replay_last_reason": str(self._diag_effective_initial_replay_last_reason or ""),
            "peak_buffer_used": int(self._diag_peak_buffer_used),
            "peak_pending_send_tasks": int(self._diag_peak_pending_send_tasks),
            "peak_pending_store_tasks": int(self._diag_peak_pending_store_tasks),
            "pressure_active": bool(self._diag_pressure_active),
            "pressure_reason": str(self._diag_pressure_reason or ""),
            "pressure_age_s": pressure_age_s,
            "pressure_activation_total": int(self._diag_pressure_activation_total),
            "pressure_clear_total": int(self._diag_pressure_clear_total),
            "live_room_refresh_recent": _live_refresh_recent_snapshot(self._diag_room_id(), limit=5),
            "ystore": self._diag_ystore_snapshot() if include_ystore else {},
        }

    def _diag_update_pressure_state(
        self,
        *,
        reason: str,
        active: bool,
        snapshot: dict[str, Any],
        buffer_used: int,
        waiting_send: int,
        waiting_receive: int,
        pending_send: int,
        pending_store: int,
        update_bytes: int,
        message_bytes: int,
    ) -> None:
        now_mono = time.monotonic()
        previous_active = bool(self._diag_pressure_active)
        previous_reason = str(self._diag_pressure_reason or "")
        transition = False
        if active:
            if not previous_active:
                self._diag_pressure_activation_total += 1
                self._diag_pressure_since_mono = now_mono
                transition = True
            elif previous_reason != reason:
                transition = True
            self._diag_pressure_active = True
            self._diag_pressure_reason = str(reason or "").strip() or "pressure"
        else:
            if previous_active:
                self._diag_pressure_clear_total += 1
                transition = True
            self._diag_pressure_active = False
            self._diag_pressure_reason = ""
            self._diag_pressure_since_mono = 0.0
        age_s = (
            round(max(0.0, now_mono - float(self._diag_pressure_since_mono or 0.0)), 3)
            if self._diag_pressure_active and float(self._diag_pressure_since_mono or 0.0) > 0.0
            else 0.0
        )
        _YROOM_PRESSURE_STATE[self._diag_room_id()] = {
            "webspace_id": self._diag_room_id(),
            "active": bool(self._diag_pressure_active),
            "reason": str(self._diag_pressure_reason or ""),
            "since_mono": float(self._diag_pressure_since_mono or 0.0),
            "age_s": age_s,
            "pending_send_tasks": int(pending_send),
            "pending_store_tasks": int(pending_store),
            "buffer_used": int(buffer_used),
            "waiting_send": int(waiting_send),
            "waiting_receive": int(waiting_receive),
            "update_bytes": int(update_bytes or 0),
            "message_bytes": int(message_bytes or 0),
            "peak_buffer_used": int(self._diag_peak_buffer_used),
            "peak_pending_send_tasks": int(self._diag_peak_pending_send_tasks),
            "peak_pending_store_tasks": int(self._diag_peak_pending_store_tasks),
            "pressure_activation_total": int(self._diag_pressure_activation_total),
            "pressure_clear_total": int(self._diag_pressure_clear_total),
            "update_total": int(snapshot.get("update_total") or 0),
            "update_bytes_total": int(snapshot.get("update_bytes_total") or 0),
        }
        if transition:
            self.log.warning(
                "yroom pressure state webspace=%s active=%s reason=%s age_s=%s "
                "send_buffer=%s waiting_send=%s waiting_receive=%s pending_send=%s pending_store=%s "
                "activations=%s clears=%s",
                self._diag_room_id(),
                bool(self._diag_pressure_active),
                str(self._diag_pressure_reason or "healthy"),
                age_s,
                int(buffer_used),
                int(waiting_send),
                int(waiting_receive),
                int(pending_send),
                int(pending_store),
                int(self._diag_pressure_activation_total),
                int(self._diag_pressure_clear_total),
            )

    def _diag_log_pressure(
        self,
        reason: str,
        *,
        force: bool = False,
        update_bytes: int | None = None,
        message_bytes: int | None = None,
    ) -> None:
        if not _YROOM_DIAG_ENABLED:
            return
        snapshot = self._diag_snapshot()
        send_stream = snapshot.get("send_stream") if isinstance(snapshot.get("send_stream"), dict) else {}
        receive_stream = snapshot.get("receive_stream") if isinstance(snapshot.get("receive_stream"), dict) else {}
        ystore = snapshot.get("ystore") if isinstance(snapshot.get("ystore"), dict) else {}
        buffer_used = int(send_stream.get("current_buffer_used") or 0)
        waiting_send = int(send_stream.get("tasks_waiting_send") or 0)
        waiting_receive = int(send_stream.get("tasks_waiting_receive") or 0)
        pending_send = int(snapshot.get("pending_send_tasks") or 0)
        pending_store = int(snapshot.get("pending_store_tasks") or 0)
        pressure = (
            buffer_used >= _YROOM_DIAG_BUFFER_WARN
            or waiting_send >= _YROOM_DIAG_PENDING_WARN
            or pending_send >= _YROOM_DIAG_PENDING_WARN
            or pending_store >= _YROOM_DIAG_PENDING_WARN
            or int(update_bytes or 0) >= _YROOM_DIAG_UPDATE_WARN_BYTES
            or int(message_bytes or 0) >= _YROOM_DIAG_UPDATE_WARN_BYTES
        )
        peak = False
        if buffer_used > self._diag_peak_buffer_used:
            self._diag_peak_buffer_used = buffer_used
            peak = True
        if pending_send > self._diag_peak_pending_send_tasks:
            self._diag_peak_pending_send_tasks = pending_send
            peak = True
        if pending_store > self._diag_peak_pending_store_tasks:
            self._diag_peak_pending_store_tasks = pending_store
            peak = True
        now_mono = time.monotonic()
        self._diag_update_pressure_state(
            reason=reason,
            active=pressure,
            snapshot=snapshot,
            buffer_used=buffer_used,
            waiting_send=waiting_send,
            waiting_receive=waiting_receive,
            pending_send=pending_send,
            pending_store=pending_store,
            update_bytes=int(update_bytes or 0),
            message_bytes=int(message_bytes or 0),
        )
        if not force and not pressure and not peak:
            return
        if not force and not peak and now_mono - self._diag_last_log_mono < _YROOM_DIAG_LOG_INTERVAL_SEC:
            return
        self._diag_last_log_mono = now_mono
        if _YROOM_DIAG_INCLUDE_YSTORE:
            ystore = self._diag_ystore_snapshot()
        self.log.warning(
            "yroom pressure webspace=%s reason=%s clients=%s update_bytes=%s message_bytes=%s "
            "send_buffer=%s/%s waiting_send=%s waiting_receive=%s pending_send=%s pending_store=%s "
            "update_total=%s update_bytes_total=%s ystore_entries=%s ystore_bytes=%s replay_bytes=%s",
            snapshot.get("webspace_id"),
            str(reason or "").strip() or "unknown",
            int(snapshot.get("client_total") or 0),
            int(update_bytes or 0),
            int(message_bytes or 0),
            buffer_used,
            int(send_stream.get("max_buffer_size") or 0),
            waiting_send,
            waiting_receive,
            pending_send,
            pending_store,
            int(snapshot.get("update_total") or 0),
            int(snapshot.get("update_bytes_total") or 0),
            int(ystore.get("update_log_entries") or 0),
            int(ystore.get("update_log_bytes") or 0),
            int(ystore.get("replay_window_bytes") or 0),
        )

    def _prune_effective_repair_replay_updates(self, now: float | None = None) -> None:
        now_ts = time.time() if now is None else float(now)
        while self._diag_effective_repair_replay_updates:
            entry = self._diag_effective_repair_replay_updates[0]
            if float(entry.get("expires_at") or 0.0) > now_ts:
                break
            self._diag_effective_repair_replay_updates.popleft()
        while len(self._diag_effective_repair_replay_updates) > _YROOM_EFFECTIVE_REPAIR_REPLAY_MAX_UPDATES:
            self._diag_effective_repair_replay_updates.popleft()

    def _queue_effective_repair_replay(self, update: bytes, *, reason: str) -> None:
        if not update or _YROOM_EFFECTIVE_REPAIR_REPLAY_TTL_SEC <= 0.0:
            return
        now_ts = time.time()
        self._prune_effective_repair_replay_updates(now_ts)
        self._diag_effective_repair_replay_updates.append(
            {
                "update": bytes(update),
                "reason": str(reason or "effective_repair"),
                "queued_at": now_ts,
                "expires_at": now_ts + float(_YROOM_EFFECTIVE_REPAIR_REPLAY_TTL_SEC),
                "sent_total": 0,
            }
        )
        self._prune_effective_repair_replay_updates(now_ts)
        self.log.warning(
            "queued Y effective repair replay webspace=%s reason=%s repair_bytes=%s pending=%s clients=%s",
            self._diag_room_id(),
            str(reason or "effective_repair"),
            len(update),
            len(self._diag_effective_repair_replay_updates),
            len(getattr(self, "clients", []) or []),
        )

    def _effective_repair_replay_entries(self) -> list[dict[str, Any]]:
        self._prune_effective_repair_replay_updates()
        return list(self._diag_effective_repair_replay_updates)

    async def _tracked_client_send(
        self,
        client: Any,
        message: bytes,
        update_bytes: int,
        live_refresh_key: tuple[str, int, str] | None = None,
    ) -> None:
        self._diag_pending_send_tasks += 1
        started = time.perf_counter()
        try:
            self._diag_log_pressure(
                "client.send.scheduled",
                update_bytes=update_bytes,
                message_bytes=len(message),
            )
            await client.send(message)
        except Exception as exc:
            # A failed transport must stop participating in the room.  Keeping
            # it in ``clients`` makes every later update retry the dead
            # recipient and turns reconnect churn into broadcast
            # amplification.  Isolate the failure here instead of letting it
            # cancel the room task group.
            self.clients = [current for current in list(getattr(self, "clients", []) or []) if current is not client]
            close = getattr(client, "close", None)
            if callable(close):
                try:
                    maybe_awaitable = close()
                    if inspect.isawaitable(maybe_awaitable):
                        await maybe_awaitable
                except Exception:
                    self.log.debug(
                        "failed closing rejected YRoom client webspace=%s",
                        self._diag_room_id(),
                        exc_info=True,
                    )
            self.log.warning(
                "pruned failed YRoom client webspace=%s update_bytes=%s error=%s remaining_clients=%s",
                self._diag_room_id(),
                int(update_bytes or 0),
                type(exc).__name__,
                len(getattr(self, "clients", []) or []),
            )
        finally:
            if live_refresh_key is not None:
                _record_live_refresh_client_send(live_refresh_key, elapsed_ms=_elapsed_ms_since(started))
            self._diag_pending_send_tasks = max(0, int(self._diag_pending_send_tasks) - 1)

    async def _tracked_ystore_write(self, update: bytes) -> None:
        ystore = getattr(self, "ystore", None)
        if ystore is None:
            return
        if _is_empty_y_update(update):
            self._diag_empty_update_skip_total += 1
            self._diag_empty_update_skip_bytes += len(update or b"")
            return
        write_meta = current_ystore_write_metadata()
        meta_source = str(write_meta.get("source") or "").strip()
        meta_channel = str(write_meta.get("channel") or "").strip()
        if (
            meta_source.startswith("yjs.gateway_ws.semantic_rebuild:builder_revision_apply")
            and meta_channel == "core.yjs.gateway.repair"
        ):
            update_len = len(update or b"")
            self._diag_backend_persist_skip_total += 1
            self._diag_backend_persist_skip_bytes += update_len
            self.log.debug(
                "Skipping builder repair YStore write from captured metadata webspace=%s bytes=%s source=%s",
                self._diag_room_id(),
                update_len,
                meta_source,
            )
            return
        suppress_until = 0.0
        try:
            suppress_until = float(getattr(self, "_suppress_backend_ystore_persist_until", 0.0) or 0.0)
        except Exception:
            suppress_until = 0.0
        if int(getattr(self, "_suppress_backend_ystore_persist", 0) or 0) > 0 or suppress_until > time.monotonic():
            update_len = len(update or b"")
            self._diag_backend_persist_skip_total += 1
            self._diag_backend_persist_skip_bytes += update_len
            self.log.debug(
                "Skipping backend YStore write while repair persistence is suppressed webspace=%s bytes=%s",
                self._diag_room_id(),
                update_len,
            )
            return
        self._diag_pending_store_tasks += 1
        try:
            persisted = consume_backend_room_update(self._diag_room_id(), update)
            if persisted is not None:
                update_len = len(update or b"")
                if bool(persisted.get("already_persisted", True)):
                    self._diag_backend_persist_skip_total += 1
                    self._diag_backend_persist_skip_bytes += update_len
                    self.log.debug(
                        "Skipping duplicate backend-origin YStore write for webspace=%s bytes=%s source=%s owner=%s",
                        self._diag_room_id(),
                        update_len,
                        persisted.get("source"),
                        persisted.get("owner"),
                    )
                    return
                root_names = persisted.get("root_names")
                if not isinstance(root_names, (list, tuple)):
                    root_names = []
                source = str(persisted.get("source") or "yjs.gateway_ws.backend_live_room")
                owner = str(persisted.get("owner") or "").strip() or "gateway_ws"
                channel = str(persisted.get("channel") or "core.yjs.gateway.live_room.persist")
                self._diag_log_pressure("ystore.write.backend_live_room", update_bytes=update_len)
                if update_len >= _YROOM_DIAG_UPDATE_WARN_BYTES:
                    self.log.warning(
                        "persisting backend-origin large YStore write webspace=%s bytes=%s source=%s owner=%s channel=%s already_persisted=%s root_names=%s",
                        self._diag_room_id(),
                        update_len,
                        source,
                        owner,
                        channel,
                        bool(persisted.get("already_persisted", False)),
                        list(root_names or [])[:12],
                    )
                async with ystore_write_metadata(
                    root_names=[
                        str(item or "").strip()
                        for item in list(root_names or ())
                        if str(item or "").strip()
                    ],
                    source=source,
                    owner=owner,
                    channel=channel,
                    governed=bool(persisted.get("governed", False)),
                ):
                    await ystore.write(update)
                _request_gateway_live_persist_compaction(
                    ystore,
                    self._diag_room_id(),
                    update_bytes=update_len,
                    source=source,
                    channel=channel,
                )
                return
            self._diag_log_pressure("ystore.write.scheduled", update_bytes=len(update))
            update_len = len(update or b"")
            check_effective = bool(
                update_len >= _YROOM_EFFECTIVE_GUARD_FULL_CHECK_BYTES
                or not (
                    isinstance(getattr(self, "_diag_effective_branch_snapshot", None), dict)
                    and getattr(self, "_diag_effective_branch_snapshot", {}).get("ready")
                )
            )
            if check_effective:
                try:
                    current_effective_ready = _room_effective_branches_ready(self.ydoc)
                except Exception:
                    current_effective_ready = False
                if not current_effective_ready:
                    try:
                        snapshot = _room_effective_branch_snapshot(self.ydoc)
                    except Exception as exc:
                        snapshot = {"ready": False, "error": f"{type(exc).__name__}: {exc}"}
                    self._diag_backend_persist_skip_total += 1
                    self._diag_backend_persist_skip_bytes += update_len
                    self.log.warning(
                        "skipping browser-origin YStore write that would persist ineffective room webspace=%s bytes=%s snapshot=%s",
                        self._diag_room_id(),
                        update_len,
                        json.dumps(snapshot, ensure_ascii=True, sort_keys=True)[:1000],
                    )
                    await self._repair_effective_branches_after_client_update(
                        update_bytes=update_len,
                        reason="client_update_persist_contract_guard",
                    )
                    return
            if update_len >= _YROOM_DIAG_UPDATE_WARN_BYTES:
                self.log.warning(
                    "persisting browser-origin large YStore write webspace=%s bytes=%s effective_ready=%s snapshot=%s",
                    self._diag_room_id(),
                    update_len,
                    bool(
                        isinstance(getattr(self, "_diag_effective_branch_snapshot", None), dict)
                        and getattr(self, "_diag_effective_branch_snapshot", {}).get("ready")
                    ),
                    json.dumps(
                        getattr(self, "_diag_effective_branch_snapshot", None)
                        if isinstance(getattr(self, "_diag_effective_branch_snapshot", None), dict)
                        else {},
                        ensure_ascii=True,
                        sort_keys=True,
                    )[:1000],
                )
            async with ystore_write_metadata(
                source="yjs.gateway_ws",
                owner="gateway_ws",
                channel="core.yjs.gateway.live_room.persist",
            ):
                await ystore.write(update)
            _request_gateway_live_persist_compaction(
                ystore,
                self._diag_room_id(),
                update_bytes=len(update or b""),
                source="yjs.gateway_ws",
                channel="core.yjs.gateway.live_room.persist",
            )
        finally:
            self._diag_pending_store_tasks = max(0, int(self._diag_pending_store_tasks) - 1)

    async def _repair_effective_branches_after_destructive_update(
        self,
        *,
        destructive_update_bytes: int,
        snapshot: dict[str, Any],
    ) -> bytes:
        self._diag_destructive_update_block_total += 1
        self._diag_destructive_update_block_bytes += int(destructive_update_bytes or 0)
        self.log.warning(
            "blocked destructive YRoom update webspace=%s bytes=%s blocks=%s snapshot=%s",
            self._diag_room_id(),
            int(destructive_update_bytes or 0),
            int(self._diag_destructive_update_block_total),
            json.dumps(snapshot, ensure_ascii=True, sort_keys=True)[:1000],
        )
        repair_update = await _repair_room_effective_branches(
            self._diag_room_id(),
            getattr(self, "ystore", None),
            self,
            reason="destructive_client_update",
        )
        if repair_update:
            self._diag_effective_repair_total += 1
            self._diag_effective_repair_bytes += len(repair_update)
            try:
                self._diag_effective_branch_snapshot = _room_effective_branch_snapshot(self.ydoc)
            except Exception as exc:
                self._diag_effective_branch_snapshot = {
                    "ready": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
        return repair_update

    async def _repair_effective_branches_after_client_update(
        self,
        *,
        update_bytes: int,
        reason: str,
    ) -> bytes:
        repair_update = await _repair_room_effective_branches(
            self._diag_room_id(),
            getattr(self, "ystore", None),
            self,
            reason=reason,
        )
        if repair_update:
            self._diag_effective_repair_total += 1
            self._diag_effective_repair_bytes += len(repair_update)
            try:
                self._diag_effective_branch_snapshot = _room_effective_branch_snapshot(self.ydoc)
            except Exception as exc:
                self._diag_effective_branch_snapshot = {
                    "ready": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
            self.log.warning(
                "repaired YRoom effective branches after client update webspace=%s reason=%s update_bytes=%s repair_bytes=%s repairs=%s",
                self._diag_room_id(),
                reason,
                int(update_bytes or 0),
                len(repair_update),
                int(self._diag_effective_repair_total),
            )
        return repair_update

    async def _repair_authoritative_selector_after_update(
        self,
        *,
        update_bytes: int,
    ) -> bytes | None:
        authoritative_scenario = _authoritative_current_scenario(self._diag_room_id())
        if not authoritative_scenario:
            return None
        current_scenario = _room_current_scenario(self.ydoc)
        if current_scenario == authoritative_scenario:
            return None
        self.log.warning(
            "blocked stale YRoom selector update webspace=%s bytes=%s current=%s authoritative=%s",
            self._diag_room_id(),
            int(update_bytes or 0),
            current_scenario,
            authoritative_scenario,
        )
        return await self._repair_effective_branches_after_client_update(
            update_bytes=int(update_bytes or 0),
            reason="authoritative_selector_drift",
        )

    async def _send_initial_effective_state_replay(self, websocket: YWebsocket) -> bool:
        if not _YROOM_EFFECTIVE_INITIAL_REPLAY:
            self._diag_effective_initial_replay_skip_total += 1
            self._diag_effective_initial_replay_last_reason = "disabled"
            return False
        try:
            if not _room_effective_top_level_ready(self.ydoc):
                self._diag_effective_initial_replay_skip_total += 1
                self._diag_effective_initial_replay_last_reason = "room_not_effective_ready"
                return False
        except Exception as exc:
            self._diag_effective_initial_replay_skip_total += 1
            self._diag_effective_initial_replay_last_reason = f"ready_check_failed:{type(exc).__name__}"
            return False
        try:
            import y_py as Y  # pylint: disable=import-outside-toplevel

            encode_started = time.perf_counter()
            update = Y.encode_state_as_update(self.ydoc)  # type: ignore[arg-type]
            encode_ms = _elapsed_ms_since(encode_started)
        except Exception as exc:
            self._diag_effective_initial_replay_skip_total += 1
            self._diag_effective_initial_replay_last_reason = f"encode_failed:{type(exc).__name__}"
            self.log.warning(
                "failed to encode initial Y effective state replay webspace=%s reason=%s",
                self._diag_room_id(),
                exc,
                exc_info=True,
            )
            return False
        update_len = len(update or b"")
        if update_len <= 0:
            self._diag_effective_initial_replay_skip_total += 1
            self._diag_effective_initial_replay_last_reason = "empty_update"
            return False
        if update_len > _YROOM_EFFECTIVE_INITIAL_REPLAY_MAX_BYTES:
            self._diag_effective_initial_replay_skip_total += 1
            self._diag_effective_initial_replay_last_reason = "update_too_large"
            self.log.warning(
                "skipping oversized initial Y effective state replay webspace=%s bytes=%s max_bytes=%s",
                self._diag_room_id(),
                update_len,
                _YROOM_EFFECTIVE_INITIAL_REPLAY_MAX_BYTES,
            )
            return False
        send_started = time.perf_counter()
        await websocket.send(create_update_message(update))
        send_ms = _elapsed_ms_since(send_started)
        self._diag_effective_initial_replay_total += 1
        self._diag_effective_initial_replay_bytes += update_len
        self._diag_effective_initial_replay_last_reason = "sent"
        self.log.warning(
            "sent initial Y effective state replay webspace=%s endpoint=%s bytes=%s encode_ms=%.3f send_ms=%.3f total=%s",
            self._diag_room_id(),
            getattr(websocket, "path", None),
            update_len,
            encode_ms,
            send_ms,
            int(self._diag_effective_initial_replay_total),
        )
        return True

    async def serve(self, websocket: YWebsocket):
        if sync is None or process_sync_message is None or read_sync_message is None:
            raise RuntimeError("ypy_websocket.yutils sync helpers are unavailable")
        async with create_task_group() as tg:
            self.clients.append(websocket)
            await sync(self.ydoc, websocket, self.log)
            # Normal y-websocket/DataChannel providers always emit STEP1 and
            # require the corresponding STEP2 to declare first sync complete.
            # Sending a full effective replay here, before reading STEP1, used
            # to transfer the same document three times during one handshake.
            # Keep the replay only as an exceptional malformed/preflight
            # recovery path below.
            initial_native_update_pending = True
            try:
                async for message in websocket:
                    skip = False
                    if self.on_message:
                        maybe_skip = self.on_message(message)
                        skip = await maybe_skip if inspect.isawaitable(maybe_skip) else maybe_skip
                    if skip:
                        continue
                    message_type = message[0]
                    if message_type == YMessageType.SYNC:
                        sync_type, inbound_payload = _extract_inbound_y_sync_payload(message)
                        if sync_type is not None and inbound_payload is None:
                            self._diag_native_preflight_block_total += 1
                            self._diag_native_preflight_block_bytes += len(message)
                            self._diag_native_preflight_last_reason = "malformed_sync_frame"
                            _ylog.error(
                                "blocked malformed inbound Y sync payload before native call "
                                "webspace=%s bytes=%s digest=%s",
                                self._diag_room_id(),
                                len(message),
                                hashlib.sha256(message).hexdigest(),
                            )
                            await self._send_initial_effective_state_replay(websocket)
                            continue
                        should_preflight = bool(
                            sync_type is not None
                            and inbound_payload is not None
                            and _YROOM_NATIVE_PREFLIGHT_ENABLED
                            and (
                                sync_type == int(YSyncMessageType.SYNC_STEP1)
                                or initial_native_update_pending
                                or len(inbound_payload) >= _YROOM_NATIVE_PREFLIGHT_THRESHOLD_BYTES
                            )
                        )
                        if should_preflight:
                            self._diag_native_preflight_total += 1
                            try:
                                import y_py as Y  # pylint: disable=import-outside-toplevel

                                current = Y.encode_state_as_update(self.ydoc)
                                accepted, reason = await asyncio.to_thread(
                                    _preflight_inbound_y_sync_payload,
                                    current,
                                    inbound_payload,
                                    sync_type=int(sync_type),
                                )
                            except Exception as exc:
                                accepted = False
                                reason = f"preflight_error:{type(exc).__name__}"
                            if not accepted:
                                self._diag_native_preflight_block_total += 1
                                self._diag_native_preflight_block_bytes += len(inbound_payload)
                                self._diag_native_preflight_last_reason = str(reason or "blocked")
                                _ylog.error(
                                    "blocked inbound Y sync payload after native subprocess preflight "
                                    "webspace=%s bytes=%s digest=%s reason=%s",
                                    self._diag_room_id(),
                                    len(inbound_payload),
                                    hashlib.sha256(inbound_payload).hexdigest(),
                                    reason,
                                )
                                await self._send_initial_effective_state_replay(websocket)
                                continue
                        # A browser SYNC_STEP1 contains only its state vector.  It
                        # cannot mutate the server document and must reach
                        # process_sync_message so the server returns SYNC_STEP2.
                        # y-websocket marks the provider synced only after that
                        # response; dropping STEP1 leaves the provider forever in
                        # `connecting` and eventually creates a reconnect storm.
                        # Keep the server-authoritative guard on the initial
                        # client state/update frames, which are the mutating part
                        # of the handshake.
                        authoritative_initial = bool(
                            sync_type is not None
                            and inbound_payload is not None
                            and _YROOM_SERVER_AUTHORITATIVE_INITIAL_SYNC
                            and initial_native_update_pending
                            and sync_type
                            in {
                                int(YSyncMessageType.SYNC_STEP2),
                                int(YSyncMessageType.SYNC_UPDATE),
                            }
                        )
                        if authoritative_initial:
                            sync_name = (
                                YSyncMessageType(int(sync_type)).name
                                if int(sync_type) in {0, 1, 2}
                                else str(sync_type)
                            )
                            self._diag_authoritative_initial_skip_total += 1
                            self._diag_authoritative_initial_skip_bytes += len(inbound_payload)
                            self._diag_authoritative_initial_last_sync_type = sync_name
                            if sync_type in {
                                int(YSyncMessageType.SYNC_STEP2),
                                int(YSyncMessageType.SYNC_UPDATE),
                            }:
                                initial_native_update_pending = False
                                # STEP1 is handled by process_sync_message and
                                # returns the authoritative STEP2.  Replaying a
                                # full update after discarding the browser's
                                # initial state duplicated that same response.
                                # A prior exceptional replay is already enough;
                                # either way no additional payload is needed.
                                self._diag_effective_initial_replay_dedupe_total += 1
                            _ylog.warning(
                                "ignored initial browser Y sync payload in server-authoritative mode "
                                "webspace=%s sync_type=%s bytes=%s digest=%s",
                                self._diag_room_id(),
                                sync_name,
                                len(inbound_payload),
                                hashlib.sha256(inbound_payload).hexdigest(),
                            )
                            continue
                        if sync_type in {
                            int(YSyncMessageType.SYNC_STEP2),
                            int(YSyncMessageType.SYNC_UPDATE),
                        }:
                            initial_native_update_pending = False
                        tg.start_soon(
                            process_sync_message,
                            message[1:],
                            self.ydoc,
                            websocket,
                            self.log,
                        )
                    elif message_type == YMessageType.AWARENESS:
                        self.log.debug(
                            "Received %s message from endpoint: %s",
                            YMessageType.AWARENESS.name,
                            websocket.path,
                        )
                        for client in self.clients:
                            self.log.debug(
                                "Sending Y awareness from client with endpoint %s to client with endpoint: %s",
                                websocket.path,
                                client.path,
                            )
                            tg.start_soon(client.send, message)
            except Exception as exc:
                self.log.debug("Error serving endpoint: %s", websocket.path, exc_info=exc)

            self.clients = [client for client in self.clients if client != websocket]

    async def _broadcast_updates(self):
        if self.ystore is not None and not self.ystore.started.is_set():
            self._task_group.start_soon(self.ystore.start)

        async with self._update_receive_stream:
            async for update in self._update_receive_stream:
                if self._task_group.cancel_scope.cancel_called:
                    return
                update_len = len(update or b"")
                self._diag_update_total += 1
                self._diag_update_bytes_total += update_len
                if _is_empty_y_update(update):
                    self._diag_empty_update_skip_total += 1
                    self._diag_empty_update_skip_bytes += update_len
                    continue
                self._diag_log_pressure("broadcast.update.received", update_bytes=update_len)
                if update_len >= _YROOM_INBOUND_GUARD_BLOCK_BYTES:
                    webspace_id = self._diag_room_id()
                    reset_reserved = _reserve_inbound_guard_reset(webspace_id, time.monotonic())
                    self._diag_inbound_guard_block_total += 1
                    self._diag_inbound_guard_block_bytes += update_len
                    self._diag_inbound_guard_last_bytes = update_len
                    self._diag_inbound_guard_last_block_bytes = int(_YROOM_INBOUND_GUARD_BLOCK_BYTES)
                    self._diag_inbound_guard_last_at = time.time()
                    self._diag_inbound_guard_last_reset_reserved = bool(reset_reserved)
                    self.log.warning(
                        "blocked oversized inbound YWS update webspace=%s update_bytes=%s block_bytes=%s reset_reserved=%s reason=inbound_yws_update_payload_blocked",
                        webspace_id,
                        update_len,
                        _YROOM_INBOUND_GUARD_BLOCK_BYTES,
                        reset_reserved,
                    )
                    if reset_reserved:
                        asyncio.create_task(
                            reset_live_webspace_room(
                                webspace_id,
                                close_reason="inbound_yws_update_payload_blocked",
                                persist_ystore_snapshot=False,
                                reset_route_runtime=True,
                            )
                        )
                    continue
                authoritative_repair = await self._repair_authoritative_selector_after_update(
                    update_bytes=update_len,
                )
                if authoritative_repair is not None:
                    repair_update = authoritative_repair
                    if repair_update:
                        repair_message = create_update_message(repair_update)
                        clients = list(getattr(self, "clients", []) or [])
                        if not clients:
                            self._queue_effective_repair_replay(
                                repair_update,
                                reason="authoritative_selector_drift",
                            )
                        for client in clients:
                            self._task_group.start_soon(
                                self._tracked_client_send,
                                client,
                                repair_message,
                                len(repair_update),
                            )
                    # The stale update is already integrated in the room and
                    # has been reconciled above.  Never broadcast or persist it.
                    continue
                previous_effective_ready = bool(
                    isinstance(self._diag_effective_branch_snapshot, dict)
                    and self._diag_effective_branch_snapshot.get("ready")
                )
                effective_ready = previous_effective_ready
                effective_snapshot: dict[str, Any] = {"ready": effective_ready}
                try:
                    now_mono = time.monotonic()
                    check_age = now_mono - float(self._diag_effective_last_full_check_mono or 0.0)
                    min_check_elapsed = (
                        _YROOM_EFFECTIVE_GUARD_MIN_CHECK_INTERVAL_SEC <= 0.0
                        or check_age >= _YROOM_EFFECTIVE_GUARD_MIN_CHECK_INTERVAL_SEC
                    )
                    full_check_due = min_check_elapsed and (
                        update_len >= _YROOM_EFFECTIVE_GUARD_FULL_CHECK_BYTES
                        or (
                            _YROOM_EFFECTIVE_GUARD_FULL_CHECK_INTERVAL_SEC > 0.0
                            and check_age >= _YROOM_EFFECTIVE_GUARD_FULL_CHECK_INTERVAL_SEC
                        )
                    )
                    force_initial_check = bool(
                        self._diag_update_total <= 1
                        or (
                            _YROOM_EFFECTIVE_GUARD_INITIAL_FULL_CHECK_UPDATES > 0
                            and self._diag_update_total <= _YROOM_EFFECTIVE_GUARD_INITIAL_FULL_CHECK_UPDATES
                        )
                    )
                    checked_effective = False
                    if previous_effective_ready and not full_check_due:
                        # Keep the steady-state broadcast path free of live
                        # YDoc inspection. Even top-level YMap reads can block
                        # long enough to starve the websocket fanout under
                        # load; periodic/initial checks still validate the
                        # effective branch contract.
                        if force_initial_check:
                            self._diag_effective_last_full_check_mono = now_mono
                            checked_effective = True
                            effective_ready = _room_effective_top_level_ready(self.ydoc)
                            effective_snapshot = {
                                "ready": effective_ready,
                                "mode": "top_level_initial",
                            }
                        else:
                            effective_ready = True
                            effective_snapshot = {"ready": True, "mode": "cached"}
                    elif full_check_due or force_initial_check:
                        self._diag_effective_last_full_check_mono = now_mono
                        checked_effective = True
                        if previous_effective_ready and _YROOM_EFFECTIVE_GUARD_STRICT_FULL_CHECKS:
                            effective_snapshot = _room_effective_branch_snapshot(self.ydoc)
                            effective_ready = bool(effective_snapshot.get("ready"))
                        else:
                            effective_ready = _room_effective_top_level_ready(self.ydoc)
                            effective_snapshot = {
                                "ready": effective_ready,
                                "mode": "top_level_periodic" if previous_effective_ready else "top_level",
                            }
                    else:
                        effective_snapshot = {"ready": effective_ready, "mode": "cached_missing"}
                    if checked_effective and not effective_ready:
                        self._diag_effective_last_full_check_mono = now_mono
                        effective_snapshot = {"ready": False, "mode": "top_level_missing"}
                    elif not previous_effective_ready:
                        effective_snapshot = {"ready": effective_ready, "mode": "top_level"}
                    self._diag_effective_branch_snapshot = effective_snapshot
                except Exception as exc:
                    effective_snapshot = {
                        "ready": previous_effective_ready,
                        "error": f"{type(exc).__name__}: {exc}",
                    }
                    effective_ready = previous_effective_ready
                initial_repair_due = bool(
                    _YROOM_EFFECTIVE_GUARD_REPAIR_INITIAL_UPDATES > 0
                    and self._diag_update_total <= _YROOM_EFFECTIVE_GUARD_REPAIR_INITIAL_UPDATES
                )
                repair_cooldown_due = bool(
                    _YROOM_EFFECTIVE_GUARD_REPAIR_COOLDOWN_SEC <= 0.0
                    or time.monotonic() - float(self._diag_effective_last_repair_mono or 0.0)
                    >= _YROOM_EFFECTIVE_GUARD_REPAIR_COOLDOWN_SEC
                )
                if effective_ready and initial_repair_due and repair_cooldown_due:
                    self._diag_effective_last_repair_mono = time.monotonic()
                    repair_update = await self._repair_effective_branches_after_client_update(
                        update_bytes=update_len,
                        reason="initial_client_update_reconcile",
                    )
                    if repair_update:
                        repair_message = create_update_message(repair_update)
                        clients = list(getattr(self, "clients", []) or [])
                        if not clients:
                            self._queue_effective_repair_replay(
                                repair_update,
                                reason="initial_client_update_reconcile",
                            )
                        for client in clients:
                            self.log.debug("Sending Y repair update to client with endpoint: %s", client.path)
                            self._task_group.start_soon(
                                self._tracked_client_send,
                                client,
                                repair_message,
                                len(repair_update),
                            )
                        continue
                if previous_effective_ready and not effective_ready:
                    repair_update = await self._repair_effective_branches_after_destructive_update(
                        destructive_update_bytes=update_len,
                        snapshot=effective_snapshot,
                    )
                    if repair_update:
                        repair_message = create_update_message(repair_update)
                        clients = list(getattr(self, "clients", []) or [])
                        if not clients:
                            self._queue_effective_repair_replay(
                                repair_update,
                                reason="destructive_client_update",
                            )
                        for client in clients:
                            self.log.debug("Sending Y repair update to client with endpoint: %s", client.path)
                            self._task_group.start_soon(
                                self._tracked_client_send,
                                client,
                                repair_message,
                                len(repair_update),
                            )
                    continue
                clients = list(getattr(self, "clients", []) or [])
                live_refresh_key = _record_live_refresh_observer_broadcast(
                    self._diag_room_id(),
                    update,
                    client_count=len(clients),
                )
                if live_refresh_key is None:
                    pending_key = getattr(self, "_diag_live_refresh_pending_key", None)
                    if isinstance(pending_key, tuple) and len(pending_key) == 3:
                        live_refresh_key = _record_live_refresh_observer_broadcast_for_key(
                            pending_key,  # type: ignore[arg-type]
                            update=update,
                            client_count=len(clients),
                            exact_update_match=False,
                        )
                if live_refresh_key is not None and getattr(self, "_diag_live_refresh_pending_key", None) == live_refresh_key:
                    try:
                        delattr(self, "_diag_live_refresh_pending_key")
                    except Exception:
                        pass
                message = b""
                if clients:
                    message_started = time.perf_counter()
                    message = create_update_message(update)
                    _record_live_refresh_message_create(live_refresh_key, _elapsed_ms_since(message_started))
                for client in clients:
                    self.log.debug("Sending Y update to client with endpoint: %s", client.path)
                    self._task_group.start_soon(
                        self._tracked_client_send,
                        client,
                        message,
                        update_len,
                        live_refresh_key,
                    )
                if self.ystore:
                    self.log.debug("Writing Y update to YStore")
                    write_meta = current_ystore_write_metadata()
                    meta_source = str(write_meta.get("source") or "").strip()
                    meta_channel = str(write_meta.get("channel") or "").strip()
                    if (
                        meta_source.startswith("yjs.gateway_ws.semantic_rebuild:builder_revision_apply")
                        and meta_channel == "core.yjs.gateway.repair"
                    ):
                        self._diag_backend_persist_skip_total += 1
                        self._diag_backend_persist_skip_bytes += update_len
                        self.log.debug(
                            "Skipping builder repair YStore write scheduling webspace=%s bytes=%s source=%s",
                            self._diag_room_id(),
                            update_len,
                            meta_source,
                        )
                    else:
                        self._task_group.start_soon(self._tracked_ystore_write, update)


def _command_payload_fingerprint(kind: str, payload: Any) -> str:
    raw = dict(payload or {}) if isinstance(payload, dict) else {}
    raw.pop("_meta", None)
    try:
        encoded = json.dumps(
            {
                "kind": str(kind or "").strip(),
                "payload": raw,
            },
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")
    except Exception:
        encoded = f"{kind}:{sorted(raw.items())}".encode("utf-8", errors="replace")
    return hashlib.sha1(encoded).hexdigest()[:12]


def _record_command_trace(
    *,
    kind: str,
    cmd_id: str | None,
    payload: dict[str, Any] | None,
    device_id: str | None,
    webspace_id: str | None,
    client_label: str | None,
) -> dict[str, Any]:
    global _COMMAND_TRACE_SEQ

    now = time.time()
    normalized_kind = str(kind or "").strip() or "-"
    effective_payload = dict(payload or {})
    effective_webspace = str(
        effective_payload.get("webspace_id")
        or effective_payload.get("workspace_id")
        or webspace_id
        or "default"
    ).strip() or "default"
    fingerprint = _command_payload_fingerprint(normalized_kind, effective_payload)
    scenario_id = str(effective_payload.get("scenario_id") or "").strip() or None
    recreate_room = bool(effective_payload.get("recreate_room"))
    duplicate_recent = False
    duplicate_delta_ms: float | None = None
    duplicate_count_10s = 0

    with _COMMAND_TRACE_LOCK:
        for previous in reversed(_COMMAND_TRACE_HISTORY):
            if str(previous.get("kind") or "") != normalized_kind:
                continue
            if str(previous.get("webspace_id") or "") != effective_webspace:
                continue
            if str(previous.get("fingerprint") or "") != fingerprint:
                continue
            previous_ts = float(previous.get("ts") or 0.0)
            if previous_ts <= 0.0:
                continue
            delta_s = now - previous_ts
            if delta_s <= 10.0:
                duplicate_count_10s += 1
            if not duplicate_recent and delta_s <= 10.0:
                duplicate_recent = True
                duplicate_delta_ms = round(delta_s * 1000.0, 3)

        _COMMAND_TRACE_SEQ += 1
        record = {
            "seq": int(_COMMAND_TRACE_SEQ),
            "ts": now,
            "kind": normalized_kind,
            "cmd_id": str(cmd_id or "").strip() or None,
            "device_id": str(device_id or "").strip() or None,
            "webspace_id": effective_webspace,
            "client": str(client_label or "").strip() or None,
            "scenario_id": scenario_id,
            "recreate_room": recreate_room,
            "fingerprint": fingerprint,
            "duplicate_recent": duplicate_recent,
            "duplicate_delta_ms": duplicate_delta_ms,
            "duplicate_count_10s": duplicate_count_10s,
        }
        _COMMAND_TRACE_HISTORY.append(record)
        if normalized_kind == "desktop.webspace.reload":
            _COMMAND_TRACE_STATS["reload_total"] = int(_COMMAND_TRACE_STATS.get("reload_total") or 0) + 1
            if duplicate_recent:
                _COMMAND_TRACE_STATS["reload_duplicate_total"] = int(_COMMAND_TRACE_STATS.get("reload_duplicate_total") or 0) + 1
        elif normalized_kind == "desktop.webspace.reset":
            _COMMAND_TRACE_STATS["reset_total"] = int(_COMMAND_TRACE_STATS.get("reset_total") or 0) + 1
            if duplicate_recent:
                _COMMAND_TRACE_STATS["reset_duplicate_total"] = int(_COMMAND_TRACE_STATS.get("reset_duplicate_total") or 0) + 1
    return record


def _command_trace_snapshot(now: float) -> dict[str, Any]:
    with _COMMAND_TRACE_LOCK:
        history = list(_COMMAND_TRACE_HISTORY)
        stats = dict(_COMMAND_TRACE_STATS)
    recent_reload_60s = 0
    recent_reset_60s = 0
    last_reload: dict[str, Any] | None = None
    last_reset: dict[str, Any] | None = None
    recent_items: list[dict[str, Any]] = []
    for record in reversed(history):
        ts = float(record.get("ts") or 0.0)
        age_s = round(max(0.0, now - ts), 3) if ts > 0.0 else None
        entry = {
            "seq": int(record.get("seq") or 0),
            "kind": str(record.get("kind") or ""),
            "cmd_id": record.get("cmd_id"),
            "device_id": record.get("device_id"),
            "webspace_id": record.get("webspace_id"),
            "client": record.get("client"),
            "scenario_id": record.get("scenario_id"),
            "recreate_room": bool(record.get("recreate_room")),
            "fingerprint": record.get("fingerprint"),
            "duplicate_recent": bool(record.get("duplicate_recent")),
            "duplicate_delta_ms": record.get("duplicate_delta_ms"),
            "duplicate_count_10s": int(record.get("duplicate_count_10s") or 0),
            "age_s": age_s,
        }
        if entry["kind"] == "desktop.webspace.reload":
            if age_s is not None and age_s <= 60.0:
                recent_reload_60s += 1
            if last_reload is None:
                last_reload = dict(entry)
        elif entry["kind"] == "desktop.webspace.reset":
            if age_s is not None and age_s <= 60.0:
                recent_reset_60s += 1
            if last_reset is None:
                last_reset = dict(entry)
        if len(recent_items) < 8:
            recent_items.append(entry)
    return {
        "reload_total": int(stats.get("reload_total") or 0),
        "reload_duplicate_total": int(stats.get("reload_duplicate_total") or 0),
        "reload_recent_60s": int(recent_reload_60s),
        "reset_total": int(stats.get("reset_total") or 0),
        "reset_duplicate_total": int(stats.get("reset_duplicate_total") or 0),
        "reset_recent_60s": int(recent_reset_60s),
        "last_reload": last_reload or {},
        "last_reset": last_reset or {},
        "recent": recent_items,
    }


def _mark_room_created(webspace_id: str, room: Any) -> None:
    key = str(webspace_id or "").strip() or "default"
    ydoc = getattr(room, "ydoc", None)
    now = time.time()
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        entry["generation"] = int(entry.get("generation") or 0) + 1
        entry["create_total"] = int(entry.get("create_total") or 0) + 1
        entry["last_created_at"] = now
        entry["last_room_object_id"] = id(room)
        entry["last_ydoc_object_id"] = id(ydoc) if ydoc is not None else None


def _mark_room_open(
    webspace_id: str,
    room: Any,
    *,
    created: bool,
    open_total_ms: float | None = None,
    seed_result: dict[str, Any] | None = None,
) -> None:
    key = str(webspace_id or "").strip() or "default"
    now = time.time()
    lifecycle = dict(seed_result or {})
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        entry["open_total"] = int(entry.get("open_total") or 0) + 1
        if created:
            entry["cold_open_total"] = int(entry.get("cold_open_total") or 0) + 1
            if bool(lifecycle.get("used_provided_ydoc")):
                entry["single_pass_bootstrap_total"] = int(entry.get("single_pass_bootstrap_total") or 0) + 1
        else:
            entry["reuse_total"] = int(entry.get("reuse_total") or 0) + 1
        entry["last_open_at"] = now
        entry["last_open_mode"] = "cold_open" if created else "room_reuse"
        entry["last_open_total_ms"] = round(float(open_total_ms), 3) if open_total_ms is not None else None
        if created:
            entry["last_open_apply_updates_ms"] = (
                round(float(lifecycle.get("apply_updates_ms") or 0.0), 3) if lifecycle else None
            )
            entry["last_open_bootstrap_total_ms"] = (
                round(float(lifecycle.get("total_ms") or 0.0), 3) if lifecycle else None
            )
            entry["last_open_bootstrap_mode"] = (
                str(lifecycle.get("mode") or "").strip() or None if lifecycle else None
            )
            entry["last_open_bootstrap_persisted_via"] = (
                str(lifecycle.get("persisted_via") or "").strip() or None if lifecycle else None
            )
            entry["last_open_bootstrap_single_pass"] = bool(lifecycle.get("used_provided_ydoc")) if lifecycle else False


def _mark_room_reset(
    webspace_id: str,
    *,
    close_reason: str,
    room: Any | None,
    room_dropped: bool,
    closed_connections: int,
    closed_webrtc_peers: int,
) -> None:
    key = str(webspace_id or "").strip() or "default"
    ydoc = getattr(room, "ydoc", None) if room is not None else None
    now = time.time()
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        entry["reset_total"] = int(entry.get("reset_total") or 0) + 1
        entry["last_reset_at"] = now
        entry["last_reset_reason"] = str(close_reason or "").strip() or "webspace_reload"
        entry["last_reset_closed_connections"] = int(closed_connections or 0)
        entry["last_reset_closed_webrtc_peers"] = int(closed_webrtc_peers or 0)
        entry["last_reset_room_dropped"] = bool(room_dropped)
        if room is not None:
            entry["last_reset_room_object_id"] = id(room)
        if ydoc is not None:
            entry["last_reset_ydoc_object_id"] = id(ydoc)
        if room_dropped:
            entry["drop_total"] = int(entry.get("drop_total") or 0) + 1
            entry["last_dropped_at"] = now


def _next_room_bootstrap_attempt_id(webspace_id: str) -> str:
    global _YROOM_BOOTSTRAP_ATTEMPT_SEQ
    now = time.time()
    with _YROOM_LIFECYCLE_LOCK:
        _YROOM_BOOTSTRAP_ATTEMPT_SEQ += 1
        return f"yroom-{int(now * 1000):x}-{_YROOM_BOOTSTRAP_ATTEMPT_SEQ:x}"


def _mark_room_bootstrap_started(webspace_id: str, *, yws_attempt_id: str | None = None) -> str:
    key = str(webspace_id or "").strip() or "default"
    yws_token = str(yws_attempt_id or "").strip()
    attempt_id = _next_room_bootstrap_attempt_id(key)
    now = time.time()
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        entry["bootstrap_total"] = int(entry.get("bootstrap_total") or 0) + 1
        entry["last_bootstrap_attempt_id"] = attempt_id
        entry["last_bootstrap_yws_attempt_id"] = yws_token or None
        entry["last_bootstrap_started_at"] = now
        entry["last_bootstrap_finished_at"] = None
        entry["last_bootstrap_duration_ms"] = None
        entry["last_bootstrap_state"] = "starting"
        entry["last_bootstrap_step"] = None
        entry["last_bootstrap_error"] = None
        entry["bootstrap_stuck"] = False
        entry["stuck_step"] = None
        entry["stuck_since"] = None
        entry["stuck_reason"] = None
        entry["stuck_attempt_id"] = None
        entry["recommended_action"] = None
    return attempt_id


def _mark_room_bootstrap_step(webspace_id: str, bootstrap_attempt_id: str, step: str) -> None:
    key = str(webspace_id or "").strip() or "default"
    attempt_id = str(bootstrap_attempt_id or "").strip()
    if not attempt_id:
        return
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        if str(entry.get("last_bootstrap_attempt_id") or "") != attempt_id:
            return
        entry["last_bootstrap_step"] = str(step or "").strip() or None


def _mark_room_bootstrap_finished(
    webspace_id: str,
    bootstrap_attempt_id: str,
    *,
    state: str,
    step: str | None = None,
    error: str | None = None,
) -> None:
    key = str(webspace_id or "").strip() or "default"
    attempt_id = str(bootstrap_attempt_id or "").strip()
    if not attempt_id:
        return
    state_token = str(state or "").strip().lower() or "unknown"
    now = time.time()
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        if str(entry.get("last_bootstrap_attempt_id") or "") != attempt_id:
            return
        started_at = float(entry.get("last_bootstrap_started_at") or 0.0)
        entry["last_bootstrap_finished_at"] = now
        entry["last_bootstrap_duration_ms"] = round(max(0.0, now - started_at) * 1000.0, 3) if started_at > 0.0 else None
        entry["last_bootstrap_state"] = state_token
        if step is not None:
            entry["last_bootstrap_step"] = str(step or "").strip() or None
        entry["last_bootstrap_error"] = str(error or "").strip()[:240] or None
        if state_token == "ready":
            entry["bootstrap_success_total"] = int(entry.get("bootstrap_success_total") or 0) + 1
            entry["bootstrap_stuck"] = False
            entry["stuck_step"] = None
            entry["stuck_since"] = None
            entry["stuck_reason"] = None
            entry["stuck_attempt_id"] = None
            entry["recommended_action"] = None
        else:
            entry["bootstrap_failure_total"] = int(entry.get("bootstrap_failure_total") or 0) + 1
            if state_token == "timeout":
                entry["bootstrap_timeout_total"] = int(entry.get("bootstrap_timeout_total") or 0) + 1


def _mark_room_bootstrap_stuck(
    webspace_id: str,
    bootstrap_attempt_id: str,
    *,
    step: str,
    reason: str,
) -> dict[str, Any]:
    key = str(webspace_id or "").strip() or "default"
    attempt_id = str(bootstrap_attempt_id or "").strip()
    now = time.time()
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        if attempt_id and str(entry.get("last_bootstrap_attempt_id") or "") != attempt_id:
            return {}
        timeout_total = int(entry.get("bootstrap_timeout_total") or 0) + 1
        action = "reset_runtime_room"
        if timeout_total >= 2:
            action = "evict_ystore_runtime"
        if timeout_total >= int(_YWS_ROOM_RESTART_RECOMMEND_TIMEOUTS):
            action = "controlled_runtime_restart"
        entry["bootstrap_stuck"] = True
        entry["stuck_step"] = str(step or "").strip() or "unknown"
        entry["stuck_since"] = entry.get("stuck_since") or now
        entry["stuck_reason"] = str(reason or "").strip()[:240] or "bootstrap_step_timeout"
        entry["stuck_attempt_id"] = attempt_id or None
        entry["recommended_action"] = action
        entry["last_bootstrap_state"] = "stuck"
        entry["last_bootstrap_step"] = entry["stuck_step"]
        entry["last_bootstrap_error"] = entry["stuck_reason"]
        entry["bootstrap_timeout_total"] = timeout_total
        return dict(entry)


def _mark_room_wait_timeout(
    webspace_id: str,
    *,
    dev_id: str,
    yws_attempt_id: str | None,
    waited_s: float,
) -> None:
    key = str(webspace_id or "").strip() or "default"
    now = time.time()
    with _YROOM_LIFECYCLE_LOCK:
        entry = _YROOM_LIFECYCLE.setdefault(key, {})
        entry["room_wait_timeout_total"] = int(entry.get("room_wait_timeout_total") or 0) + 1
        entry["last_wait_timeout_at"] = now
        entry["last_wait_timeout_s"] = round(max(0.0, float(waited_s or 0.0)), 3)
        entry["last_wait_timeout_dev_id"] = str(dev_id or "").strip() or "unknown"
        entry["last_wait_timeout_yws_attempt_id"] = str(yws_attempt_id or "").strip() or None


def _room_debug_snapshot(webspace_id: str, room: Any | None, now: float) -> dict[str, Any]:
    key = str(webspace_id or "").strip() or "default"
    with _YROOM_LIFECYCLE_LOCK:
        meta = dict(_YROOM_LIFECYCLE.get(key) or {})

    # Do not borrow ``room.ydoc`` here. Reliability/control snapshots can run
    # in a worker thread while the event-loop thread tears a room down. Merely
    # retaining the Python YDoc wrapper until this function returns can make
    # the worker the owner of its final decref after ``_release_room_refs``
    # clears the room attribute. y_py objects are thread-affine on Windows and
    # reject that cross-thread drop. The object id is captured as plain data by
    # ``_mark_room_created`` on the room owner thread instead.
    ystore = getattr(room, "ystore", None) if room is not None else None
    clients = getattr(room, "clients", None) if room is not None else None
    send_stream_stats = _memory_stream_statistics(getattr(room, "_update_send_stream", None) if room is not None else None)
    recv_stream_stats = _memory_stream_statistics(getattr(room, "_update_receive_stream", None) if room is not None else None)
    started_event = getattr(room, "_started", None) if room is not None else None
    task_group = getattr(room, "_task_group", None) if room is not None else None
    ystore_runtime = {}
    if ystore is not None:
        runtime_snapshot = getattr(ystore, "runtime_snapshot", None)
        if callable(runtime_snapshot):
            try:
                raw = runtime_snapshot(now_ts=now)
            except Exception:
                raw = {}
            if isinstance(raw, dict):
                ystore_runtime = {
                    "update_log_entries": int(raw.get("update_log_entries") or 0),
                    "update_log_bytes": int(raw.get("update_log_bytes") or 0),
                    "replay_window_bytes": int(raw.get("replay_window_bytes") or 0),
                    "last_update_bytes": int(raw.get("last_update_bytes") or 0),
                }
    room_diagnostic = {}
    diagnostic_snapshot = getattr(room, "_diag_snapshot", None) if room is not None else None
    if callable(diagnostic_snapshot):
        try:
            raw_diag = diagnostic_snapshot()
        except Exception:
            raw_diag = {}
        if isinstance(raw_diag, dict):
            send_stream = dict(raw_diag.get("send_stream") or {}) if isinstance(raw_diag.get("send_stream"), dict) else {}
            receive_stream = dict(raw_diag.get("receive_stream") or {}) if isinstance(raw_diag.get("receive_stream"), dict) else {}
            diag_ystore = dict(raw_diag.get("ystore") or {}) if isinstance(raw_diag.get("ystore"), dict) else {}
            live_room_refresh_recent = raw_diag.get("live_room_refresh_recent")
            if not isinstance(live_room_refresh_recent, list):
                live_room_refresh_recent = []
            room_diagnostic = {
                "pending_send_tasks": int(raw_diag.get("pending_send_tasks") or 0),
                "pending_store_tasks": int(raw_diag.get("pending_store_tasks") or 0),
                "update_total": int(raw_diag.get("update_total") or 0),
                "update_bytes_total": int(raw_diag.get("update_bytes_total") or 0),
                "destructive_update_block_total": int(raw_diag.get("destructive_update_block_total") or 0),
                "destructive_update_block_bytes": int(raw_diag.get("destructive_update_block_bytes") or 0),
                "inbound_guard_block_total": int(raw_diag.get("inbound_guard_block_total") or 0),
                "inbound_guard_block_bytes": int(raw_diag.get("inbound_guard_block_bytes") or 0),
                "inbound_guard_last_bytes": int(raw_diag.get("inbound_guard_last_bytes") or 0),
                "inbound_guard_last_block_bytes": int(raw_diag.get("inbound_guard_last_block_bytes") or 0),
                "inbound_guard_last_at": raw_diag.get("inbound_guard_last_at") or None,
                "inbound_guard_last_ago_s": raw_diag.get("inbound_guard_last_ago_s"),
                "inbound_guard_last_reset_reserved": bool(raw_diag.get("inbound_guard_last_reset_reserved")),
                "effective_repair_total": int(raw_diag.get("effective_repair_total") or 0),
                "effective_repair_bytes": int(raw_diag.get("effective_repair_bytes") or 0),
                "effective_initial_replay_total": int(raw_diag.get("effective_initial_replay_total") or 0),
                "effective_initial_replay_bytes": int(raw_diag.get("effective_initial_replay_bytes") or 0),
                "effective_initial_replay_skip_total": int(
                    raw_diag.get("effective_initial_replay_skip_total") or 0
                ),
                "effective_initial_replay_dedupe_total": int(
                    raw_diag.get("effective_initial_replay_dedupe_total") or 0
                ),
                "effective_initial_replay_last_reason": str(
                    raw_diag.get("effective_initial_replay_last_reason") or ""
                ),
                "send_stream": {
                    "current_buffer_used": int(send_stream.get("current_buffer_used") or 0),
                    "max_buffer_size": int(send_stream.get("max_buffer_size") or 0),
                    "tasks_waiting_send": int(send_stream.get("tasks_waiting_send") or 0),
                    "tasks_waiting_receive": int(send_stream.get("tasks_waiting_receive") or 0),
                },
                "receive_stream": {
                    "current_buffer_used": int(receive_stream.get("current_buffer_used") or 0),
                    "max_buffer_size": int(receive_stream.get("max_buffer_size") or 0),
                    "tasks_waiting_send": int(receive_stream.get("tasks_waiting_send") or 0),
                    "tasks_waiting_receive": int(receive_stream.get("tasks_waiting_receive") or 0),
                },
                "ystore": {
                    "update_log_entries": int(diag_ystore.get("update_log_entries") or 0),
                    "update_log_bytes": int(diag_ystore.get("update_log_bytes") or 0),
                    "replay_window_bytes": int(diag_ystore.get("replay_window_bytes") or 0),
                    "last_update_bytes": int(diag_ystore.get("last_update_bytes") or 0),
                },
                "live_room_refresh_recent": live_room_refresh_recent[:5],
            }

    return {
        "webspace_id": key,
        "active": bool(room is not None),
        "generation": int(meta.get("generation") or 0),
        "create_total": int(meta.get("create_total") or 0),
        "reset_total": int(meta.get("reset_total") or 0),
        "drop_total": int(meta.get("drop_total") or 0),
        "last_created_at": meta.get("last_created_at"),
        "last_created_ago_s": _seconds_ago(meta.get("last_created_at"), now),
        "last_open_at": meta.get("last_open_at"),
        "last_open_ago_s": _seconds_ago(meta.get("last_open_at"), now),
        "last_reset_at": meta.get("last_reset_at"),
        "last_reset_ago_s": _seconds_ago(meta.get("last_reset_at"), now),
        "last_dropped_at": meta.get("last_dropped_at"),
        "last_dropped_ago_s": _seconds_ago(meta.get("last_dropped_at"), now),
        "open_total": int(meta.get("open_total") or 0),
        "cold_open_total": int(meta.get("cold_open_total") or 0),
        "reuse_total": int(meta.get("reuse_total") or 0),
        "single_pass_bootstrap_total": int(meta.get("single_pass_bootstrap_total") or 0),
        "bootstrap_total": int(meta.get("bootstrap_total") or 0),
        "bootstrap_success_total": int(meta.get("bootstrap_success_total") or 0),
        "bootstrap_failure_total": int(meta.get("bootstrap_failure_total") or 0),
        "bootstrap_timeout_total": int(meta.get("bootstrap_timeout_total") or 0),
        "room_wait_timeout_total": int(meta.get("room_wait_timeout_total") or 0),
        "last_open_mode": str(meta.get("last_open_mode") or "").strip() or None,
        "last_open_total_ms": meta.get("last_open_total_ms"),
        "last_open_apply_updates_ms": meta.get("last_open_apply_updates_ms"),
        "last_open_bootstrap_total_ms": meta.get("last_open_bootstrap_total_ms"),
        "last_open_bootstrap_mode": str(meta.get("last_open_bootstrap_mode") or "").strip() or None,
        "last_open_bootstrap_persisted_via": str(meta.get("last_open_bootstrap_persisted_via") or "").strip() or None,
        "last_open_bootstrap_single_pass": bool(meta.get("last_open_bootstrap_single_pass")),
        "last_bootstrap_attempt_id": str(meta.get("last_bootstrap_attempt_id") or "").strip() or None,
        "last_bootstrap_yws_attempt_id": str(meta.get("last_bootstrap_yws_attempt_id") or "").strip() or None,
        "last_bootstrap_started_at": meta.get("last_bootstrap_started_at"),
        "last_bootstrap_started_ago_s": _seconds_ago(meta.get("last_bootstrap_started_at"), now),
        "last_bootstrap_finished_at": meta.get("last_bootstrap_finished_at"),
        "last_bootstrap_finished_ago_s": _seconds_ago(meta.get("last_bootstrap_finished_at"), now),
        "last_bootstrap_duration_ms": meta.get("last_bootstrap_duration_ms"),
        "last_bootstrap_state": str(meta.get("last_bootstrap_state") or "").strip() or None,
        "last_bootstrap_step": str(meta.get("last_bootstrap_step") or "").strip() or None,
        "last_bootstrap_error": str(meta.get("last_bootstrap_error") or "").strip() or None,
        "bootstrap_stuck": bool(meta.get("bootstrap_stuck")),
        "stuck_step": str(meta.get("stuck_step") or "").strip() or None,
        "stuck_since": meta.get("stuck_since"),
        "stuck_age_s": _seconds_ago(meta.get("stuck_since"), now),
        "stuck_reason": str(meta.get("stuck_reason") or "").strip() or None,
        "stuck_attempt_id": str(meta.get("stuck_attempt_id") or "").strip() or None,
        "recommended_action": str(meta.get("recommended_action") or "").strip() or None,
        "last_wait_timeout_at": meta.get("last_wait_timeout_at"),
        "last_wait_timeout_ago_s": _seconds_ago(meta.get("last_wait_timeout_at"), now),
        "last_wait_timeout_s": meta.get("last_wait_timeout_s"),
        "last_wait_timeout_dev_id": str(meta.get("last_wait_timeout_dev_id") or "").strip() or None,
        "last_wait_timeout_yws_attempt_id": str(meta.get("last_wait_timeout_yws_attempt_id") or "").strip() or None,
        "last_reset_reason": str(meta.get("last_reset_reason") or "").strip() or None,
        "last_reset_closed_connections": int(meta.get("last_reset_closed_connections") or 0),
        "last_reset_closed_webrtc_peers": int(meta.get("last_reset_closed_webrtc_peers") or 0),
        "last_reset_room_dropped": bool(meta.get("last_reset_room_dropped")),
        "room_object_id": id(room) if room is not None else meta.get("last_room_object_id"),
        "ydoc_object_id": meta.get("last_ydoc_object_id"),
        "client_total": len(clients) if isinstance(clients, list) else 0,
        "ready": bool(getattr(room, "_ready", False)) if room is not None else False,
        "started": bool(getattr(started_event, "is_set", lambda: False)()) if started_event is not None else False,
        "task_group_active": bool(task_group is not None),
        "ystore_attached": bool(ystore is not None),
        "effective_branches": (
            getattr(room, "_diag_effective_branch_snapshot", None)
            if isinstance(getattr(room, "_diag_effective_branch_snapshot", None), dict)
            else {"ready": False, "error": "not_observed"}
        ),
        "ystore_runtime": ystore_runtime,
        "diagnostic": room_diagnostic,
        "update_send_stream": send_stream_stats,
        "update_receive_stream": recv_stream_stats,
    }


def _room_debug_snapshot_all(now: float) -> tuple[dict[str, Any], dict[str, int]]:
    room_keys = set()
    try:
        room_keys.update(str(key) for key in getattr(y_server, "rooms", {}).keys())
    except Exception:
        pass
    with _YROOM_LIFECYCLE_LOCK:
        room_keys.update(str(key) for key in _YROOM_LIFECYCLE.keys())

    room_details: dict[str, Any] = {}
    aggregated = {
        "active_room_total": 0,
        "room_create_total": 0,
        "room_reset_total": 0,
        "room_drop_total": 0,
        "room_generation_max": 0,
        "room_open_total": 0,
        "room_cold_open_total": 0,
        "room_reuse_total": 0,
        "room_single_pass_bootstrap_total": 0,
        "room_bootstrap_total": 0,
        "room_bootstrap_success_total": 0,
        "room_bootstrap_failure_total": 0,
        "room_bootstrap_timeout_total": 0,
        "room_wait_timeout_total": 0,
        "update_stream_buffer_used_total": 0,
        "update_stream_waiting_send_total": 0,
        "update_stream_waiting_receive_total": 0,
        "inbound_guard_block_total": 0,
        "inbound_guard_block_bytes": 0,
    }
    for key in sorted(room_keys):
        room = getattr(y_server, "rooms", {}).get(key)
        snapshot = _room_debug_snapshot(key, room, now)
        room_details[key] = snapshot
        aggregated["active_room_total"] += 1 if snapshot.get("active") else 0
        aggregated["room_create_total"] += int(snapshot.get("create_total") or 0)
        aggregated["room_reset_total"] += int(snapshot.get("reset_total") or 0)
        aggregated["room_drop_total"] += int(snapshot.get("drop_total") or 0)
        aggregated["room_open_total"] += int(snapshot.get("open_total") or 0)
        aggregated["room_cold_open_total"] += int(snapshot.get("cold_open_total") or 0)
        aggregated["room_reuse_total"] += int(snapshot.get("reuse_total") or 0)
        aggregated["room_single_pass_bootstrap_total"] += int(snapshot.get("single_pass_bootstrap_total") or 0)
        aggregated["room_bootstrap_total"] += int(snapshot.get("bootstrap_total") or 0)
        aggregated["room_bootstrap_success_total"] += int(snapshot.get("bootstrap_success_total") or 0)
        aggregated["room_bootstrap_failure_total"] += int(snapshot.get("bootstrap_failure_total") or 0)
        aggregated["room_bootstrap_timeout_total"] += int(snapshot.get("bootstrap_timeout_total") or 0)
        aggregated["room_wait_timeout_total"] += int(snapshot.get("room_wait_timeout_total") or 0)
        aggregated["room_generation_max"] = max(
            aggregated["room_generation_max"],
            int(snapshot.get("generation") or 0),
        )
        send_stream = snapshot.get("update_send_stream") if isinstance(snapshot.get("update_send_stream"), dict) else {}
        diagnostic = snapshot.get("diagnostic") if isinstance(snapshot.get("diagnostic"), dict) else {}
        aggregated["inbound_guard_block_total"] += int(diagnostic.get("inbound_guard_block_total") or 0)
        aggregated["inbound_guard_block_bytes"] += int(diagnostic.get("inbound_guard_block_bytes") or 0)
        aggregated["update_stream_buffer_used_total"] += int(send_stream.get("current_buffer_used") or 0)
        aggregated["update_stream_waiting_send_total"] += int(send_stream.get("tasks_waiting_send") or 0)
        aggregated["update_stream_waiting_receive_total"] += int(send_stream.get("tasks_waiting_receive") or 0)
    return room_details, aggregated


async def _close_room_stream_maybe(stream: Any) -> bool:
    if stream is None:
        return False
    closed = False
    close = getattr(stream, "close", None)
    if callable(close):
        try:
            result = close()
            if inspect.isawaitable(result):
                await result
            closed = True
        except Exception:
            closed = False
    aclose = getattr(stream, "aclose", None)
    if callable(aclose):
        try:
            result = aclose()
            if inspect.isawaitable(result):
                await result
            closed = True
        except Exception:
            pass
    return closed


async def _release_room_refs(webspace_id: str, room: Any) -> bool:
    released = False
    ydoc = getattr(room, "ydoc", None)
    if ydoc is not None:
        try:
            forget_room_observers(webspace_id, ydoc)
        except Exception:
            pass
    for attr in ("_update_send_stream", "_update_receive_stream"):
        try:
            stream = getattr(room, attr, None)
        except Exception:
            stream = None
        try:
            released = await _close_room_stream_maybe(stream) or released
        except Exception:
            pass

    clients = getattr(room, "clients", None)
    if isinstance(clients, list):
        try:
            clients.clear()
            released = True
        except Exception:
            pass

    for attr in (
        "awareness",
        "_on_message",
        "_started",
        "_exit_stack",
        "_task_group",
        "ydoc",
        "ystore",
        "_loop",
        "_thread_id",
        "ready",
        "log",
    ):
        if not hasattr(room, attr):
            continue
        try:
            setattr(room, attr, None)
            released = True
        except Exception:
            continue
    return released


async def _delete_ystore_backup_job(webspace_id: str) -> bool:
    try:
        sched = get_scheduler()
        await sched.delete(f"ystores.backup.{str(webspace_id or '').strip() or 'default'}")
        return True
    except Exception:
        _ylog.debug("failed to delete YStore backup job webspace=%s", webspace_id, exc_info=True)
        return False


def _cancel_idle_room_reset(webspace_id: str) -> bool:
    key = str(webspace_id or "").strip() or "default"
    task = _IDLE_ROOM_RESET_TASKS.pop(key, None)
    if task is None:
        return False
    current = asyncio.current_task()
    if task is not current and not task.done():
        task.cancel()
    return True


def _active_webrtc_peer_total_for_webspace(webspace_id: str) -> int:
    key = str(webspace_id or "").strip() or "default"
    try:
        from adaos.services.webrtc.peer import webrtc_peer_snapshot

        snapshot = webrtc_peer_snapshot()
    except Exception:
        return 0
    peers = snapshot.get("peers") if isinstance(snapshot, dict) else None
    if not isinstance(peers, list):
        return 0
    return sum(
        1
        for peer in peers
        if isinstance(peer, dict)
        and str(peer.get("webspace_id") or "").strip() == key
    )


def _active_yws_connection_total_for_webspace(webspace_id: str) -> int:
    key = str(webspace_id or "").strip() or "default"
    with _ACTIVE_YWS_LOCK:
        return len(_ACTIVE_YWS_CONNECTIONS.get(key) or [])


def _active_events_ws_connection_total_for_webspace(webspace_id: str) -> int:
    key = str(webspace_id or "").strip() or "default"
    with _ACTIVE_EVENTS_WS_LOCK:
        return sum(1 for value in _ACTIVE_EVENTS_WS_WEBSPACES.values() if value == key)


def _webspace_has_live_transports(webspace_id: str) -> bool:
    key = str(webspace_id or "").strip() or "default"
    if _active_yws_connection_total_for_webspace(key) > 0:
        return True
    if _active_webrtc_peer_total_for_webspace(key) > 0:
        return True
    return _active_events_ws_connection_total_for_webspace(key) > 0


def _schedule_idle_room_reset(webspace_id: str, *, reason: str = "idle_room_eviction") -> bool:
    key = str(webspace_id or "").strip() or "default"
    if _IDLE_ROOM_EVICT_SEC <= 0.0:
        return False
    if key not in getattr(y_server, "rooms", {}):
        return False
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    _cancel_idle_room_reset(key)

    async def _runner() -> None:
        try:
            await asyncio.sleep(_IDLE_ROOM_EVICT_SEC)
            if _webspace_has_live_transports(key):
                if _active_yws_connection_total_for_webspace(key) <= 0:
                    _schedule_idle_room_reset(key, reason=reason)
                return
            await reset_live_webspace_room(
                key,
                close_reason=reason,
                reset_route_runtime=False,
                prewarm_after_reset=False,
            )
        except asyncio.CancelledError:
            return
        except Exception:
            _ylog.warning(
                "idle room eviction failed webspace=%s reason=%s",
                key,
                reason,
                exc_info=True,
            )
        finally:
            current = asyncio.current_task()
            if _IDLE_ROOM_RESET_TASKS.get(key) is current:
                _IDLE_ROOM_RESET_TASKS.pop(key, None)

    _IDLE_ROOM_RESET_TASKS[key] = asyncio.create_task(
        _runner(),
        name=f"adaos-yjs-idle-room-reset-{key}",
    )
    return True


async def _accept_websocket(websocket: WebSocket, *, channel: str) -> bool:
    try:
        await websocket.accept()
        return True
    except WebSocketDisconnect:
        return False
    except RuntimeError as exc:
        if _is_websocket_accept_race(exc):
            _ylog.info(
                "%s websocket accept skipped because handshake was already closed client=%s",
                channel,
                _ws_client_str(websocket),
            )
            return False
        raise


def _transport_mark_open(name: str) -> None:
    key = str(name or "").strip().lower()
    if not key:
        return
    now = time.time()
    with _TRANSPORT_LOCK:
        entry = _TRANSPORT_STATE.setdefault(
            key,
            {
                "active_connections": 0,
                "open_total": 0,
                "close_total": 0,
                "last_open_at": 0.0,
                "last_close_at": 0.0,
            },
        )
        entry["active_connections"] = int(entry.get("active_connections") or 0) + 1
        entry["open_total"] = int(entry.get("open_total") or 0) + 1
        entry["last_open_at"] = now


def _transport_mark_close(name: str) -> None:
    key = str(name or "").strip().lower()
    if not key:
        return
    now = time.time()
    with _TRANSPORT_LOCK:
        entry = _TRANSPORT_STATE.setdefault(
            key,
            {
                "active_connections": 0,
                "open_total": 0,
                "close_total": 0,
                "last_open_at": 0.0,
                "last_close_at": 0.0,
            },
        )
        active = int(entry.get("active_connections") or 0) - 1
        entry["active_connections"] = max(0, active)
        entry["close_total"] = int(entry.get("close_total") or 0) + 1
        entry["last_close_at"] = now


def _publish_runtime_event(topic: str, payload: dict[str, Any] | None = None, *, source: str = "yjs.gateway") -> None:
    try:
        ctx = get_agent_ctx()
        ctx.bus.publish(DomainEvent(type=topic, payload=dict(payload or {}), source=source, ts=time.time()))
    except Exception:
        _log.debug("failed to publish runtime event topic=%s", topic, exc_info=True)


def _normalize_ws_event_topics(raw_topics: Any) -> set[str]:
    if not isinstance(raw_topics, list):
        return set()
    return {
        topic
        for topic in (str(raw or "").strip() for raw in raw_topics)
        if topic
    }


def _ws_event_topic_matches(subscription: str, event_type: str) -> bool:
    topic = str(subscription or "").strip()
    event = str(event_type or "").strip()
    if not topic or not event:
        return False
    if topic in {"*", ""}:
        return True
    if topic.endswith("*"):
        return event.startswith(topic[:-1])
    return event == topic


def _build_ws_event_message(
    event_type: str,
    payload: Any,
    *,
    source: str = "events_ws",
    ts: float | None = None,
) -> dict[str, Any]:
    return {
        "ch": "events",
        "t": "evt",
        "kind": str(event_type or "").strip(),
        "payload": payload if isinstance(payload, dict) else {"value": payload},
        "source": str(source or "events_ws").strip() or "events_ws",
        "ts": float(ts or time.time()),
    }


def _ws_event_message_kind(message: dict[str, Any]) -> str:
    return str(message.get("kind") or "").strip()


def _ws_event_message_coalesce_key(message: dict[str, Any]) -> tuple[str, str, str, str] | None:
    kind = _ws_event_message_kind(message)
    if not kind:
        return None
    if not (
        kind in {"node.status", "core.update.status", "supervisor.update.status.raw", "browser.session.changed", "webrtc.peer.state.changed"}
        or kind.startswith("webio.")
    ):
        return None
    payload = message.get("payload")
    payload_map = payload if isinstance(payload, dict) else {}
    route_key = (
        str(payload_map.get("topic") or "").strip()
        or str(payload_map.get("receiver") or payload_map.get("projection") or payload_map.get("slot") or "").strip()
    )
    webspace_id = str(payload_map.get("webspace_id") or payload_map.get("workspace_id") or "").strip()
    subject_id = str(payload_map.get("device_id") or payload_map.get("node_id") or payload_map.get("target_node_id") or "").strip()
    return (kind, webspace_id, route_key, subject_id)


def _ws_event_send_snapshot() -> dict[str, Any]:
    with _WS_EVENT_SEND_LOCK:
        states = list(_WS_EVENT_SEND_STATES.items())
        queue_total = 0
        active_tasks = 0
        top_queues: list[dict[str, Any]] = []
        for key, state in states:
            queue = state.get("queue")
            queue_len = len(queue) if isinstance(queue, deque) else 0
            queue_total += queue_len
            task = state.get("task")
            if isinstance(task, asyncio.Task) and not task.done():
                active_tasks += 1
            if queue_len > 0:
                top_queues.append(
                    {
                        "connection_id": str(key),
                        "queue_len": queue_len,
                        "dropped_total": int(state.get("dropped_total") or 0),
                        "coalesced_total": int(state.get("coalesced_total") or 0),
                    }
                )
        top_queues.sort(key=lambda item: (-int(item.get("queue_len") or 0), str(item.get("connection_id") or "")))
        return {
            "queue_limit": int(_WS_EVENT_SEND_QUEUE_LIMIT),
            "connection_total": len(states),
            "active_tasks": active_tasks,
            "queue_total": queue_total,
            "top_queues": top_queues[:5],
            **dict(_WS_EVENT_SEND_DIAG),
        }


def _drop_ws_event_send_state(websocket: WebSocket, *, cancel_task: bool = True) -> None:
    key = id(websocket)
    with _WS_EVENT_SEND_LOCK:
        state = _WS_EVENT_SEND_STATES.pop(key, None)
    if not isinstance(state, dict):
        return
    queue = state.get("queue")
    if isinstance(queue, deque):
        queue.clear()
    task = state.get("task")
    if cancel_task and isinstance(task, asyncio.Task) and not task.done():
        try:
            current = asyncio.current_task()
        except RuntimeError:
            current = None
        if task is not current:
            task.cancel()


def _maybe_log_ws_event_send_pressure_locked(kind: str, *, action: str, count: int) -> None:
    now = time.time()
    key = f"last_{action}_log_at"
    last = float(_WS_EVENT_SEND_DIAG.get(key) or 0.0)
    if now - last < _WS_EVENT_SEND_LOG_INTERVAL_S:
        return
    _WS_EVENT_SEND_DIAG[key] = now
    _log.warning(
        "events websocket send queue %s kind=%s count=%s queued_connections=%s queue_limit=%s",
        action,
        kind or "-",
        count,
        len(_WS_EVENT_SEND_STATES),
        _WS_EVENT_SEND_QUEUE_LIMIT,
    )


async def _drain_ws_event_send_queue(key: int, websocket: WebSocket) -> None:
    while True:
        with _WS_EVENT_SEND_LOCK:
            state = _WS_EVENT_SEND_STATES.get(key)
            if not isinstance(state, dict):
                return
            queue = state.get("queue")
            if not isinstance(queue, deque) or not queue:
                state["task"] = None
                with _WS_EVENT_SUBSCRIPTIONS_LOCK:
                    subscribed = key in _WS_EVENT_SUBSCRIBERS
                if not subscribed:
                    _WS_EVENT_SEND_STATES.pop(key, None)
                return
            message = queue.popleft()
        try:
            await _send_ws_event_message(websocket, message)
        finally:
            with _WS_EVENT_SEND_LOCK:
                _WS_EVENT_SEND_DIAG["sent_total"] = int(_WS_EVENT_SEND_DIAG.get("sent_total") or 0) + 1
        await asyncio.sleep(0)


def _enqueue_ws_event_message(websocket: WebSocket, message: dict[str, Any]) -> None:
    key = id(websocket)
    kind = _ws_event_message_kind(message)
    with _WS_EVENT_SEND_LOCK:
        state = _WS_EVENT_SEND_STATES.setdefault(
            key,
            {
                "queue": deque(),
                "task": None,
                "dropped_total": 0,
                "coalesced_total": 0,
            },
        )
        queue = state.get("queue")
        if not isinstance(queue, deque):
            queue = deque()
            state["queue"] = queue
        if len(queue) >= _WS_EVENT_SEND_QUEUE_LIMIT:
            coalesce_key = _ws_event_message_coalesce_key(message)
            if coalesce_key is not None:
                for index in range(len(queue) - 1, -1, -1):
                    queued = queue[index]
                    if isinstance(queued, dict) and _ws_event_message_coalesce_key(queued) == coalesce_key:
                        queue[index] = message
                        state["coalesced_total"] = int(state.get("coalesced_total") or 0) + 1
                        _WS_EVENT_SEND_DIAG["coalesced_total"] = int(_WS_EVENT_SEND_DIAG.get("coalesced_total") or 0) + 1
                        _WS_EVENT_SEND_DIAG["last_coalesced_at"] = time.time()
                        _WS_EVENT_SEND_DIAG["last_coalesced_kind"] = kind
                        _maybe_log_ws_event_send_pressure_locked(kind, action="coalesced", count=int(state["coalesced_total"]))
                        break
                else:
                    queue.popleft()
                    queue.append(message)
                    state["dropped_total"] = int(state.get("dropped_total") or 0) + 1
                    _WS_EVENT_SEND_DIAG["dropped_total"] = int(_WS_EVENT_SEND_DIAG.get("dropped_total") or 0) + 1
                    _WS_EVENT_SEND_DIAG["last_drop_at"] = time.time()
                    _WS_EVENT_SEND_DIAG["last_drop_kind"] = kind
                    _maybe_log_ws_event_send_pressure_locked(kind, action="dropped", count=int(state["dropped_total"]))
            else:
                queue.popleft()
                queue.append(message)
                state["dropped_total"] = int(state.get("dropped_total") or 0) + 1
                _WS_EVENT_SEND_DIAG["dropped_total"] = int(_WS_EVENT_SEND_DIAG.get("dropped_total") or 0) + 1
                _WS_EVENT_SEND_DIAG["last_drop_at"] = time.time()
                _WS_EVENT_SEND_DIAG["last_drop_kind"] = kind
                _maybe_log_ws_event_send_pressure_locked(kind, action="dropped", count=int(state["dropped_total"]))
        else:
            queue.append(message)
            _WS_EVENT_SEND_DIAG["queued_total"] = int(_WS_EVENT_SEND_DIAG.get("queued_total") or 0) + 1
        task = state.get("task")
        if not isinstance(task, asyncio.Task) or task.done():
            state["task"] = asyncio.create_task(_drain_ws_event_send_queue(key, websocket), name="events-ws-send-drain")


async def _send_ws_event_message(websocket: WebSocket, message: dict[str, Any]) -> None:
    try:
        await websocket.send_text(json.dumps(message))
    except (WebSocketDisconnect, RuntimeError):
        _unregister_ws_event_subscriptions(websocket)
        raise


def _iter_initial_ws_event_messages(topics: set[str]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    if any(_ws_event_topic_matches(topic, "node.status") for topic in topics):
        try:
            from adaos.services.bootstrap import load_config as _load_config
            from adaos.services.system_model.service import (
                current_node_status_push_payload as _current_node_status_push_payload,
            )

            conf = _load_config()
            if str(getattr(conf, "role", "") or "").strip().lower() == "hub":
                messages.append(
                    _build_ws_event_message(
                        "node.status",
                        _current_node_status_push_payload(),
                        source="node.status",
                    )
                )
        except Exception:
            _ylog.debug("failed to snapshot node.status for ws subscriber", exc_info=True)
    if any(_ws_event_topic_matches(topic, "core.update.status") for topic in topics):
        try:
            from adaos.services.core_update import read_status as _read_core_update_status

            messages.append(
                _build_ws_event_message(
                    "core.update.status",
                    _read_core_update_status() or {},
                    source="core.update.status",
                )
            )
        except Exception:
            _ylog.debug("failed to snapshot core.update.status for ws subscriber", exc_info=True)
    if any(_ws_event_topic_matches(topic, "supervisor.update.status.raw") for topic in topics):
        try:
            from adaos.services.core_update import read_public_update_status as _read_public_update_status

            messages.append(
                _build_ws_event_message(
                    "supervisor.update.status.raw",
                    _read_public_update_status(),
                    source="supervisor.update.status.raw",
                )
            )
        except Exception:
            _ylog.debug("failed to snapshot supervisor.update.status.raw for ws subscriber", exc_info=True)
    return messages


def _stable_json_for_dedupe(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(value)


def _webio_control_dedupe_key(event_type: str, payload: dict[str, Any]) -> str:
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    params = payload.get("params")
    if params is None and isinstance(meta, dict):
        params = meta.get("params")
    node_id = (
        str(payload.get("target_node_id") or "").strip()
        or str(payload.get("node_id") or "").strip()
        or str(meta.get("target_node_id") or "").strip()
        or str(meta.get("node_id") or "").strip()
    )
    return _stable_json_for_dedupe(
        {
            "type": str(event_type or "").strip(),
            "webspace_id": str(payload.get("webspace_id") or meta.get("webspace_id") or "").strip(),
            "receiver": str(payload.get("receiver") or "").strip(),
            "topic": str(payload.get("topic") or "").strip(),
            "node_id": node_id,
            "params": params if isinstance(params, (dict, list, str, int, float, bool)) or params is None else str(params),
            "action": str(payload.get("action") or "").strip(),
        }
    )


def _should_drop_duplicate_webio_control_event(event_type: str, payload: Any) -> bool:
    if event_type not in _WEBIO_CONTROL_EVENT_TYPES:
        return False
    if not isinstance(payload, dict) or _WEBIO_CONTROL_DEDUPE_TTL_S <= 0:
        return False
    now = time.monotonic()
    key = _webio_control_dedupe_key(event_type, payload)
    with _WEBIO_CONTROL_DEDUPE_LOCK:
        last_at = float(_WEBIO_CONTROL_DEDUPE_RECENT.get(key) or 0.0)
        if last_at > 0 and now - last_at < _WEBIO_CONTROL_DEDUPE_TTL_S:
            _log_deduped_webio_control_event(event_type, key, now)
            return True
        _WEBIO_CONTROL_DEDUPE_RECENT[key] = now
        if len(_WEBIO_CONTROL_DEDUPE_RECENT) > _WEBIO_CONTROL_DEDUPE_MAX:
            cutoff = now - max(_WEBIO_CONTROL_DEDUPE_TTL_S, 1.0)
            stale = [item_key for item_key, ts in _WEBIO_CONTROL_DEDUPE_RECENT.items() if ts < cutoff]
            for item_key in stale:
                _WEBIO_CONTROL_DEDUPE_RECENT.pop(item_key, None)
            while len(_WEBIO_CONTROL_DEDUPE_RECENT) > _WEBIO_CONTROL_DEDUPE_MAX:
                try:
                    _WEBIO_CONTROL_DEDUPE_RECENT.pop(next(iter(_WEBIO_CONTROL_DEDUPE_RECENT)))
                except StopIteration:
                    break
    return False


def _log_deduped_webio_control_event(event_type: str, key: str, now: float) -> None:
    if _WEBIO_CONTROL_DEDUPE_LOG_INTERVAL_S <= 0:
        return
    last_log_at, suppressed = _WEBIO_CONTROL_DEDUPE_LOG_RECENT.get(key, (0.0, 0))
    if last_log_at <= 0 or now - last_log_at >= _WEBIO_CONTROL_DEDUPE_LOG_INTERVAL_S:
        if suppressed > 0:
            _ylog.debug(
                "deduped webio control event type=%s key=%s suppressed=%s",
                event_type,
                key,
                suppressed,
            )
        else:
            _ylog.debug("deduped webio control event type=%s key=%s", event_type, key)
        _WEBIO_CONTROL_DEDUPE_LOG_RECENT[key] = (now, 0)
        return
    _WEBIO_CONTROL_DEDUPE_LOG_RECENT[key] = (last_log_at, suppressed + 1)


def _publish_webio_snapshot_request(event_type: str, payload: dict[str, Any], source: str) -> None:
    ctx = get_agent_ctx()
    ctx.bus.publish(
        DomainEvent(
            type=event_type,
            payload=dict(payload or {}),
            source=str(source or "events_ws"),
            ts=time.time(),
        )
    )


def _request_webio_stream_snapshots(topics: set[str], *, transport: str) -> None:
    for topic in topics:
        token = str(topic or "").strip()
        prefix = "webio.stream."
        if not token.startswith(prefix):
            continue
        suffix = token[len(prefix):]
        parts = [str(part or "").strip() for part in suffix.split(".") if str(part or "").strip()]
        if len(parts) < 2:
            continue
        node_id = None
        if parts[0] == "nodes":
            if len(parts) < 3:
                continue
            webspace_id = _coerce_gateway_webspace_id(None)
            node_id = parts[1]
            receiver_parts = parts[2:]
        else:
            webspace_id = _coerce_gateway_webspace_id(parts[0])
            receiver_parts = parts[1:]
        if len(receiver_parts) >= 3 and receiver_parts[0] == "nodes":
            node_id = receiver_parts[1]
            receiver_parts = receiver_parts[2:]
        receiver = ".".join(receiver_parts).strip()
        if not webspace_id or not receiver:
            continue
        try:
            payload = {
                "topic": token,
                "webspace_id": webspace_id,
                "receiver": receiver,
                "transport": str(transport or "ws"),
            }
            if node_id:
                payload["node_id"] = node_id
                payload["target_node_id"] = node_id
                payload["_meta"] = {"webspace_id": webspace_id, "target_node_id": node_id}
            request_snapshot_event(
                "webio.stream.snapshot.requested",
                payload,
                "events_ws",
                _publish_webio_snapshot_request,
            )
        except Exception:
            _ylog.debug("failed to request webio stream snapshot topic=%s", token, exc_info=True)


def _publish_webio_stream_subscription_change(
    topics: set[str],
    *,
    action: str,
    transport: str,
    connection_id: str | None = None,
) -> None:
    for topic in topics:
        token = str(topic or "").strip()
        prefix = "webio.stream."
        if not token.startswith(prefix):
            continue
        suffix = token[len(prefix):]
        parts = [str(part or "").strip() for part in suffix.split(".") if str(part or "").strip()]
        if len(parts) < 2:
            continue
        node_id = None
        if parts[0] == "nodes":
            if len(parts) < 3:
                continue
            webspace_id = _coerce_gateway_webspace_id(None)
            node_id = parts[1]
            receiver_parts = parts[2:]
        else:
            webspace_id = _coerce_gateway_webspace_id(parts[0])
            receiver_parts = parts[1:]
        if len(receiver_parts) >= 3 and receiver_parts[0] == "nodes":
            node_id = receiver_parts[1]
            receiver_parts = receiver_parts[2:]
        receiver = ".".join(receiver_parts).strip()
        if not webspace_id or not receiver:
            continue
        try:
            ctx = get_agent_ctx()
            payload = {
                "topic": token,
                "webspace_id": webspace_id,
                "receiver": receiver,
                "transport": str(transport or "ws"),
                "action": str(action or "").strip() or "subscribed",
            }
            if connection_id:
                payload["connection_id"] = str(connection_id)
                payload["subscription_id"] = f"{transport}:{connection_id}:{token}"
            if node_id:
                payload["node_id"] = node_id
                payload["target_node_id"] = node_id
                payload["_meta"] = {"webspace_id": webspace_id, "target_node_id": node_id}
            if _should_drop_duplicate_webio_control_event("webio.stream.subscription.changed", payload):
                continue
            ctx.bus.publish(
                DomainEvent(
                    type="webio.stream.subscription.changed",
                    payload=payload,
                    source="events_ws",
                    ts=time.time(),
                )
            )
        except Exception:
            _ylog.debug("failed to publish webio stream subscription change topic=%s", token, exc_info=True)


def _parse_webio_yjs_projection_topic(topic: str) -> dict[str, Any] | None:
    token = str(topic or "").strip()
    prefix = "webio.yjs."
    if not token.startswith(prefix):
        return None
    suffix = token[len(prefix):]
    parts = [str(part or "").strip() for part in suffix.split(".") if str(part or "").strip()]
    if len(parts) < 2:
        return None
    node_id = None
    if parts[0] == "nodes":
        if len(parts) < 3:
            return None
        webspace_id = _coerce_gateway_webspace_id(None)
        node_id = parts[1]
        slot_parts = parts[2:]
    else:
        webspace_id = _coerce_gateway_webspace_id(parts[0])
        slot_parts = parts[1:]
    if len(slot_parts) >= 3 and slot_parts[0] == "nodes":
        node_id = slot_parts[1]
        slot_parts = slot_parts[2:]
    slot = ".".join(slot_parts).strip()
    if not webspace_id or not slot:
        return None
    payload: dict[str, Any] = {
        "topic": token,
        "webspace_id": webspace_id,
        "slot": slot,
        "projection": slot,
    }
    if node_id:
        payload["node_id"] = node_id
        payload["target_node_id"] = node_id
        payload["_meta"] = {"webspace_id": webspace_id, "target_node_id": node_id}
    return payload


def _request_webio_yjs_projection_snapshots(topics: set[str], *, transport: str) -> None:
    for topic in topics:
        parsed = _parse_webio_yjs_projection_topic(topic)
        if not parsed:
            continue
        try:
            payload = dict(parsed)
            payload["transport"] = str(transport or "ws")
            request_snapshot_event(
                "webio.yjs.snapshot.requested",
                payload,
                "events_ws",
                _publish_webio_snapshot_request,
            )
        except Exception:
            _ylog.debug("failed to request webio yjs projection snapshot topic=%s", topic, exc_info=True)


def _publish_webio_yjs_projection_subscription_change(
    topics: set[str],
    *,
    action: str,
    transport: str,
    connection_id: str | None = None,
) -> None:
    for topic in topics:
        parsed = _parse_webio_yjs_projection_topic(topic)
        if not parsed:
            continue
        try:
            payload = dict(parsed)
            payload["transport"] = str(transport or "ws")
            payload["action"] = str(action or "").strip() or "subscribed"
            if connection_id:
                payload["connection_id"] = str(connection_id)
                payload["subscription_id"] = f"{transport}:{connection_id}:{payload['topic']}"
            try:
                from adaos.sdk.data.projections import record_projection_subscription_change

                record_projection_subscription_change(payload)
            except Exception:
                _ylog.debug("failed to record webio yjs projection demand topic=%s", topic, exc_info=True)
            if _should_drop_duplicate_webio_control_event("webio.yjs.subscription.changed", payload):
                continue
            ctx = get_agent_ctx()
            ctx.bus.publish(
                DomainEvent(
                    type="webio.yjs.subscription.changed",
                    payload=payload,
                    source="events_ws",
                    ts=time.time(),
                )
            )
        except Exception:
            _ylog.debug("failed to publish webio yjs projection subscription change topic=%s", topic, exc_info=True)


async def _send_initial_ws_event_messages(websocket: WebSocket, topics: set[str]) -> None:
    for message in _iter_initial_ws_event_messages(topics):
        try:
            await _send_ws_event_message(websocket, message)
        except (WebSocketDisconnect, RuntimeError):
            return


def _ensure_ws_event_forwarder() -> None:
    global _WS_EVENT_FORWARDER_INSTALLED
    with _WS_EVENT_SUBSCRIPTIONS_LOCK:
        if _WS_EVENT_FORWARDER_INSTALLED:
            return
        ctx = get_agent_ctx()
        ctx.bus.subscribe("*", _forward_ws_bus_event)
        _WS_EVENT_FORWARDER_INSTALLED = True


def _register_ws_event_subscriptions(
    websocket: WebSocket,
    loop: asyncio.AbstractEventLoop,
    raw_topics: Any,
) -> set[str]:
    topics = _normalize_ws_event_topics(raw_topics)
    if not topics:
        return set()
    _ensure_ws_event_forwarder()
    with _WS_EVENT_SUBSCRIPTIONS_LOCK:
        entry = _WS_EVENT_SUBSCRIBERS.setdefault(
            id(websocket),
            {
                "websocket": websocket,
                "loop": loop,
                "topics": set(),
            },
        )
        entry["loop"] = loop
        tracked = entry.setdefault("topics", set())
        added = set(topics) - set(tracked)
        tracked.update(topics)
    if added:
        _publish_webio_stream_subscription_change(
            added,
            action="subscribed",
            transport="ws",
            connection_id=str(id(websocket)),
        )
        _publish_webio_yjs_projection_subscription_change(
            added,
            action="subscribed",
            transport="ws",
            connection_id=str(id(websocket)),
        )
    return added


def _unregister_ws_event_subscriptions(websocket: WebSocket) -> None:
    with _WS_EVENT_SUBSCRIPTIONS_LOCK:
        entry = _WS_EVENT_SUBSCRIBERS.pop(id(websocket), None)
    _drop_ws_event_send_state(websocket)
    topics = set(entry.get("topics") or []) if isinstance(entry, dict) else set()
    if topics:
        _publish_webio_stream_subscription_change(
            topics,
            action="unsubscribed",
            transport="ws",
            connection_id=str(id(websocket)),
        )
        _publish_webio_yjs_projection_subscription_change(
            topics,
            action="unsubscribed",
            transport="ws",
            connection_id=str(id(websocket)),
        )


def _unregister_ws_event_subscription_topics(websocket: WebSocket, raw_topics: Any) -> set[str]:
    topics = _normalize_ws_event_topics(raw_topics)
    if not topics:
        return set()
    with _WS_EVENT_SUBSCRIPTIONS_LOCK:
        entry = _WS_EVENT_SUBSCRIBERS.get(id(websocket))
        if not isinstance(entry, dict):
            return set()
        tracked = entry.setdefault("topics", set())
        removed = set(topics) & set(tracked)
        tracked.difference_update(removed)
        if not tracked:
            _WS_EVENT_SUBSCRIBERS.pop(id(websocket), None)
            _drop_ws_event_send_state(websocket)
    if removed:
        _publish_webio_stream_subscription_change(
            removed,
            action="unsubscribed",
            transport="ws",
            connection_id=str(id(websocket)),
        )
        _publish_webio_yjs_projection_subscription_change(
            removed,
            action="unsubscribed",
            transport="ws",
            connection_id=str(id(websocket)),
        )
    return removed


def _forward_ws_bus_event(ev: DomainEvent) -> None:
    event_type = str(getattr(ev, "type", "") or "").strip()
    if not event_type:
        return
    with _WS_EVENT_SUBSCRIPTIONS_LOCK:
        subscribers = [
            dict(entry)
            for entry in _WS_EVENT_SUBSCRIBERS.values()
            if any(_ws_event_topic_matches(topic, event_type) for topic in entry.get("topics", set()))
        ]
    if not subscribers:
        return
    message = _build_ws_event_message(
        event_type,
        getattr(ev, "payload", {}) or {},
        source=str(getattr(ev, "source", "") or "events_ws"),
        ts=float(getattr(ev, "ts", 0.0) or time.time()),
    )
    for entry in subscribers:
        websocket = entry.get("websocket")
        loop = entry.get("loop")
        if websocket is None or not isinstance(loop, asyncio.AbstractEventLoop):
            continue
        try:
            loop.call_soon_threadsafe(_enqueue_ws_event_message, websocket, message)
        except Exception:
            _unregister_ws_event_subscriptions(websocket)


def _track_yws_connection(webspace_id: str, websocket: WebSocket, *, device_id: str | None = None) -> None:
    key = str(webspace_id or "").strip() or "default"
    client_key = _websocket_yws_client_limit_key(websocket, fallback_device_id=device_id)
    _cancel_idle_room_reset(key)
    with _ACTIVE_YWS_LOCK:
        items = _ACTIVE_YWS_CONNECTIONS.setdefault(key, [])
        if websocket not in items:
            items.append(websocket)
        clients = _ACTIVE_YWS_CLIENTS.setdefault(key, {})
        clients[client_key] = int(clients.get(client_key) or 0) + 1


def _track_events_ws_connection(webspace_id: str, websocket: WebSocket) -> None:
    key = str(webspace_id or "").strip() or "default"
    connection_key = id(websocket)
    with _ACTIVE_EVENTS_WS_LOCK:
        previous = _ACTIVE_EVENTS_WS_WEBSPACES.get(connection_key)
        _ACTIVE_EVENTS_WS_WEBSPACES[connection_key] = key
    _cancel_idle_room_reset(key)
    if previous and previous != key and not _webspace_has_live_transports(previous):
        _schedule_idle_room_reset(previous)


def _untrack_events_ws_connection(websocket: WebSocket) -> None:
    with _ACTIVE_EVENTS_WS_LOCK:
        key = _ACTIVE_EVENTS_WS_WEBSPACES.pop(id(websocket), None)
    if key and not _webspace_has_live_transports(key):
        _schedule_idle_room_reset(key)


def _next_yws_attempt_id(webspace_id: str, dev_id: str) -> str:
    global _YWS_ATTEMPT_SEQ
    now = time.time()
    with _YWS_ATTEMPT_LOCK:
        _YWS_ATTEMPT_SEQ += 1
        seq = _YWS_ATTEMPT_SEQ
        attempt_id = f"yws-{int(now * 1000):x}-{seq:x}"
        _YWS_ATTEMPT_DIAG.update(
            {
                "last_attempt_id": attempt_id,
                "last_attempt_at": now,
                "last_attempt_webspace_id": str(webspace_id or "").strip() or "default",
                "last_attempt_dev_id": str(dev_id or "").strip() or "unknown",
            }
        )
    return attempt_id


def _remember_yws_attempt(
    attempt_id: str,
    state: str,
    *,
    close_code: int | None = None,
    close_reason: str | None = None,
) -> None:
    token = str(attempt_id or "").strip()
    if not token:
        return
    now = time.time()
    with _YWS_ATTEMPT_LOCK:
        if state == "open":
            _YWS_ATTEMPT_DIAG["last_open_attempt_id"] = token
            _YWS_ATTEMPT_DIAG["last_open_at"] = now
        elif state == "closed":
            _YWS_ATTEMPT_DIAG["last_close_attempt_id"] = token
            _YWS_ATTEMPT_DIAG["last_close_at"] = now
            _YWS_ATTEMPT_DIAG["last_close_code"] = close_code
            _YWS_ATTEMPT_DIAG["last_close_reason"] = str(close_reason or "").strip()[:160]
        elif state == "guard_reject":
            _YWS_ATTEMPT_DIAG["last_guard_reject_attempt_id"] = token
        elif state == "room_timeout":
            _YWS_ATTEMPT_DIAG["last_room_timeout_attempt_id"] = token


def _set_websocket_yws_attempt_id(websocket: WebSocket, attempt_id: str) -> None:
    token = str(attempt_id or "").strip()
    if not token:
        return
    try:
        scope = getattr(websocket, "scope", None)
        if isinstance(scope, dict):
            scope["adaos_yws_attempt_id"] = token
    except Exception:
        pass
    try:
        setattr(websocket, "_adaos_yws_attempt_id", token)
    except Exception:
        pass


def _websocket_yws_attempt_id(websocket: WebSocket) -> str:
    try:
        token = str(getattr(websocket, "_adaos_yws_attempt_id", "") or "").strip()
        if token:
            return token
    except Exception:
        pass
    try:
        scope = getattr(websocket, "scope", None)
        if isinstance(scope, dict):
            token = str(scope.get("adaos_yws_attempt_id") or "").strip()
            if token:
                return token
    except Exception:
        pass
    return ""


def _websocket_device_id(websocket: WebSocket) -> str:
    try:
        params = getattr(websocket, "query_params", {}) or {}
        return str(params.get("dev") or "unknown").strip() or "unknown"
    except Exception:
        return "unknown"


def _websocket_browser_session_id(websocket: WebSocket) -> str:
    try:
        params = getattr(websocket, "query_params", {}) or {}
    except Exception:
        params = {}
    return (
        _clean_browser_metadata_value(
            params.get("browser_session_id")
            or params.get("browserSessionId")
            or params.get("client_session_id")
            or params.get("clientSessionId"),
            max_len=128,
        )
        or ""
    )


def _active_yws_connection_total_for_client(
    webspace_id: str,
    dev_id: str,
    *,
    browser_page_id: str | None = None,
    browser_session_id: str | None = None,
    client_attempt_id: str | None = None,
) -> int:
    key = str(webspace_id or "").strip() or "default"
    device_key = str(dev_id or "").strip() or "unknown"
    client_key = _yws_client_limit_key(
        device_key,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id,
    )
    with _ACTIVE_YWS_LOCK:
        clients = _ACTIVE_YWS_CLIENTS.get(key)
        if isinstance(clients, dict):
            if browser_page_id or browser_session_id or client_attempt_id:
                return max(0, int(clients.get(client_key) or 0))
            return sum(
                max(0, int(count or 0))
                for stored_key, count in clients.items()
                if _split_yws_client_limit_key(stored_key)[0] == device_key
            )
        return sum(
            1
            for websocket in list(_ACTIVE_YWS_CONNECTIONS.get(key) or [])
            if (
                _websocket_yws_client_limit_key(websocket, fallback_device_id=device_key) == client_key
                if browser_page_id or browser_session_id or client_attempt_id
                else _websocket_device_id(websocket) == device_key
            )
        )


def _active_yws_connection_total_for_device(dev_id: str) -> int:
    device_key = str(dev_id or "").strip() or "unknown"
    if not device_key or device_key == "unknown":
        return 0
    total = 0
    with _ACTIVE_YWS_LOCK:
        for sockets in _ACTIVE_YWS_CONNECTIONS.values():
            total += sum(1 for websocket in list(sockets or []) if _websocket_device_id(websocket) == device_key)
    return total


def _should_mark_yws_browser_session_offline(dev_id: str) -> bool:
    return _active_yws_connection_total_for_device(dev_id) <= 0


def _active_yws_client_rows() -> list[dict[str, Any]]:
    with _ACTIVE_YWS_LOCK:
        clients = {
            webspace_id: dict(device_counts)
            for webspace_id, device_counts in _ACTIVE_YWS_CLIENTS.items()
            if isinstance(device_counts, dict)
        }
        attempts: dict[str, list[str]] = {}
        for webspace_id, sockets in _ACTIVE_YWS_CONNECTIONS.items():
            for websocket in list(sockets or []):
                client_key = _websocket_yws_client_limit_key(websocket)
                attempt_id = _websocket_yws_attempt_id(websocket)
                if attempt_id:
                    attempts.setdefault(f"{webspace_id}::{client_key}", []).append(attempt_id)
    rows: list[dict[str, Any]] = []
    for webspace_id, device_counts in clients.items():
        for client_key, count in sorted(device_counts.items()):
            device_id, scoped_client_id = _split_yws_client_limit_key(client_key)
            row = {
                "webspace_id": str(webspace_id or "").strip() or "default",
                "dev_id": str(device_id or "").strip() or "unknown",
                "session_count": max(0, int(count or 0)),
            }
            if scoped_client_id:
                row["client_limit_id"] = scoped_client_id
            attempt_ids = attempts.get(f"{webspace_id}::{client_key}") or []
            if attempt_ids:
                row["attempt_ids"] = attempt_ids[:3]
                row["latest_attempt_id"] = attempt_ids[-1]
            rows.append(row)
    rows.sort(key=lambda item: (-int(item.get("session_count") or 0), str(item.get("dev_id") or "")))
    return rows


async def _close_existing_yws_client_connections(
    webspace_id: str,
    dev_id: str,
    *,
    browser_page_id: str | None = None,
    browser_session_id: str | None = None,
    client_attempt_id: str | None = None,
) -> int:
    key = str(webspace_id or "").strip() or "default"
    device_key = str(dev_id or "").strip() or "unknown"
    if not device_key or device_key == "unknown":
        return 0
    client_key = _yws_client_limit_key(
        device_key,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id,
    )
    with _ACTIVE_YWS_LOCK:
        sockets = [
            websocket
            for websocket in list(_ACTIVE_YWS_CONNECTIONS.get(key) or [])
            if (
                _websocket_yws_client_limit_key(websocket, fallback_device_id=device_key) == client_key
                if browser_page_id or browser_session_id or client_attempt_id
                else _websocket_device_id(websocket) == device_key
            )
        ]
    scoped_client = bool(browser_page_id or browser_session_id or client_attempt_id)
    replace_existing = scoped_client and bool(_YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS)
    overflow = len(sockets) if replace_existing else len(sockets) - _YWS_MAX_ACTIVE_PER_CLIENT + 1
    if overflow <= 0:
        return 0
    closed = 0
    for websocket in sockets[:overflow]:
        close_ok = False
        try:
            await asyncio.wait_for(
                websocket.close(code=1012, reason="replaced_by_new_yws_session"),
                timeout=0.5,
            )
            close_ok = True
            closed += 1
        except Exception:
            pass
        if close_ok:
            try:
                _untrack_yws_connection(key, websocket)
            except Exception:
                pass
    if closed:
        _YWS_GUARD_DIAG["last_replaced_at"] = time.time()
        _YWS_GUARD_DIAG["last_replaced_webspace_id"] = key
        _YWS_GUARD_DIAG["last_replaced_dev_id"] = device_key
        _YWS_GUARD_DIAG["replaced_total"] = int(_YWS_GUARD_DIAG.get("replaced_total") or 0) + closed
        if replace_existing:
            _YWS_GUARD_DIAG["scoped_replaced_total"] = int(_YWS_GUARD_DIAG.get("scoped_replaced_total") or 0) + closed
        _ylog.warning(
            "yws guard replaced stale client sessions webspace=%s dev=%s closed=%s max_active_per_client=%s scoped=%s",
            key,
            device_key,
            closed,
            _YWS_MAX_ACTIVE_PER_CLIENT,
            replace_existing,
        )
        await asyncio.sleep(0)
    return closed


async def close_browser_yws_connections(
    token: str,
    *,
    code: int = 1008,
    reason: str = "browser_access_revoked",
) -> int:
    clean_token = _clean_browser_metadata_value(token, max_len=128)
    if not clean_token:
        return 0
    close_reason = str(reason or "browser_access_revoked").strip()[:120] or "browser_access_revoked"
    with _ACTIVE_YWS_LOCK:
        sockets = [
            (webspace_id, websocket)
            for webspace_id, webspace_sockets in _ACTIVE_YWS_CONNECTIONS.items()
            for websocket in list(webspace_sockets or [])
            if _websocket_device_id(websocket) == clean_token
            or _websocket_browser_session_id(websocket) == clean_token
        ]
    closed = 0
    for webspace_id, websocket in sockets:
        close_ok = False
        try:
            await asyncio.wait_for(websocket.close(code=code, reason=close_reason), timeout=0.5)
            close_ok = True
            closed += 1
        except Exception:
            pass
        if close_ok:
            try:
                _untrack_yws_connection(webspace_id, websocket)
            except Exception:
                pass
    if closed:
        _ylog.info("closed browser yws connections token=%s closed=%s reason=%s", clean_token, closed, close_reason)
        await asyncio.sleep(0)
    return closed


def request_close_browser_yws_connections(
    token: str,
    *,
    code: int = 1008,
    reason: str = "browser_access_revoked",
) -> int:
    clean_token = _clean_browser_metadata_value(token, max_len=128)
    if not clean_token:
        return 0
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        try:
            return int(asyncio.run(close_browser_yws_connections(clean_token, code=code, reason=reason)) or 0)
        except Exception:
            _ylog.exception("failed to close browser yws connections token=%s", clean_token)
            return 0
    try:
        loop.create_task(close_browser_yws_connections(clean_token, code=code, reason=reason))
    except Exception:
        _ylog.exception("failed to schedule browser yws disconnect token=%s", clean_token)
    return 0


def _record_yws_open(webspace_id: str, dev_id: str) -> None:
    now = time.time()
    key = _yws_guard_client_history_key(webspace_id, dev_id)
    with _YWS_STORM_LOCK:
        _YWS_OPEN_HISTORY.append(now)
        items = _YWS_CLIENT_OPEN_HISTORY.setdefault(key, deque(maxlen=64))
        items.append(now)
        cutoff = now - 60.0
        stale_keys: list[str] = []
        for client_key, queue in _YWS_CLIENT_OPEN_HISTORY.items():
            while queue and queue[0] < cutoff:
                queue.popleft()
            if not queue:
                stale_keys.append(client_key)
        for client_key in stale_keys:
            _YWS_CLIENT_OPEN_HISTORY.pop(client_key, None)
        recent_15s = sum(1 for ts in items if ts >= now - 15.0)
    if recent_15s >= 8:
        _ylog.warning(
            "yws reconnect storm detected webspace=%s dev=%s opens_15s=%s",
            str(webspace_id or "").strip() or "default",
            str(dev_id or "").strip() or "unknown",
            recent_15s,
        )


def _record_yws_guard_attempt(
    webspace_id: str,
    dev_id: str,
    *,
    browser_page_id: str | None = None,
    browser_session_id: str | None = None,
    client_attempt_id: str | None = None,
) -> None:
    now = time.time()
    planned_transition = _yws_guard_planned_transition_snapshot(now_ts=now)
    if bool(planned_transition.get("suppress_reconnect_guard")):
        with _YWS_STORM_LOCK:
            _YWS_GUARD_DIAG["planned_transition_attempt_ignored_total"] = int(
                _YWS_GUARD_DIAG.get("planned_transition_attempt_ignored_total") or 0
            ) + 1
            _YWS_GUARD_DIAG["last_planned_transition_attempt_ignored_at"] = now
            _YWS_GUARD_DIAG["last_planned_transition_marker"] = str(
                planned_transition.get("marker") or ""
            )
        return
    key = _yws_guard_client_history_key(
        webspace_id,
        dev_id,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id,
    )
    with _YWS_STORM_LOCK:
        _YWS_ATTEMPT_HISTORY.append(now)
        items = _YWS_CLIENT_ATTEMPT_HISTORY.setdefault(key, deque(maxlen=128))
        items.append(now)
        cutoff = now - 60.0
        stale_keys: list[str] = []
        for client_key, queue in _YWS_CLIENT_ATTEMPT_HISTORY.items():
            while queue and queue[0] < cutoff:
                queue.popleft()
            if not queue:
                stale_keys.append(client_key)
        for client_key in stale_keys:
            _YWS_CLIENT_ATTEMPT_HISTORY.pop(client_key, None)


def _record_yws_short_session(
    webspace_id: str,
    dev_id: str,
    *,
    lifetime_s: float,
    browser_page_id: str | None = None,
    browser_session_id: str | None = None,
    client_attempt_id: str | None = None,
) -> None:
    if _YWS_GUARD_MIN_STABLE_SESSION_S <= 0.0:
        return
    if lifetime_s >= _YWS_GUARD_MIN_STABLE_SESSION_S:
        return
    now = time.time()
    if bool(_yws_guard_planned_transition_snapshot(now_ts=now).get("suppress_reconnect_guard")):
        return
    key = _yws_guard_client_history_key(
        webspace_id,
        dev_id,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id,
    )
    with _YWS_STORM_LOCK:
        items = _YWS_CLIENT_SHORT_SESSION_HISTORY.setdefault(key, deque(maxlen=64))
        items.append(now)
        cutoff = now - max(1.0, float(_YWS_GUARD_SHORT_SESSION_WINDOW_S))
        stale_keys: list[str] = []
        for client_key, queue in _YWS_CLIENT_SHORT_SESSION_HISTORY.items():
            while queue and queue[0] < cutoff:
                queue.popleft()
            if not queue:
                stale_keys.append(client_key)
        for client_key in stale_keys:
            _YWS_CLIENT_SHORT_SESSION_HISTORY.pop(client_key, None)
        recent = sum(1 for ts in items if ts >= cutoff)
        _YWS_GUARD_DIAG["last_short_session_at"] = now
        _YWS_GUARD_DIAG["last_short_session_webspace_id"] = str(webspace_id or "").strip() or "default"
        _YWS_GUARD_DIAG["last_short_session_dev_id"] = str(dev_id or "").strip() or "unknown"
        _YWS_GUARD_DIAG["last_short_session_lifetime_s"] = round(max(0.0, lifetime_s), 3)
        _YWS_GUARD_DIAG["last_short_session_recent"] = recent


def _yws_guard_quarantine_key(webspace_id: str, dev_id: str | None = None) -> str:
    webspace_key = str(webspace_id or "").strip() or "default"
    dev_key = str(dev_id or "").strip() or "*"
    return f"{webspace_key}::{dev_key}"


def _yws_guard_client_history_key(
    webspace_id: str,
    dev_id: str,
    *,
    browser_page_id: str | None = None,
    browser_session_id: str | None = None,
    client_attempt_id: str | None = None,
) -> str:
    webspace_key = str(webspace_id or "").strip() or "default"
    device_key = str(dev_id or "").strip() or "unknown"
    client_key = _yws_client_limit_key(
        device_key,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id,
    )
    return f"{webspace_key}::{client_key}"


def clear_yws_guard_state_for_webspace(
    webspace_id: str,
    *,
    reason: str = "manual_webspace_recovery",
) -> dict[str, Any]:
    """Clear reconnect-storm backoff for an operator-triggered webspace recovery."""

    key = str(webspace_id or "").strip() or "default"
    now = time.time()
    history_prefix = f"{key}::"
    log_prefix = f"{key}:"

    def _drop_prefixed(mapping: dict[str, Any], prefix: str) -> int:
        removed = 0
        for item_key in list(mapping.keys()):
            if str(item_key or "").startswith(prefix):
                mapping.pop(item_key, None)
                removed += 1
        return removed

    with _YWS_STORM_LOCK:
        client_open_history_cleared = _drop_prefixed(_YWS_CLIENT_OPEN_HISTORY, history_prefix)
        client_attempt_history_cleared = _drop_prefixed(_YWS_CLIENT_ATTEMPT_HISTORY, history_prefix)
        client_short_session_history_cleared = _drop_prefixed(_YWS_CLIENT_SHORT_SESSION_HISTORY, history_prefix)
        quarantine_cleared = _drop_prefixed(_YWS_GUARD_QUARANTINE_UNTIL, history_prefix)
        recovery_in_flight_cleared = _drop_prefixed(_YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL, history_prefix)
        incident_cleared = _drop_prefixed(_YWS_GUARD_INCIDENTS, history_prefix)
        log_cleared = _drop_prefixed(_YWS_GUARD_LAST_LOG_AT, log_prefix)
        notify_cleared = _drop_prefixed(_YWS_GUARD_LAST_NOTIFY_AT, log_prefix)
        cleared_total = (
            client_open_history_cleared
            + client_attempt_history_cleared
            + client_short_session_history_cleared
            + quarantine_cleared
            + recovery_in_flight_cleared
            + incident_cleared
            + log_cleared
            + notify_cleared
        )
        _YWS_GUARD_DIAG["manual_reset_total"] = int(_YWS_GUARD_DIAG.get("manual_reset_total") or 0) + 1
        _YWS_GUARD_DIAG["last_manual_reset_at"] = now
        _YWS_GUARD_DIAG["last_manual_reset_webspace_id"] = key
        _YWS_GUARD_DIAG["last_manual_reset_reason"] = str(reason or "").strip() or "manual_webspace_recovery"
        _YWS_GUARD_DIAG["last_manual_reset_cleared_total"] = cleared_total
        _YWS_GUARD_DIAG["last_manual_reset_quarantine_cleared"] = quarantine_cleared

    result = {
        "ok": True,
        "webspace_id": key,
        "reason": str(reason or "").strip() or "manual_webspace_recovery",
        "cleared_total": cleared_total,
        "client_open_history_cleared": client_open_history_cleared,
        "client_attempt_history_cleared": client_attempt_history_cleared,
        "client_short_session_history_cleared": client_short_session_history_cleared,
        "quarantine_cleared": quarantine_cleared,
        "recovery_in_flight_cleared": recovery_in_flight_cleared,
        "incident_cleared": incident_cleared,
        "log_cleared": log_cleared,
        "notify_cleared": notify_cleared,
    }
    if cleared_total:
        _ylog.warning(
            "cleared YWS guard recovery state webspace=%s reason=%s cleared_total=%s quarantine=%s attempts=%s short_sessions=%s",
            key,
            result["reason"],
            cleared_total,
            quarantine_cleared,
            client_attempt_history_cleared,
            client_short_session_history_cleared,
        )
    else:
        _ylog.info(
            "YWS guard recovery state already clear webspace=%s reason=%s",
            key,
            result["reason"],
        )
    return result


def _set_yws_guard_quarantine_locked(key: str, now: float) -> tuple[float, float, int]:
    incident = _YWS_GUARD_INCIDENTS.get(key) or {}
    last_at = float(incident.get("last_at") or 0.0)
    count = int(incident.get("count") or 0)
    if last_at <= 0.0 or now - last_at > _YWS_GUARD_ESCALATION_WINDOW_S:
        count = 0
    count += 1
    base_ttl = max(0.0, float(_YWS_GUARD_COOLDOWN_S))
    max_ttl = max(base_ttl, float(_YWS_GUARD_MAX_COOLDOWN_S))
    ttl = min(max_ttl, base_ttl * float(2 ** max(0, count - 1))) if base_ttl > 0.0 else 0.0
    until = now + ttl
    _YWS_GUARD_INCIDENTS[key] = {
        "count": float(count),
        "last_at": now,
        "last_ttl_s": ttl,
        "until": until,
    }
    _YWS_GUARD_QUARANTINE_UNTIL[key] = until
    _YWS_GUARD_DIAG["last_quarantine_ttl_s"] = ttl
    _YWS_GUARD_DIAG["last_quarantine_incident_count"] = count
    return until, ttl, count


def _yws_guard_log(
    *,
    webspace_id: str,
    dev_id: str,
    reason: str,
    active_total: int,
    recent_10s: int,
    client_15s: int,
    cooldown_s: float | None = None,
    incident_count: int | None = None,
) -> None:
    now = time.time()
    log_key = f"{webspace_id}:{dev_id}:{reason}"
    with _YWS_STORM_LOCK:
        last = float(_YWS_GUARD_LAST_LOG_AT.get(log_key) or 0.0)
        if now - last < 5.0:
            return
        _YWS_GUARD_LAST_LOG_AT[log_key] = now
    _ylog.warning(
        "yws guard rejected connection webspace=%s dev=%s reason=%s active=%s recent_open_10s=%s client_open_15s=%s cooldown_s=%.1f incident=%s",
        webspace_id,
        dev_id,
        reason,
        active_total,
        recent_10s,
        client_15s,
        float(cooldown_s if cooldown_s is not None else _YWS_GUARD_COOLDOWN_S),
        incident_count,
    )


def _yws_guard_should_notify(*, webspace_id: str, dev_id: str, reason: str) -> bool:
    now = time.time()
    notify_key = f"{str(webspace_id or '').strip() or 'default'}:{str(dev_id or '').strip() or 'unknown'}:{str(reason or '').strip() or 'guard'}"
    with _YWS_STORM_LOCK:
        last = float(_YWS_GUARD_LAST_NOTIFY_AT.get(notify_key) or 0.0)
        if now - last < _YWS_GUARD_NOTIFY_INTERVAL_S:
            return False
        _YWS_GUARD_LAST_NOTIFY_AT[notify_key] = now
    return True


def _yws_guard_reject_hold_seconds(reason: str, diag: dict[str, Any] | None) -> float:
    reason_token = str(reason or "").strip().lower()
    if reason_token not in {
        "client_reconnect_storm",
        "client_reconnect_backoff",
        "client_recovery_in_progress",
        "client_short_session_storm",
        "webspace_reconnect_storm",
        "webspace_reconnect_backoff",
    }:
        return 0.0
    max_hold_s = max(0.0, float(_YWS_GUARD_REJECT_HOLD_MAX_SEC))
    if max_hold_s <= 0.0:
        return 0.0
    try:
        quarantine_ttl_s = float((diag or {}).get("quarantine_ttl_s") or 0.0)
    except Exception:
        quarantine_ttl_s = 0.0
    if quarantine_ttl_s <= 0.0:
        return 0.0
    return max(0.0, min(max_hold_s, quarantine_ttl_s))


async def _hold_yws_guard_reject(
    websocket: WebSocket,
    *,
    webspace_id: str,
    dev_id: str,
    attempt_id: str,
    client_attempt_id: str | None,
    guard_reason: str,
    guard_diag: dict[str, Any] | None,
) -> bool:
    hold_s = _yws_guard_reject_hold_seconds(guard_reason, guard_diag)
    if hold_s <= 0.0:
        return True
    now = time.time()
    with _YWS_STORM_LOCK:
        _YWS_GUARD_DIAG["reject_hold_total"] = int(_YWS_GUARD_DIAG.get("reject_hold_total") or 0) + 1
        _YWS_GUARD_DIAG["last_reject_hold_at"] = now
        _YWS_GUARD_DIAG["last_reject_hold_reason"] = guard_reason
        _YWS_GUARD_DIAG["last_reject_hold_seconds"] = hold_s
        _YWS_GUARD_DIAG["last_reject_hold_attempt_id"] = attempt_id
    _ylog.debug(
        "yws guard holding rejected connection webspace=%s dev=%s attempt=%s client_attempt=%s reason=%s hold_s=%.1f",
        webspace_id,
        dev_id,
        attempt_id,
        client_attempt_id or None,
        guard_reason,
        hold_s,
    )
    deadline = time.monotonic() + hold_s
    while True:
        remaining_s = deadline - time.monotonic()
        if remaining_s <= 0.0:
            return True
        step_s = min(max(0.05, float(_YWS_GUARD_REJECT_HOLD_STEP_SEC)), remaining_s)
        try:
            message = await asyncio.wait_for(websocket.receive(), timeout=step_s)
        except asyncio.TimeoutError:
            continue
        except (WebSocketDisconnect, RuntimeError):
            return False
        except Exception:
            _ylog.debug(
                "yws guard hold ended by receive error webspace=%s dev=%s attempt=%s",
                webspace_id,
                dev_id,
                attempt_id,
                exc_info=True,
            )
            return False
        if isinstance(message, dict) and message.get("type") == "websocket.disconnect":
            return False


async def _reject_yws_guard_connection(
    websocket: WebSocket,
    *,
    webspace_id: str,
    dev_id: str,
    browser_metadata: dict[str, Any],
    attempt_id: str,
    client_attempt_id: str | None,
    guard_reason: str,
    guard_diag: dict[str, Any],
) -> None:
    state_token = f"yws_guard_{guard_reason}"
    _remember_yws_attempt(attempt_id, "guard_reject")
    _ylog.debug(
        "yws guard rejected connection webspace=%s dev=%s attempt=%s client_attempt=%s reason=%s active=%s recent_open_10s=%s client_open_15s=%s",
        webspace_id,
        dev_id,
        attempt_id,
        client_attempt_id or None,
        guard_reason,
        guard_diag.get("active_total"),
        guard_diag.get("recent_open_10s"),
        guard_diag.get("client_open_15s"),
    )
    if _yws_guard_should_notify(webspace_id=webspace_id, dev_id=dev_id, reason=guard_reason):
        try:
            from adaos.services.access_links import touch_browser_session

            await asyncio.to_thread(
                touch_browser_session,
                dev_id,
                webspace_id=webspace_id,
                connection_state=state_token,
                online=False,
                **browser_metadata,
            )
        except Exception:
            _ylog.debug("browser access registry guard update failed webspace=%s dev=%s", webspace_id, dev_id, exc_info=True)
        _publish_runtime_event(
            "browser.session.changed",
            {
                "device_id": dev_id,
                "webspace_id": webspace_id,
                "connection_state": state_token,
                "yjs_channel_state": "rejected",
                "yjs_attempt_id": attempt_id,
                "client_yws_attempt_id": client_attempt_id or None,
                "reason": guard_reason,
                "active_yws": guard_diag.get("active_total"),
                "recent_open_10s": guard_diag.get("recent_open_10s"),
                "client_open_15s": guard_diag.get("client_open_15s"),
                "source": "yws.gateway.guard",
            },
        )
    try:
        should_close = await _hold_yws_guard_reject(
            websocket,
            webspace_id=webspace_id,
            dev_id=dev_id,
            attempt_id=attempt_id,
            client_attempt_id=client_attempt_id or None,
            guard_reason=guard_reason,
            guard_diag=guard_diag,
        )
        if should_close:
            await websocket.close(code=1013, reason=state_token[:120])
            _remember_yws_attempt(attempt_id, "closed", close_code=1013, close_reason=state_token[:120])
        else:
            _remember_yws_attempt(attempt_id, "closed", close_reason="guard_reject_peer_disconnected")
    except Exception:
        pass


def _yws_client_recent_open_counts_locked(webspace_key: str, now: float) -> tuple[int, int]:
    recent_10s = 0
    distinct_clients_10s = 0
    for client_key, queue in _YWS_CLIENT_ATTEMPT_HISTORY.items():
        client_webspace, _, _client_dev = str(client_key or "").partition("::")
        if (client_webspace or "default") != webspace_key:
            continue
        client_recent_10s = sum(1 for ts in queue if ts >= now - 10.0)
        if client_recent_10s <= 0:
            continue
        recent_10s += client_recent_10s
        distinct_clients_10s += 1
    return recent_10s, distinct_clients_10s


def _yws_guard_note_client_storm(
    *,
    webspace_id: str,
    dev_id: str,
    active_total: int,
    client_15s: int,
    webspace_recent_10s: int,
    webspace_distinct_clients_10s: int,
) -> None:
    now = time.time()
    with _YWS_STORM_LOCK:
        _YWS_GUARD_DIAG["client_reconnect_storm_observed_total"] = int(
            _YWS_GUARD_DIAG.get("client_reconnect_storm_observed_total") or 0
        ) + 1
        _YWS_GUARD_DIAG["last_client_reconnect_storm_at"] = now
        _YWS_GUARD_DIAG["last_client_reconnect_storm_webspace_id"] = webspace_id
        _YWS_GUARD_DIAG["last_client_reconnect_storm_dev_id"] = dev_id
        _YWS_GUARD_DIAG["last_client_reconnect_storm_open_15s"] = client_15s
        _YWS_GUARD_DIAG["last_webspace_recent_open_10s"] = webspace_recent_10s
        _YWS_GUARD_DIAG["last_webspace_distinct_clients_10s"] = webspace_distinct_clients_10s
        log_key = f"{webspace_id}:{dev_id}:client_reconnect_storm_observed"
        last = float(_YWS_GUARD_LAST_LOG_AT.get(log_key) or 0.0)
        if now - last < 5.0:
            return
        _YWS_GUARD_LAST_LOG_AT[log_key] = now
    _ylog.warning(
        "yws guard observed client reconnect storm webspace=%s dev=%s action=observed active=%s client_open_15s=%s webspace_open_10s=%s webspace_clients_10s=%s",
        webspace_id,
        dev_id,
        active_total,
        client_15s,
        webspace_recent_10s,
        webspace_distinct_clients_10s,
    )


def _yws_guard_note_webspace_storm(
    *,
    webspace_id: str,
    dev_id: str,
    active_total: int,
    recent_10s: int,
    client_15s: int,
    webspace_distinct_clients_10s: int,
) -> None:
    now = time.time()
    with _YWS_STORM_LOCK:
        _YWS_GUARD_DIAG["webspace_reconnect_storm_observed_total"] = int(
            _YWS_GUARD_DIAG.get("webspace_reconnect_storm_observed_total") or 0
        ) + 1
        _YWS_GUARD_DIAG["last_webspace_reconnect_storm_at"] = now
        _YWS_GUARD_DIAG["last_webspace_reconnect_storm_webspace_id"] = webspace_id
        _YWS_GUARD_DIAG["last_webspace_reconnect_storm_dev_id"] = dev_id
        _YWS_GUARD_DIAG["last_webspace_reconnect_storm_recent_open_10s"] = recent_10s
        _YWS_GUARD_DIAG["last_webspace_reconnect_storm_clients_10s"] = webspace_distinct_clients_10s
        log_key = f"{webspace_id}:*:webspace_reconnect_storm_observed"
        last = float(_YWS_GUARD_LAST_LOG_AT.get(log_key) or 0.0)
        if now - last < 5.0:
            return
        _YWS_GUARD_LAST_LOG_AT[log_key] = now
    _ylog.warning(
        "yws guard observed webspace reconnect storm webspace=%s dev=%s action=observed active=%s recent_open_10s=%s client_open_15s=%s webspace_clients_10s=%s",
        webspace_id,
        dev_id,
        active_total,
        recent_10s,
        client_15s,
        webspace_distinct_clients_10s,
    )


def _float_or_none(value: Any) -> float | None:
    try:
        number = float(value)
    except Exception:
        return None
    if number <= 0.0:
        return None
    return number


def _yws_guard_planned_transition_snapshot(*, now_ts: float | None = None) -> dict[str, Any]:
    """Identify the bounded reconnect window created by an AdaOS rollout.

    A warm switch and the following root-supervisor restart close several
    browser sockets at once.  Those reconnects are expected and must not be
    classified as an independent multi-client attack.  The exemption is
    bounded by transition age/completion grace and admission still requires a
    healthy route (checked by the caller) plus the normal active-session cap.
    """

    now = time.time() if now_ts is None else float(now_ts)
    try:
        from adaos.services.core_update import read_status as _read_core_update_status

        status = _read_core_update_status() or {}
    except Exception as exc:
        return {
            "active": False,
            "recently_completed": False,
            "suppress_reconnect_guard": False,
            "reason": "update_status_unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }

    state = str(status.get("state") or "").strip().lower()
    phase = str(status.get("phase") or "").strip().lower()
    updated_at = _float_or_none(status.get("updated_at"))
    status_age_s = max(0.0, now - updated_at) if updated_at is not None else None
    active_transition = bool(
        state in {"countdown", "draining", "stopping", "restarting", "applying"}
        or phase in {
            "drain",
            "shutdown",
            "launch",
            "root_promotion_pending",
            "root_promoted",
        }
    )
    active = bool(
        active_transition
        and (status_age_s is None or status_age_s <= float(_YWS_GUARD_PLANNED_TRANSITION_MAX_AGE_S))
    )
    completion_at = max(
        [
            value
            for value in (
                _float_or_none(status.get("root_restart_completed_at")),
                _float_or_none(status.get("validated_at")),
                _float_or_none(status.get("finished_at")),
            )
            if value is not None
        ]
        or [0.0]
    )
    completion_age_s = max(0.0, now - completion_at) if completion_at > 0.0 else None
    recently_completed = bool(
        state in {"succeeded", "success"}
        and completion_age_s is not None
        and completion_age_s <= float(_YWS_GUARD_PLANNED_TRANSITION_GRACE_S)
    )
    marker = "|".join(
        token
        for token in (
            str(status.get("target_version") or "").strip(),
            state,
            phase,
            str(int(completion_at)) if completion_at > 0.0 else "",
        )
        if token
    )
    return {
        "active": active,
        "recently_completed": recently_completed,
        "suppress_reconnect_guard": bool(active or recently_completed),
        "reason": "planned_transition_active" if active else (
            "planned_transition_completion_grace" if recently_completed else "no_planned_transition"
        ),
        "state": state or None,
        "phase": phase or None,
        "status_age_s": round(status_age_s, 3) if status_age_s is not None else None,
        "completion_age_s": round(completion_age_s, 3) if completion_age_s is not None else None,
        "marker": marker,
    }


def _yws_guard_route_dependency_snapshot(*, now_ts: float | None = None) -> dict[str, Any]:
    """Return whether route semantics are healthy enough to permit a YWS rescue."""
    now = time.time() if now_ts is None else float(now_ts)
    if not _YWS_GUARD_ROUTE_DEPENDENCY_RECOVERY:
        return {"ready": False, "reason": "route_dependency_recovery_disabled"}
    try:
        from adaos.services.reliability import hub_root_protocol_snapshot, runtime_signal_snapshot

        signals = runtime_signal_snapshot()
        protocol = hub_root_protocol_snapshot(now_ts=now)
    except Exception as exc:
        return {
            "ready": False,
            "reason": "route_dependency_unavailable",
            "error": f"{type(exc).__name__}: {exc}",
        }

    route_signal = signals.get("route") if isinstance(signals.get("route"), dict) else {}
    route_status = str(route_signal.get("status") or "").strip().lower()
    route_details = route_signal.get("details") if isinstance(route_signal.get("details"), dict) else {}
    route_runtime = protocol.get("route_runtime") if isinstance(protocol.get("route_runtime"), dict) else {}
    assessment = protocol.get("assessment") if isinstance(protocol.get("assessment"), dict) else {}
    flows = route_runtime.get("flows") if isinstance(route_runtime.get("flows"), dict) else {}
    control_flow = flows.get("control") if isinstance(flows.get("control"), dict) else {}
    frame_flow = flows.get("frame") if isinstance(flows.get("frame"), dict) else {}

    def _int(value: Any) -> int:
        try:
            return int(value or 0)
        except Exception:
            return 0

    probe_reply_at = _float_or_none(route_details.get("last_http_probe_reply_at"))
    probe_rx_at = _float_or_none(route_details.get("last_http_probe_rx_at"))
    probe_age_s: float | None = None
    fresh_probe = False
    if probe_reply_at is not None:
        probe_age_s = max(0.0, now - probe_reply_at)
        fresh_probe = (
            probe_age_s <= float(_YWS_GUARD_ROUTE_PROBE_FRESH_S)
            and (probe_rx_at is None or probe_reply_at + 0.001 >= probe_rx_at)
        )

    pending_tunnels = _int(route_runtime.get("pending_tunnels"))
    pending_events = _int(route_runtime.get("pending_events"))
    pending_chunks = _int(route_runtime.get("pending_chunks"))
    active_tunnels = _int(route_runtime.get("active_tunnels"))
    guardrail_active = bool(route_runtime.get("guardrail_active"))
    assessment_state = str(assessment.get("state") or "").strip().lower()
    control_state = str(control_flow.get("state") or "").strip().lower()
    frame_state = str(frame_flow.get("state") or "").strip().lower()
    frame_event = str(frame_flow.get("last_event") or "").strip().lower()
    frame_reason = str(frame_flow.get("reason") or "").strip().lower()
    frame_error = str(frame_flow.get("last_error") or "").strip().lower()
    frame_degraded_by_sync_shedding = (
        "sync_backpressure" in frame_event
        or "sync_backpressure" in frame_reason
        or frame_error in {"route_sync_backpressure", "route_subnet_sync_backpressure"}
    )

    pressure: list[str] = []
    if guardrail_active:
        pressure.append("route_guardrail_active")
    if pending_tunnels > 0:
        pressure.append("pending_tunnels")
    if pending_events > 0:
        pressure.append("pending_events")
    if pending_chunks > 0:
        pressure.append("pending_chunks")
    if control_state in {"pressure", "degraded"}:
        pressure.append(f"control_{control_state}")
    if frame_state in {"pressure", "degraded"} and not frame_degraded_by_sync_shedding:
        pressure.append(f"frame_{frame_state}")

    ready = False
    reason = "route_signal_not_ready"
    if fresh_probe:
        ready = not pressure
        reason = "fresh_lightweight_route_probe" if ready else "fresh_probe_with_route_pressure"
    elif route_status == "ready":
        ready = not pressure
        reason = "route_signal_ready" if ready else "route_signal_ready_with_pressure"
    elif active_tunnels > 0:
        ready = not pressure
        reason = "active_route_tunnel" if ready else "active_route_tunnel_with_pressure"
    elif pressure:
        reason = "route_runtime_pressure"

    return {
        "ready": bool(ready),
        "reason": reason,
        "route_status": route_status,
        "fresh_probe": bool(fresh_probe),
        "probe_age_s": round(probe_age_s, 3) if probe_age_s is not None else None,
        "active_tunnels": active_tunnels,
        "pending_tunnels": pending_tunnels,
        "pending_events": pending_events,
        "pending_chunks": pending_chunks,
        "guardrail_active": guardrail_active,
        "assessment_state": assessment_state,
        "control_state": control_state,
        "frame_state": frame_state,
        "frame_degraded_by_sync_shedding": frame_degraded_by_sync_shedding,
        "pressure": pressure,
    }


def _yws_guard_reject_reason(
    webspace_id: str,
    dev_id: str,
    *,
    browser_page_id: str | None = None,
    browser_session_id: str | None = None,
    client_attempt_id: str | None = None,
) -> tuple[str, dict[str, Any]]:
    webspace_key = str(webspace_id or "").strip() or "default"
    dev_key = str(dev_id or "").strip() or "unknown"
    now = time.time()
    active_total = _active_yws_connection_total_for_webspace(webspace_key)
    reason = ""
    recent_10s = 0
    webspace_distinct_clients_10s = 0
    client_15s = 0
    client_short_sessions = 0
    active_client_total = _active_yws_connection_total_for_client(
        webspace_key,
        dev_key,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id,
    )
    client_reconnect_storm = False
    client_short_session_storm = False
    webspace_reconnect_storm = False
    cleared_client_quarantine = False
    cleared_webspace_quarantine = False
    quarantine_until = 0.0
    quarantine_ttl_s: float | None = None
    quarantine_incident_count: int | None = None
    recovery_in_progress_until = 0.0
    recovery_in_progress_ttl_s: float | None = None
    recovery_admission_reserved = False
    route_dependency: dict[str, Any] = {}
    dependency_recovery_allowed = False
    dependency_recovery_reason = ""
    planned_transition = _yws_guard_planned_transition_snapshot(now_ts=now)
    planned_transition_recovery_allowed = False
    planned_transition_cleared_total = 0

    def _dependency_allows_recovery(trigger: str) -> bool:
        nonlocal route_dependency, dependency_recovery_allowed, dependency_recovery_reason
        if active_total > 0:
            return False
        if not route_dependency:
            route_dependency = _yws_guard_route_dependency_snapshot(now_ts=now)
        if not bool(route_dependency.get("ready")):
            return False
        dependency_recovery_allowed = True
        dependency_recovery_reason = str(trigger or "").strip() or "route_dependency_ready"
        return True

    def _record_dependency_recovery() -> None:
        if not dependency_recovery_allowed:
            return
        _YWS_GUARD_DIAG["dependency_recovery_allowed_total"] = int(
            _YWS_GUARD_DIAG.get("dependency_recovery_allowed_total") or 0
        ) + 1
        _YWS_GUARD_DIAG["last_dependency_recovery_at"] = now
        _YWS_GUARD_DIAG["last_dependency_recovery_reason"] = dependency_recovery_reason
        _YWS_GUARD_DIAG["last_dependency_recovery_webspace_id"] = webspace_key
        _YWS_GUARD_DIAG["last_dependency_recovery_dev_id"] = dev_key
        _YWS_GUARD_DIAG["last_dependency_recovery_route_reason"] = str(route_dependency.get("reason") or "")

    with _YWS_STORM_LOCK:
        def _reserve_recovery_admission_locked(trigger: str) -> bool:
            nonlocal recovery_in_progress_until, recovery_in_progress_ttl_s
            nonlocal recovery_admission_reserved, dependency_recovery_allowed, dependency_recovery_reason
            existing_until = float(_YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL.get(client_key) or 0.0)
            if existing_until > now:
                recovery_in_progress_until = existing_until
                recovery_in_progress_ttl_s = max(0.0, existing_until - now)
                return False
            ttl_s = max(1.0, float(_YWS_GUARD_RECOVERY_IN_PROGRESS_S))
            recovery_in_progress_until = now + ttl_s
            recovery_in_progress_ttl_s = ttl_s
            _YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL[client_key] = recovery_in_progress_until
            recovery_admission_reserved = True
            dependency_recovery_allowed = True
            dependency_recovery_reason = str(trigger or "").strip() or "client_recovery_admission"
            _record_dependency_recovery()
            _YWS_GUARD_DIAG["recovery_admission_reserved_total"] = int(
                _YWS_GUARD_DIAG.get("recovery_admission_reserved_total") or 0
            ) + 1
            _YWS_GUARD_DIAG["last_recovery_admission_at"] = now
            _YWS_GUARD_DIAG["last_recovery_admission_webspace_id"] = webspace_key
            _YWS_GUARD_DIAG["last_recovery_admission_dev_id"] = dev_key
            _YWS_GUARD_DIAG["last_recovery_admission_reason"] = dependency_recovery_reason
            return True

        cutoff_60 = now - 60.0
        while _YWS_OPEN_HISTORY and _YWS_OPEN_HISTORY[0] < cutoff_60:
            _YWS_OPEN_HISTORY.popleft()
        while _YWS_ATTEMPT_HISTORY and _YWS_ATTEMPT_HISTORY[0] < cutoff_60:
            _YWS_ATTEMPT_HISTORY.popleft()
        stale_keys: list[str] = []
        for client_key, queue in _YWS_CLIENT_OPEN_HISTORY.items():
            while queue and queue[0] < cutoff_60:
                queue.popleft()
            if not queue:
                stale_keys.append(client_key)
        for client_key in stale_keys:
            _YWS_CLIENT_OPEN_HISTORY.pop(client_key, None)
        stale_attempt_keys: list[str] = []
        for client_key, queue in _YWS_CLIENT_ATTEMPT_HISTORY.items():
            while queue and queue[0] < cutoff_60:
                queue.popleft()
            if not queue:
                stale_attempt_keys.append(client_key)
        for client_key in stale_attempt_keys:
            _YWS_CLIENT_ATTEMPT_HISTORY.pop(client_key, None)
        short_cutoff = now - max(1.0, float(_YWS_GUARD_SHORT_SESSION_WINDOW_S))
        stale_short_keys: list[str] = []
        for client_key, queue in _YWS_CLIENT_SHORT_SESSION_HISTORY.items():
            while queue and queue[0] < short_cutoff:
                queue.popleft()
            if not queue:
                stale_short_keys.append(client_key)
        for client_key in stale_short_keys:
            _YWS_CLIENT_SHORT_SESSION_HISTORY.pop(client_key, None)
        for key0 in list(_YWS_GUARD_QUARANTINE_UNTIL.keys()):
            if float(_YWS_GUARD_QUARANTINE_UNTIL.get(key0) or 0.0) <= now:
                _YWS_GUARD_QUARANTINE_UNTIL.pop(key0, None)
        for key0 in list(_YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL.keys()):
            if float(_YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL.get(key0) or 0.0) <= now:
                _YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL.pop(key0, None)
        recent_10s, webspace_distinct_clients_10s = _yws_client_recent_open_counts_locked(webspace_key, now)
        client_key = _yws_guard_client_history_key(
            webspace_key,
            dev_key,
            browser_page_id=browser_page_id,
            browser_session_id=browser_session_id,
            client_attempt_id=client_attempt_id,
        )
        client_queue = _YWS_CLIENT_ATTEMPT_HISTORY.get(client_key) or deque()
        client_15s = sum(1 for ts in client_queue if ts >= now - 15.0)
        short_queue = _YWS_CLIENT_SHORT_SESSION_HISTORY.get(client_key) or deque()
        client_short_sessions = sum(1 for ts in short_queue if ts >= short_cutoff)
        client_quarantine_until = float(_YWS_GUARD_QUARANTINE_UNTIL.get(client_key) or 0.0)
        webspace_quarantine_until = float(
            _YWS_GUARD_QUARANTINE_UNTIL.get(_yws_guard_quarantine_key(webspace_key)) or 0.0
        )
        if bool(planned_transition.get("suppress_reconnect_guard")):
            if active_total > 0:
                planned_transition_recovery_allowed = True
            elif _dependency_allows_recovery("planned_update_reconnect"):
                planned_transition_recovery_allowed = True
            if planned_transition_recovery_allowed:
                history_prefix = f"{webspace_key}::"
                for mapping in (
                    _YWS_CLIENT_OPEN_HISTORY,
                    _YWS_CLIENT_ATTEMPT_HISTORY,
                    _YWS_CLIENT_SHORT_SESSION_HISTORY,
                    _YWS_GUARD_QUARANTINE_UNTIL,
                    _YWS_GUARD_RECOVERY_IN_FLIGHT_UNTIL,
                    _YWS_GUARD_INCIDENTS,
                ):
                    for item_key in list(mapping.keys()):
                        if str(item_key or "").startswith(history_prefix):
                            mapping.pop(item_key, None)
                            planned_transition_cleared_total += 1
                cleared_client_quarantine = client_quarantine_until > now
                cleared_webspace_quarantine = webspace_quarantine_until > now
                _YWS_GUARD_DIAG["planned_transition_recovery_total"] = int(
                    _YWS_GUARD_DIAG.get("planned_transition_recovery_total") or 0
                ) + 1
                _YWS_GUARD_DIAG["last_planned_transition_recovery_at"] = now
                _YWS_GUARD_DIAG["last_planned_transition_recovery_webspace_id"] = webspace_key
                _YWS_GUARD_DIAG["last_planned_transition_recovery_marker"] = str(
                    planned_transition.get("marker") or ""
                )
                _YWS_GUARD_DIAG["last_planned_transition_recovery_cleared_total"] = (
                    planned_transition_cleared_total
                )
        if active_total >= _YWS_MAX_ACTIVE_PER_WEBSPACE:
            reason = "active_limit"
        elif planned_transition_recovery_allowed:
            reason = ""
        elif client_quarantine_until > now:
            if active_total <= 0 and _dependency_allows_recovery("client_reconnect_backoff_no_active_yws"):
                _YWS_GUARD_QUARANTINE_UNTIL.pop(client_key, None)
                cleared_client_quarantine = True
                if not _reserve_recovery_admission_locked("client_reconnect_backoff_no_active_yws"):
                    reason = "client_recovery_in_progress"
                    quarantine_until = recovery_in_progress_until
                    quarantine_ttl_s = recovery_in_progress_ttl_s
            else:
                quarantine_until = client_quarantine_until
                quarantine_ttl_s = max(0.0, client_quarantine_until - now)
                reason = "client_reconnect_backoff"
        elif webspace_quarantine_until > now:
            if active_total > 0:
                reason = "webspace_reconnect_backoff"
                quarantine_until = webspace_quarantine_until
                quarantine_ttl_s = max(0.0, webspace_quarantine_until - now)
        else:
            client_reconnect_storm = client_15s >= _YWS_GUARD_CLIENT_OPEN_15S
            webspace_reconnect_storm = (
                recent_10s >= _YWS_GUARD_RECENT_OPEN_10S
                and webspace_distinct_clients_10s >= _YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S
            )
            if client_reconnect_storm:
                _yws_guard_note_client_storm(
                    webspace_id=webspace_key,
                    dev_id=dev_key,
                    active_total=active_total,
                    client_15s=client_15s,
                    webspace_recent_10s=recent_10s,
                    webspace_distinct_clients_10s=webspace_distinct_clients_10s,
                )
                if active_client_total > 0:
                    reason = "client_recovery_in_progress"
                    quarantine_until = now + max(1.0, float(_YWS_GUARD_RECOVERY_IN_PROGRESS_S))
                    quarantine_ttl_s = max(1.0, float(_YWS_GUARD_RECOVERY_IN_PROGRESS_S))
                    recovery_in_progress_until = quarantine_until
                    recovery_in_progress_ttl_s = quarantine_ttl_s
                elif active_total <= 0 and _dependency_allows_recovery("client_reconnect_storm_no_active_yws"):
                    if not _reserve_recovery_admission_locked("client_reconnect_storm_no_active_yws"):
                        reason = "client_recovery_in_progress"
                        quarantine_until = recovery_in_progress_until
                        quarantine_ttl_s = recovery_in_progress_ttl_s
                elif (
                    webspace_distinct_clients_10s < _YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S
                    and client_15s < _yws_single_client_reconnect_escalation_limit()
                ):
                    dependency_recovery_allowed = True
                    dependency_recovery_reason = "single_client_reconnect_storm_replacement"
                    _record_dependency_recovery()
                else:
                    quarantine_until, quarantine_ttl_s, quarantine_incident_count = _set_yws_guard_quarantine_locked(
                        client_key,
                        now,
                    )
                    reason = "client_reconnect_storm"
            client_short_session_storm = client_short_sessions >= _YWS_GUARD_SHORT_SESSION_LIMIT
            if client_short_session_storm and not reason:
                if _dependency_allows_recovery("client_short_session_storm"):
                    _record_dependency_recovery()
                elif active_total <= 0:
                    dependency_recovery_allowed = True
                    dependency_recovery_reason = "client_short_session_storm_no_active_yws"
                    _record_dependency_recovery()
                elif (
                    webspace_distinct_clients_10s < _YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S
                    and client_short_sessions < _yws_single_client_short_session_escalation_limit()
                ):
                    dependency_recovery_allowed = True
                    dependency_recovery_reason = "single_client_short_session_replacement"
                    _record_dependency_recovery()
                elif (
                    not webspace_reconnect_storm
                    and client_short_sessions < _yws_single_client_short_session_escalation_limit()
                ):
                    dependency_recovery_allowed = True
                    dependency_recovery_reason = "client_short_session_storm_without_webspace_pressure"
                    _record_dependency_recovery()
                else:
                    quarantine_until, quarantine_ttl_s, quarantine_incident_count = _set_yws_guard_quarantine_locked(
                        client_key,
                        now,
                    )
                    reason = "client_short_session_storm"
                    _YWS_GUARD_DIAG["client_short_session_storm_observed_total"] = int(
                        _YWS_GUARD_DIAG.get("client_short_session_storm_observed_total") or 0
                    ) + 1
                    _YWS_GUARD_DIAG["last_client_short_session_storm_at"] = now
                    _YWS_GUARD_DIAG["last_client_short_session_storm_webspace_id"] = webspace_key
                    _YWS_GUARD_DIAG["last_client_short_session_storm_dev_id"] = dev_key
                    _YWS_GUARD_DIAG["last_client_short_session_storm_recent"] = client_short_sessions
            if webspace_reconnect_storm:
                _yws_guard_note_webspace_storm(
                    webspace_id=webspace_key,
                    dev_id=dev_key,
                    active_total=active_total,
                    recent_10s=recent_10s,
                    client_15s=client_15s,
                    webspace_distinct_clients_10s=webspace_distinct_clients_10s,
                )
                quarantine_until, quarantine_ttl_s, quarantine_incident_count = _set_yws_guard_quarantine_locked(
                    _yws_guard_quarantine_key(webspace_key),
                    now,
                )
                reason = "webspace_reconnect_storm"
        if reason:
            _YWS_GUARD_DIAG["reject_total"] = int(_YWS_GUARD_DIAG.get("reject_total") or 0) + 1
            _YWS_GUARD_DIAG["last_reject_at"] = now
            _YWS_GUARD_DIAG["last_reject_reason"] = reason
            _YWS_GUARD_DIAG["last_reject_webspace_id"] = webspace_key
            _YWS_GUARD_DIAG["last_reject_dev_id"] = dev_key
            if quarantine_ttl_s is not None:
                _YWS_GUARD_DIAG["last_reject_quarantine_ttl_s"] = quarantine_ttl_s
            if quarantine_incident_count is not None:
                _YWS_GUARD_DIAG["last_reject_incident_count"] = quarantine_incident_count
    diag = {
        "active_total": active_total,
        "active_client_total": active_client_total,
        "recent_open_10s": recent_10s,
        "webspace_distinct_clients_10s": webspace_distinct_clients_10s,
        "client_open_15s": client_15s,
        "client_short_sessions": client_short_sessions,
        "client_reconnect_storm": client_reconnect_storm,
        "client_short_session_storm": client_short_session_storm,
        "webspace_reconnect_storm": webspace_reconnect_storm,
        "single_client_reconnect_escalate_at": _yws_single_client_reconnect_escalation_limit(),
        "single_client_short_session_escalate_at": _yws_single_client_short_session_escalation_limit(),
        "client_quarantine_cleared": cleared_client_quarantine,
        "webspace_quarantine_cleared": cleared_webspace_quarantine,
        "quarantine_until": quarantine_until,
        "quarantine_ttl_s": quarantine_ttl_s,
        "quarantine_incident_count": quarantine_incident_count,
        "recovery_admission_reserved": recovery_admission_reserved,
        "recovery_in_progress_until": recovery_in_progress_until,
        "recovery_in_progress_ttl_s": recovery_in_progress_ttl_s,
        "route_dependency": route_dependency,
        "dependency_recovery_allowed": dependency_recovery_allowed,
        "dependency_recovery_reason": dependency_recovery_reason,
        "planned_transition": planned_transition,
        "planned_transition_recovery_allowed": planned_transition_recovery_allowed,
        "planned_transition_cleared_total": planned_transition_cleared_total,
    }
    if reason:
        _yws_guard_log(
            webspace_id=webspace_key,
            dev_id=dev_key,
            reason=reason,
            active_total=active_total,
            recent_10s=recent_10s,
            client_15s=client_15s,
            cooldown_s=quarantine_ttl_s,
            incident_count=quarantine_incident_count,
        )
    return reason, diag


def _yws_storm_snapshot(now: float) -> dict[str, Any]:
    active_clients = _active_yws_client_rows()
    with _YWS_STORM_LOCK:
        recent_10s = sum(1 for ts in _YWS_OPEN_HISTORY if ts >= now - 10.0)
        recent_60s = sum(1 for ts in _YWS_OPEN_HISTORY if ts >= now - 60.0)
        quarantined_total = sum(
            1 for until in _YWS_GUARD_QUARANTINE_UNTIL.values() if float(until or 0.0) > now
        )
        incident_total = len(_YWS_GUARD_INCIDENTS)
        guard_diag = dict(_YWS_GUARD_DIAG)
        hot_clients: list[dict[str, Any]] = []
        distinct_hot_clients_10s = 0
        client_reconnect_storm_detected = False
        for key, queue in _YWS_CLIENT_ATTEMPT_HISTORY.items():
            client_recent_10s = sum(1 for ts in queue if ts >= now - 10.0)
            if client_recent_10s > 0:
                distinct_hot_clients_10s += 1
            recent_15s = sum(1 for ts in queue if ts >= now - 15.0)
            if recent_15s <= 0:
                continue
            if recent_15s >= _YWS_GUARD_CLIENT_OPEN_15S:
                client_reconnect_storm_detected = True
            webspace_id, _, dev_id = key.partition("::")
            short_queue = _YWS_CLIENT_SHORT_SESSION_HISTORY.get(key) or deque()
            short_sessions = sum(
                1 for ts in short_queue if ts >= now - max(1.0, float(_YWS_GUARD_SHORT_SESSION_WINDOW_S))
            )
            hot_clients.append(
                {
                    "webspace_id": webspace_id or "default",
                    "dev_id": dev_id or "unknown",
                    "open_15s": recent_15s,
                    "attempt_15s": recent_15s,
                    "short_sessions": short_sessions,
                }
            )
    with _YWS_ATTEMPT_LOCK:
        attempt_diag = dict(_YWS_ATTEMPT_DIAG)
    hot_clients.sort(key=lambda item: (-int(item.get("open_15s") or 0), str(item.get("dev_id") or "")))
    return {
        "recent_open_10s": recent_10s,
        "recent_open_60s": recent_60s,
        "distinct_hot_clients_10s": distinct_hot_clients_10s,
        "storm_detected": recent_10s >= _YWS_GUARD_RECENT_OPEN_10S
        and distinct_hot_clients_10s >= _YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S,
        "client_reconnect_storm_detected": client_reconnect_storm_detected,
        "hot_clients": hot_clients[:3],
        "active_clients": active_clients[:8],
        "attempts": attempt_diag,
        "guard": {
            "max_active_per_webspace": _YWS_MAX_ACTIVE_PER_WEBSPACE,
            "max_active_per_client": _YWS_MAX_ACTIVE_PER_CLIENT,
            "recent_open_10s_limit": _YWS_GUARD_RECENT_OPEN_10S,
            "client_open_15s_limit": _YWS_GUARD_CLIENT_OPEN_15S,
            "short_session_limit": _YWS_GUARD_SHORT_SESSION_LIMIT,
            "single_client_reconnect_escalate_at": _yws_single_client_reconnect_escalation_limit(),
            "single_client_short_session_escalate_at": _yws_single_client_short_session_escalation_limit(),
            "short_session_window_s": _YWS_GUARD_SHORT_SESSION_WINDOW_S,
            "min_stable_session_s": _YWS_GUARD_MIN_STABLE_SESSION_S,
            "webspace_min_clients_10s": _YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S,
            "cooldown_s": _YWS_GUARD_COOLDOWN_S,
            "max_cooldown_s": _YWS_GUARD_MAX_COOLDOWN_S,
            "escalation_window_s": _YWS_GUARD_ESCALATION_WINDOW_S,
            "notify_interval_s": _YWS_GUARD_NOTIFY_INTERVAL_S,
            "quarantined_total": quarantined_total,
            "incident_total": incident_total,
            **guard_diag,
        },
    }


def _remaining_quarantine_s(until: float, now: float) -> float:
    return round(max(0.0, float(until or 0.0) - now), 3)


def _yjs_balancer_state(
    *,
    server_ready: bool,
    direct_transport_enabled: bool,
    active_connections: int,
    active_connection_limit: int,
    webspace_quarantined: bool,
    client_quarantined: bool,
    webspace_storm_threshold_reached: bool,
    client_storm_threshold_reached: bool,
    short_session_threshold_reached: bool,
    active_fill_ratio: float,
) -> tuple[str, str]:
    if not direct_transport_enabled:
        return "disabled", "direct_transport_disabled"
    if not server_ready:
        return "critical", "y_server_not_ready"
    if webspace_quarantined:
        return "critical", "webspace_quarantine"
    if active_connections >= active_connection_limit:
        return "critical", "active_connection_limit"
    if webspace_storm_threshold_reached:
        return "critical", "webspace_reconnect_storm_threshold"
    if client_quarantined:
        return "watch", "client_quarantine"
    if client_storm_threshold_reached:
        return "watch", "client_reconnect_storm_threshold"
    if short_session_threshold_reached:
        return "watch", "short_session_storm_threshold"
    if active_fill_ratio >= 0.8:
        return "watch", "active_connection_limit_near"
    return "nominal", "within_limits"


def yjs_balancer_snapshot(webspace_id: str | None = None, *, now_ts: float | None = None) -> dict[str, Any]:
    """Return bounded YWS health/usage/guard telemetry for operational policy work."""

    now = time.time() if now_ts is None else float(now_ts)
    selected_webspace_id = _coerce_gateway_webspace_id(webspace_id)
    with _ACTIVE_YWS_LOCK:
        active_by_webspace = {
            str(key or "").strip() or "default": len(list(sockets or []))
            for key, sockets in _ACTIVE_YWS_CONNECTIONS.items()
        }
        active_clients_by_webspace = {
            str(key or "").strip() or "default": dict(value)
            for key, value in _ACTIVE_YWS_CLIENTS.items()
            if isinstance(value, dict)
        }

    active_connections = int(active_by_webspace.get(selected_webspace_id) or 0)
    active_client_counts = active_clients_by_webspace.get(selected_webspace_id) or {}
    active_client_rows = [
        row
        for row in _active_yws_client_rows()
        if str(row.get("webspace_id") or "").strip() == selected_webspace_id
    ]
    active_client_rows = active_client_rows[:16]

    hot_clients: list[dict[str, Any]] = []
    quarantined_clients: list[dict[str, Any]] = []
    recent_attempts_10s = 0
    recent_attempts_60s = 0
    distinct_clients_10s = 0
    max_client_attempts_15s = 0
    short_sessions_window_total = 0
    max_client_short_sessions = 0
    webspace_quarantine_until = 0.0
    webspace_incident = {}
    selected_incident_total = 0
    guard_diag: dict[str, Any] = {}
    global_recent_open_10s = 0
    global_recent_open_60s = 0
    global_recent_attempts_10s = 0
    global_recent_attempts_60s = 0
    with _YWS_STORM_LOCK:
        global_recent_open_10s = sum(1 for ts in _YWS_OPEN_HISTORY if ts >= now - 10.0)
        global_recent_open_60s = sum(1 for ts in _YWS_OPEN_HISTORY if ts >= now - 60.0)
        global_recent_attempts_10s = sum(1 for ts in _YWS_ATTEMPT_HISTORY if ts >= now - 10.0)
        global_recent_attempts_60s = sum(1 for ts in _YWS_ATTEMPT_HISTORY if ts >= now - 60.0)
        for raw_key, queue in _YWS_CLIENT_ATTEMPT_HISTORY.items():
            key = str(raw_key or "")
            client_webspace_id, _, client_token = key.partition("::")
            client_webspace_id = client_webspace_id or "default"
            if client_webspace_id != selected_webspace_id:
                continue
            client_attempts_10s = sum(1 for ts in queue if ts >= now - 10.0)
            client_attempts_15s = sum(1 for ts in queue if ts >= now - 15.0)
            client_attempts_60s = sum(1 for ts in queue if ts >= now - 60.0)
            recent_attempts_10s += client_attempts_10s
            recent_attempts_60s += client_attempts_60s
            if client_attempts_10s > 0:
                distinct_clients_10s += 1
            max_client_attempts_15s = max(max_client_attempts_15s, client_attempts_15s)
            short_queue = _YWS_CLIENT_SHORT_SESSION_HISTORY.get(key) or deque()
            short_sessions = sum(
                1
                for ts in short_queue
                if ts >= now - max(1.0, float(_YWS_GUARD_SHORT_SESSION_WINDOW_S))
            )
            short_sessions_window_total += short_sessions
            max_client_short_sessions = max(max_client_short_sessions, short_sessions)
            if client_attempts_15s <= 0 and short_sessions <= 0:
                continue
            device_id, scoped_client_id = _split_yws_client_limit_key(client_token)
            row: dict[str, Any] = {
                "device_id": device_id,
                "attempt_10s": client_attempts_10s,
                "attempt_15s": client_attempts_15s,
                "attempt_60s": client_attempts_60s,
                "short_sessions": short_sessions,
            }
            if scoped_client_id:
                row["client_limit_id"] = scoped_client_id
            hot_clients.append(row)
        webspace_quarantine_key = _yws_guard_quarantine_key(selected_webspace_id)
        webspace_quarantine_until = float(_YWS_GUARD_QUARANTINE_UNTIL.get(webspace_quarantine_key) or 0.0)
        webspace_incident = dict(_YWS_GUARD_INCIDENTS.get(webspace_quarantine_key) or {})
        selected_prefix = f"{selected_webspace_id}::"
        for raw_key, raw_until in _YWS_GUARD_QUARANTINE_UNTIL.items():
            key = str(raw_key or "")
            until = float(raw_until or 0.0)
            if not key.startswith(selected_prefix) or until <= now:
                continue
            selected_incident_total += 1
            _webspace, _, client_token = key.partition("::")
            if client_token == "*":
                continue
            device_id, scoped_client_id = _split_yws_client_limit_key(client_token)
            row = {
                "device_id": device_id,
                "until": until,
                "remaining_s": _remaining_quarantine_s(until, now),
            }
            if scoped_client_id:
                row["client_limit_id"] = scoped_client_id
            quarantined_clients.append(row)
        guard_diag = dict(_YWS_GUARD_DIAG)

    with _YWS_ATTEMPT_LOCK:
        attempt_diag = dict(_YWS_ATTEMPT_DIAG)

    active_connection_limit = max(1, int(_YWS_MAX_ACTIVE_PER_WEBSPACE))
    active_fill_ratio = round(active_connections / float(active_connection_limit), 3)
    webspace_storm_threshold_reached = (
        recent_attempts_10s >= int(_YWS_GUARD_RECENT_OPEN_10S)
        and distinct_clients_10s >= int(_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S)
    )
    client_storm_threshold_reached = max_client_attempts_15s >= int(_YWS_GUARD_CLIENT_OPEN_15S)
    short_session_threshold_reached = max_client_short_sessions >= int(_YWS_GUARD_SHORT_SESSION_LIMIT)
    webspace_quarantined = webspace_quarantine_until > now
    client_quarantined = bool(quarantined_clients)
    planned_transition = _yws_guard_planned_transition_snapshot(now_ts=now)
    server_snapshot = _y_server_runtime_snapshot()
    active_events_connections = _active_events_ws_connection_total_for_webspace(selected_webspace_id)
    active_webrtc_peers = _active_webrtc_peer_total_for_webspace(selected_webspace_id)
    room_details = server_snapshot.get("room_effective_branches")
    room_present = isinstance(room_details, dict) and selected_webspace_id in room_details
    retained_by = []
    if active_connections > 0:
        retained_by.append("yws")
    if active_events_connections > 0:
        retained_by.append("events_ws")
    if active_webrtc_peers > 0:
        retained_by.append("webrtc")
    direct_transport_enabled = _yws_direct_transport_enabled()
    state, reason = _yjs_balancer_state(
        server_ready=bool(server_snapshot.get("ready")),
        direct_transport_enabled=direct_transport_enabled,
        active_connections=active_connections,
        active_connection_limit=active_connection_limit,
        webspace_quarantined=webspace_quarantined,
        client_quarantined=client_quarantined,
        webspace_storm_threshold_reached=webspace_storm_threshold_reached,
        client_storm_threshold_reached=client_storm_threshold_reached,
        short_session_threshold_reached=short_session_threshold_reached,
        active_fill_ratio=active_fill_ratio,
    )
    hot_clients.sort(
        key=lambda item: (
            -int(item.get("attempt_15s") or 0),
            -int(item.get("short_sessions") or 0),
            str(item.get("device_id") or ""),
        )
    )
    quarantined_clients.sort(key=lambda item: (-float(item.get("remaining_s") or 0.0), str(item.get("device_id") or "")))
    active_by_webspace_rows = [
        {"webspace_id": key, "active_connections": int(count or 0)}
        for key, count in sorted(active_by_webspace.items(), key=lambda item: (-int(item[1] or 0), str(item[0])))
    ]
    return {
        "schema": "adaos.yjs_balancer.v1",
        "webspace_id": selected_webspace_id,
        "updated_at": now,
        "state": state,
        "reason": reason,
        "health": {
            "available": bool(direct_transport_enabled and server_snapshot.get("ready")),
            "state": state,
            "reason": reason,
            "server_ready": bool(server_snapshot.get("ready")),
            "direct_transport_enabled": direct_transport_enabled,
            "capacity_ok": active_connections < active_connection_limit,
            "guard_ok": not bool(
                webspace_quarantined
                or webspace_storm_threshold_reached
                or client_storm_threshold_reached
                or short_session_threshold_reached
            ),
            "quarantined": bool(webspace_quarantined or client_quarantined),
        },
        "usage": {
            "active_connections": active_connections,
            "active_connection_limit": active_connection_limit,
            "active_connection_fill_ratio": active_fill_ratio,
            "active_clients": len(active_client_counts),
            "active_client_session_max": max([int(count or 0) for count in active_client_counts.values()] or [0]),
            "active_client_sessions": active_client_rows,
            "active_webspaces": len(active_by_webspace),
            "active_connections_all_webspaces": sum(int(count or 0) for count in active_by_webspace.values()),
            "active_events_ws_connections": active_events_connections,
            "active_webrtc_peers": active_webrtc_peers,
        },
        "limits": {
            "max_active_per_webspace": active_connection_limit,
            "max_active_per_client": int(_YWS_MAX_ACTIVE_PER_CLIENT),
            "replace_scoped_client_connections": bool(_YWS_REPLACE_SCOPED_CLIENT_CONNECTIONS),
            "recent_open_10s_limit": int(_YWS_GUARD_RECENT_OPEN_10S),
            "webspace_min_clients_10s": int(_YWS_GUARD_WEBSPACE_MIN_CLIENTS_10S),
            "client_open_15s_limit": int(_YWS_GUARD_CLIENT_OPEN_15S),
            "single_client_reconnect_escalate_at": _yws_single_client_reconnect_escalation_limit(),
            "short_session_limit": int(_YWS_GUARD_SHORT_SESSION_LIMIT),
            "single_client_short_session_escalate_at": _yws_single_client_short_session_escalation_limit(),
            "short_session_window_s": float(_YWS_GUARD_SHORT_SESSION_WINDOW_S),
            "min_stable_session_s": float(_YWS_GUARD_MIN_STABLE_SESSION_S),
            "cooldown_s": float(_YWS_GUARD_COOLDOWN_S),
            "max_cooldown_s": float(_YWS_GUARD_MAX_COOLDOWN_S),
            "escalation_window_s": float(_YWS_GUARD_ESCALATION_WINDOW_S),
            "notify_interval_s": float(_YWS_GUARD_NOTIFY_INTERVAL_S),
            "planned_transition_grace_s": float(_YWS_GUARD_PLANNED_TRANSITION_GRACE_S),
            "planned_transition_max_age_s": float(_YWS_GUARD_PLANNED_TRANSITION_MAX_AGE_S),
        },
        "guard": {
            "planned_transition": planned_transition,
            "recent_attempts_10s": recent_attempts_10s,
            "recent_attempts_60s": recent_attempts_60s,
            "distinct_clients_10s": distinct_clients_10s,
            "client_attempts_15s_max": max_client_attempts_15s,
            "short_sessions_window_total": short_sessions_window_total,
            "client_short_sessions_max": max_client_short_sessions,
            "webspace_storm_threshold_reached": webspace_storm_threshold_reached,
            "client_storm_threshold_reached": client_storm_threshold_reached,
            "short_session_threshold_reached": short_session_threshold_reached,
            "webspace_quarantined": webspace_quarantined,
            "webspace_quarantine_until": webspace_quarantine_until if webspace_quarantined else None,
            "webspace_quarantine_remaining_s": _remaining_quarantine_s(webspace_quarantine_until, now)
            if webspace_quarantined
            else 0.0,
            "client_quarantined": client_quarantined,
            "quarantined_clients": quarantined_clients[:8],
            "quarantine_incident_count": int(float(webspace_incident.get("count") or 0.0)),
            "selected_quarantine_total": selected_incident_total,
            "reject_total": int(guard_diag.get("reject_total") or 0),
            "last_reject_at": guard_diag.get("last_reject_at") or None,
            "last_reject_reason": str(guard_diag.get("last_reject_reason") or ""),
            "last_reject_webspace_id": str(guard_diag.get("last_reject_webspace_id") or ""),
            "last_reject_dev_id": str(guard_diag.get("last_reject_dev_id") or ""),
        },
        "observed": {
            "room_retention": {
                "room_present": room_present,
                "retained_by": retained_by,
                "idle_eviction_eligible": bool(room_present and not retained_by),
                "idle_eviction_delay_s": float(_IDLE_ROOM_EVICT_SEC),
            },
            "hot_clients": hot_clients[:8],
            "active_by_webspace": active_by_webspace_rows[:16],
            "global_recent_open_10s": global_recent_open_10s,
            "global_recent_open_60s": global_recent_open_60s,
            "global_recent_attempts_10s": global_recent_attempts_10s,
            "global_recent_attempts_60s": global_recent_attempts_60s,
            "attempts": attempt_diag,
            "server": {
                "requested": bool(server_snapshot.get("requested")),
                "started_event": bool(server_snapshot.get("started_event")),
                "task_running": bool(server_snapshot.get("task_running")),
                "task_done": bool(server_snapshot.get("task_done")),
                "task_cancelled": bool(server_snapshot.get("task_cancelled")),
                "room_total": int(server_snapshot.get("room_total") or 0),
                "ready": bool(server_snapshot.get("ready")),
                "error": server_snapshot.get("error"),
            },
        },
    }


def _untrack_yws_connection(webspace_id: str, websocket: WebSocket) -> None:
    key = str(webspace_id or "").strip() or "default"
    remaining_connections = 0
    with _ACTIVE_YWS_LOCK:
        items = _ACTIVE_YWS_CONNECTIONS.get(key)
        if not items:
            device_key = None
        else:
            try:
                items.remove(websocket)
            except ValueError:
                pass
            remaining_connections = len(items)
        if not items:
            _ACTIVE_YWS_CONNECTIONS.pop(key, None)
        client_key = _websocket_yws_client_limit_key(websocket)
        clients = _ACTIVE_YWS_CLIENTS.get(key)
        if clients:
            remaining = int(clients.get(client_key) or 0) - 1
            if remaining > 0:
                clients[client_key] = remaining
            else:
                clients.pop(client_key, None)
            if not clients:
                _ACTIVE_YWS_CLIENTS.pop(key, None)
    if remaining_connections <= 0:
        room = getattr(y_server, "rooms", {}).get(key)
        if room is not None:
            diag_logger = getattr(room, "_diag_log_pressure", None)
            if callable(diag_logger):
                try:
                    diag_logger("last_client_detached", force=True)
                except Exception:
                    pass
    if remaining_connections <= 0:
        _schedule_idle_room_reset(key)


def active_browser_session_snapshot(*, now_ts: float | None = None) -> dict[str, Any]:
    now = time.time() if now_ts is None else float(now_ts)
    with _ACTIVE_YWS_LOCK:
        clients = {
            webspace_id: dict(device_counts)
            for webspace_id, device_counts in _ACTIVE_YWS_CLIENTS.items()
            if isinstance(device_counts, dict)
        }
    peers: list[dict[str, Any]] = []
    for webspace_id, device_counts in clients.items():
        for client_key, session_count in sorted(device_counts.items()):
            device_id, scoped_client_id = _split_yws_client_limit_key(client_key)
            token = str(device_id or "").strip()
            if not token:
                continue
            peer = {
                "device_id": token,
                "webspace_id": str(webspace_id or "").strip() or "default",
                "connection_state": "connected",
                "yjs_channel_state": "open",
                "session_count": int(session_count or 0),
                "source": "yws_gateway",
            }
            if scoped_client_id:
                peer["client_limit_id"] = scoped_client_id
            peers.append(peer)
    return {
        "peer_total": len(peers),
        "peers": peers,
        "updated_at": now,
    }


async def close_webspace_yws_connections(
    webspace_id: str,
    *,
    code: int = 1012,
    reason: str = "webspace_reload",
) -> int:
    key = str(webspace_id or "").strip() or "default"
    with _ACTIVE_YWS_LOCK:
        sockets = list(_ACTIVE_YWS_CONNECTIONS.get(key) or [])
    closed = 0
    close_reason = str(reason or "webspace_reload")[:120]
    for websocket in sockets:
        try:
            await websocket.close(code=code, reason=close_reason)
            closed += 1
        except Exception:
            pass
    if closed:
        await asyncio.sleep(0)
    return closed


async def close_webspace_webrtc_peers(
    webspace_id: str,
    *,
    reason: str = "webspace_reload",
) -> int:
    try:
        from adaos.services.webrtc.peer import close_peers_for_webspace
    except Exception:
        return 0
    try:
        return int(await close_peers_for_webspace(webspace_id, reason=reason) or 0)
    except Exception:
        _ylog.debug(
            "failed to close webrtc peers for webspace=%s reason=%s",
            webspace_id,
            reason,
            exc_info=True,
        )
        return 0


async def reset_hub_route_runtime(
    *,
    reason: str = "webspace_reload",
    notify_browser: bool = True,
) -> dict[str, Any]:
    try:
        from adaos.services.bootstrap import request_hub_root_route_reset
    except Exception:
        return {
            "ok": False,
            "reason": str(reason or "").strip() or "route_reset",
            "notify_browser": bool(notify_browser),
            "skipped": "route_reset_unavailable",
        }
    try:
        result = await request_hub_root_route_reset(
            reason=str(reason or "").strip() or "route_reset",
            notify_browser=bool(notify_browser),
        )
    except Exception as exc:
        _ylog.debug(
            "failed to reset hub route runtime reason=%s",
            reason,
            exc_info=True,
        )
        return {
            "ok": False,
            "reason": str(reason or "").strip() or "route_reset",
            "notify_browser": bool(notify_browser),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return dict(result) if isinstance(result, dict) else {"ok": True, "result": result}


async def reset_live_webspace_room(
    webspace_id: str,
    *,
    close_reason: str = "webspace_reload",
    persist_ystore_snapshot: bool = True,
    reset_route_runtime: bool = True,
    prewarm_after_reset: bool | None = None,
) -> dict[str, Any]:
    key = str(webspace_id or "").strip() or "default"
    room_for_owner = y_server.rooms.get(key)
    if room_for_owner is not None:
        owner_thread = getattr(room_for_owner, "_thread_id", None)
        owner_loop = getattr(room_for_owner, "_loop", None)
        if owner_thread is not None and owner_thread != threading.get_ident():
            if owner_loop is None or not owner_loop.is_running():
                raise RuntimeError(
                    f"cannot reset YRoom {key!r} outside its owner thread: owner loop is not running"
                )
            future = asyncio.run_coroutine_threadsafe(
                reset_live_webspace_room(
                    key,
                    close_reason=close_reason,
                    persist_ystore_snapshot=bool(persist_ystore_snapshot),
                    reset_route_runtime=bool(reset_route_runtime),
                    prewarm_after_reset=prewarm_after_reset,
                ),
                owner_loop,
            )
            result = dict(await asyncio.wrap_future(future))
            result["owner_handoff_mode"] = "threadsafe_owner_loop"
            return result

    _cancel_idle_room_reset(key)
    if reset_route_runtime:
        route_reset = await reset_hub_route_runtime(
            reason=f"yjs:{close_reason}",
            notify_browser=True,
        )
    else:
        route_reset = {
            "ok": True,
            "reason": f"yjs:{close_reason}",
            "notify_browser": False,
            "skipped": "route_reset_disabled",
        }
    closed_webrtc_peers = await close_webspace_webrtc_peers(
        key,
        reason=close_reason,
    )
    closed_connections = await close_webspace_yws_connections(
        key,
        code=1012,
        reason=close_reason,
    )
    if closed_connections or closed_webrtc_peers or bool(route_reset.get("closed_tunnels")):
        # Let the active serve() coroutines observe disconnect and run cleanup before
        # a new room is created for the same webspace.
        await asyncio.sleep(0.15)

    room = y_server.rooms.pop(key, None)
    if room is not None:
        diag_logger = getattr(room, "_diag_log_pressure", None)
        if callable(diag_logger):
            try:
                diag_logger(f"room_reset:{close_reason}", force=True)
            except Exception:
                pass
    _mark_room_reset(
        key,
        close_reason=close_reason,
        room=room,
        room_dropped=room is not None,
        closed_connections=closed_connections,
        closed_webrtc_peers=closed_webrtc_peers,
    )
    _room_locks.pop(key, None)
    room_stopped = False
    ystore_stopped = False
    ystore_evicted = False
    ystore_snapshot_persisted = False
    scheduler_job_deleted = False
    runtime_compaction_requested = False
    room_refs_released = False
    room_prewarmed = False
    room_prewarm_error = ""

    scheduler_job_deleted = await _delete_ystore_backup_job(key)

    if room is not None:
        stop_room = getattr(room, "stop", None)
        if callable(stop_room):
            try:
                result = stop_room()
                if inspect.isawaitable(result):
                    await result
                room_stopped = True
            except Exception:
                room_stopped = False
        ystore = getattr(room, "ystore", None)
        if ystore is not None:
            try:
                await _stop_ystore_maybe_async(ystore)
                ystore_stopped = True
            except Exception:
                ystore_stopped = False
            try:
                eviction = await evict_ystore_for_webspace(
                    key,
                    store=ystore,
                    persist_snapshot=bool(persist_ystore_snapshot),
                    compact_runtime=True,
                    backup_kind=f"room_reset:{close_reason}",
                )
            except BaseException as exc:
                if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                    raise
                eviction = {
                    "ok": False,
                    "persisted": False,
                    "backup_skipped": False,
                    "ystore_found": False,
                }
                _ylog.warning(
                    "failed to evict YStore for webspace=%s close_reason=%s",
                    key,
                    close_reason,
                    exc_info=True,
                )
            ystore_evicted = bool(eviction.get("ystore_found"))
            ystore_snapshot_persisted = bool(eviction.get("persisted"))
            runtime_compaction_requested = bool(
                ystore_snapshot_persisted or eviction.get("backup_skipped")
            )
        room_refs_released = await _release_room_refs(key, room)
    else:
        try:
            eviction = await evict_ystore_for_webspace(
                key,
                persist_snapshot=bool(persist_ystore_snapshot),
                compact_runtime=True,
                backup_kind=f"room_reset:{close_reason}",
            )
        except Exception:
            eviction = {
                "ok": False,
                "persisted": False,
                "backup_skipped": False,
                "ystore_found": False,
            }
            _ylog.warning(
                "failed to evict detached YStore for webspace=%s close_reason=%s",
                key,
                close_reason,
                exc_info=True,
            )
        ystore_evicted = bool(eviction.get("ystore_found"))
        ystore_snapshot_persisted = bool(eviction.get("persisted"))
        ystore_stopped = ystore_evicted
        runtime_compaction_requested = bool(
            ystore_snapshot_persisted or eviction.get("backup_skipped")
        )

    should_prewarm_after_reset = (
        str(os.getenv("ADAOS_YJS_PREWARM_ROOM_AFTER_RESET", "1") or "1").strip().lower()
        not in {"0", "false", "no", "off"}
        if prewarm_after_reset is None
        else bool(prewarm_after_reset)
    )
    if should_prewarm_after_reset:
        try:
            await y_server.get_room(key)
            room_prewarmed = True
        except Exception as exc:
            room_prewarmed = False
            room_prewarm_error = f"{type(exc).__name__}: {exc}"
            _ylog.debug(
                "failed to prewarm YRoom after reset webspace=%s reason=%s",
                key,
                close_reason,
                exc_info=True,
            )

    _ylog.info(
        "reset live Yjs room webspace=%s reason=%s room_dropped=%s closed_yws=%s closed_webrtc=%s "
        "ystore_evicted=%s snapshot_persisted=%s prewarmed=%s",
        key,
        close_reason,
        bool(room is not None),
        closed_connections,
        closed_webrtc_peers,
        ystore_evicted,
        ystore_snapshot_persisted,
        room_prewarmed,
    )

    return {
        "webspace_id": key,
        "route_reset": route_reset,
        "closed_webrtc_peers": closed_webrtc_peers,
        "closed_connections": closed_connections,
        "room_dropped": room is not None,
        "persist_ystore_snapshot": bool(persist_ystore_snapshot),
        "reset_route_runtime": bool(reset_route_runtime),
        "room_stopped": room_stopped,
        "ystore_stopped": ystore_stopped,
        "ystore_evicted": ystore_evicted,
        "ystore_snapshot_persisted": ystore_snapshot_persisted,
        "scheduler_job_deleted": scheduler_job_deleted,
        "runtime_compaction_requested": runtime_compaction_requested,
        "room_refs_released": room_refs_released,
        "prewarm_after_reset": should_prewarm_after_reset,
        "room_prewarmed": room_prewarmed,
        "room_prewarm_error": room_prewarm_error,
        "owner_handoff_mode": "direct_owner_thread",
    }


def _y_server_runtime_snapshot() -> dict[str, Any]:
    task = _y_server_task
    requested = bool(_y_server_started)
    started_handle = getattr(y_server, "started", None)
    started_event = bool(getattr(started_handle, "is_set", lambda: False)())
    task_running = bool(task is not None and not task.done())
    task_done = bool(task is not None and task.done())
    task_cancelled = bool(task is not None and task.cancelled())
    rooms = getattr(y_server, "rooms", None)
    room_total = len(rooms) if isinstance(rooms, dict) else 0
    room_effective_branches: dict[str, Any] = {}
    if isinstance(rooms, dict):
        for room_name, room in list(rooms.items()):
            room_key = str(room_name or "")
            try:
                clients = getattr(room, "clients", None)
                client_total = len(clients) if hasattr(clients, "__len__") else None
            except Exception:
                client_total = None
            cached_branches = getattr(room, "_diag_effective_branch_snapshot", None)
            room_effective_branches[room_key] = {
                "client_total": client_total,
                "active_events_ws": _active_events_ws_connection_total_for_webspace(room_key),
                "active_yws": _active_yws_connection_total_for_webspace(room_key),
                "active_webrtc": _active_webrtc_peer_total_for_webspace(room_key),
                "branches": cached_branches if isinstance(cached_branches, dict) else {"ready": False, "error": "not_observed"},
            }
    error: str | None = None
    if task_done and not task_cancelled:
        try:
            exc = task.exception()
        except Exception as exc:  # pragma: no cover - defensive runtime snapshot
            error = f"{type(exc).__name__}: {exc}"
        else:
            if exc is not None:
                error = f"{type(exc).__name__}: {exc}"
    ready = bool(requested and started_event and task_running and not error)
    return {
        "requested": requested,
        "started_event": started_event,
        "task_running": task_running,
        "task_done": task_done,
        "task_cancelled": task_cancelled,
        "room_total": room_total,
        "room_effective_branches": room_effective_branches,
        "ready": ready,
        "error": error,
    }


def _gateway_lifecycle_manager() -> str:
    token = str(os.getenv("ADAOS_SUPERVISOR_ENABLED", "0") or "").strip().lower()
    return "supervisor" if token in {"1", "true", "yes", "on"} else "runtime"


def _gateway_transport_ownership_snapshot() -> dict[str, dict[str, Any]]:
    lifecycle_manager = _gateway_lifecycle_manager()
    try:
        from adaos.services import realtime_sidecar as _realtime_sidecar_mod

        route_contract = _realtime_sidecar_mod.realtime_sidecar_route_tunnel_contract()
    except Exception:
        route_contract = {}
    ws_contract = route_contract.get("ws") if isinstance(route_contract.get("ws"), dict) else {}
    yws_contract = route_contract.get("yws") if isinstance(route_contract.get("yws"), dict) else {}
    return {
        "ws": {
            "current_owner": ws_contract.get("current_owner") or "runtime",
            "lifecycle_manager": ws_contract.get("lifecycle_manager") or lifecycle_manager,
            "planned_owner": ws_contract.get("planned_owner") or "sidecar",
            "migration_phase": ws_contract.get("migration_phase") or "phase_2_route_tunnel_ownership",
            "logical_channels": list(
                ws_contract.get("logical_channels")
                or [
                    "hub_member.command",
                    "hub_member.event",
                    "hub_member.presence",
                ]
            ),
            "current_support": ws_contract.get("current_support") or "planned",
            "delegation_mode": ws_contract.get("delegation_mode") or "not_implemented",
            "listener_ready": bool(ws_contract.get("listener_ready")),
            "handoff_ready": bool(ws_contract.get("handoff_ready")),
            "handoff_blockers": list(
                ws_contract.get("blockers")
                or [
                    "browser route websocket still terminates in the runtime FastAPI app",
                ]
            ),
        },
        "yws": {
            "current_owner": yws_contract.get("current_owner") or "runtime",
            "lifecycle_manager": yws_contract.get("lifecycle_manager") or lifecycle_manager,
            "planned_owner": yws_contract.get("planned_owner") or "sidecar",
            "migration_phase": yws_contract.get("migration_phase") or "phase_2_route_tunnel_ownership",
            "logical_channels": list(
                yws_contract.get("logical_channels")
                or [
                    "hub_member.sync",
                ]
            ),
            "current_support": yws_contract.get("current_support") or "planned",
            "delegation_mode": yws_contract.get("delegation_mode") or "not_implemented",
            "listener_ready": bool(yws_contract.get("listener_ready")),
            "handoff_ready": bool(yws_contract.get("handoff_ready")),
            "handoff_blockers": list(
                yws_contract.get("blockers")
                or [
                    "Yjs websocket/session ownership still lives in the runtime gateway",
                ]
            ),
        },
    }


def _build_gateway_transport_snapshot(*, now_ts: float | None = None) -> dict[str, Any]:
    now = time.time() if now_ts is None else float(now_ts)
    with _TRANSPORT_LOCK:
        state = json.loads(json.dumps(_TRANSPORT_STATE))
    for entry in state.values():
        if not isinstance(entry, dict):
            continue
        last_open_at = entry.get("last_open_at")
        last_close_at = entry.get("last_close_at")
        entry["last_open_ago_s"] = (
            round(max(0.0, now - float(last_open_at)), 3)
            if isinstance(last_open_at, (int, float)) and float(last_open_at) > 0.0
            else None
        )
        entry["last_close_ago_s"] = (
            round(max(0.0, now - float(last_close_at)), 3)
            if isinstance(last_close_at, (int, float)) and float(last_close_at) > 0.0
            else None
        )
    yws_state = state.get("yws") if isinstance(state.get("yws"), dict) else None
    if yws_state is not None:
        yws_state.update(_yws_storm_snapshot(now))
    ws_state = state.get("ws") if isinstance(state.get("ws"), dict) else None
    if ws_state is not None:
        ws_state["send_queue"] = _ws_event_send_snapshot()
    room_details, room_aggregates = _room_debug_snapshot_all(now)
    if yws_state is not None:
        yws_state.update(room_aggregates)
    snapshot = {
        "transports": state,
        "servers": {
            "yws": _y_server_runtime_snapshot(),
        },
        "rooms": room_details,
        "commands": _command_trace_snapshot(now),
        "ownership": _gateway_transport_ownership_snapshot(),
        "webio_snapshot_demand": snapshot_demand_snapshot(),
        "updated_at": now,
    }
    # This snapshot crosses worker boundaries in reliability and root-control
    # reporting. Serializing it on the Yjs owner thread guarantees that no
    # bound room method, task, YDoc, transaction, or other y_py wrapper can be
    # retained by the receiving worker through an otherwise innocent-looking
    # diagnostics value.
    plain_snapshot = json.loads(json.dumps(snapshot))
    with _GATEWAY_SNAPSHOT_OWNER_LOCK:
        _GATEWAY_SNAPSHOT_CACHE.clear()
        _GATEWAY_SNAPSHOT_CACHE.update(plain_snapshot)
    return plain_snapshot


async def _build_gateway_transport_snapshot_on_owner(now_ts: float | None) -> dict[str, Any]:
    return _build_gateway_transport_snapshot(now_ts=now_ts)


def gateway_transport_snapshot(*, now_ts: float | None = None) -> dict[str, Any]:
    """Return plain gateway diagnostics without moving live Yjs objects across threads."""

    current_thread_id = threading.get_ident()
    with _GATEWAY_SNAPSHOT_OWNER_LOCK:
        owner_thread_id = _GATEWAY_SNAPSHOT_OWNER_THREAD_ID
        owner_loop = _GATEWAY_SNAPSHOT_OWNER_LOOP

    if owner_thread_id is None or current_thread_id == owner_thread_id:
        return _build_gateway_transport_snapshot(now_ts=now_ts)

    if owner_loop is not None and owner_loop.is_running() and not owner_loop.is_closed():
        future = asyncio.run_coroutine_threadsafe(
            _build_gateway_transport_snapshot_on_owner(now_ts),
            owner_loop,
        )
        try:
            return future.result(timeout=2.0)
        except Exception:
            future.cancel()
            _ylog.debug("gateway diagnostics owner-thread handoff failed", exc_info=True)

    # During shutdown the owner loop can disappear before a final reliability
    # read. Only return the last JSON-normalized snapshot; walking rooms from
    # this worker would reintroduce cross-thread y_py finalization.
    with _GATEWAY_SNAPSHOT_OWNER_LOCK:
        return json.loads(json.dumps(_GATEWAY_SNAPSHOT_CACHE)) if _GATEWAY_SNAPSHOT_CACHE else {}


def _ws_trace_enabled() -> bool:
    return os.getenv("HUB_WS_TRACE", "0") == "1"


def _ws_client_str(websocket: WebSocket) -> str:
    try:
        client = getattr(websocket, "client", None)
        if client and getattr(client, "host", None) is not None:
            return f"{client.host}:{client.port}"
    except Exception:
        pass
    try:
        scope = getattr(websocket, "scope", None) or {}
        client = scope.get("client")
        if isinstance(client, (tuple, list)) and len(client) >= 2:
            return f"{client[0]}:{client[1]}"
    except Exception:
        pass
    return "unknown"


class WorkspaceWebsocketServer(WebsocketServer):
    """
    WebsocketServer that binds each room to a webspace-backed YStore snapshot.

    We use the websocket path as the webspace id (e.g. "default").
    """

    async def get_room(self, name: str) -> YRoom:  # type: ignore[override]
        webspace_id = name or "default"
        room_open_started = time.perf_counter()
        created_room = False
        seed_result: dict[str, Any] | None = None

        _cancel_idle_room_reset(webspace_id)

        # Double-checked locking to prevent concurrent room creation.
        # Without this, multiple concurrent get_room() calls can both pass
        # the `if name not in self.rooms` check and create duplicate rooms,
        # causing the second room to overwrite the first and orphan clients.
        if name not in self.rooms:
            lock = _room_locks.setdefault(webspace_id, asyncio.Lock())
            async with lock:
                bootstrap_attempt_id = ""

                async def _await_bootstrap_step(label: str, awaitable: Any, *, cancel_on_timeout: bool = True) -> Any:
                    _mark_room_bootstrap_step(webspace_id, bootstrap_attempt_id, label)
                    timeout_s = max(float(_YWS_ROOM_BOOTSTRAP_STEP_TIMEOUT_S), 0.0)
                    if timeout_s <= 0.0:
                        return await awaitable
                    if not cancel_on_timeout:
                        task = asyncio.ensure_future(awaitable)
                        try:
                            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout_s)
                        except asyncio.TimeoutError:
                            _mark_room_bootstrap_stuck(
                                webspace_id,
                                bootstrap_attempt_id,
                                step=label,
                                reason=f"{label}_slow_after_{timeout_s:.3f}s",
                            )
                            _ylog.warning(
                                "yws room bootstrap step slow; continuing without cancellation webspace=%s step=%s timeout_s=%.3f",
                                webspace_id,
                                label,
                                timeout_s,
                            )
                            return await asyncio.shield(task)
                    try:
                        return await asyncio.wait_for(awaitable, timeout=timeout_s)
                    except asyncio.TimeoutError:
                        incident = _mark_room_bootstrap_stuck(
                            webspace_id,
                            bootstrap_attempt_id,
                            step=label,
                            reason=f"{label}_timeout_after_{timeout_s:.3f}s",
                        )
                        _ylog.warning(
                            "yws room bootstrap step timeout webspace=%s step=%s timeout_s=%.3f recommended_action=%s",
                            webspace_id,
                            label,
                            timeout_s,
                            incident.get("recommended_action") if isinstance(incident, dict) else None,
                        )
                        raise

                # Second check after acquiring lock - another coroutine may
                # have already created the room while we were waiting.
                if name not in self.rooms:
                    yws_attempt_id = str(_CURRENT_YWS_ATTEMPT_ID.get() or "").strip()
                    bootstrap_attempt_id = _mark_room_bootstrap_started(webspace_id, yws_attempt_id=yws_attempt_id)
                    _ylog.info(
                        "creating YRoom for webspace=%s bootstrap_attempt=%s yws_attempt=%s",
                        webspace_id,
                        bootstrap_attempt_id,
                        yws_attempt_id or None,
                    )
                    room: DiagnosticYRoom | None = None
                    ystore = None
                    try:
                        workspace = await _workspace_bootstrap_snapshot(webspace_id)
                        ystore = get_ystore_for_webspace(webspace_id)
                        space = str(workspace.get("effective_source_mode") or "workspace")
                        row_current_scenario = str(workspace.get("current_scenario_overlay") or "")
                        target_scenario_id = (
                            row_current_scenario
                            or str(workspace.get("effective_home_scenario") or "web_desktop")
                        )
                        prefer_manifest_home = bool(row_current_scenario or workspace.get("home_scenario"))
                        room = DiagnosticYRoom(ready=self.rooms_ready, ystore=ystore, log=self.log)
                        room._webspace_id = webspace_id
                        room._thread_id = threading.get_ident()
                        room._loop = asyncio.get_running_loop()
                        # Ensure periodic in-memory snapshotting for this webspace.
                        try:
                            sched = get_scheduler()
                            await _await_bootstrap_step(
                                "schedule_backup",
                                sched.ensure_every(
                                    name=f"ystores.backup.{webspace_id}",
                                    interval=6000.0,
                                    topic="sys.ystore.backup",
                                    payload={"webspace_id": webspace_id},
                                ),
                            )
                        except Exception:
                            _ylog.warning("failed to register YStore backup job for webspace=%s", webspace_id, exc_info=True)
                        created_room = True
                        bootstrap_materialization = _ROOM_BOOTSTRAP_MATERIALIZATION.get()
                        if not (
                            isinstance(bootstrap_materialization, Mapping)
                            and str(bootstrap_materialization.get("webspace_id") or "").strip() == webspace_id
                            and isinstance(bootstrap_materialization.get("payload"), Mapping)
                        ):
                            bootstrap_materialization = None
                        seed_kwargs: dict[str, Any] = {
                            "webspace_id": webspace_id,
                            "default_scenario_id": target_scenario_id,
                            "space": space,
                            "ydoc": room.ydoc,
                            "prefer_default_scenario": prefer_manifest_home,
                        }
                        if bootstrap_materialization is not None:
                            # The scenario switch already produced the authoritative
                            # branches. Loading persisted state is still required, but
                            # projecting the scenario and emitting scenarios.synced
                            # here would start a second semantic rebuild.
                            seed_kwargs["seed_if_missing"] = False
                        seed_result = await _await_bootstrap_step(
                            "seed_from_scenario",
                            ensure_webspace_seeded_from_scenario(ystore, **seed_kwargs),
                        )
                        if bool((seed_result or {}).get("fresh_ydoc_required")):
                            # The first room doc was partially mutated by a
                            # corrupt replay. Recreate the native owner before
                            # any projection/encoding and retry against the
                            # recovered durable base (or an empty store).
                            room = DiagnosticYRoom(ready=self.rooms_ready, ystore=ystore, log=self.log)
                            room._webspace_id = webspace_id
                            room._thread_id = threading.get_ident()
                            room._loop = asyncio.get_running_loop()
                            seed_kwargs["ydoc"] = room.ydoc
                            recovered_seed = await _await_bootstrap_step(
                                "seed_after_corrupt_replay",
                                ensure_webspace_seeded_from_scenario(ystore, **seed_kwargs),
                            )
                            seed_result = dict(recovered_seed or {})
                            seed_result["room_recreated_after_corrupt_replay"] = True
                        if bootstrap_materialization is not None:
                            materialized_update, materialized_result = await _await_bootstrap_step(
                                "apply_materialized_payload",
                                _apply_room_materialized_payload(
                                    webspace_id,
                                    ystore,
                                    room,
                                    bootstrap_materialization["payload"],
                                    reason=str(bootstrap_materialization.get("reason") or "room_bootstrap"),
                                    persist_repair=bool(bootstrap_materialization.get("persist_repair", True)),
                                    force_full_state_update=bool(
                                        bootstrap_materialization.get("force_full_state_update", False)
                                    ),
                                    materialization_identity=bootstrap_materialization.get(
                                        "materialization_identity"
                                    ),
                                ),
                            )
                            if not bool((materialized_result or {}).get("ready")):
                                raise RuntimeError("room bootstrap materialized payload was not ready")
                            bootstrap_ready = await _await_bootstrap_step(
                                "finalize_materialized_bootstrap",
                                _finalize_materialized_room_bootstrap(
                                    webspace_id,
                                    ystore,
                                    room,
                                    scenario_id=str(
                                        bootstrap_materialization["payload"].get("scenario_id")
                                        or target_scenario_id
                                    ),
                                    space=space,
                                ),
                            )
                            seed_result = dict(seed_result or {})
                            seed_result.update(
                                {
                                    "mode": "materialized_payload",
                                    "room_effective_materialized": True,
                                    "room_effective_materialized_persisted": bool(materialized_update),
                                    "room_effective_materialized_bytes": len(materialized_update or b""),
                                    "room_bootstrap_marker_persisted": bool(
                                        (bootstrap_ready or {}).get("persisted")
                                    ),
                                }
                            )
                            room._bootstrap_materialization_handoff = {
                                "request": bootstrap_materialization,
                                "update": bytes(materialized_update or b""),
                                "result": dict(materialized_result or {}),
                            }
                        else:
                            await _await_bootstrap_step(
                                "effective_materialized",
                                _ensure_room_effective_materialized(
                                    webspace_id,
                                    ystore,
                                    room,
                                    seed_result=seed_result,
                                ),
                            )
                        await _await_bootstrap_step(
                            "finalize_rebuild_status",
                            _finalize_room_bootstrap_rebuild_status(
                                webspace_id,
                                seed_result=seed_result,
                                room=room,
                            ),
                        )
                        self.rooms[name] = room
                        _mark_room_created(webspace_id, room)
                        _mark_room_bootstrap_finished(webspace_id, bootstrap_attempt_id, state="ready")
                    except BaseException as exc:
                        if isinstance(exc, asyncio.TimeoutError):
                            bootstrap_state = "timeout"
                        elif isinstance(exc, asyncio.CancelledError):
                            bootstrap_state = "cancelled"
                        else:
                            bootstrap_state = "failed"
                        _mark_room_bootstrap_finished(
                            webspace_id,
                            bootstrap_attempt_id,
                            state=bootstrap_state,
                            error=f"{type(exc).__name__}: {exc}",
                        )
                        self.rooms.pop(name, None)
                        _room_locks.pop(webspace_id, None)
                        if ystore is not None:
                            try:
                                await asyncio.wait_for(
                                    evict_ystore_for_webspace(
                                        webspace_id,
                                        store=ystore,
                                        persist_snapshot=False,
                                        compact_runtime=False,
                                        backup_kind="room_bootstrap_failed",
                                    ),
                                    timeout=max(float(_YWS_ROOM_STALE_RECOVERY_TIMEOUT_S), 0.25),
                                )
                            except Exception:
                                _ylog.warning("failed to evict YStore after room bootstrap failure webspace=%s", webspace_id, exc_info=True)
                        raise
        room = self.rooms[name]
        cached_snapshot = getattr(room, "_diag_effective_branch_snapshot", None)
        effective_ready = bool(isinstance(cached_snapshot, dict) and cached_snapshot.get("ready"))
        if not effective_ready:
            effective_ready = _room_effective_top_level_ready(getattr(room, "ydoc", None))
        if not effective_ready:
            repair_update = await _repair_room_effective_branches(
                webspace_id,
                getattr(room, "ystore", None),
                room,
                reason="room_open_missing_effective_branches",
            )
            if repair_update:
                try:
                    room._diag_effective_repair_total += 1
                    room._diag_effective_repair_bytes += len(repair_update)
                    room._diag_effective_branch_snapshot = _room_effective_branch_snapshot(room.ydoc)
                except Exception:
                    pass
                self.log.warning(
                    "repaired missing YRoom effective branches before open webspace=%s repair_bytes=%s",
                    webspace_id,
                    len(repair_update),
                )
        else:
            try:
                if not (isinstance(cached_snapshot, dict) and cached_snapshot.get("ready")):
                    room._diag_effective_branch_snapshot = _room_effective_branch_snapshot(room.ydoc)
                    room._diag_effective_last_full_check_mono = time.monotonic()
            except Exception:
                pass
        room._webspace_id = webspace_id
        room._thread_id = getattr(room, "_thread_id", threading.get_ident())
        room._loop = getattr(room, "_loop", asyncio.get_running_loop())
        try:
            attach_room_observers(webspace_id, room.ydoc)
        except Exception:
            _ylog.warning("attach_room_observers failed for webspace=%s", webspace_id, exc_info=True)
        try:
            await self.start_room(room)
        except RuntimeError as exc:
            if "YRoom already running" not in str(exc):
                raise
            _ylog.warning(
                "YRoom start skipped because room is already running webspace=%s",
                webspace_id,
            )
        _mark_room_open(
            webspace_id,
            room,
            created=created_room,
            open_total_ms=(time.perf_counter() - room_open_started) * 1000.0,
            seed_result=seed_result,
        )
        try:
            from adaos.services.named_entity_projection import notify_named_entity_room_ready

            await notify_named_entity_room_ready(webspace_id)
        except Exception:
            _ylog.debug(
                "failed to schedule named-entity projection after YRoom open webspace=%s",
                webspace_id,
                exc_info=True,
            )
        if _ylog.isEnabledFor(logging.DEBUG):
            try:
                ui_map = room.ydoc.get_map("ui")
                data_map = room.ydoc.get_map("data")
                ui_keys = list(ui_map.keys())
                data_keys = list(data_map.keys())
                # y_py YMap objects are thread-affine. Keep only plain lists in
                # diagnostics locals so later cross-thread frame sampling cannot
                # drop a live YMap on the wrong thread.
                del ui_map
                del data_map
                room._diag_effective_branch_snapshot = {
                    "ready": _room_effective_top_level_ready(room.ydoc),
                    "mode": "top_level_debug",
                }
                _ylog.debug(
                    "YRoom ready webspace=%s ui keys=%s data keys=%s",
                    webspace_id,
                    ui_keys,
                    data_keys,
                )
            except Exception:
                _ylog.warning("failed to inspect YDoc for webspace=%s", webspace_id, exc_info=True)
        return room


y_server = WorkspaceWebsocketServer(auto_clean_rooms=False)


def live_webspace_ids(*, require_transport: bool = False) -> list[str]:
    """Return already-created room ids without opening or seeding YDocs."""
    room_ids = [
        str(item or "").strip()
        for item in getattr(y_server, "rooms", {}).keys()
        if str(item or "").strip()
    ]
    if require_transport:
        room_ids = [item for item in room_ids if _webspace_has_live_transports(item)]
    return sorted(set(room_ids))


_y_server_started = False
_y_server_task: asyncio.Task[None] | None = None
_room_locks: dict[str, asyncio.Lock] = {}


def _task_exception_summary(task: asyncio.Task[Any] | None) -> str | None:
    if task is None or not task.done() or task.cancelled():
        return None
    try:
        exc = task.exception()
    except BaseException as exc:  # pragma: no cover - defensive diagnostics
        return f"{type(exc).__name__}: {exc}"
    if exc is None:
        return None
    return f"{type(exc).__name__}: {exc}"


def _on_y_server_task_done(task: asyncio.Task[None]) -> None:
    summary = _task_exception_summary(task)
    if summary:
        _ylog.error("Yjs websocket server background task stopped unexpectedly: %s", summary)


def _recreate_y_server_after_failure(reason: str) -> None:
    global y_server, _y_server_started, _y_server_task
    old_server = y_server
    try:
        for room in list(getattr(old_server, "rooms", {}).values()):
            try:
                stop_room = getattr(room, "stop", None)
                if callable(stop_room):
                    stop_room()
            except Exception:
                pass
    except Exception:
        pass
    try:
        stop_server = getattr(old_server, "stop", None)
        if callable(stop_server):
            stop_server()
    except Exception:
        pass
    y_server = WorkspaceWebsocketServer(auto_clean_rooms=False)
    _room_locks.clear()
    _y_server_started = False
    _y_server_task = None
    _ylog.warning("Yjs websocket server runtime recreated after failure reason=%s", reason)


def _room_branch_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        return value.get(key)
    getter = getattr(value, "get", None)
    if callable(getter):
        try:
            return getter(key)
        except Exception:
            return None
    return None


def _room_branch_is_mapping(value: Any) -> bool:
    if isinstance(value, dict):
        return True
    return callable(getattr(value, "get", None)) and callable(getattr(value, "keys", None))


def _room_branch_keys(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(key) for key in value.keys()]
    keys = getattr(value, "keys", None)
    if not callable(keys):
        return []
    try:
        return [str(key) for key in keys()]
    except Exception:
        return []


def _room_branch_items(value: Any) -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        return [(str(key), item) for key, item in value.items()]
    items = getattr(value, "items", None)
    if callable(items):
        try:
            return [(str(key), item) for key, item in items()]
        except Exception:
            return []
    return []


def _room_optional_token(value: Any) -> str | None:
    if value is None:
        return None
    token = str(value).strip()
    return token or None


def _room_current_scenario(ydoc: Any) -> str | None:
    try:
        ui_map = ydoc.get_map("ui")
        return _room_optional_token(ui_map.get("current_scenario"))
    except Exception:
        return None


def _room_materialized_scenario(ydoc: Any) -> str | None:
    try:
        runtime_map = ydoc.get_map("runtime")
        environment = runtime_map.get("environment")
        materialization = _room_branch_get(environment, "materialization")
        return _room_optional_token(_room_branch_get(materialization, "scenario_id"))
    except Exception:
        return None


def _room_materialization_mismatch(ydoc: Any) -> bool:
    current = _room_current_scenario(ydoc)
    materialized = _room_materialized_scenario(ydoc)
    if current and not materialized:
        return True
    return bool(current and materialized and current != materialized)


def _room_effective_branches_ready(ydoc: Any) -> bool:
    try:
        return not _room_materialization_mismatch(ydoc) and not _room_effective_missing_required_branches(ydoc)
    except Exception:
        return False


def _room_effective_application_ready(application: Any) -> bool:
    if not _room_branch_is_mapping(application) or not _room_branch_keys(application):
        return False
    desktop = _room_branch_get(application, "desktop")
    modals = _room_branch_get(application, "modals")
    if not _room_branch_is_mapping(desktop) or not _room_branch_keys(desktop):
        return False
    if not _room_branch_is_mapping(_room_branch_get(desktop, "pageSchema")):
        return False
    if (
        not _room_branch_is_mapping(modals)
        or _room_branch_get(modals, "apps_catalog") is None
        or _room_branch_get(modals, "widgets_catalog") is None
    ):
        return False
    return True


def _room_effective_catalog_ready(catalog: Any) -> bool:
    return (
        _room_branch_is_mapping(catalog)
        and isinstance(_room_branch_get(catalog, "apps"), list)
        and isinstance(_room_branch_get(catalog, "widgets"), list)
    )


def _room_effective_installed_ready(installed: Any) -> bool:
    return (
        _room_branch_is_mapping(installed)
        and isinstance(_room_branch_get(installed, "apps"), list)
        and isinstance(_room_branch_get(installed, "widgets"), list)
    )


def _room_effective_data_desktop_ready(desktop: Any) -> bool:
    return _room_branch_is_mapping(desktop)


def _room_effective_required_branches(ydoc: Any) -> tuple[str, ...]:
    try:
        runtime_map = ydoc.get_map("runtime")
        environment = runtime_map.get("environment")
        materialization = _room_branch_get(environment, "materialization")
        if _room_branch_is_mapping(materialization):
            required = _normalize_required_branch_list(_room_branch_get(materialization, "required_branches"))
            if required:
                return required
    except Exception:
        pass
    return _yroom_effective_env_required_branches()


def _room_effective_branch_value(ydoc: Any, path: str) -> Any:
    parts = [part for part in str(path or "").split(".") if part]
    if len(parts) < 2:
        return None
    try:
        current = ydoc.get_map(parts[0])
    except Exception:
        return None
    for part in parts[1:]:
        try:
            current = _room_branch_get(current, part)
        except Exception:
            return None
        if current is None:
            return None
    return current


def _room_effective_required_branch_ready(path: str, value: Any) -> bool:
    if path == "ui.application":
        return _room_effective_application_ready(value)
    if path == "data.catalog":
        return _room_effective_catalog_ready(value)
    if path == "data.installed":
        return _room_effective_installed_ready(value)
    if path == "data.desktop":
        return _room_effective_data_desktop_ready(value)
    return value is not None


def _room_effective_missing_required_branches(ydoc: Any) -> list[str]:
    missing: list[str] = []
    for path in _room_effective_required_branches(ydoc):
        value = _room_effective_branch_value(ydoc, path)
        if not _room_effective_required_branch_ready(path, value):
            missing.append(path)
    return missing


def _room_effective_missing_required_data_keys(data_map: Any) -> list[str]:
    missing: list[str] = []
    for path in _yroom_effective_env_required_branches():
        if not path.startswith("data."):
            continue
        key = path.split(".", 1)[1]
        try:
            value = data_map.get(key)
        except Exception:
            value = None
        if value is None:
            missing.append(key)
    return missing


def _room_effective_top_level_ready(ydoc: Any) -> bool:
    """
    Cheap hot-path invariant check for the shared desktop document.

    The full effective snapshot intentionally materializes several large Yjs
    branches and is too expensive to run for every update. This top-level check
    catches the destructive class that removes required roots/branches while
    keeping ordinary Yjs fanout cheap.
    """
    if not _YROOM_EFFECTIVE_GUARD_TOP_LEVEL_CHECKS:
        return True
    try:
        return _room_effective_branches_ready(ydoc)
    except Exception:
        return False


def _branch_collection_count(value: Any) -> int:
    if isinstance(value, (dict, list, tuple, set)):
        return len(value)
    if _room_branch_is_mapping(value):
        return len(_room_branch_keys(value))
    return 0


def _branch_json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth >= 5:
        return "<max_depth>"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {
            str(key): _branch_json_safe(item, depth=depth + 1)
            for key, item in sorted(value.items(), key=lambda item: str(item[0]))[:80]
        }
    if _room_branch_is_mapping(value):
        return {
            str(key): _branch_json_safe(item, depth=depth + 1)
            for key, item in sorted(_room_branch_items(value), key=lambda item: str(item[0]))[:80]
        }
    if isinstance(value, (list, tuple)):
        return [_branch_json_safe(item, depth=depth + 1) for item in list(value)[:80]]
    return repr(value)[:200]


def _branch_hash(value: Any) -> str | None:
    if value is None:
        return None
    try:
        payload = json.dumps(
            _branch_json_safe(value),
            sort_keys=True,
            ensure_ascii=True,
            separators=(",", ":"),
        )
    except Exception:
        payload = repr(value)
    return hashlib.sha1(payload.encode("utf-8", errors="replace")).hexdigest()[:12]


def _room_effective_branch_snapshot(ydoc: Any) -> dict[str, Any]:
    if ydoc is None:
        return {"ready": False, "error": "missing_ydoc"}
    if not _YROOM_EFFECTIVE_GUARD_SNAPSHOT_DETAILS:
        try:
            required_branches = list(_room_effective_required_branches(ydoc))
            missing_required_branches = _room_effective_missing_required_branches(ydoc)
        except Exception:
            required_branches = []
            missing_required_branches = []
        current_scenario = _room_current_scenario(ydoc)
        materialized_scenario = _room_materialized_scenario(ydoc)
        materialization_mismatch = _room_materialization_mismatch(ydoc)
        return {
            "ready": _room_effective_top_level_ready(ydoc),
            "mode": "top_level_snapshot",
            "details": "disabled",
            "required_branches": required_branches,
            "missing_required_branches": missing_required_branches,
            "current_scenario": current_scenario,
            "materialized_scenario": materialized_scenario,
            "materialization_mismatch": materialization_mismatch,
        }
    try:
        ui_map = ydoc.get_map("ui")
        data_map = ydoc.get_map("data")
        registry_map = ydoc.get_map("registry")
        ui_keys = [str(key) for key in list(ui_map.keys())[:40]]
        data_keys = [str(key) for key in list(data_map.keys())[:80]]
        registry_keys = [str(key) for key in list(registry_map.keys())[:40]]
        application = ui_map.get("application")
        application_desktop = _room_branch_get(application, "desktop")
        modals = _room_branch_get(application, "modals")
        catalog = data_map.get("catalog")
        installed = data_map.get("installed")
        desktop = data_map.get("desktop")
        required_branches = list(_room_effective_required_branches(ydoc))
        missing_required_branches = _room_effective_missing_required_branches(ydoc)
        current_scenario = _room_current_scenario(ydoc)
        materialized_scenario = _room_materialized_scenario(ydoc)
        materialization_mismatch = _room_materialization_mismatch(ydoc)
        snapshot = {
            "ready": _room_effective_branches_ready(ydoc),
            "ui_keys": ui_keys,
            "data_keys": data_keys,
            "registry_keys": registry_keys,
            "required_branches": required_branches,
            "missing_required_branches": missing_required_branches,
            "current_scenario": current_scenario,
            "materialized_scenario": materialized_scenario,
            "materialization_mismatch": materialization_mismatch,
            "has_application": _room_branch_is_mapping(application) and bool(_room_branch_keys(application)),
            "has_application_desktop": _room_branch_is_mapping(application_desktop)
            and bool(_room_branch_keys(application_desktop)),
            "has_application_page_schema": _room_branch_is_mapping(_room_branch_get(application_desktop, "pageSchema")),
            "modal_count": _branch_collection_count(modals),
            "has_apps_catalog_modal": _room_branch_is_mapping(modals)
            and _room_branch_get(modals, "apps_catalog") is not None,
            "has_widgets_catalog_modal": _room_branch_is_mapping(modals)
            and _room_branch_get(modals, "widgets_catalog") is not None,
            "has_catalog_apps": isinstance(_room_branch_get(catalog, "apps"), list),
            "has_catalog_widgets": isinstance(_room_branch_get(catalog, "widgets"), list),
            "has_installed_apps": isinstance(_room_branch_get(installed, "apps"), list),
            "has_installed_widgets": isinstance(_room_branch_get(installed, "widgets"), list),
            "has_data_desktop": _room_branch_is_mapping(desktop),
            "catalog_app_count": _branch_collection_count(_room_branch_get(catalog, "apps")),
            "catalog_widget_count": _branch_collection_count(_room_branch_get(catalog, "widgets")),
            "installed_key_count": _branch_collection_count(installed),
            "installed_app_count": _branch_collection_count(_room_branch_get(installed, "apps")),
            "installed_widget_count": _branch_collection_count(_room_branch_get(installed, "widgets")),
            "desktop_key_count": _branch_collection_count(desktop),
            "desktop_widget_count": _branch_collection_count(_room_branch_get(desktop, "widgets")),
        }
        if _YROOM_EFFECTIVE_GUARD_SNAPSHOT_HASHES:
            snapshot.update(
                {
                    "application_hash": _branch_hash(application),
                    "catalog_hash": _branch_hash(catalog),
                    "installed_hash": _branch_hash(installed),
                    "desktop_hash": _branch_hash(desktop),
                }
            )
        return snapshot
    except Exception as exc:
        return {
            "ready": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _materialized_payload_apply_ready_snapshot(
    payload: Mapping[str, Any],
    apply_summary: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    if not _YJS_MATERIALIZED_PAYLOAD_TRUST_APPLY_SUMMARY or _YROOM_EFFECTIVE_GUARD_STRICT_FULL_CHECKS:
        return None
    summary = apply_summary if isinstance(apply_summary, Mapping) else {}
    try:
        failed_count = int(summary.get("failed_branches") or 0)
    except Exception:
        failed_count = 0
    failed_paths = summary.get("failed_paths") if isinstance(summary.get("failed_paths"), list) else []
    if failed_count > 0 or failed_paths:
        return None
    try:
        trusted_skip_count = int(summary.get("trusted_fingerprint_unchanged_branches") or 0)
    except Exception:
        trusted_skip_count = 0
    try:
        stale_fingerprint_count = int(summary.get("stale_fingerprint_branches") or 0)
    except Exception:
        stale_fingerprint_count = 0
    if trusted_skip_count > 0 or stale_fingerprint_count > 0:
        return None
    scenario_id = str(payload.get("scenario_id") or "").strip()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), Mapping) else {}
    materialization = (
        metadata.get("materialization")
        if isinstance(metadata, Mapping) and isinstance(metadata.get("materialization"), Mapping)
        else {}
    )
    required = _normalize_required_branch_list(
        materialization.get("required_branches") if isinstance(materialization, Mapping) else None
    )
    if not required:
        required = _yroom_effective_env_required_branches()
    return {
        "ready": True,
        "mode": "materialized_payload_apply_summary",
        "details": "trusted_apply_summary",
        "required_branches": list(required),
        "missing_required_branches": [],
        "current_scenario": scenario_id or None,
        "materialized_scenario": scenario_id or None,
        "materialization_mismatch": False,
    }


async def _apply_room_materialized_payload_on_owner_loop(
    webspace_id: str,
    ystore: Any,
    room: Any,
    payload: Mapping[str, Any],
    *,
    reason: str,
    persist_repair: bool = True,
    force_full_state_update: bool = False,
    materialization_identity: Mapping[str, Any] | None = None,
) -> tuple[bytes, str, dict[str, Any]]:
    handoff_started = time.perf_counter()
    owner_thread = getattr(room, "_thread_id", None)
    owner_loop = getattr(room, "_loop", None)
    current_thread = threading.get_ident()
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    if owner_thread is not None and owner_thread != current_thread:
        if owner_loop is None or not owner_loop.is_running():
            handoff_ms = _elapsed_ms_since(handoff_started)
            return b"", "skipped_no_owner_loop", {
                "ok": False,
                "ready": False,
                "error": "owner_loop_not_running",
                "owner_handoff_mode": "skipped_no_owner_loop",
                "phase_timings_ms": {"owner_handoff": handoff_ms, "total": handoff_ms},
            }
        future = asyncio.run_coroutine_threadsafe(
            _apply_room_materialized_payload(
                webspace_id,
                ystore,
                room,
                payload,
                reason=reason,
                persist_repair=bool(persist_repair),
                force_full_state_update=bool(force_full_state_update),
                materialization_identity=materialization_identity,
            ),
            owner_loop,
        )
        update, result = await asyncio.wrap_future(future)
        result = dict(result or {})
        phase_timings = dict(result.get("phase_timings_ms") or {})
        phase_timings["owner_handoff"] = _elapsed_ms_since(handoff_started)
        result["phase_timings_ms"] = phase_timings
        result["owner_handoff_mode"] = "threadsafe_owner_loop"
        return update, "threadsafe_owner_loop", result

    if (
        owner_loop is not None
        and current_loop is not None
        and owner_loop is not current_loop
        and owner_loop.is_running()
    ):
        future = asyncio.run_coroutine_threadsafe(
            _apply_room_materialized_payload(
                webspace_id,
                ystore,
                room,
                payload,
                reason=reason,
                persist_repair=bool(persist_repair),
                force_full_state_update=bool(force_full_state_update),
                materialization_identity=materialization_identity,
            ),
            owner_loop,
        )
        update, result = await asyncio.wrap_future(future)
        result = dict(result or {})
        phase_timings = dict(result.get("phase_timings_ms") or {})
        phase_timings["owner_handoff"] = _elapsed_ms_since(handoff_started)
        result["phase_timings_ms"] = phase_timings
        result["owner_handoff_mode"] = "loop_owner_loop"
        return update, "loop_owner_loop", result

    update, result = await _apply_room_materialized_payload(
        webspace_id,
        ystore,
        room,
        payload,
        reason=reason,
        persist_repair=bool(persist_repair),
        force_full_state_update=bool(force_full_state_update),
        materialization_identity=materialization_identity,
    )
    result = dict(result or {})
    phase_timings = dict(result.get("phase_timings_ms") or {})
    phase_timings["owner_handoff"] = 0.0
    result["phase_timings_ms"] = phase_timings
    result["owner_handoff_mode"] = "direct_owner_context"
    return update, "direct_owner_context", result


async def _apply_room_materialized_payload(
    webspace_id: str,
    ystore: Any,
    room: Any,
    payload: Mapping[str, Any],
    *,
    reason: str,
    persist_repair: bool = True,
    force_full_state_update: bool = False,
    materialization_identity: Mapping[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    total_started = time.perf_counter()
    phase_timings_ms: dict[str, float] = {}
    ydoc = getattr(room, "ydoc", None)
    if ydoc is None:
        phase_timings_ms["total"] = _elapsed_ms_since(total_started)
        return b"", {"ok": False, "ready": False, "error": "missing_ydoc", "phase_timings_ms": phase_timings_ms}
    if not isinstance(payload, Mapping):
        phase_timings_ms["total"] = _elapsed_ms_since(total_started)
        return b"", {"ok": False, "ready": False, "error": "missing_materialized_payload", "phase_timings_ms": phase_timings_ms}
    payload_scenario = str(payload.get("scenario_id") or "").strip()
    previous_authoritative_scenario = _authoritative_current_scenario(webspace_id)
    try:
        import y_py as Y  # pylint: disable=import-outside-toplevel
        from adaos.services.scenario.webspace_runtime import WebspaceScenarioRuntime  # pylint: disable=import-outside-toplevel

        stage_started = time.perf_counter()
        before = Y.encode_state_vector(ydoc)
        phase_timings_ms["encode_state_vector"] = _elapsed_ms_since(stage_started)
        runtime = WebspaceScenarioRuntime()
        # Publish the selector authority before mutating the room.  The room
        # observer fans YDoc updates out asynchronously; without this ordering
        # an already-connected browser can merge and re-emit its stale selector
        # between the atomic materialization commit and the old post-apply
        # lease publication.
        if payload_scenario:
            note_authoritative_current_scenario(
                webspace_id,
                payload_scenario,
                reason=f"{reason}:materialized_prepare",
            )
        suppress_attr = "_suppress_backend_ystore_persist"
        previous_suppress = int(getattr(room, suppress_attr, 0) or 0)
        stage_started = time.perf_counter()
        if not persist_repair:
            setattr(room, suppress_attr, previous_suppress + 1)
            deadline_attr = "_suppress_backend_ystore_persist_until"
            try:
                current_deadline = float(getattr(room, deadline_attr, 0.0) or 0.0)
            except Exception:
                current_deadline = 0.0
            setattr(room, deadline_attr, max(current_deadline, time.monotonic() + 5.0))
        phase_timings_ms["persist_suppression_setup"] = _elapsed_ms_since(stage_started)
        try:
            stage_started = time.perf_counter()
            previous_payload = getattr(room, "_last_materialized_payload", None)
            if not isinstance(previous_payload, Mapping):
                previous_payload = None
            try:
                with ystore_write_metadata_sync(
                    root_names=["ui", "data", "registry", "runtime"],
                    source=f"yjs.gateway_ws.{reason}.materialized_payload",
                    owner="core:yjs_gateway",
                    channel="core.yjs.gateway.materialized_payload",
                    governed=True,
                ):
                    runtime.apply_materialized_payload_to_doc(
                        ydoc,
                        webspace_id,
                        payload,
                        materialization_identity=materialization_identity,
                        previous_payload=previous_payload,
                        # Scenario navigation is an explicit projection
                        # boundary.  Do not let a stale persisted fingerprint
                        # turn mere branch presence into proof that the live
                        # room contains the requested scenario.  Other rebuilds
                        # retain the bounded fingerprint fast path.
                        verify_branch_fingerprints="scenario_switch" in str(reason or "").lower(),
                    )
            finally:
                phase_timings_ms["branch_apply"] = _elapsed_ms_since(stage_started)
            try:
                invalidate_live_map_value_cache(webspace_id)
            except Exception:
                pass
        finally:
            stage_started = time.perf_counter()
            if not persist_repair:
                setattr(room, suppress_attr, previous_suppress)
            phase_timings_ms["persist_suppression_restore"] = _elapsed_ms_since(stage_started)

        stage_started = time.perf_counter()
        apply_summary = getattr(runtime, "_last_apply_summary", None)
        snapshot = _materialized_payload_apply_ready_snapshot(payload, apply_summary)
        if snapshot is None:
            snapshot = _room_effective_branch_snapshot(ydoc)
        else:
            phase_timings_ms["effective_snapshot_trusted"] = 0.0
        phase_timings_ms["effective_snapshot"] = _elapsed_ms_since(stage_started)
        ready = bool(snapshot.get("ready"))
        if not ready:
            phase_timings_ms["total"] = _elapsed_ms_since(total_started)
            return b"", {
                "ok": False,
                "ready": False,
                "snapshot": snapshot,
                "semantic_timings_ms": getattr(runtime, "_last_rebuild_timings_ms", None),
                "apply_summary": apply_summary,
                "phase_timings_ms": phase_timings_ms,
            }
        payload_ui = payload.get("ui") if isinstance(payload.get("ui"), Mapping) else {}
        committed_scenario = str(
            payload_ui.get("current_scenario")
            or snapshot.get("current_scenario")
            or ""
        ).strip()
        if committed_scenario:
            note_authoritative_current_scenario(
                webspace_id,
                committed_scenario,
                reason=f"{reason}:materialized_commit",
            )
        stage_started = time.perf_counter()
        state_vector: bytes | None = None
        full_state_update = b""
        if force_full_state_update:
            update = Y.encode_state_as_update(ydoc, before)  # type: ignore[arg-type]
            phase_timings_ms["encode_update"] = _elapsed_ms_since(stage_started)
            stage_started = time.perf_counter()
            full_state_update = Y.encode_state_as_update(ydoc)  # type: ignore[arg-type]
            state_vector = Y.encode_state_vector(ydoc)  # type: ignore[arg-type]
            phase_timings_ms["encode_full_state_update"] = _elapsed_ms_since(stage_started)
        else:
            update = Y.encode_state_as_update(ydoc, before)  # type: ignore[arg-type]
            phase_timings_ms["encode_update"] = _elapsed_ms_since(stage_started)
            phase_timings_ms["encode_full_state_update"] = 0.0
        full_state_snapshot_result: dict[str, Any] | None = None
        full_state_snapshot_persisted = False
        snapshot_update = full_state_update if force_full_state_update else b""
        if snapshot_update and force_full_state_update and ystore is not None and persist_repair:
            stage_started = time.perf_counter()
            try:
                async with ystore_write_metadata(
                    root_names=["ui", "data", "registry", "runtime"],
                    source=f"yjs.gateway_ws.{reason}.materialized_payload_full_state",
                    owner="core:yjs_gateway",
                    channel="core.yjs.gateway.materialized_payload_full_state",
                    governed=True,
                ):
                    replace_snapshot = getattr(ystore, "replace_snapshot_update", None)
                    if callable(replace_snapshot):
                        replace_result = await replace_snapshot(
                            snapshot_update,
                            state_vector=state_vector,
                            backup_kind=f"{reason}.materialized_payload_full_state",
                            persist_snapshot=True,
                            notify=False,
                        )
                        if isinstance(replace_result, Mapping):
                            full_state_snapshot_result = dict(replace_result)
                            full_state_snapshot_persisted = bool(replace_result.get("ok"))
                        else:
                            full_state_snapshot_result = {"ok": replace_result is not None}
                            full_state_snapshot_persisted = replace_result is not None
                    else:
                        write_update = getattr(ystore, "write_update", None)
                        if callable(write_update):
                            write_result = await write_update(
                                snapshot_update,
                                update_kind="snapshot",
                                state_vector=state_vector,
                                notify=False,
                            )
                            full_state_snapshot_result = {"ok": bool(write_result), "mode": "write_update_snapshot"}
                            full_state_snapshot_persisted = bool(write_result)
            except Exception as exc:
                full_state_snapshot_result = {
                    "ok": False,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                _ylog.warning(
                    "failed to persist full materialized Yjs snapshot webspace=%s reason=%s",
                    webspace_id,
                    reason,
                    exc_info=True,
                )
            phase_timings_ms["persist_full_state_snapshot"] = _elapsed_ms_since(stage_started)
        else:
            phase_timings_ms["persist_full_state_snapshot"] = 0.0
        if force_full_state_update and persist_repair and ystore is not None and not full_state_snapshot_persisted:
            update = snapshot_update or update
            phase_timings_ms["broadcast_full_state_fallback"] = 1.0
        else:
            phase_timings_ms["broadcast_full_state_fallback"] = 0.0
        broadcast_marker: dict[str, Any] = {}
        if update:
            stage_started = time.perf_counter()
            mark_backend_room_update(
                webspace_id,
                update,
                source=f"yjs.gateway_ws.{reason}.materialized_payload",
                owner="core:yjs_gateway",
                channel="core.yjs.gateway.materialized_payload",
                root_names=["ui", "data", "registry", "runtime"],
                already_persisted=bool((not persist_repair) or full_state_snapshot_persisted),
                governed=True,
            )
            phase_timings_ms["mark_backend_update"] = _elapsed_ms_since(stage_started)
            phase_timings_ms["total"] = _elapsed_ms_since(total_started)
            broadcast_marker = _register_live_refresh_update(
                webspace_id,
                update,
                reason=f"{reason}.materialized_payload",
                phase_timings_ms=phase_timings_ms,
            )
            marker_key = _live_refresh_update_key(webspace_id, update)
            if marker_key is not None:
                try:
                    setattr(room, "_diag_live_refresh_pending_key", marker_key)
                except Exception:
                    pass
        else:
            phase_timings_ms["mark_backend_update"] = 0.0
            phase_timings_ms["total"] = _elapsed_ms_since(total_started)
        direct_client_broadcast_count = 0
        direct_client_broadcast_failed = 0
        client_broadcast_update = (
            full_state_update
            if force_full_state_update and full_state_update
            else update
        )
        if client_broadcast_update:
            stage_started = time.perf_counter()
            clients = list(getattr(room, "clients", []) or [])
            if clients:
                message = create_update_message(bytes(client_broadcast_update))

                async def _send_materialized_update(client: Any) -> bool:
                    try:
                        await client.send(message)
                        return True
                    except Exception:
                        return False

                delivery_results = await asyncio.gather(
                    *(_send_materialized_update(client) for client in clients)
                )
                direct_client_broadcast_count = sum(1 for delivered in delivery_results if delivered)
                direct_client_broadcast_failed = len(delivery_results) - direct_client_broadcast_count
            phase_timings_ms["direct_client_broadcast"] = _elapsed_ms_since(stage_started)
        else:
            phase_timings_ms["direct_client_broadcast"] = 0.0
        phase_timings_ms["total"] = _elapsed_ms_since(total_started)
        try:
            setattr(room, "_last_materialized_payload", _compact_materialized_payload_for_room_history(payload))
        except Exception:
            try:
                setattr(room, "_last_materialized_payload", dict(payload))
            except Exception:
                pass
        return bytes(update or b""), {
            "ok": True,
            "ready": True,
            "snapshot": snapshot,
            "semantic_timings_ms": getattr(runtime, "_last_rebuild_timings_ms", None),
            "apply_summary": apply_summary,
            "phase_timings_ms": phase_timings_ms,
            "broadcast_diagnostics": broadcast_marker,
            "force_full_state_update": bool(force_full_state_update),
            "full_state_snapshot_persisted": bool(full_state_snapshot_persisted),
            "full_state_snapshot_result": full_state_snapshot_result,
            "broadcast_update_bytes": len(update or b""),
            "full_state_update_bytes": len(full_state_update or b""),
            "direct_client_broadcast_count": int(direct_client_broadcast_count),
            "direct_client_broadcast_failed": int(direct_client_broadcast_failed),
            "direct_client_broadcast_bytes": len(client_broadcast_update or b""),
        }
    except BaseException as exc:
        if _is_control_flow_base_exception(exc):
            raise
        if payload_scenario and _authoritative_current_scenario(webspace_id) == payload_scenario:
            if previous_authoritative_scenario:
                note_authoritative_current_scenario(
                    webspace_id,
                    previous_authoritative_scenario,
                    reason=f"{reason}:materialized_rollback",
                )
            else:
                _clear_authoritative_current_scenario(
                    webspace_id,
                    reason=f"{reason}:materialized_failed",
                )
        _ylog.warning(
            "YRoom materialized payload apply failed webspace=%s reason=%s: %s",
            webspace_id,
            reason,
            exc,
            exc_info=True,
        )
        phase_timings_ms["total"] = _elapsed_ms_since(total_started)
        return b"", {"ok": False, "ready": False, "error": f"{type(exc).__name__}: {exc}", "phase_timings_ms": phase_timings_ms}


def _apply_materialized_payload_detached_sync(
    webspace_id: str,
    payload: Mapping[str, Any],
    *,
    reason: str,
    materialization_identity: Mapping[str, Any] | None = None,
) -> tuple[bytes, dict[str, Any]]:
    """Commit a materialized payload without creating an owner-loop YRoom."""

    total_started = time.perf_counter()
    phase_timings_ms: dict[str, float] = {}
    ydoc_timings_ms: dict[str, float] = {}
    worker_thread_id = threading.get_ident()
    try:
        import y_py as Y  # pylint: disable=import-outside-toplevel
        from adaos.services.scenario.webspace_runtime import WebspaceScenarioRuntime  # pylint: disable=import-outside-toplevel
        from adaos.services.yjs.doc import get_ydoc  # pylint: disable=import-outside-toplevel

        runtime = WebspaceScenarioRuntime()
        update = b""
        snapshot: dict[str, Any] = {"ready": False}
        with get_ydoc(
            webspace_id,
            timings=ydoc_timings_ms,
            timing_prefix="detached_",
            load_mark_roots=["ui", "data", "registry", "runtime"],
            governed=True,
        ) as ydoc:
            stage_started = time.perf_counter()
            before = Y.encode_state_vector(ydoc)
            phase_timings_ms["encode_state_vector"] = _elapsed_ms_since(stage_started)

            stage_started = time.perf_counter()
            with ystore_write_metadata_sync(
                root_names=["ui", "data", "registry", "runtime"],
                source=f"yjs.gateway_ws.{reason}.detached_materialized_payload",
                owner="core:yjs_gateway",
                channel="core.yjs.gateway.detached_materialized_payload",
                governed=True,
            ):
                runtime.apply_materialized_payload_to_doc(
                    ydoc,
                    webspace_id,
                    payload,
                    materialization_identity=materialization_identity,
                    verify_branch_fingerprints="scenario_switch" in str(reason or "").lower(),
                )
            phase_timings_ms["branch_apply"] = _elapsed_ms_since(stage_started)

            stage_started = time.perf_counter()
            apply_summary = getattr(runtime, "_last_apply_summary", None)
            snapshot = _materialized_payload_apply_ready_snapshot(payload, apply_summary)
            if snapshot is None:
                snapshot = _room_effective_branch_snapshot(ydoc)
            phase_timings_ms["effective_snapshot"] = _elapsed_ms_since(stage_started)
            if not bool(snapshot.get("ready")):
                phase_timings_ms["total"] = _elapsed_ms_since(total_started)
                return b"", {
                    "ok": False,
                    "ready": False,
                    "snapshot": snapshot,
                    "apply_summary": apply_summary,
                    "semantic_timings_ms": getattr(runtime, "_last_rebuild_timings_ms", None),
                    "ydoc_timings_ms": ydoc_timings_ms,
                    "phase_timings_ms": phase_timings_ms,
                    "worker_thread_id": worker_thread_id,
                }

            stage_started = time.perf_counter()
            update = bytes(Y.encode_state_as_update(ydoc, before) or b"")
            phase_timings_ms["encode_update"] = _elapsed_ms_since(stage_started)

        phase_timings_ms["total"] = _elapsed_ms_since(total_started)
        payload_ui = payload.get("ui") if isinstance(payload.get("ui"), Mapping) else {}
        committed_scenario = str(
            payload_ui.get("current_scenario")
            or snapshot.get("current_scenario")
            or payload.get("scenario_id")
            or ""
        ).strip()
        return update, {
            "ok": True,
            "ready": True,
            "snapshot": snapshot,
            "apply_summary": getattr(runtime, "_last_apply_summary", None),
            "semantic_timings_ms": getattr(runtime, "_last_rebuild_timings_ms", None),
            "ydoc_timings_ms": ydoc_timings_ms,
            "phase_timings_ms": phase_timings_ms,
            "worker_thread_id": worker_thread_id,
            "committed_scenario": committed_scenario,
            "update_bytes": len(update),
        }
    except BaseException as exc:
        if _is_control_flow_base_exception(exc):
            raise
        phase_timings_ms["total"] = _elapsed_ms_since(total_started)
        return b"", {
            "ok": False,
            "ready": False,
            "error": f"{type(exc).__name__}: {exc}",
            "ydoc_timings_ms": ydoc_timings_ms,
            "phase_timings_ms": phase_timings_ms,
            "worker_thread_id": worker_thread_id,
        }


def _detached_live_refresh_response(
    webspace_id: str,
    *,
    reason: str,
    persist_repair: bool,
    force_full_state_update: bool,
    update: bytes = b"",
    result: Mapping[str, Any] | None = None,
    phase_timings_ms: Mapping[str, float] | None = None,
    skipped: str = "",
) -> dict[str, Any]:
    direct_result = dict(result or {})
    ready = bool(direct_result.get("ready")) if direct_result else bool(skipped)
    update_size = len(update or b"")
    return {
        "ok": ready,
        "webspace_id": webspace_id,
        "reason": reason,
        "mode": "detached_no_live_transport",
        "skipped": skipped or None,
        "room_present": False,
        "room_created": False,
        "room_dropped": False,
        "room_repaired": update_size > 0,
        "repair_bytes": update_size,
        "repair_persisted": bool(persist_repair and ready and update_size > 0),
        "force_full_state_update": bool(force_full_state_update),
        "materialized_payload_applied": bool(direct_result and ready),
        "materialized_payload_update_bytes": update_size,
        "materialized_payload": direct_result or None,
        "broadcast_diagnostics": {},
        "fallback_repair": False,
        "semantic_repair": False,
        "thread_handoff": "detached_worker" if direct_result else "not_required",
        "closed_connections": 0,
        "closed_webrtc_peers": 0,
        "reset_route_runtime": False,
        "phase_timings_ms": dict(phase_timings_ms or {}),
    }


async def _update_live_webspace_effective_branches(
    webspace_id: str,
    *,
    reason: str = "live_room_refresh",
    persist_repair: bool = True,
    force_full_state_update: bool = False,
    materialized_payload: Mapping[str, Any] | None = None,
    materialization_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Repair effective scenario branches without tearing down live transports.

    A scenario materialization refresh should update the active YDoc contents,
    not close the browser's YWS/WebRTC datachannel path. Hard room resets remain
    available for explicit recovery paths.
    """

    total_started = time.perf_counter()
    phase_timings_ms: dict[str, float] = {}
    key = str(webspace_id or "").strip() or "default"
    room_created = False
    stage_started = time.perf_counter()
    room = y_server.rooms.get(key)
    phase_timings_ms["room_lookup"] = _elapsed_ms_since(stage_started)
    bootstrap_materialization: dict[str, Any] | None = None
    if room is None:
        room_lock = _room_locks.setdefault(key, asyncio.Lock())
        async with room_lock:
            room = y_server.rooms.get(key)
            if room is None and not _webspace_has_live_transports(key):
                if not isinstance(materialized_payload, Mapping) or not materialized_payload:
                    phase_timings_ms["room_create"] = 0.0
                    phase_timings_ms["detached_apply"] = 0.0
                    phase_timings_ms["total"] = _elapsed_ms_since(total_started)
                    return _detached_live_refresh_response(
                        key,
                        reason=reason,
                        persist_repair=persist_repair,
                        force_full_state_update=force_full_state_update,
                        phase_timings_ms=phase_timings_ms,
                        skipped="no_live_transport",
                    )
                if not persist_repair:
                    phase_timings_ms["room_create"] = 0.0
                    phase_timings_ms["detached_apply"] = 0.0
                    phase_timings_ms["total"] = _elapsed_ms_since(total_started)
                    return _detached_live_refresh_response(
                        key,
                        reason=reason,
                        persist_repair=persist_repair,
                        force_full_state_update=force_full_state_update,
                        phase_timings_ms=phase_timings_ms,
                        skipped="durable_payload_already_committed",
                    )

                payload_scenario = str(materialized_payload.get("scenario_id") or "").strip()
                previous_authoritative_scenario = _authoritative_current_scenario(key)
                if payload_scenario:
                    note_authoritative_current_scenario(
                        key,
                        payload_scenario,
                        reason=f"{reason}:detached_prepare",
                    )
                stage_started = time.perf_counter()
                update, detached_result = await asyncio.to_thread(
                    _apply_materialized_payload_detached_sync,
                    key,
                    materialized_payload,
                    reason=reason,
                    materialization_identity=materialization_identity,
                )
                phase_timings_ms["room_create"] = 0.0
                phase_timings_ms["detached_apply"] = _elapsed_ms_since(stage_started)
                phase_timings_ms["total"] = _elapsed_ms_since(total_started)
                if bool(detached_result.get("ready")):
                    committed_scenario = str(detached_result.get("committed_scenario") or payload_scenario).strip()
                    if committed_scenario:
                        note_authoritative_current_scenario(
                            key,
                            committed_scenario,
                            reason=f"{reason}:detached_commit",
                        )
                    invalidate_live_map_value_cache(key)
                elif payload_scenario and _authoritative_current_scenario(key) == payload_scenario:
                    if previous_authoritative_scenario:
                        note_authoritative_current_scenario(
                            key,
                            previous_authoritative_scenario,
                            reason=f"{reason}:detached_rollback",
                        )
                    else:
                        _clear_authoritative_current_scenario(
                            key,
                            reason=f"{reason}:detached_failed",
                        )
                _ylog.info(
                    "refreshed detached Yjs state without live transport webspace=%s reason=%s update_bytes=%s ready=%s worker_thread=%s phases=%s",
                    key,
                    reason,
                    len(update or b""),
                    bool(detached_result.get("ready")),
                    detached_result.get("worker_thread_id"),
                    json.dumps(phase_timings_ms, ensure_ascii=True, sort_keys=True),
                )
                return _detached_live_refresh_response(
                    key,
                    reason=reason,
                    persist_repair=persist_repair,
                    force_full_state_update=force_full_state_update,
                    update=update,
                    result=detached_result,
                    phase_timings_ms=phase_timings_ms,
                )

        if isinstance(materialized_payload, Mapping) and materialized_payload:
            bootstrap_materialization = {
                "webspace_id": key,
                "payload": materialized_payload,
                "reason": reason,
                "persist_repair": bool(persist_repair),
                "force_full_state_update": bool(force_full_state_update),
                "materialization_identity": materialization_identity,
            }
            bootstrap_token = _ROOM_BOOTSTRAP_MATERIALIZATION.set(bootstrap_materialization)
        else:
            bootstrap_token = None
        try:
            stage_started = time.perf_counter()
            room = await y_server.get_room(key)
            phase_timings_ms["room_create"] = _elapsed_ms_since(stage_started)
            room_created = True
        except Exception as exc:
            phase_timings_ms["room_create"] = _elapsed_ms_since(stage_started)
            phase_timings_ms["total"] = _elapsed_ms_since(total_started)
            _ylog.warning(
                "failed to refresh live Yjs room effective branches webspace=%s reason=%s",
                key,
                reason,
                exc_info=True,
            )
            return {
                "ok": False,
                "webspace_id": key,
                "reason": reason,
                "error": f"{type(exc).__name__}: {exc}",
                "room_present": False,
                "room_created": False,
                "room_dropped": False,
                "closed_connections": 0,
                "closed_webrtc_peers": 0,
                "reset_route_runtime": False,
                "phase_timings_ms": phase_timings_ms,
            }
        finally:
            if bootstrap_token is not None:
                _ROOM_BOOTSTRAP_MATERIALIZATION.reset(bootstrap_token)
    else:
        phase_timings_ms["room_create"] = 0.0

    direct_result: dict[str, Any] | None = None
    direct_update_size = 0
    handoff = "not_attempted"
    update: bytes = b""
    broadcast_diagnostics: dict[str, Any] = {}
    if isinstance(materialized_payload, Mapping) and materialized_payload:
        bootstrap_handoff = getattr(room, "_bootstrap_materialization_handoff", None)
        if (
            isinstance(bootstrap_handoff, Mapping)
            and bootstrap_handoff.get("request") is bootstrap_materialization
        ):
            update = bytes(bootstrap_handoff.get("update") or b"")
            direct_result = dict(bootstrap_handoff.get("result") or {})
            handoff = "room_bootstrap"
            phase_timings_ms["materialized_owner_apply"] = 0.0
            try:
                delattr(room, "_bootstrap_materialization_handoff")
            except Exception:
                pass
        else:
            stage_started = time.perf_counter()
            try:
                update, handoff, direct_result = await _apply_room_materialized_payload_on_owner_loop(
                    key,
                    getattr(room, "ystore", None),
                    room,
                    materialized_payload,
                    reason=reason,
                    persist_repair=bool(persist_repair),
                    force_full_state_update=bool(force_full_state_update),
                    materialization_identity=materialization_identity,
                )
            except BaseException as exc:
                if _is_control_flow_base_exception(exc):
                    raise
                update = b""
                direct_result = {
                    "ok": False,
                    "ready": False,
                    "error": f"{type(exc).__name__}: {exc}",
                    "phase_timings_ms": {"total": _elapsed_ms_since(stage_started)},
                }
            phase_timings_ms["materialized_owner_apply"] = _elapsed_ms_since(stage_started)
        direct_update_size = len(update or b"")
        if bool((direct_result or {}).get("ready")):
            marker_key = _live_refresh_update_key(key, update)
            stage_started = time.perf_counter()
            broadcast_diagnostics = _live_refresh_snapshot_by_key(marker_key)
            if not bool(broadcast_diagnostics.get("client_sync_done")):
                try:
                    client_count = len(list(getattr(room, "clients", []) or []))
                except Exception:
                    client_count = 0
                if client_count <= 0:
                    broadcast_diagnostics = _mark_live_refresh_no_clients(marker_key)
                elif float(_LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS) <= 0.0:
                    broadcast_diagnostics = _mark_live_refresh_wait_skipped(
                        marker_key,
                        client_count=client_count,
                        reason="wait_disabled",
                    )
                else:
                    broadcast_diagnostics = await _wait_live_refresh_client_sync(
                        marker_key,
                        timeout_ms=float(_LIVE_ROOM_REFRESH_CLIENT_SYNC_WAIT_MS),
                    )
            phase_timings_ms["client_sync_wait"] = _elapsed_ms_since(stage_started)
            if broadcast_diagnostics:
                direct_result = dict(direct_result or {})
                direct_result["broadcast_diagnostics"] = broadcast_diagnostics
                broadcast_phases = broadcast_diagnostics.get("phase_timings_ms")
                if isinstance(broadcast_phases, Mapping):
                    for phase_name in ("observer_broadcast", "observer_message_create", "client_sync"):
                        phase_value = broadcast_phases.get(phase_name)
                        if isinstance(phase_value, (int, float)):
                            phase_timings_ms[phase_name] = float(phase_value)
            try:
                if hasattr(room, "_diag_effective_branch_snapshot"):
                    room._diag_effective_branch_snapshot = dict((direct_result or {}).get("snapshot") or {"ready": True})
            except Exception:
                pass
        else:
            phase_timings_ms["client_sync_wait"] = 0.0
            _ylog.warning(
                "materialized payload did not refresh live Yjs room; falling back to semantic repair webspace=%s reason=%s result=%s",
                key,
                reason,
                json.dumps(
                    _compact_materialized_payload_apply_result_for_log(direct_result),
                    ensure_ascii=True,
                    sort_keys=True,
                ),
            )
            update = b""
    else:
        phase_timings_ms["materialized_owner_apply"] = 0.0
        phase_timings_ms["client_sync_wait"] = 0.0

    semantic_repair = not bool((direct_result or {}).get("ready"))
    fallback_repair = bool(direct_result) and bool(semantic_repair)
    if semantic_repair:
        stage_started = time.perf_counter()
        try:
            update, handoff = await _repair_room_effective_branches_on_owner_loop(
                key,
                getattr(room, "ystore", None),
                room,
                reason=reason,
                persist_repair=bool(persist_repair),
            )
        except BaseException as exc:
            if _is_control_flow_base_exception(exc):
                raise
            update = b""
            handoff = "failed"
            direct_result = dict(direct_result or {})
            direct_result.setdefault("ok", False)
            direct_result.setdefault("ready", False)
            direct_result["fallback_repair_error"] = f"{type(exc).__name__}: {exc}"
        phase_timings_ms["fallback_repair"] = _elapsed_ms_since(stage_started)
    else:
        phase_timings_ms["fallback_repair"] = 0.0
    update_size = len(update or b"")
    phase_timings_ms["total"] = _elapsed_ms_since(total_started)
    _ylog.info(
        "refreshed live Yjs room effective branches webspace=%s reason=%s room_created=%s update_bytes=%s thread_handoff=%s materialized_payload=%s fallback_repair=%s phases=%s",
        key,
        reason,
        room_created,
        update_size,
        handoff,
        bool(direct_result),
        bool(fallback_repair),
        json.dumps(phase_timings_ms, ensure_ascii=True, sort_keys=True),
    )
    return {
        "ok": True,
        "webspace_id": key,
        "reason": reason,
        "room_present": True,
        "room_created": room_created,
        "room_dropped": False,
        "room_repaired": update_size > 0,
        "repair_bytes": update_size,
        "repair_persisted": bool(persist_repair and update_size > 0),
        "force_full_state_update": bool(force_full_state_update),
        "materialized_payload_applied": bool(direct_result and direct_result.get("ready")),
        "materialized_payload_update_bytes": direct_update_size,
        "materialized_payload": direct_result,
        "broadcast_diagnostics": broadcast_diagnostics,
        "fallback_repair": bool(fallback_repair),
        "semantic_repair": bool(semantic_repair),
        "thread_handoff": handoff,
        "closed_connections": 0,
        "closed_webrtc_peers": 0,
        "reset_route_runtime": False,
        "phase_timings_ms": phase_timings_ms,
    }


async def apply_materialized_payload_to_live_room(
    webspace_id: str,
    materialized_payload: Mapping[str, Any],
    *,
    reason: str = "materialized_payload_commit",
    persist_repair: bool = True,
    force_full_state_update: bool = False,
    materialization_identity: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Apply an already-resolved payload to the live room atomically."""

    if not isinstance(materialized_payload, Mapping) or not materialized_payload:
        raise ValueError("materialized_payload is required")
    return await _update_live_webspace_effective_branches(
        webspace_id,
        reason=reason,
        persist_repair=persist_repair,
        force_full_state_update=force_full_state_update,
        materialized_payload=materialized_payload,
        materialization_identity=materialization_identity,
    )


async def reconcile_live_webspace_effective_branches(
    webspace_id: str,
    *,
    reason: str = "live_room_reconcile",
    persist_repair: bool = True,
) -> dict[str, Any]:
    """Repair a live room from its current selector and persisted sources."""

    return await _update_live_webspace_effective_branches(
        webspace_id,
        reason=reason,
        persist_repair=persist_repair,
    )


async def _repair_room_effective_branches_on_owner_loop(
    webspace_id: str,
    ystore: Any,
    room: Any,
    *,
    reason: str,
    persist_repair: bool = True,
) -> tuple[bytes, str]:
    owner_thread = getattr(room, "_thread_id", None)
    owner_loop = getattr(room, "_loop", None)
    current_thread = threading.get_ident()
    try:
        current_loop = asyncio.get_running_loop()
    except RuntimeError:
        current_loop = None

    def _repair_coro() -> Any:
        kwargs: dict[str, Any] = {"reason": reason}
        try:
            if "persist_repair" in inspect.signature(_repair_room_effective_branches).parameters:
                kwargs["persist_repair"] = bool(persist_repair)
        except Exception:
            kwargs["persist_repair"] = bool(persist_repair)
        return _repair_room_effective_branches(webspace_id, ystore, room, **kwargs)

    if (
        owner_thread is not None
        and owner_thread != current_thread
    ):
        if owner_loop is None or not owner_loop.is_running():
            _ylog.warning(
                "skipped live YRoom repair from non-owner thread without running owner loop webspace=%s reason=%s owner_thread=%s current_thread=%s",
                webspace_id,
                reason,
                owner_thread,
                current_thread,
            )
            return b"", "skipped_no_owner_loop"
        future = asyncio.run_coroutine_threadsafe(_repair_coro(), owner_loop)
        wrapped = asyncio.wrap_future(future)
        return await wrapped, "threadsafe_owner_loop"

    if (
        owner_loop is not None
        and current_loop is not None
        and owner_loop is not current_loop
        and owner_loop.is_running()
    ):
        future = asyncio.run_coroutine_threadsafe(_repair_coro(), owner_loop)
        wrapped = asyncio.wrap_future(future)
        return await wrapped, "loop_owner_loop"

    return await _repair_coro(), "direct_owner_context"


async def _repair_room_effective_branches(
    webspace_id: str,
    ystore: Any,
    room: Any,
    *,
    reason: str,
    persist_repair: bool = True,
) -> bytes:
    ydoc = getattr(room, "ydoc", None)
    if ydoc is None:
        return b""
    try:
        from adaos.services.scenario.webspace_runtime import WebspaceScenarioRuntime  # pylint: disable=import-outside-toplevel

        runtime = WebspaceScenarioRuntime()
        scenario_id = _authoritative_current_scenario(webspace_id) or _room_current_scenario(ydoc)
        await runtime.resolve_materialized_payload_from_doc_async(
            ydoc,
            webspace_id,
            scenario_id=scenario_id,
        )
        payload = getattr(runtime, "_last_materialized_payload", None)
        if not isinstance(payload, Mapping) or not payload:
            raise RuntimeError("room_repair_resolver_missing_payload")
        update, apply_result = await _apply_room_materialized_payload(
            webspace_id,
            ystore,
            room,
            payload,
            reason=f"{reason}.resolved_repair",
            persist_repair=bool(persist_repair),
        )
        if not bool(apply_result.get("ready")):
            _ylog.warning(
                "YRoom effective branch repair did not restore required branches webspace=%s reason=%s snapshot=%s",
                webspace_id,
                reason,
                json.dumps(_room_effective_branch_snapshot(ydoc), ensure_ascii=True, sort_keys=True)[:1000],
            )
            return b""
        _ylog.warning(
            "YRoom effective branches repaired webspace=%s reason=%s bytes=%s persisted=%s",
            webspace_id,
            reason,
            len(update or b""),
            bool(update and ystore is not None and persist_repair),
        )
        return bytes(update or b"")
    except Exception as exc:
        _ylog.warning(
            "YRoom effective branch repair failed webspace=%s reason=%s: %s",
            webspace_id,
            reason,
            exc,
            exc_info=True,
        )
        return b""


async def _ensure_room_effective_materialized(
    webspace_id: str,
    ystore: Any,
    room: Any,
    *,
    seed_result: dict[str, Any] | None = None,
) -> bool:
    """Resolve and apply missing effective branches before exposing a cold room."""

    ydoc = getattr(room, "ydoc", None)
    if ydoc is None:
        return False
    authoritative_scenario = _authoritative_current_scenario(webspace_id)
    seed_scenario = str((seed_result or {}).get("scenario_id") or "").strip()
    if seed_scenario and bool((seed_result or {}).get("current_scenario_overridden")):
        if authoritative_scenario and authoritative_scenario != seed_scenario:
            _clear_authoritative_current_scenario(
                webspace_id,
                reason="room_bootstrap_seed_overrode_current",
            )
        authoritative_scenario = seed_scenario
    current_scenario = _room_current_scenario(ydoc)
    expected_scenario = authoritative_scenario or seed_scenario or current_scenario or "web_desktop"
    if (
        str((seed_result or {}).get("mode") or "").strip() == "persisted_effective_state"
        and bool((seed_result or {}).get("persisted_effective_state_ready"))
        and current_scenario == expected_scenario
        and seed_scenario == expected_scenario
    ):
        trusted_snapshot = {
            "ready": True,
            "mode": "persisted_effective_state",
            "details": "trusted_persisted_marker",
            "required_branches": list(_room_effective_required_branches(ydoc)),
            "missing_required_branches": [],
            "current_scenario": current_scenario,
            "materialized_scenario": current_scenario,
            "materialization_mismatch": False,
        }
        try:
            room._diag_effective_branch_snapshot = trusted_snapshot
            room._diag_effective_last_full_check_mono = time.monotonic()
        except Exception:
            pass
        if seed_result is not None:
            seed_result.update(
                {
                    "room_effective_materialized": False,
                    "room_effective_reused": True,
                    "room_effective_validation": "trusted_persisted_marker",
                }
            )
        return False
    if _room_effective_branches_ready(ydoc) and (
        not expected_scenario or current_scenario == expected_scenario
    ):
        ready_result = await _finalize_materialized_room_bootstrap(
            webspace_id,
            ystore,
            room,
            scenario_id=current_scenario or expected_scenario,
            space=str((seed_result or {}).get("space") or "workspace"),
            mode="persisted_effective_state",
        )
        if seed_result is not None:
            seed_result.update(
                {
                    "mode": "persisted_effective_state",
                    "room_effective_materialized": False,
                    "room_effective_reused": True,
                    "room_bootstrap_marker_persisted": bool(ready_result.get("persisted")),
                }
            )
        return False

    try:
        from adaos.services.scenario.webspace_runtime import WebspaceScenarioRuntime  # pylint: disable=import-outside-toplevel

        runtime = WebspaceScenarioRuntime()
        await runtime.resolve_materialized_payload_from_doc_async(
            ydoc,
            webspace_id,
            scenario_id=expected_scenario,
        )
        payload = getattr(runtime, "_last_materialized_payload", None)
        if not isinstance(payload, Mapping) or not payload:
            raise RuntimeError("room_bootstrap_resolver_missing_payload")
        update, apply_result = await _apply_room_materialized_payload(
            webspace_id,
            ystore,
            room,
            payload,
            reason="room_bootstrap.resolve_apply",
            persist_repair=True,
        )
        if not bool(apply_result.get("ready")):
            raise RuntimeError(
                str(apply_result.get("error") or "room_bootstrap_effective_branches_not_ready")
            )
        ready_result = await _finalize_materialized_room_bootstrap(
            webspace_id,
            ystore,
            room,
            scenario_id=expected_scenario,
            space=str((seed_result or {}).get("space") or "workspace"),
            mode="resolved_payload",
        )
        if seed_result is not None:
            seed_result.update(
                {
                    "mode": "resolved_payload",
                    "room_effective_materialized": True,
                    "room_effective_materialized_persisted": bool(update),
                    "room_effective_materialized_bytes": len(update or b""),
                    "room_bootstrap_marker_persisted": bool(ready_result.get("persisted")),
                    "room_resolver_timings_ms": dict(runtime._last_rebuild_timings_ms or {}),
                }
            )
        try:
            room._diag_effective_branch_snapshot = _room_effective_branch_snapshot(ydoc)
            room._diag_effective_last_full_check_mono = time.monotonic()
        except Exception:
            pass
        _ylog.info(
            "YRoom effective branches materialized before open webspace=%s persisted=%s bytes=%d",
            webspace_id,
            bool(update),
            len(update or b""),
        )
        return True
    except Exception as exc:
        if seed_result is not None:
            seed_result["room_effective_materialized"] = False
            seed_result["room_effective_materialize_error"] = f"{type(exc).__name__}: {exc}"
        _ylog.warning(
            "YRoom effective materialization failed before open webspace=%s: %s",
            webspace_id,
            exc,
            exc_info=True,
        )
        return False


async def _finalize_materialized_room_bootstrap(
    webspace_id: str,
    ystore: Any,
    room: Any,
    *,
    scenario_id: str,
    space: str,
    mode: str = "materialized_payload",
) -> dict[str, Any]:
    """Persist the ready marker after a payload-owned cold room bootstrap."""

    ydoc = getattr(room, "ydoc", None)
    if ydoc is None:
        return {"ready": False, "persisted": False, "error": "missing_ydoc"}
    try:
        import y_py as Y  # pylint: disable=import-outside-toplevel

        before = Y.encode_state_vector(ydoc)
        changed = write_runtime_bootstrap_state(
            ydoc,
            webspace_id=webspace_id,
            scenario_id=str(scenario_id or "").strip() or "web_desktop",
            state="ready",
            stage="room_bootstrap_ready",
            ready=True,
            mode=str(mode or "").strip() or "materialized_payload",
            extra={
                "space": str(space or "").strip() or None,
                "room_effective_materialized": True,
            },
        )
        update = Y.encode_state_as_update(ydoc, before) if changed else b""
        persisted = False
        if update and ystore is not None:
            async with ystore_write_metadata(
                root_names=["runtime"],
                source="yjs.gateway_ws.room_bootstrap.materialized_ready",
                owner="core:yjs_gateway",
                channel="core.yjs.gateway.bootstrap",
                governed=True,
            ):
                persisted = bool(await ystore.write_update(update, update_kind="diff", notify=False))
        return {
            "ready": True,
            "changed": bool(changed),
            "persisted": bool(persisted),
            "update_bytes": len(update or b""),
        }
    except Exception as exc:
        _ylog.warning(
            "failed to finalize materialized room bootstrap webspace=%s",
            webspace_id,
            exc_info=True,
        )
        return {
            "ready": False,
            "persisted": False,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _finalize_room_bootstrap_rebuild_status(
    webspace_id: str,
    *,
    seed_result: dict[str, Any] | None = None,
    room: Any | None = None,
) -> None:
    """
    Publish a semantic rebuild status for rooms restored from a disk snapshot.

    A cold YRoom can already contain all effective branches because the durable
    snapshot is healthy. In that path no semantic rebuild event is emitted, so
    in-memory diagnostics may still report ``materialization_not_ready`` after a
    process restart. The room has already gone through effective branch
    materialization, so this finalizer only records the lightweight readiness
    result and avoids a second full semantic rebuild on the event loop.
    """
    try:
        ydoc = getattr(room, "ydoc", None)
        cached_snapshot = getattr(room, "_diag_effective_branch_snapshot", None)
        ready = bool(isinstance(cached_snapshot, dict) and cached_snapshot.get("ready"))
        if not ready:
            ready = _room_effective_top_level_ready(ydoc)
        if seed_result is not None:
            seed_result["room_bootstrap_rebuild_status"] = "ready" if ready else "not_ready"
            seed_result["room_bootstrap_rebuild_error"] = None if ready else "effective_branches_not_ready"
    except Exception as exc:
        if seed_result is not None:
            seed_result["room_bootstrap_rebuild_status"] = "failed"
            seed_result["room_bootstrap_rebuild_error"] = f"{type(exc).__name__}: {exc}"
        _ylog.warning(
            "YRoom bootstrap rebuild status finalization failed webspace=%s: %s",
            webspace_id,
            exc,
            exc_info=True,
        )


async def start_y_server() -> None:
    """
    Ensure the shared Y websocket server background task is running.
    """
    global _y_server_started, _y_server_task
    global _GATEWAY_SNAPSHOT_OWNER_LOOP, _GATEWAY_SNAPSHOT_OWNER_THREAD_ID
    owner_loop = asyncio.get_running_loop()
    owner_thread_id = threading.get_ident()
    with _GATEWAY_SNAPSHOT_OWNER_LOCK:
        _GATEWAY_SNAPSHOT_OWNER_LOOP = owner_loop
        _GATEWAY_SNAPSHOT_OWNER_THREAD_ID = owner_thread_id
    if _y_server_started:
        task = _y_server_task
        if task is not None and task.done():
            _recreate_y_server_after_failure(_task_exception_summary(task) or "task_done")
        else:
            return
    _y_server_started = True

    async def _runner() -> None:
        await y_server.start()

    _y_server_task = asyncio.create_task(_runner(), name="adaos-yjs-websocket-server")
    _y_server_task.add_done_callback(_on_y_server_task_done)
    await y_server.started.wait()


async def stop_y_server() -> None:
    """
    Stop the shared Y websocket server background task.

    Without an explicit stop, the anyio task group inside ypy-websocket can
    keep the process alive after FastAPI/uvicorn shutdown.
    """
    global _y_server_started, _y_server_task
    if not _y_server_started:
        return
    for webspace_id in list(_IDLE_ROOM_RESET_TASKS.keys()):
        _cancel_idle_room_reset(webspace_id)
    for webspace_id in list(getattr(y_server, "rooms", {}).keys()):
        try:
            await reset_live_webspace_room(
                str(webspace_id),
                close_reason="y_server_shutdown",
                reset_route_runtime=False,
                prewarm_after_reset=False,
            )
        except Exception:
            _ylog.debug("failed to reset room during y_server shutdown webspace=%s", webspace_id, exc_info=True)
    try:
        y_server.stop()
    except Exception:
        pass
    task = _y_server_task
    _y_server_task = None
    _y_server_started = False
    if task is None:
        return
    try:
        await task
    except asyncio.CancelledError:
        pass
    except Exception:
        # shutdown path: ignore
        pass


def _workspace_bootstrap_snapshot_sync(webspace_id: str) -> dict[str, Any]:
    ensure_workspace(webspace_id)
    row = get_workspace(webspace_id)
    if row is None:
        return {
            "effective_source_mode": "workspace",
            "current_scenario_overlay": "",
            "home_scenario": "",
            "effective_home_scenario": "web_desktop",
            "is_dev": False,
        }
    current_scenario_overlay = (
        str(getattr(row, "current_scenario_overlay", "") or "").strip()
        if bool(getattr(row, "has_current_scenario_overlay", False))
        else ""
    )
    return {
        "effective_source_mode": str(getattr(row, "effective_source_mode", "") or "workspace"),
        "current_scenario_overlay": current_scenario_overlay,
        "home_scenario": str(getattr(row, "home_scenario", "") or ""),
        "effective_home_scenario": str(getattr(row, "effective_home_scenario", "") or "web_desktop"),
        "is_dev": bool(getattr(row, "is_dev", False)),
    }


async def _workspace_bootstrap_snapshot(webspace_id: str) -> dict[str, Any]:
    return await asyncio.to_thread(_workspace_bootstrap_snapshot_sync, webspace_id)


async def ensure_webspace_ready(webspace_id: str, scenario_id: str | None = None) -> None:
    webspace_id = _coerce_gateway_webspace_id(webspace_id)
    workspace = await _workspace_bootstrap_snapshot(webspace_id)
    ystore = get_ystore_for_webspace(webspace_id)
    space = str(workspace.get("effective_source_mode") or "workspace")
    base_scenario = str(scenario_id or "").strip()
    if not base_scenario and workspace.get("home_scenario"):
        base_scenario = str(workspace.get("effective_home_scenario") or "")
    if not base_scenario:
        base_scenario = "web_desktop"
    prefer_default_scenario = bool(
        scenario_id
        or (
            workspace.get("home_scenario")
            and (workspace.get("is_dev") or workspace.get("effective_source_mode") == "dev")
        )
    )

    try:
        await ensure_webspace_seeded_from_scenario(
            ystore,
            webspace_id=webspace_id,
            default_scenario_id=base_scenario,
            space=space,
            prefer_default_scenario=prefer_default_scenario,
        )
    finally:
        try:
            await _stop_ystore_maybe_async(ystore)
        except Exception:
            pass


class FastAPIWebsocketAdapter:
    """
    Adapt FastAPI's WebSocket to the minimal protocol expected by ypy-websocket.
    """

    def __init__(self, ws: WebSocket, path: str):
        self._ws = ws
        self._path = path
        self._first_message_timeout_s = _YWS_FIRST_MESSAGE_TIMEOUT_S
        self._first_message_received = False

    @property
    def path(self) -> str:
        return self._path

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except Exception:
            raise StopAsyncIteration()

    async def send(self, message: bytes) -> None:
        try:
            await self._ws.send_bytes(message)
        except (WebSocketDisconnect, RuntimeError):
            # Client is already gone; ypy-websocket treats send failures inside
            # its room task group as fatal unless the adapter absorbs them.
            return
        except Exception:
            _ylog.debug("yws send ignored after client disconnect path=%s", self._path, exc_info=True)
            return

    async def recv(self) -> bytes:
        while True:
            try:
                if not self._first_message_received and self._first_message_timeout_s > 0:
                    # Healthy Yjs clients should send their first sync frame immediately.
                    # If the proxy path wedges before that point we would otherwise leak a
                    # runtime session until the process restarts.
                    msg = await asyncio.wait_for(self._ws.receive(), timeout=self._first_message_timeout_s)
                else:
                    msg = await self._ws.receive()
            except asyncio.TimeoutError as exc:
                raise RuntimeError("websocket first message timeout") from exc
            msg_type = msg.get("type")
            if msg_type == "websocket.receive":
                if msg.get("bytes") is not None:
                    data = msg["bytes"]
                    if data:
                        self._first_message_received = True
                        return data
                    continue
                if msg.get("text") is not None:
                    data = msg["text"].encode("utf-8")
                    if data:
                        self._first_message_received = True
                        return data
                    continue
                continue
            if msg_type == "websocket.disconnect":
                raise RuntimeError("websocket disconnected")
            raise RuntimeError(f"unexpected websocket event: {msg_type}")


async def _update_device_presence(webspace_id: str, device_id: str) -> bool:
    """
    Project basic device presence into an existing transport-owned Yjs room.

    Events/control registration normally precedes the YWS connection. It must
    not create a room and replay the durable store on the API owner loop merely
    to publish ephemeral presence; the admitted YWS path retries this update
    after it has acquired and registered its room.
    """
    if not _yws_direct_transport_enabled():
        _log.warning(
            "skipped device presence Yjs update because direct yws is disabled webspace=%s device=%s",
            webspace_id,
            device_id,
        )
        return False
    key = _coerce_gateway_webspace_id(webspace_id)
    room = y_server.rooms.get(key)
    if room is None or not _webspace_has_live_transports(key):
        _log.debug(
            "deferred device presence until Yjs transport owns room webspace=%s device=%s room_present=%s",
            key,
            device_id,
            room is not None,
        )
        return False
    ydoc = room.ydoc
    now_ms = int(time.time() * 1000)

    with ystore_write_metadata_sync(
        root_names=["devices"],
        source="yjs.gateway_ws",
        owner="core:yjs_gateway",
        channel="core.yjs.gateway.sync",
    ):
        with ydoc.begin_transaction() as txn:
            devices = ydoc.get_map("devices")
            current = devices.get(device_id)
            node = dict(current or {}) if isinstance(current, dict) else {}

            meta = dict(node.get("meta") or {})
            if "created_at" not in meta:
                meta["created_at"] = now_ms
            meta["kind"] = "browser"

            presence = dict(node.get("presence") or {})
            presence["online"] = True
            presence.setdefault("since", now_ms)
            presence["lastSeen"] = now_ms

            node["meta"] = meta
            node["presence"] = presence

            devices.set(txn, device_id, node)
    return True


async def _recover_stale_yws_room_bootstrap(webspace: str, dev_id: str, *, waited_s: float, reason: str) -> None:
    """
    Break a stale room bootstrap so reconnect loops do not keep piling onto the
    same locked YWS room creation path.

    This is deliberately scoped to runtime objects only. The persisted snapshot
    remains the source of truth; the next browser connection can create a fresh
    room and replay from disk.
    """
    _ylog.warning(
        "recovering stale yws room bootstrap webspace=%s dev=%s waited_s=%.3f reason=%s",
        webspace,
        dev_id,
        waited_s,
        reason,
    )
    _room_locks.pop(webspace, None)
    room = getattr(y_server, "rooms", {}).pop(webspace, None)
    try:
        _mark_room_reset(
            webspace,
            close_reason=reason,
            room=room,
            room_dropped=room is not None,
            closed_connections=0,
            closed_webrtc_peers=0,
        )
    except Exception:
        _ylog.debug("failed to mark stale yws room bootstrap reset webspace=%s", webspace, exc_info=True)
    try:
        await asyncio.wait_for(
            evict_ystore_for_webspace(
                webspace,
                store=getattr(room, "ystore", None) if room is not None else None,
                persist_snapshot=False,
                compact_runtime=False,
                backup_kind=reason,
            ),
            timeout=max(float(_YWS_ROOM_STALE_RECOVERY_TIMEOUT_S), 0.25),
        )
    except Exception:
        _ylog.warning("failed to evict YStore during stale yws room bootstrap recovery webspace=%s", webspace, exc_info=True)


async def _acquire_yws_room(webspace_id: str, dev_id: str, *, yws_attempt_id: str | None = None) -> YRoom:
    """
    Resolve YJS room with bounded waiting and cache fallback.

    We keep waiting long enough for legitimate warm bootstrap but avoid hard
    12-second reconnect loops from every connection when startup has stalled.
    """
    webspace = _shorten_webspace_id(webspace_id)
    timeout_s = max(float(_YWS_ROOM_READY_TIMEOUT_S), 0.0)
    max_wait_s = max(float(_YWS_ROOM_READY_MAX_S), 0.0)
    poll_s = max(float(_YWS_ROOM_READY_POLL_S), 0.25)

    yws_attempt_token = str(yws_attempt_id or "").strip()
    token = _CURRENT_YWS_ATTEMPT_ID.set(yws_attempt_token)
    try:
        wait_task: asyncio.Task[YRoom] = asyncio.create_task(y_server.get_room(webspace))
    finally:
        _CURRENT_YWS_ATTEMPT_ID.reset(token)
    started = time.perf_counter()
    attempts = 0

    def _consume_background_room_bootstrap(task: asyncio.Task[YRoom]) -> None:
        try:
            task.result()
        except asyncio.CancelledError:
            _ylog.warning(
                "background yws room bootstrap task was cancelled webspace=%s dev=%s yws_attempt=%s",
                webspace,
                dev_id,
                yws_attempt_token or None,
            )
        except Exception:
            _ylog.warning(
                "background yws room bootstrap task failed webspace=%s dev=%s yws_attempt=%s",
                webspace,
                dev_id,
                yws_attempt_token or None,
                exc_info=True,
            )

    if max_wait_s <= 0.0:
        max_wait_s = timeout_s if timeout_s > 0.0 else 0.0

    try:
        while True:
            attempts += 1
            if timeout_s <= 0.0:
                return await wait_task

            remaining_for_wait = max(max_wait_s - (time.perf_counter() - started), 0.0)
            if remaining_for_wait <= 0.0:
                raise asyncio.TimeoutError("room wait timeout exceeded")

            try:
                return await asyncio.wait_for(
                    asyncio.shield(wait_task),
                    timeout=min(timeout_s, remaining_for_wait),
                )
            except asyncio.TimeoutError:
                if wait_task.done():
                    try:
                        return wait_task.result()
                    except asyncio.TimeoutError:
                        _ylog.warning(
                            "yws room bootstrap task timed out internally webspace=%s dev=%s yws_attempt=%s waited_s=%.3f",
                            webspace,
                            dev_id,
                            yws_attempt_token or None,
                            time.perf_counter() - started,
                            exc_info=True,
                        )
                        raise
                pass

            elapsed = time.perf_counter() - started
            room = getattr(y_server, "rooms", {}).get(webspace)
            if room is not None:
                _ylog.info(
                    "yws room cache hit after timeout webspace=%s dev=%s attempt=%s waited_s=%.3f",
                    webspace,
                    dev_id,
                    attempts,
                    elapsed,
                )
                if not wait_task.done():
                    _ylog.debug("yws room cache hit but bootstrap task still running webspace=%s dev=%s", webspace, dev_id)
                return room

            if remaining_for_wait <= 0.0:
                raise asyncio.TimeoutError("room wait timeout exceeded")

            _ylog.warning(
                "yws room ready timeout webspace=%s dev=%s yws_attempt=%s timeout_s=%.3f waited_s=%.3f",
                webspace,
                dev_id,
                yws_attempt_token or None,
                timeout_s,
                elapsed,
            )
            await asyncio.sleep(min(poll_s, remaining_for_wait))
    except asyncio.TimeoutError:
        room = getattr(y_server, "rooms", {}).get(webspace)
        if room is not None:
            _ylog.info(
                "yws room cache hit at final timeout webspace=%s dev=%s waited_s=%.3f",
                webspace,
                dev_id,
                time.perf_counter() - started,
            )
            return room
        if wait_task.done():
            try:
                room = wait_task.result()
            except Exception:
                raise
            return room
        waited_s = time.perf_counter() - started
        _mark_room_wait_timeout(
            webspace,
            dev_id=dev_id,
            yws_attempt_id=yws_attempt_token,
            waited_s=waited_s,
        )
        wait_task.add_done_callback(_consume_background_room_bootstrap)
        _ylog.warning(
            "leaving yws room bootstrap task running after room wait timeout webspace=%s dev=%s yws_attempt=%s waited_s=%.3f",
            webspace,
            dev_id,
            yws_attempt_token or None,
            waited_s,
        )
        room = getattr(y_server, "rooms", {}).get(webspace)
        if room is not None:
            _ylog.info(
                "yws room cache hit after room wait timeout webspace=%s dev=%s waited_s=%.3f",
                webspace,
                dev_id,
                time.perf_counter() - started,
            )
            return room
        raise


async def _flush_pending_effective_repair_replays(
    room_ref: Any,
    adapter: YWebsocket,
    *,
    webspace_id: str,
    attempt_id: str,
    client_attempt_id: str | None = None,
) -> None:
    """
    Replay effective-branch repairs that were produced before ypy registered
    the just-connected client in room.clients.
    """
    entries_func = getattr(room_ref, "_effective_repair_replay_entries", None)
    if not callable(entries_func) or _YROOM_EFFECTIVE_REPAIR_REPLAY_FLUSH_SEC <= 0.0:
        return
    deadline = time.monotonic() + float(_YROOM_EFFECTIVE_REPAIR_REPLAY_FLUSH_SEC)
    sent_entry_ids: set[int] = set()
    while time.monotonic() <= deadline:
        entries = entries_func()
        pending = [entry for entry in entries if id(entry) not in sent_entry_ids]
        for entry in pending:
            update = bytes(entry.get("update") or b"")
            if not update:
                sent_entry_ids.add(id(entry))
                continue
            await adapter.send(create_update_message(update))
            sent_entry_ids.add(id(entry))
            entry["sent_total"] = int(entry.get("sent_total") or 0) + 1
            _ylog.warning(
                "replayed pending Y effective repair to yws client webspace=%s attempt=%s client_attempt=%s reason=%s repair_bytes=%s sent_total=%s",
                webspace_id,
                attempt_id,
                client_attempt_id or None,
                str(entry.get("reason") or "effective_repair"),
                len(update),
                int(entry.get("sent_total") or 0),
            )
        await asyncio.sleep(float(_YROOM_EFFECTIVE_REPAIR_REPLAY_INTERVAL_SEC))


async def _yws_impl(websocket: WebSocket, room: str | None) -> None:
    """
    Internal Yjs sync handler used by both /yws and /yws/<room> routes.

    Dev policy:
      - if a room segment is present in the path, it is treated as webspace_id;
      - otherwise, fallback to ?ws=<webspace_id> query param;
      - default is "default".
    """
    params: Dict[str, str] = dict(websocket.query_params)
    webspace_id = _coerce_gateway_webspace_id(room or params.get("ws"))
    dev_id = params.get("dev") or "unknown"
    attempt_id = _next_yws_attempt_id(webspace_id, dev_id)
    _set_websocket_yws_attempt_id(websocket, attempt_id)
    client_attempt_id = _clean_browser_metadata_value(
        params.get("client_yws_attempt_id") or params.get("client_attempt_id"),
        max_len=128,
    ) or ""
    browser_session_id = _clean_browser_metadata_value(
        params.get("browser_session_id")
        or params.get("browserSessionId")
        or params.get("client_session_id")
        or params.get("clientSessionId"),
        max_len=128,
    )
    browser_page_id = _clean_browser_metadata_value(
        params.get("browser_page_id") or params.get("browserPageId"),
        max_len=128,
    )
    browser_metadata = _browser_session_metadata(params)

    if _ws_trace_enabled():
        try:
            token_present = "token" in params
            _ylog.info(
                "yws trace open client=%s webspace=%s dev=%s attempt=%s client_attempt=%s token=%s",
                _ws_client_str(websocket),
                webspace_id,
                dev_id,
                attempt_id,
                client_attempt_id or None,
                token_present,
            )
        except Exception:
            pass
    try:
        from adaos.services.access_links import authorize_link, touch_browser_session

        reason = _browser_env_rejected_reason(dev_id, browser_metadata)
        allowed = reason is None
        if allowed:
            allowed, reason = await asyncio.to_thread(authorize_link, "browser", dev_id)
        if not allowed:
            reason_token = str(reason or "denied").strip().lower() or "denied"
            try:
                await asyncio.to_thread(
                    touch_browser_session,
                    dev_id,
                    webspace_id=webspace_id,
                    connection_state=reason_token,
                    online=False,
                    **browser_metadata,
                )
            except Exception:
                pass
            # Accept before closing so browsers receive a real close event with
            # a policy reason. Closing before accept is exposed as an opaque 403
            # in Chrome/WebView, which lets y-websocket keep reconnecting.
            if await _accept_websocket(websocket, channel="yws.auth_denied"):
                try:
                    close_code, close_reason = _browser_env_rejected_yws_close(reason_token)
                    await websocket.close(code=close_code, reason=close_reason)
                    _remember_yws_attempt(attempt_id, "closed", close_code=close_code, close_reason=close_reason)
                except Exception:
                    pass
            return
    except Exception:
        _ylog.debug("browser access policy check failed webspace=%s dev=%s attempt=%s", webspace_id, dev_id, attempt_id, exc_info=True)
    if not _yws_direct_transport_enabled():
        try:
            from adaos.services.access_links import touch_browser_session

            await asyncio.to_thread(
                touch_browser_session,
                dev_id,
                webspace_id=webspace_id,
                connection_state="yws_disabled",
                online=True,
                **browser_metadata,
            )
        except Exception:
            pass
        if await _accept_websocket(websocket, channel="yws.disabled"):
            try:
                hold_s = _yws_disabled_reject_hold_sec()
                if hold_s > 0.0:
                    await asyncio.sleep(min(hold_s, 120.0))
                close_reason = "yws_guard_direct_yws_disabled"
                await websocket.close(code=1013, reason=close_reason)
                _remember_yws_attempt(attempt_id, "closed", close_code=1013, close_reason=close_reason)
            except Exception:
                pass
        return
    # Recover a failed shared server before reconnect-storm admission. If the
    # guard rejects this particular socket, the next admitted attempt still
    # finds a live YWS owner instead of waiting out the full quarantine.
    await start_y_server()
    _record_yws_guard_attempt(
        webspace_id,
        dev_id,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id or None,
    )
    if not await _accept_websocket(websocket, channel="yws"):
        return
    guard_reason, guard_diag = _yws_guard_reject_reason(
        webspace_id,
        dev_id,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id or None,
    )
    if guard_reason:
        await _reject_yws_guard_connection(
            websocket,
            webspace_id=webspace_id,
            dev_id=dev_id,
            browser_metadata=browser_metadata,
            attempt_id=attempt_id,
            client_attempt_id=client_attempt_id or None,
            guard_reason=guard_reason,
            guard_diag=guard_diag,
        )
        return
    replaced_existing = await _close_existing_yws_client_connections(
        webspace_id,
        dev_id,
        browser_page_id=browser_page_id,
        browser_session_id=browser_session_id,
        client_attempt_id=client_attempt_id or None,
    )
    if replaced_existing:
        deadline = time.monotonic() + 1.0
        while (
            _active_yws_connection_total_for_client(
                webspace_id,
                dev_id,
                browser_page_id=browser_page_id,
                browser_session_id=browser_session_id,
                client_attempt_id=client_attempt_id or None,
            )
            >= _YWS_MAX_ACTIVE_PER_CLIENT
            and time.monotonic() < deadline
        ):
            await asyncio.sleep(0.05)
    if guard_diag.get("client_reconnect_storm") or guard_diag.get("webspace_reconnect_storm"):
        _ylog.warning(
            "yws guard allowed reconnect storm webspace=%s dev=%s attempt=%s client_attempt=%s client_storm=%s webspace_storm=%s active=%s recent_open_10s=%s client_open_15s=%s",
            webspace_id,
            dev_id,
            attempt_id,
            client_attempt_id or None,
            bool(guard_diag.get("client_reconnect_storm")),
            bool(guard_diag.get("webspace_reconnect_storm")),
            guard_diag.get("active_total"),
            guard_diag.get("recent_open_10s"),
            guard_diag.get("client_open_15s"),
        )
    _ylog.info("yws connection open webspace=%s dev=%s attempt=%s client_attempt=%s", webspace_id, dev_id, attempt_id, client_attempt_id or None)
    adapter: YWebsocket = FastAPIWebsocketAdapter(websocket, path=webspace_id)
    try:
        room_ref = await _acquire_yws_room(webspace_id, dev_id, yws_attempt_id=attempt_id)
    except asyncio.TimeoutError:
        _remember_yws_attempt(attempt_id, "room_timeout")
        _ylog.warning(
            "yws room ready timeout webspace=%s dev=%s attempt=%s timeout_s=%.3f max_wait_s=%.3f",
            webspace_id,
            dev_id,
            attempt_id,
            _YWS_ROOM_READY_TIMEOUT_S,
            _YWS_ROOM_READY_MAX_S,
        )
        try:
            await websocket.close(code=1013, reason="room_ready_timeout")
            _remember_yws_attempt(attempt_id, "closed", close_code=1013, close_reason="room_ready_timeout")
        except Exception:
            pass
        return
    _record_yws_open(webspace_id, dev_id)
    _track_yws_connection(webspace_id, websocket, device_id=dev_id)
    _transport_mark_open("yws")
    _remember_yws_attempt(attempt_id, "open")
    yws_opened_at = time.time()
    try:
        await _update_device_presence(webspace_id, dev_id)
    except Exception:
        _ylog.debug(
            "failed to project device presence after yws room admission webspace=%s dev=%s attempt=%s",
            webspace_id,
            dev_id,
            attempt_id,
            exc_info=True,
        )
    try:
        from adaos.services.access_links import touch_browser_session

        await asyncio.to_thread(
            touch_browser_session,
            dev_id,
            webspace_id=webspace_id,
            connection_state="connected",
            online=True,
            **browser_metadata,
        )
    except Exception:
        _ylog.debug("browser access registry open update failed webspace=%s dev=%s", webspace_id, dev_id, exc_info=True)
    _publish_runtime_event(
        "browser.session.changed",
        {
            "device_id": dev_id,
            "webspace_id": webspace_id,
            "connection_state": "connected",
            "yjs_channel_state": "open",
            "yjs_attempt_id": attempt_id,
            "client_yws_attempt_id": client_attempt_id or None,
            "source": "yws.gateway",
        },
    )
    repair_replay_task: asyncio.Task[None] | None = asyncio.create_task(
        _flush_pending_effective_repair_replays(
            room_ref,
            adapter,
            webspace_id=webspace_id,
            attempt_id=attempt_id,
            client_attempt_id=client_attempt_id or None,
        )
    )
    try:
        await room_ref.serve(adapter)
    except RuntimeError:
        return
    except Exception:
        _ylog.debug(
            "yws room serve ended with error webspace=%s dev=%s attempt=%s",
            webspace_id,
            dev_id,
            attempt_id,
            exc_info=True,
        )
        return
    finally:
        if repair_replay_task is not None:
            repair_replay_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await repair_replay_task
        yws_lifetime_s = max(0.0, time.time() - yws_opened_at)
        _record_yws_short_session(
            webspace_id,
            dev_id,
            lifetime_s=yws_lifetime_s,
            browser_page_id=browser_page_id,
            browser_session_id=browser_session_id,
            client_attempt_id=client_attempt_id or None,
        )
        _untrack_yws_connection(webspace_id, websocket)
        _transport_mark_close("yws")
        mark_offline = _should_mark_yws_browser_session_offline(dev_id)
        if mark_offline:
            try:
                from adaos.services.access_links import touch_browser_session

                await asyncio.to_thread(
                    touch_browser_session,
                    dev_id,
                    webspace_id=webspace_id,
                    connection_state="closed",
                    online=False,
                    **browser_metadata,
                )
            except Exception:
                _ylog.debug("browser access registry close update failed webspace=%s dev=%s", webspace_id, dev_id, exc_info=True)
            _publish_runtime_event(
                "browser.session.changed",
                {
                    "device_id": dev_id,
                    "webspace_id": webspace_id,
                    "connection_state": "closed",
                    "yjs_channel_state": "closed",
                    "yjs_attempt_id": attempt_id,
                    "client_yws_attempt_id": client_attempt_id or None,
                    "source": "yws.gateway",
                },
            )
        else:
            _ylog.debug(
                "yws connection closed but browser session remains active webspace=%s dev=%s attempt=%s client_attempt=%s active_sessions=%s",
                webspace_id,
                dev_id,
                attempt_id,
                client_attempt_id or None,
                _active_yws_connection_total_for_device(dev_id),
            )
        close_code = None
        close_reason = ""
        try:
            raw_code = getattr(websocket, "close_code", None)
            close_code = int(raw_code) if raw_code is not None else None
        except Exception:
            close_code = None
        try:
            close_reason = str(getattr(websocket, "close_reason", "") or "").strip()
        except Exception:
            close_reason = ""
        _remember_yws_attempt(attempt_id, "closed", close_code=close_code, close_reason=close_reason)
        _ylog.info("yws connection closed webspace=%s dev=%s attempt=%s client_attempt=%s code=%s reason=%s", webspace_id, dev_id, attempt_id, client_attempt_id or None, close_code, close_reason)
        if _ws_trace_enabled():
            try:
                _ylog.info(
                    "yws trace closed client=%s webspace=%s dev=%s attempt=%s client_attempt=%s code=%s",
                    _ws_client_str(websocket),
                    webspace_id,
                    dev_id,
                    attempt_id,
                    client_attempt_id or None,
                    close_code,
                )
            except Exception:
                pass


@router.websocket("/yws")
async def yws(websocket: WebSocket):
    """
    Binary Yjs sync endpoint backed by ypy-websocket.

    Frontend connects via y-websocket with:
      ws://host:port/yws/<webspace_id>?dev=<device_id>
    """
    await _yws_impl(websocket, room=None)


@router.websocket("/yws/{room:path}")
async def yws_room(websocket: WebSocket, room: str):
    """
    Route compatible with y-websocket default URL pattern:
      ws://host:port/yws/<webspace_id>?dev=<device_id>
    """
    await _yws_impl(websocket, room=room)


@router.get("/api/browser/session/authorize")
async def browser_session_authorize(
    dev: str | None = None,
    ws: str | None = None,
    browser_family: str | None = None,
    client_build_id: str | None = None,
    client_build_version: str | None = None,
    os_name: str | None = None,
    form_factor: str | None = None,
    user_agent: str | None = None,
    media_audio_input_device_id: str | None = None,
    media_audio_input_label: str | None = None,
    media_audio_output_device_id: str | None = None,
    media_audio_output_label: str | None = None,
    media_volume: str | None = None,
    media_muted: str | None = None,
    media_audio_input_supported: str | None = None,
    media_audio_output_supported: str | None = None,
    media_audio_output_selection_supported: str | None = None,
    media_route_status_level: str | None = None,
    media_route_status_state: str | None = None,
    media_route_status_reason: str | None = None,
    media_route_status_detail: str | None = None,
    media_route_checked_at: str | None = None,
    media_route_recent_device_change: str | None = None,
    media_route_bluetooth_profile_hint: str | None = None,
    media_route_output_routed: str | None = None,
    media_route_input_applied: str | None = None,
):
    """
    Lightweight browser-device preflight for clients before opening /yws.

    WebSocket close reasons can be hidden by browsers/proxies when the server
    rejects before accept. This JSON endpoint gives the shell a stable,
    product-level state so revoked/expired endpoints can enter login instead
    of running a noisy reconnect loop.
    """
    dev_id = str(dev or "").strip() or "unknown"
    webspace_id = _coerce_gateway_webspace_id(ws)
    metadata_params = {
        "browser_family": browser_family or "",
        "client_build_id": client_build_id or "",
        "client_build_version": client_build_version or "",
        "os_name": os_name or "",
        "form_factor": form_factor or "",
        "user_agent": user_agent or "",
    }
    for key, value in {
        "media_audio_input_device_id": media_audio_input_device_id,
        "media_audio_input_label": media_audio_input_label,
        "media_audio_output_device_id": media_audio_output_device_id,
        "media_audio_output_label": media_audio_output_label,
        "media_volume": media_volume,
        "media_muted": media_muted,
        "media_audio_input_supported": media_audio_input_supported,
        "media_audio_output_supported": media_audio_output_supported,
        "media_audio_output_selection_supported": media_audio_output_selection_supported,
        "media_route_status_level": media_route_status_level,
        "media_route_status_state": media_route_status_state,
        "media_route_status_reason": media_route_status_reason,
        "media_route_status_detail": media_route_status_detail,
        "media_route_checked_at": media_route_checked_at,
        "media_route_recent_device_change": media_route_recent_device_change,
        "media_route_bluetooth_profile_hint": media_route_bluetooth_profile_hint,
        "media_route_output_routed": media_route_output_routed,
        "media_route_input_applied": media_route_input_applied,
    }.items():
        if value is not None:
            metadata_params[key] = value
    metadata = _browser_session_metadata(metadata_params)
    try:
        from adaos.services.access_links import authorize_link, touch_browser_session

        reason = _browser_env_rejected_reason(dev_id, metadata)
        allowed = reason is None
        if allowed:
            allowed, reason = await asyncio.to_thread(authorize_link, "browser", dev_id)
        if not allowed:
            try:
                await asyncio.to_thread(
                    touch_browser_session,
                    dev_id,
                    webspace_id=webspace_id,
                    connection_state=reason or "denied",
                    online=False,
                    **metadata,
                )
            except Exception:
                pass
        return _browser_auth_response_payload(
            dev_id=dev_id,
            webspace_id=webspace_id,
            allowed=allowed,
            reason=reason,
        )
    except Exception:
        _ylog.debug(
            "browser session authorize policy check failed webspace=%s dev=%s",
            webspace_id,
            dev_id,
            exc_info=True,
        )
        # Match /yws behavior: policy storage failures must not lock users out.
        return _browser_auth_response_payload(
            dev_id=dev_id,
            webspace_id=webspace_id,
            allowed=True,
            reason=None,
        )


def _make_publish_bus(
    device_id_ref: Callable[[], str | None],
    webspace_id_ref: Callable[[], str],
) -> Callable[[str, Dict[str, Any] | None], None]:
    """Create a ``_publish_bus`` closure bound to mutable connection state."""

    def _publish_bus(topic: str, extra: Dict[str, Any] | None = None) -> None:
        data = dict(extra or {})
        effective_ws = str(data.get("webspace_id") or webspace_id_ref())
        if not data.get("webspace_id"):
            data["webspace_id"] = effective_ws
        meta = dict(data.get("_meta") or {})
        meta.setdefault("webspace_id", effective_ws)
        target_node_id = str(
            data.get("target_node_id")
            or data.get("node_target_id")
            or meta.get("target_node_id")
            or meta.get("node_target_id")
            or data.get("node_id")
            or ""
        ).strip()
        if target_node_id:
            data.setdefault("target_node_id", target_node_id)
            meta.setdefault("target_node_id", target_node_id)
        did = device_id_ref()
        if did:
            meta.setdefault("device_id", did)
        data["_meta"] = meta
        try:
            ctx = get_agent_ctx()
            ev = DomainEvent(type=topic, payload=data, source="events_ws", ts=time.time())
            ctx.bus.publish(ev)
        except Exception:
            _log.warning("failed to publish %s", topic, exc_info=True)

    return _publish_bus


async def process_events_command(
    kind: str,
    cmd_id: str,
    payload: dict[str, Any],
    device_id: str,
    webspace_id: str,
    send_response: Callable[[dict[str, Any]], Awaitable[None]],
    client_label: str | None = None,
) -> str | None:
    """
    Process a single events-channel command and send ack via *send_response*.

    Returns the **new** ``webspace_id`` when the command changed it (e.g.
    ``device.register``, ``desktop.webspace.use``), or ``None`` if unchanged.

    This function is shared between the ``/ws`` WebSocket endpoint and the
    WebRTC events DataChannel so that both transports execute the same logic.
    """

    _publish_bus = _make_publish_bus(lambda: device_id, lambda: webspace_id)

    async def _ack(ok: bool = True, *, data: dict[str, Any] | None = None, error: str | None = None) -> None:
        msg: dict[str, Any] = {"ch": "events", "t": "ack", "id": cmd_id, "ok": ok}
        if data is not None:
            msg["data"] = data
        if error is not None:
            msg["error"] = error
        await send_response(msg)

    if kind == "device.register":
        new_device = payload.get("device_id") or "dev-unknown"
        requested_webspace = payload.get("webspace_id") or payload.get("id")
        new_webspace = _coerce_gateway_webspace_id(requested_webspace)
        browser_metadata = _browser_session_metadata(payload)

        captured_device = new_device
        captured_ws = new_webspace
        env_reject_reason = _browser_env_rejected_reason(captured_device, browser_metadata)
        if env_reject_reason:
            try:
                from adaos.services.access_links import touch_browser_session

                await asyncio.to_thread(
                    touch_browser_session,
                    captured_device,
                    webspace_id=captured_ws,
                    connection_state=env_reject_reason,
                    online=False,
                    **browser_metadata,
                )
            except Exception:
                pass
            await _ack(False, data={"webspace_id": new_webspace, "reason": env_reject_reason}, error=env_reject_reason)
            return new_webspace

        async def _post_register() -> dict[str, Any]:
            try:
                if not _yws_direct_transport_enabled():
                    _log.warning(
                        "device.register skipped Yjs post steps because direct yws is disabled webspace=%s device=%s",
                        captured_ws,
                        captured_device,
                    )
                    return {
                        "yjs_post_skipped": True,
                        "yjs_guard_reason": "direct_yws_disabled",
                    }
                # The events channel is authoritative and may remain healthy
                # after the shared YWS task fails. Use registration as a
                # recovery opportunity even while YWS admission is backing
                # off a reconnecting client.
                await start_y_server()
                guard_reason, guard_diag = _yws_guard_reject_reason(captured_ws, captured_device)
                if guard_reason:
                    _log.warning(
                        "device.register skipped Yjs post steps due yws guard webspace=%s device=%s reason=%s active=%s recent_open_10s=%s client_open_15s=%s",
                        captured_ws,
                        captured_device,
                        guard_reason,
                        guard_diag.get("active_total"),
                        guard_diag.get("recent_open_10s"),
                        guard_diag.get("client_open_15s"),
                    )
                    return {
                        "yjs_post_skipped": True,
                        "yjs_guard_reason": guard_reason,
                    }
                presence_updated = await _update_device_presence(captured_ws, captured_device)
                # Sync webspace listing directly to the live room's YDoc.
                # This ensures the frontend sees data.webspaces immediately.
                try:
                    from adaos.services.scenario.webspace_runtime import _webspace_listing

                    room = y_server.rooms.get(captured_ws)
                    if room:
                        listing, catalog_version = await asyncio.to_thread(
                            lambda: (_webspace_listing(), workspace_catalog_version())
                        )
                        with ystore_write_metadata_sync(
                            root_names=["data"],
                            source="yjs.gateway_ws",
                            owner="core:yjs_gateway",
                            channel="core.yjs.gateway.sync",
                        ):
                            with room.ydoc.begin_transaction() as txn:
                                data_map = room.ydoc.get_map("data")
                                data_map.set(
                                    txn,
                                    "webspaces",
                                    {
                                        "schema": "adaos.workspace_catalog.v1",
                                        "version": catalog_version,
                                        "items": listing,
                                    },
                                )
                        _log.debug("wrote webspaces listing to room webspace=%s items=%d", captured_ws, len(listing))
                except Exception:
                    _log.debug("webspace listing sync failed", exc_info=True)
                _log.debug("device.register post steps ok webspace=%s device=%s", captured_ws, captured_device)
                return {
                    "yjs_post_skipped": False,
                    "yjs_presence_deferred": not bool(presence_updated),
                    "yjs_presence_reason": "awaiting_yws_transport" if not presence_updated else None,
                }
            except Exception:
                _log.warning("device.register post steps failed webspace=%s device=%s", captured_ws, captured_device, exc_info=True)
                return {"yjs_post_failed": True}

        try:
            # Ack control registration without opening a YRoom. The browser can
            # now establish YWS, whose admitted room bootstrap is authoritative.
            post_result = await _post_register()
            event_payload = {
                "device_id": captured_device,
                "webspace_id": captured_ws,
                "kind": "browser",
            }
            if post_result.get("yjs_post_skipped"):
                event_payload["yjs_post_skipped"] = True
                event_payload["yjs_guard_reason"] = str(post_result.get("yjs_guard_reason") or "")
            if post_result.get("yjs_presence_deferred"):
                event_payload["yjs_presence_deferred"] = True
                event_payload["yjs_presence_reason"] = str(post_result.get("yjs_presence_reason") or "")
            _publish_bus(
                "device.registered",
                event_payload,
            )
            ack_data = {"webspace_id": new_webspace}
            if post_result.get("yjs_post_skipped"):
                ack_data["yjs_post_skipped"] = True
                ack_data["yjs_guard_reason"] = str(post_result.get("yjs_guard_reason") or "")
            if post_result.get("yjs_presence_deferred"):
                ack_data["yjs_presence_deferred"] = True
                ack_data["yjs_presence_reason"] = str(post_result.get("yjs_presence_reason") or "")
            await _ack(data=ack_data)
        except Exception:
            # Best-effort: still send ack even if post-register fails
            await _ack(data={"webspace_id": new_webspace})
        return new_webspace

    if kind == "desktop.toggleInstall":
        _publish_bus("desktop.toggleInstall", {"type": payload.get("type"), "id": payload.get("id"), "webspace_id": payload.get("webspace_id")})
        await _ack()
        return None

    if kind == "desktop.webspace.create":
        _publish_bus("desktop.webspace.create", {"id": payload.get("id"), "title": payload.get("title"), "scenario_id": payload.get("scenario_id"), "dev": payload.get("dev")})
        await _ack()
        return None

    if kind == "desktop.webspace.rename":
        _publish_bus("desktop.webspace.rename", {"id": payload.get("id"), "title": payload.get("title")})
        await _ack()
        return None

    if kind == "desktop.webspace.update":
        _publish_bus(
            "desktop.webspace.update",
            {
                "id": payload.get("id") or payload.get("webspace_id"),
                "title": payload.get("title"),
                "home_scenario": payload.get("home_scenario") or payload.get("scenario_id"),
            },
        )
        await _ack()
        return None

    if kind == "desktop.webspace.delete":
        _publish_bus("desktop.webspace.delete", {"id": payload.get("id")})
        await _ack()
        return None

    if kind == "desktop.webspace.refresh":
        _publish_bus("desktop.webspace.refresh", payload)
        await _ack()
        return None

    if kind == "desktop.webspace.go_home":
        payload = dict(payload or {})
        target_webspace = _coerce_gateway_webspace_id(
            payload.get("webspace_id") or payload.get("workspace_id") or webspace_id
        )
        wait_for_rebuild = (
            bool(payload.get("wait_for_rebuild"))
            if "wait_for_rebuild" in payload
            else False
        )
        try:
            from adaos.services.scenario.webspace_runtime import go_home_webspace

            result = await go_home_webspace(target_webspace, wait_for_rebuild=wait_for_rebuild)
            await _ack(bool(result.get("accepted", result.get("ok", True))), data=result)
        except Exception as exc:
            _log.warning("desktop.webspace.go_home direct switch failed webspace=%s", target_webspace, exc_info=True)
            await _ack(False, error=f"{type(exc).__name__}: {exc}")
        return None

    if kind == "desktop.webspace.set_home":
        target = (payload or {}).get("scenario_id")
        if not target:
            await _ack(False, error="scenario_id required")
        else:
            _publish_bus("desktop.webspace.set_home", payload)
            await _ack()
        return None

    if kind == "desktop.webspace.ensure_dev":
        target = str((payload or {}).get("scenario_id") or "").strip()
        if not target:
            await _ack(False, error="scenario_id required")
            return None
        try:
            from adaos.services.scenario.webspace_runtime import ensure_dev_webspace_for_scenario

            result = await ensure_dev_webspace_for_scenario(
                target,
                requested_id=str((payload or {}).get("id") or (payload or {}).get("requested_id") or "").strip() or None,
                title=str((payload or {}).get("title") or "").strip() or None,
            )
            ensured_webspace_id = str(result.get("webspace_id") or "").strip() or None
            if ensured_webspace_id:
                await ensure_webspace_ready(
                    ensured_webspace_id,
                    scenario_id=str(result.get("home_scenario") or target).strip() or target,
                )
            await _ack(data=result)
        except ValueError as exc:
            await _ack(False, error=str(exc) or "scenario_id required")
        except Exception:
            _log.warning("desktop.webspace.ensure_dev failed scenario=%s", target, exc_info=True)
            await _ack(False, error="dev_webspace_unavailable")
        return None

    if kind == "desktop.webspace.use":
        target = payload.get("id") or payload.get("webspace_id")
        if not target:
            await _ack(False, error="webspace_id required")
            return None
        new_webspace = _coerce_gateway_webspace_id(target)
        target_scenario = str(payload.get("scenario_id") or "").strip()
        try:
            switch_result: dict[str, Any] | None = None
            if target_scenario:
                from adaos.services.scenario.webspace_runtime import switch_webspace_scenario

                switch_result = await switch_webspace_scenario(
                    new_webspace,
                    target_scenario,
                    set_home=False,
                    wait_for_rebuild=True,
                    request_source="gateway_ws.desktop.webspace.use",
                    request_client=str(client_label or "").strip() or None,
                )
                if not bool(switch_result.get("accepted", switch_result.get("ok", True))):
                    await _ack(False, error=str(switch_result.get("error") or "scenario_unavailable"))
                    return None
            else:
                await ensure_webspace_ready(new_webspace)
            await _update_device_presence(new_webspace, device_id or "dev-unknown")
            _publish_bus("desktop.webspace.refresh", {"webspace_id": new_webspace})
            ack_data: dict[str, Any] = {"webspace_id": new_webspace}
            if target_scenario:
                ack_data["scenario_id"] = target_scenario
                if switch_result is not None:
                    ack_data["scenario_switch"] = switch_result
            await _ack(data=ack_data)
            return new_webspace
        except Exception:
            await _ack(False, error="webspace_unavailable")
            return None

    if kind == "skill.event.publish":
        event_type = str((payload or {}).get("event_type") or (payload or {}).get("type") or "").strip()
        if not event_type:
            await _ack(False, error="event_type required")
            return None
        raw_event_payload = (payload or {}).get("payload")
        if isinstance(raw_event_payload, dict):
            event_payload = dict(raw_event_payload)
        elif raw_event_payload is None:
            event_payload = {}
        else:
            event_payload = {"value": raw_event_payload}
        for key in ("webspace_id", "workspace_id", "node_id", "target_node_id"):
            value = (payload or {}).get(key)
            if value is not None and not event_payload.get(key):
                event_payload[key] = value
        meta = dict(event_payload.get("_meta") or {})
        top_meta = (payload or {}).get("_meta")
        if isinstance(top_meta, dict):
            for key, value in top_meta.items():
                meta.setdefault(key, value)
        if meta:
            event_payload["_meta"] = meta
        _publish_bus(event_type, event_payload)
        await _ack(data={"event_type": event_type})
        return None

    if kind == "voice.activation.claim":
        try:
            from adaos.services.voice_runtime import claim_voice_activation

            candidate = dict(payload or {})
            try:
                node_id = str(getattr(get_agent_ctx().config, "node_id", "") or "").strip()
                subnet_id = str(getattr(get_agent_ctx().config, "subnet_id", "") or "").strip()
            except Exception:
                node_id = ""
                subnet_id = ""
            candidate["device_id"] = node_id or device_id or "dev-unknown"
            candidate["room_id"] = subnet_id or webspace_id
            result = await asyncio.to_thread(
                claim_voice_activation,
                candidate,
                window_ms=int(candidate.get("window_ms") or 280),
            )
            await _ack(data=result)
        except (TypeError, ValueError) as exc:
            await _ack(False, error=str(exc))
        except Exception:
            _log.warning("voice activation claim failed device=%s", device_id, exc_info=True)
            await _ack(False, error="voice_activation_arbitration_failed")
        return None

    if kind == "demo_metrics.host_action":
        event_payload = dict(payload or {})
        event_payload["webspace_id"] = payload.get("webspace_id")
        _publish_bus("demo_metrics.host_action", event_payload)
        await _ack()
        return None

    if kind == "voice.chat.open":
        event_payload = dict(payload or {})
        event_payload["webspace_id"] = payload.get("webspace_id")
        _publish_bus("voice.chat.open", event_payload)
        await _ack()
        return None

    if kind in {"voice.chat.user", "dialog.user_message"}:
        event_payload = dict(payload or {})
        event_payload["text"] = payload.get("text")
        event_payload["webspace_id"] = payload.get("webspace_id")
        if kind == "dialog.user_message":
            meta = dict(event_payload.get("_meta") or {})
            meta.setdefault("dialog_event_kind", "dialog.user_message")
            meta.setdefault("canonical_event_kind", "dialog.user_message")
            meta.setdefault("input_event_kind", "dialog.user_message")
            event_payload["_meta"] = meta
        _publish_bus(kind, event_payload)
        await _ack()
        return None

    if kind == "pending_actions.publish.request":
        event_payload = dict(payload or {})
        event_payload.pop("_meta", None)
        try:
            from adaos.services.pending_actions import publish_pending_action_async

            action = await publish_pending_action_async(ctx=get_agent_ctx(), **event_payload)
            await _ack(data={"action": action})
        except Exception as exc:
            _log.warning("pending action publish command failed", exc_info=True)
            await _ack(False, error=f"{type(exc).__name__}: {exc}")
        return None

    if kind == "pending_actions.respond.request":
        event_payload = dict(payload or {})
        event_payload.pop("_meta", None)
        action_id = str(event_payload.pop("action_id", event_payload.pop("pending_action_id", "")) or "").strip()
        response_action_id = str(event_payload.pop("response_action_id", event_payload.pop("action", "")) or "").strip()
        try:
            from adaos.services.pending_actions import respond_pending_action_async

            result = await respond_pending_action_async(
                action_id,
                response_action_id,
                ctx=get_agent_ctx(),
                **event_payload,
            )
            await _ack(data=result)
        except Exception as exc:
            _log.warning(
                "pending action respond command failed action_id=%s response_action_id=%s",
                action_id or "-",
                response_action_id or "-",
                exc_info=True,
            )
            await _ack(False, error=f"{type(exc).__name__}: {exc}")
        return None

    if kind == "conversation.interaction.respond.request":
        event_payload = dict(payload or {})
        meta = event_payload.pop("_meta", None)
        meta = dict(meta) if isinstance(meta, Mapping) else {}
        action_token = str(event_payload.get("action_token") or "").strip()
        idempotency_key = str(event_payload.get("idempotency_key") or "").strip()
        if not idempotency_key and action_token:
            idempotency_key = f"web:{str(event_payload.get('source_message_id') or 'message')}:{action_token}"
        try:
            from adaos.services import conversation_interactions

            result = conversation_interactions.submit_action_token(
                action_token,
                actor_id=str(meta.get("user_id") or "user:local").strip() or "user:local",
                idempotency_key=idempotency_key,
                metadata={
                    **meta,
                    "io_type": "web",
                    "source_message_id": str(event_payload.get("source_message_id") or "").strip()
                    or None,
                    "webspace_id": str(
                        event_payload.get("webspace_id") or meta.get("webspace_id") or ""
                    ).strip(),
                },
            )
            _publish_bus("conversation.interaction.responded", result)
            await _ack(data=result)
        except Exception as exc:
            _log.warning("conversation interaction response command failed", exc_info=True)
            await _ack(False, error=f"{type(exc).__name__}: {exc}")
        return None

    if kind == "pending_actions.expire.request":
        event_payload = dict(payload or {})
        event_payload.pop("_meta", None)
        try:
            from adaos.services.pending_actions import expire_pending_actions_async

            result = await expire_pending_actions_async(
                webspace_id=event_payload.get("webspace_id"),
                ctx=get_agent_ctx(),
            )
            await _ack(data=result)
        except Exception as exc:
            _log.warning("pending action expire command failed", exc_info=True)
            await _ack(False, error=f"{type(exc).__name__}: {exc}")
        return None

    if kind == "desktop.webspace.reload":
        payload = dict(payload or {})
        trace = _record_command_trace(
            kind=kind,
            cmd_id=cmd_id,
            payload=payload,
            device_id=device_id,
            webspace_id=webspace_id,
            client_label=client_label,
        )
        meta = dict(payload.get("_meta") or {})
        meta.setdefault("cmd_id", str(cmd_id or "").strip() or None)
        meta.setdefault("gateway_client", str(client_label or "").strip() or None)
        meta.setdefault("gateway_command_seq", int(trace.get("seq") or 0))
        meta.setdefault("gateway_command_fingerprint", str(trace.get("fingerprint") or ""))
        payload["_meta"] = meta
        _ylog.warning(
            "desktop.webspace.reload ingress cmd=%s seq=%s webspace=%s device=%s client=%s scenario=%s recreate_room=%s dup_recent=%s dup10s=%s fp=%s",
            cmd_id or "-",
            trace.get("seq") or 0,
            trace.get("webspace_id") or webspace_id,
            device_id or "-",
            client_label or "-",
            trace.get("scenario_id") or "-",
            "yes" if trace.get("recreate_room") else "no",
            "yes" if trace.get("duplicate_recent") else "no",
            trace.get("duplicate_count_10s") or 0,
            trace.get("fingerprint") or "-",
        )
        if bool(trace.get("duplicate_recent")):
            _ylog.warning(
                "desktop.webspace.reload duplicate suppressed webspace=%s cmd_id=%s seq=%s fp=%s dup10s=%s",
                webspace_id,
                cmd_id or "-",
                trace.get("seq") or 0,
                trace.get("fingerprint") or "-",
                trace.get("duplicate_count_10s") or 0,
            )
            await _ack(
                data={
                    "duplicate": True,
                    "suppressed": True,
                    "gateway_command_seq": int(trace.get("seq") or 0),
                    "gateway_command_fingerprint": str(trace.get("fingerprint") or ""),
                }
            )
            return None
        guard_reset = clear_yws_guard_state_for_webspace(
            str(trace.get("webspace_id") or webspace_id or "default"),
            reason="desktop.webspace.reload",
        )
        payload["_meta"]["yws_guard_reset"] = guard_reset
        _publish_bus("desktop.webspace.reload", payload)
        await _ack()
        return None

    if kind == "desktop.webspace.reset":
        payload = dict(payload or {})
        trace = _record_command_trace(
            kind=kind,
            cmd_id=cmd_id,
            payload=payload,
            device_id=device_id,
            webspace_id=webspace_id,
            client_label=client_label,
        )
        meta = dict(payload.get("_meta") or {})
        meta.setdefault("cmd_id", str(cmd_id or "").strip() or None)
        meta.setdefault("gateway_client", str(client_label or "").strip() or None)
        meta.setdefault("gateway_command_seq", int(trace.get("seq") or 0))
        meta.setdefault("gateway_command_fingerprint", str(trace.get("fingerprint") or ""))
        payload["_meta"] = meta
        _ylog.warning(
            "desktop.webspace.reset ingress cmd=%s seq=%s webspace=%s device=%s client=%s scenario=%s dup_recent=%s dup10s=%s fp=%s",
            cmd_id or "-",
            trace.get("seq") or 0,
            trace.get("webspace_id") or webspace_id,
            device_id or "-",
            client_label or "-",
            trace.get("scenario_id") or "-",
            "yes" if trace.get("duplicate_recent") else "no",
            trace.get("duplicate_count_10s") or 0,
            trace.get("fingerprint") or "-",
        )
        guard_reset = clear_yws_guard_state_for_webspace(
            str(trace.get("webspace_id") or webspace_id or "default"),
            reason="desktop.webspace.reset",
        )
        payload["_meta"]["yws_guard_reset"] = guard_reset
        _publish_bus("desktop.webspace.reset", payload)
        await _ack()
        return None

    if kind == "desktop.scenario.set":
        payload = dict(payload or {})
        target = payload.get("scenario_id")
        if not target:
            await _ack(False, error="scenario_id required")
        else:
            try:
                from adaos.services.scenario.webspace_runtime import switch_webspace_scenario

                target_webspace = str(
                    payload.get("webspace_id")
                    or payload.get("workspace_id")
                    or webspace_id
                    or "default"
                ).strip() or "default"
                if "set_home" in payload:
                    set_home = bool(payload.get("set_home"))
                elif "persist_home" in payload:
                    set_home = bool(payload.get("persist_home"))
                else:
                    set_home = None
                wait_for_rebuild = (
                    bool(payload.get("wait_for_rebuild"))
                    if "wait_for_rebuild" in payload
                    else False
                )
                result = await switch_webspace_scenario(
                    target_webspace,
                    str(target),
                    set_home=set_home,
                    wait_for_rebuild=wait_for_rebuild,
                    request_source="gateway_ws.desktop.scenario.set",
                    request_client=str(client_label or "").strip() or None,
                )
                await _ack(bool(result.get("accepted", result.get("ok", True))), data=result)
            except Exception as exc:
                _log.warning(
                    "desktop.scenario.set direct switch failed webspace=%s scenario=%s",
                    webspace_id,
                    target,
                    exc_info=True,
                )
                await _ack(False, error=f"{type(exc).__name__}: {exc}")
        return None

    if kind == "skills.update":
        try:
            from adaos.services.agent_context import get_ctx as _get_ctx
            from adaos.services.artifact_subscription_update import (
                ArtifactSubscriptionUpdateCoordinator,
            )
            from adaos.services.skill.update import SkillUpdateService

            ctx = _get_ctx()
            skill_name = str(payload.get("name") or payload.get("skill") or "").strip()
            dry_run = bool(payload.get("dry_run", False))
            if not skill_name:
                await _ack(False, error="name required")
                return None
            coordinator = ArtifactSubscriptionUpdateCoordinator(ctx)
            update_route = coordinator.select_route(skill_name)
            if update_route.package_required:
                result = await coordinator.update(
                    "skill",
                    skill_name,
                    dry_run=dry_run,
                    webspace_id=str(payload.get("webspace_id") or webspace_id),
                    defer_webspace_rebuild=bool(payload.get("defer_webspace_rebuild", False)),
                    expected_plan_digest=(
                        str(payload.get("expected_plan_digest") or "").strip() or None
                    ),
                    permission_decision=(
                        payload.get("permission_decision")
                        if isinstance(payload.get("permission_decision"), dict)
                        else None
                    ),
                    idempotency_key=(
                        str(payload.get("idempotency_key") or "").strip() or None
                    ),
                )
                await _ack(True, data=result)
                return None
            # Explicit compatibility fallback for installations that have not
            # acquired a stable package subscription yet.
            result = SkillUpdateService(ctx).request_update(skill_name, dry_run=dry_run)
            _publish_bus("skills.updated", {"name": skill_name, "version": result.version, "updated": result.updated})
            await _ack(
                True,
                data={
                    "name": skill_name,
                    "updated": result.updated,
                    "version": result.version,
                    "mode": "legacy_source_pull",
                    "update_route": update_route.to_dict(),
                    "legacy_materialization": True,
                    "warning": "no stable package subscription; compatibility git pull was used",
                },
            )
        except FileNotFoundError:
            await _ack(False, error="skill_not_installed")
        except PermissionError as exc:
            await _ack(False, error=str(exc) or "fs_readonly")
        except Exception as exc:
            detail = exc.to_detail() if callable(getattr(exc, "to_detail", None)) else None
            await _ack(
                False,
                error=(
                    json.dumps(detail, ensure_ascii=False)
                    if isinstance(detail, dict)
                    else str(exc) or "update_failed"
                ),
            )
        return None

    if kind == "nlp.teacher.candidate.apply":
        _publish_bus(
            "nlp.teacher.candidate.apply",
            {
                "candidate_id": payload.get("candidate_id"),
                "target": payload.get("target"),
                "webspace_id": payload.get("webspace_id"),
                "_meta": payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {},
            },
        )
        await _ack()
        return None

    if kind == "nlp.teacher.candidate.test":
        _publish_bus(
            "nlp.teacher.candidate.test",
            {
                "candidate_id": payload.get("candidate_id"),
                "target": payload.get("target"),
                "webspace_id": payload.get("webspace_id"),
                "_meta": payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {},
            },
        )
        await _ack()
        return None

    if kind == "nlp.teacher.revision.apply":
        _publish_bus(
            "nlp.teacher.revision.apply",
            {
                "revision_id": payload.get("revision_id"),
                "intent": payload.get("intent"),
                "examples": payload.get("examples"),
                "slots": payload.get("slots"),
                "webspace_id": payload.get("webspace_id"),
                "_meta": payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {},
            },
        )
        await _ack()
        return None

    if kind == "nlp.teacher.regex_rule.apply":
        _publish_bus(
            "nlp.teacher.regex_rule.apply",
            {
                "candidate_id": payload.get("candidate_id"),
                "intent": payload.get("intent"),
                "pattern": payload.get("pattern"),
                "target": payload.get("target"),
                "webspace_id": payload.get("webspace_id"),
                "_meta": payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {},
            },
        )
        await _ack()
        return None

    if kind == "scenario.workflow.action":
        _publish_bus("scenario.workflow.action", payload)
        await _ack()
        return None

    if kind == "scenario.workflow.set_state":
        _publish_bus("scenario.workflow.set_state", payload)
        await _ack()
        return None

    if kind == "webio.yjs.subscription.changed":
        try:
            from adaos.sdk.data.projections import record_projection_subscription_change

            record_projection_subscription_change(payload)
        except Exception:
            _log.debug("failed to record explicit webio.yjs.subscription.changed", exc_info=True)
        _publish_bus("webio.yjs.subscription.changed", payload)
        await _ack()
        return None

    if kind == "infrastate.action" and str(payload.get("id") or "").strip() == "marketplace_install":
        try:
            from adaos.services.operations import submit_marketplace_install_action

            operation = await asyncio.to_thread(
                submit_marketplace_install_action,
                payload,
                webspace_id=webspace_id,
                initiator_kind="events_ws",
                ctx=get_agent_ctx(),
            )
        except ValueError as exc:
            error = str(exc) or "marketplace_install_invalid"
            _log.warning("marketplace install command rejected error=%s", error)
            await _ack(False, error=error)
            return None
        except Exception as exc:
            _log.warning("marketplace install command failed", exc_info=True)
            await _ack(False, error=f"marketplace_install_failed:{type(exc).__name__}")
            return None
        await _ack(
            data={
                "action": "marketplace_install",
                "operation_id": operation.get("operation_id"),
                "operation": operation,
            }
        )
        return None

    # Default behaviour for declarative host actions: publish unknown command
    # kinds to the local bus so skills can subscribe to their own UI events.
    if isinstance(kind, str) and kind.strip():
        if _should_drop_duplicate_webio_control_event(kind, payload):
            await _ack()
            return None
        _publish_bus(kind, payload)
    await _ack()
    return None


@router.websocket("/ws")
async def events_ws(websocket: WebSocket):
    """
    JSON events websocket.

    Implements device.register, desktop/voice/scenario commands, and WebRTC
    signaling (``rtc.offer``, ``rtc.ice``).
    """
    if not await _accept_websocket(websocket, channel="events"):
        return
    _transport_mark_open("ws")
    if _ws_trace_enabled():
        try:
            params: Dict[str, str] = dict(websocket.query_params)
            token_present = "token" in params
            _log.info(
                "ws trace open client=%s token=%s params=%s",
                _ws_client_str(websocket),
                token_present,
                ",".join(sorted(params.keys())) if params else "",
            )
        except Exception:
            pass

    device_id: str | None = None
    webspace_id = _coerce_gateway_webspace_id(None)
    _track_events_ws_connection(webspace_id, websocket)
    ws_loop = asyncio.get_running_loop()

    async def _ws_send(msg: dict[str, Any]) -> None:
        try:
            await websocket.send_text(json.dumps(msg))
        except (WebSocketDisconnect, RuntimeError):
            # Connection closed - silently return
            return

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break
            except RuntimeError as exc:
                if _is_websocket_receive_disconnect_race(exc):
                    if _ws_trace_enabled():
                        _log.info(
                            "ws receive skipped because connection is already closed client=%s reason=%s",
                            _ws_client_str(websocket),
                            str(exc),
                        )
                    break
                raise

            try:
                msg = json.loads(raw)
            except Exception:
                continue

            if msg.get("type") == "subscribe":
                added = _register_ws_event_subscriptions(
                    websocket,
                    ws_loop,
                    msg.get("topics"),
                )
                if added:
                    await _send_initial_ws_event_messages(websocket, added)
                    _request_webio_stream_snapshots(added, transport="ws")
                    _request_webio_yjs_projection_snapshots(added, transport="ws")
                continue

            if msg.get("type") == "unsubscribe":
                _unregister_ws_event_subscription_topics(websocket, msg.get("topics"))
                continue

            ch = msg.get("ch")
            t = msg.get("t")
            if ch != "events" or t != "cmd":
                continue

            cmd_id = msg.get("id")
            kind = msg.get("kind")
            payload = msg.get("payload") or {}

            # -- WebRTC signaling (rtc.offer / rtc.ice) -----------------------
            if kind == "rtc.offer":
                try:
                    from adaos.services.webrtc.peer import handle_rtc_offer

                    signal_device_id = _clean_signaling_device_id(payload.get("device_id")) or device_id or "unknown"
                    signal_peer_id = _clean_signaling_device_id(payload.get("peer_id")) or signal_device_id
                    signal_webspace_id = _coerce_gateway_webspace_id(payload.get("webspace_id") or webspace_id)
                    signal_generation_id = str(payload.get("generation_id") or "").strip() or None
                    signal_browser_session_id = _clean_browser_metadata_value(
                        payload.get("browser_session_id"),
                        max_len=128,
                    )
                    signal_client_build_id = _clean_browser_metadata_value(
                        payload.get("client_build_id"),
                        max_len=96,
                    )
                    signal_client_build_version = _clean_browser_metadata_value(
                        payload.get("client_build_version"),
                        max_len=128,
                    )
                    if device_id is None and signal_device_id != "unknown":
                        device_id = signal_device_id
                    webspace_id = signal_webspace_id
                    _track_events_ws_connection(webspace_id, websocket)

                    async def _send_ice_via_ws(candidate: dict[str, Any]) -> None:
                        try:
                            await websocket.send_text(
                                json.dumps(
                                    {
                                        "ch": "events",
                                        "t": "evt",
                                        "kind": "rtc.ice",
                                        "payload": {
                                            "candidate": candidate,
                                            "generation_id": signal_generation_id,
                                        },
                                    }
                                )
                            )
                        except (WebSocketDisconnect, RuntimeError):
                            # Connection closed - silently return
                            return

                    answer = await handle_rtc_offer(
                        offer_sdp=payload.get("sdp", ""),
                        offer_type=payload.get("type", "offer"),
                        device_id=signal_device_id,
                        webspace_id=signal_webspace_id,
                        send_ice_cb=_send_ice_via_ws,
                        generation_id=signal_generation_id,
                        negotiation_mode=payload.get("negotiation_mode"),
                        peer_id=signal_peer_id,
                        browser_session_id=signal_browser_session_id,
                        client_build_id=signal_client_build_id,
                        client_build_version=signal_client_build_version,
                    )
                    await _ws_send({"ch": "events", "t": "ack", "id": cmd_id, "ok": True, "data": answer})
                except Exception as e:
                    _log.error(f"rtc.offer failed: {e!r}", exc_info=True)
                    await _ws_send({"ch": "events", "t": "ack", "id": cmd_id, "ok": False, "error": f"rtc_offer_failed: {e}"})
                continue

            if kind == "rtc.ice":
                try:
                    from adaos.services.webrtc.peer import handle_remote_ice

                    signal_device_id = _clean_signaling_device_id(payload.get("device_id")) or device_id or "unknown"
                    signal_peer_id = _clean_signaling_device_id(payload.get("peer_id")) or signal_device_id
                    if device_id is None and signal_device_id != "unknown":
                        device_id = signal_device_id
                    if payload.get("webspace_id"):
                        webspace_id = _coerce_gateway_webspace_id(payload.get("webspace_id"))
                        _track_events_ws_connection(webspace_id, websocket)
                    await handle_remote_ice(
                        signal_device_id,
                        payload.get("candidate"),
                        generation_id=payload.get("generation_id"),
                        peer_id=signal_peer_id,
                    )
                    await _ws_send({"ch": "events", "t": "ack", "id": cmd_id, "ok": True})
                except Exception as e:
                    _log.error(f"rtc.ice failed: {e!r}", exc_info=True)
                    await _ws_send({"ch": "events", "t": "ack", "id": cmd_id, "ok": False, "error": f"rtc_ice_failed: {e}"})
                continue

            # -- Standard commands via extracted dispatcher --------------------
            new_ws = await process_events_command(
                kind=kind,
                cmd_id=cmd_id,
                payload=payload,
                device_id=device_id or "dev-unknown",
                webspace_id=webspace_id,
                client_label=_ws_client_str(websocket),
                send_response=_ws_send,
            )
            # Update connection-scoped state when a command changed it.
            if new_ws is not None:
                webspace_id = new_ws
                _track_events_ws_connection(webspace_id, websocket)
            if kind == "device.register":
                device_id = payload.get("device_id") or "dev-unknown"
    finally:
        _transport_mark_close("ws")
        _untrack_events_ws_connection(websocket)
        _unregister_ws_event_subscriptions(websocket)
        _ = device_id
        if _ws_trace_enabled():
            try:
                code = getattr(websocket, "close_code", None)
                _log.info(
                    "ws trace closed client=%s device=%s webspace=%s code=%s",
                    _ws_client_str(websocket),
                    device_id,
                    webspace_id,
                    code,
                )
            except Exception:
                pass
