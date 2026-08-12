from __future__ import annotations

import json as _json
import os
import re
import threading
import time
from pathlib import Path
from typing import Any

from adaos.services.realtime_sidecar import realtime_sidecar_route_tunnel_ws_bases
from adaos.services.reliability import observe_hub_root_route_runtime
from adaos.services.runtime_paths import current_state_dir
from adaos.services.runtime_topology import (
    DEFAULT_CANDIDATE_RUNTIME_PORT,
    DEFAULT_RUNTIME_PORT,
    http_base,
    is_loopback_http_url,
    local_http_bases,
    runtime_fallback_ws_bases,
    runtime_port_http_base_from_env,
    runtime_probe_http_bases,
    supervisor_base_candidates_from_env,
    supervisor_base_from_env,
)


def _hub_route_max_chunk_raw_bytes(pending_warn_bytes: int | None = None) -> int:
    default = 256 * 1024
    minimum = 16 * 1024
    maximum = 512 * 1024
    try:
        raw = int(str(os.getenv("HUB_ROUTE_MAX_CHUNK_RAW_BYTES") or str(default)).strip())
    except Exception:
        raw = default
    raw = max(minimum, min(maximum, raw))
    try:
        warn = int(pending_warn_bytes or 0)
    except Exception:
        warn = 0
    if warn > 0:
        # Route chunks are JSON+base64 encoded before they hit the NATS writer.
        # Keep each encoded publish below the pending-data warning budget when
        # operators tune that budget down, otherwise a single YWS repair chunk
        # can immediately trip route pressure.
        overhead_reserve = min(32 * 1024, max(4 * 1024, warn // 16))
        safe_budget = max(0, warn - overhead_reserve)
        safe_raw = max(minimum, (safe_budget * 3) // 4)
        safe_raw = max(minimum, (safe_raw // (4 * 1024)) * (4 * 1024))
        raw = min(raw, safe_raw)
    return int(raw)


def _hub_route_normalize_resend_chunk_indexes(
    missing: Any,
    total: Any,
    *,
    max_items: int = 128,
) -> list[int]:
    try:
        total_i = int(total or 0)
    except Exception:
        total_i = 0
    if total_i <= 0:
        return []
    try:
        max_i = max(1, int(max_items or 1))
    except Exception:
        max_i = 128
    if not isinstance(missing, (list, tuple)):
        return []
    indexes: list[int] = []
    seen: set[int] = set()
    for item in missing:
        try:
            idx = int(item)
        except Exception:
            continue
        if idx < 0 or idx >= total_i or idx in seen:
            continue
        indexes.append(idx)
        seen.add(idx)
        if len(indexes) >= max_i:
            break
    return indexes


def _hub_route_path_token(path: Any) -> str:
    token = str(path or "").strip().split("?", 1)[0].rstrip("/")
    return token or "/"


def _hub_route_semantic_flow_for_path(path: Any) -> str:
    token = _hub_route_path_token(path)
    if token == "/ws/subnet" or token.startswith("/ws/subnet/"):
        return "subnet"
    if token == "/yws" or token.startswith("/yws/"):
        return "sync"
    if token == "/ws" or token.startswith("/ws/"):
        return "control"
    return "route"


def _hub_route_should_shed_sync_frame(
    path: Any,
    *,
    pending_data_size: Any,
    guardrail_active: Any,
    frame_flush_pending_bytes: Any,
    sync_shed_pending_bytes: Any = None,
    payload_bytes: Any = 0,
) -> bool:
    if _hub_route_semantic_flow_for_path(path) != "sync":
        return False
    if bool(guardrail_active):
        return True
    try:
        threshold = int(sync_shed_pending_bytes or 0)
    except Exception:
        threshold = 0
    if threshold <= 0:
        return False
    try:
        pending = max(0, int(pending_data_size or 0))
    except Exception:
        pending = 0
    # YWS sync can legitimately emit a large first-state frame.  The route
    # reader chunks large payloads after this check, so sync shedding uses its
    # own high-water mark instead of the much smaller force-flush threshold.
    return pending >= threshold


def _hub_route_sync_frame_force_flush_enabled(raw: Any = None) -> bool:
    if raw is None:
        raw = os.getenv("HUB_ROUTE_SYNC_FRAME_FORCE_FLUSH")
    token = str(raw if raw is not None else "0").strip().lower()
    if not token:
        return False
    return token in {"1", "true", "yes", "on"}


def _hub_route_should_force_flush_reply(
    payload: Any,
    *,
    route_force_flush: Any,
    route_sync_frame_force_flush: Any,
    tunnel_flow: Any,
    pending_data_size: Any,
    frame_flush_pending_bytes: Any,
) -> bool:
    if not bool(route_force_flush):
        return False
    if not isinstance(payload, dict):
        return False
    t = payload.get("t")
    if t in ("open_ack", "http_resp", "close"):
        return True
    if t not in ("frame", "chunk"):
        return False

    payload_flow = str(payload.get("flow") or "").strip().lower()
    flow = payload_flow or str(tunnel_flow or "").strip().lower()
    is_sync_frame = flow == "sync"
    if is_sync_frame and t == "chunk":
        try:
            idx = int(payload.get("idx") or 0)
        except Exception:
            idx = 0
        try:
            total = int(payload.get("total") or 0)
        except Exception:
            total = 0
        if bool(route_sync_frame_force_flush):
            return total > 0
        try:
            threshold = int(frame_flush_pending_bytes or 0)
        except Exception:
            threshold = 0
        if threshold <= 0:
            return False
        try:
            pending = max(0, int(pending_data_size or 0))
        except Exception:
            pending = 0
        if pending >= threshold:
            return True
        if total > 1 and 0 <= idx < total - 1:
            return False
        return False
    if is_sync_frame and bool(route_sync_frame_force_flush):
        return True
    if is_sync_frame:
        try:
            threshold = int(frame_flush_pending_bytes or 0)
        except Exception:
            threshold = 0
        if threshold <= 0:
            return False
        try:
            pending = max(0, int(pending_data_size or 0))
        except Exception:
            pending = 0
        return pending >= threshold

    try:
        threshold = int(frame_flush_pending_bytes or 0)
    except Exception:
        threshold = 0
    if threshold <= 0:
        return False
    try:
        pending = max(0, int(pending_data_size or 0))
    except Exception:
        pending = 0
    return pending >= threshold


def _hub_route_subnet_sync_payload_type(path: Any, message: Any) -> str:
    if _hub_route_semantic_flow_for_path(path) != "subnet":
        return ""
    if not isinstance(message, str):
        return ""
    # Keep the hot path cheap: only parse likely member-link sync frames.
    if "yjs.update" not in message and "yjs.node_state" not in message:
        return ""
    try:
        payload = _json.loads(message)
    except Exception:
        return ""
    if not isinstance(payload, dict):
        return ""
    msg_type = str(payload.get("t") or "").strip()
    if msg_type in {"yjs.update", "yjs.node_state"}:
        return msg_type
    return ""


def _hub_route_should_drop_subnet_sync_frame(
    path: Any,
    payload_type: Any,
    *,
    pending_data_size: Any,
    guardrail_active: Any,
    frame_flush_pending_bytes: Any,
    payload_bytes: Any = 0,
) -> bool:
    if _hub_route_semantic_flow_for_path(path) != "subnet":
        return False
    # yjs.node_state is the semantic, bounded state path for member-owned data.
    # Raw yjs.update is best-effort sync and must not block member-link control
    # messages such as ping/pong, rpc, or core update commands.
    if str(payload_type or "").strip() != "yjs.update":
        return False
    if bool(guardrail_active):
        return True
    try:
        threshold = int(frame_flush_pending_bytes or 0)
    except Exception:
        threshold = 0
    if threshold <= 0:
        return False
    try:
        pending = max(0, int(pending_data_size or 0))
    except Exception:
        pending = 0
    try:
        payload = max(0, int(payload_bytes or 0))
    except Exception:
        payload = 0
    return pending >= threshold or payload >= threshold or (pending > 0 and pending + payload >= threshold)


def _is_local_http_base(url: str) -> bool:
    return is_loopback_http_url(url)


def _hub_route_prefers_supervisor_public_status(path_norm: str, method: str) -> bool:
    return method in ("GET", "HEAD") and path_norm in {
        "/api/supervisor/public/update-status",
        "/api/supervisor/public/memory-status",
    }


def _dev_without_supervisor() -> bool:
    env_type = str(os.getenv("ENV_TYPE") or "").strip().lower()
    if env_type != "dev":
        return False
    return str(os.getenv("ADAOS_SUPERVISOR_ENABLED") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }


def _read_json_file_silent(path: Path) -> dict[str, Any]:
    try:
        if not path.exists():
            return {}
        payload = _json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _hub_route_node_status_supervisor_runtime(ctx: AgentContext) -> dict[str, Any]:
    try:
        base_dir = ctx.paths.base_dir()
    except Exception:
        base_dir = Path(os.getenv("ADAOS_BASE_DIR") or Path.home() / ".adaos").expanduser()
    runtime_state = _read_json_file_silent((base_dir / "state" / "supervisor" / "runtime.json").resolve())
    update_attempt = _read_json_file_silent((base_dir / "state" / "supervisor" / "update_attempt.json").resolve())
    try:
        from adaos.services.core_update import read_status as _read_core_update_status

        update_status = _read_core_update_status() or {}
    except Exception:
        update_status = {}
    supervisor_enabled = str(os.getenv("ADAOS_SUPERVISOR_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    runtime_url = str(runtime_state.get("runtime_url") or "").strip()
    supervisor_url = str(os.getenv("ADAOS_SUPERVISOR_URL") or "").strip()
    if not supervisor_url and supervisor_enabled:
        supervisor_url = supervisor_base_from_env()
    return {
        "available": bool(supervisor_enabled or runtime_state),
        "enabled": bool(supervisor_enabled),
        "status": update_status if isinstance(update_status, dict) else {},
        "attempt": update_attempt if isinstance(update_attempt, dict) else {},
        "runtime": runtime_state if isinstance(runtime_state, dict) else {},
        "runtime_url": runtime_url.rstrip("/") or None,
        "supervisor_url": supervisor_url.rstrip("/") or None,
        "_served_by": "hub_route_inline_node_status",
    }


def _dev_api_serve_core_update_sync_disabled() -> bool:
    try:
        from adaos.services.core_update_policy import core_update_reactions_disabled_reason

        return core_update_reactions_disabled_reason() is not None
    except Exception:
        launch_mode = str(os.getenv("ADAOS_RUNTIME_LAUNCH_MODE") or "").strip().lower()
        if launch_mode != "api_serve":
            return False
        return str(os.getenv("ADAOS_API_SERVE_ALLOW_CORE_UPDATE") or "").strip().lower() not in {
            "1",
            "true",
            "yes",
            "on",
        }


def _supervisor_local_bases() -> list[str]:
    if _dev_without_supervisor():
        return []
    return [
        base
        for base in supervisor_base_candidates_from_env(
            require_signal=False,
            include_localhost=True,
            include_default_loopback=False,
        )
        if _is_local_http_base(base)
    ]


class HubRouteDiscoveryState:
    """Own local runtime route discovery cache and diagnostics."""

    def __init__(self) -> None:
        self.lock = threading.RLock()
        self.cache: dict[str, Any] = {"value": None, "expires_at": 0.0}
        self.diagnostics: dict[str, Any] = {
            "local_base_discovery_total": 0,
            "local_base_cache_hit_total": 0,
            "local_base_error_total": 0,
            "local_base_runtime_port_shortcut_total": 0,
            "local_base_last_source": "",
            "local_base_last_value": "",
            "local_base_last_latency_ms": None,
            "local_base_last_error": "",
            "local_base_last_error_at": 0.0,
            "local_base_last_discovered_at": 0.0,
        }

    def snapshot(self) -> dict[str, Any]:
        with self.lock:
            return {"cache": dict(self.cache), "diagnostics": dict(self.diagnostics)}


_DEFAULT_ROUTE_DISCOVERY_STATE = HubRouteDiscoveryState()


def _route_local_base_cache_ttl_s() -> float:
    raw = str(os.getenv("HUB_ROUTE_LOCAL_BASE_CACHE_TTL_S") or "").strip()
    if not raw:
        return 5.0
    try:
        value = float(raw)
    except Exception:
        return 5.0
    if value < 0.0:
        return 0.0
    if value > 60.0:
        return 60.0
    return value


def _runtime_port_local_http_base() -> str | None:
    return runtime_port_http_base_from_env()


def _runtime_port_probe_candidates() -> list[str]:
    return runtime_probe_http_bases(
        include_runtime_env=True,
        include_localhost=True,
        ports=(DEFAULT_CANDIDATE_RUNTIME_PORT, DEFAULT_RUNTIME_PORT),
    )


def _route_state_dir_from_ctx(ctx: Any | None) -> Path | None:
    try:
        paths = getattr(ctx, "paths", None)
        raw = getattr(paths, "state_dir", None)
        value = raw() if callable(raw) else raw
        if value:
            return Path(value).expanduser().resolve()
    except Exception:
        return None
    return None


def _route_state_dir_fallback() -> Path | None:
    try:
        return current_state_dir()
    except Exception:
        return None


def _active_runtime_state_local_http_bases(ctx: Any | None = None) -> list[str]:
    """Return supervisor-advertised local runtime bases without network probing.

    Route handlers run on the runtime event loop and must not synchronously probe
    localhost on the hot path. Supervisor state and node_runtime.json are useful
    fallbacks during early bootstrap and slot transitions, but they are persisted
    files and can briefly lag behind the process that is handling this message.
    """
    state_dir = _route_state_dir_from_ctx(ctx) or _route_state_dir_fallback()
    if state_dir is None:
        return []

    paths = [
        state_dir / "supervisor" / "runtime.json",
        state_dir / "node_runtime.json",
    ]
    bases: list[str] = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as fh:
                payload = _json.load(fh)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue

        for key in ("runtime_url", "hub_url"):
            value = str(payload.get(key) or "").strip().rstrip("/")
            if value and _is_local_http_base(value):
                bases.append(value)

        try:
            port_raw = payload.get("runtime_port")
            port = int(port_raw) if port_raw is not None else 0
            host = str(payload.get("runtime_host") or "").strip() or "127.0.0.1"
            if port > 0 and _is_local_http_base(http_base(host=host, port=port)):
                bases.append(http_base(port=port))
        except Exception:
            pass

    seen: set[str] = set()
    return [b for b in bases if (b not in seen and not seen.add(b))]


def _append_local_http_base(bases: list[str], value: str | None) -> None:
    base = str(value or "").strip().rstrip("/")
    if base and _is_local_http_base(base):
        bases.append(base)


def _hub_route_requested_timeout_s(headers: Any | None = None) -> float | None:
    if not isinstance(headers, dict):
        return None
    raw = ""
    for key, value in headers.items():
        if str(key or "").strip().lower() == "x-adaos-timeout-ms":
            raw = str(value or "").strip()
            break
    if not raw:
        return None
    try:
        parsed_ms = float(raw)
    except Exception:
        return None
    if parsed_ms <= 0.0:
        return None
    return max(1.0, min(parsed_ms / 1000.0, 600.0))


def _hub_route_local_http_timeout(path: str, headers: Any | None = None) -> tuple[float, float]:
    path_norm = "/" + str(path or "").split("?", 1)[0].lstrip("/")
    if path_norm in ("/api/node/status", "/api/ping", "/healthz"):
        return (0.5, 1.2)
    if re.match(r"^/api/skills/[^/]+/files/", path_norm):
        return (3.0, 300.0)
    if re.match(r"^/api/builder/projects/skill/[^/]+/artifacts/[^/]+/[^/]+/content$", path_norm):
        return (3.0, 300.0)
    if path_norm.startswith("/api/media/files/"):
        return (3.0, 300.0)
    if path_norm == "/api/tools/call":
        requested_timeout_s = _hub_route_requested_timeout_s(headers)
        if requested_timeout_s is not None:
            return (1.5, min(605.0, max(55.0, requested_timeout_s + 5.0)))
        # Root allows tools/call to take up to 60s. Keep the local hop below
        # that ceiling, but do not make member-link tools fail under normal
        # cross-node latency.
        return (1.5, 55.0)
    return (1.5, 2.5)


def _hub_route_tools_call_has_idempotency(body: bytes | None = None) -> bool:
    if not body:
        return False
    try:
        payload = _json.loads(bytes(body).decode("utf-8", errors="replace"))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    for key in ("idempotency_key", "request_id"):
        if str(payload.get(key) or "").strip():
            return True
    arguments = payload.get("arguments")
    if isinstance(arguments, dict):
        meta = arguments.get("_meta") if isinstance(arguments.get("_meta"), dict) else {}
        for key in ("idempotency_key", "request_id"):
            if str(arguments.get(key) or meta.get(key) or "").strip():
                return True
    return False


def _hub_route_should_retry_http_upstream_error(
    *, method: str, path: str, error_kind: str, body: bytes | None = None
) -> bool:
    path_norm = "/" + str(path or "").split("?", 1)[0].lstrip("/")
    method_norm = str(method or "").strip().upper()
    kind = str(error_kind or "").strip()
    if path_norm == "/api/tools/call":
        if kind in {"ConnectionError", "ConnectTimeout", "NewConnectionError"}:
            return True
        if kind == "ReadTimeout" and _hub_route_tools_call_has_idempotency(body):
            return True
        # Other failures may happen after the tool committed a side effect.
        # Execution stays at-most-once unless the caller supplied idempotency.
        return False
    if kind == "ReadTimeout" and (
        method_norm not in {"GET", "HEAD"}
    ):
        return False
    return True


def _hub_route_parse_resend_delays(raw: Any, *, max_delay_s: float = 10.0, max_count: int = 8) -> list[float]:
    text = str(raw or "").strip()
    if not text:
        return []
    delays: list[float] = []
    seen: set[float] = set()
    for item in text.split(","):
        item = item.strip()
        if not item:
            continue
        try:
            delay = float(item)
        except Exception:
            continue
        if delay <= 0:
            continue
        try:
            delay = min(float(delay), max(0.001, float(max_delay_s)))
        except Exception:
            delay = min(float(delay), 10.0)
        if delay in seen:
            continue
        seen.add(delay)
        delays.append(delay)
        if len(delays) >= max(1, int(max_count)):
            break
    return delays


def _hub_route_should_resend_http_resp(path: Any) -> bool:
    path_norm = "/" + str(path or "").split("?", 1)[0].lstrip("/")
    if path_norm in (
        "/api/node/status",
        "/api/ping",
        "/healthz",
        "/api/supervisor/public/update-status",
        "/api/node/ui/diagnostics",
        "/api/node/yjs/runtime",
    ):
        return True
    return bool(re.match(r"^/api/node/yjs/webspaces/[^/]+/materialization$", path_norm))


def _probe_runtime_http_base(sess: Any, *, base: str, timeout_s: float) -> bool:
    try:
        response = sess.get(
            str(base).rstrip("/") + "/api/ping",
            headers={"Accept": "application/json"},
            timeout=max(0.1, float(timeout_s)),
        )
        if int(response.status_code) != 200:
            return False
        payload = response.json()
        if not isinstance(payload, dict):
            return False
        if not bool(payload.get("ok")):
            return False
        return str(payload.get("service") or "").strip() == "adaos-runtime"
    except Exception:
        return False


def _observe_route_local_base_diag(
    *,
    state: HubRouteDiscoveryState | None = None,
    **details: Any,
) -> None:
    owner = state or _DEFAULT_ROUTE_DISCOVERY_STATE
    with owner.lock:
        owner.diagnostics.update(details)
        snapshot = dict(owner.diagnostics)
    try:
        observe_hub_root_route_runtime(**snapshot)
    except Exception:
        pass


def _note_route_local_base_shortcut(
    *,
    source: str,
    value: str | None,
    state: HubRouteDiscoveryState | None = None,
) -> None:
    owner = state or _DEFAULT_ROUTE_DISCOVERY_STATE
    now = time.time()
    with owner.lock:
        owner.diagnostics["local_base_runtime_port_shortcut_total"] = int(
            owner.diagnostics.get("local_base_runtime_port_shortcut_total") or 0
        ) + 1
        owner.diagnostics["local_base_last_source"] = str(source or "").strip() or "runtime_port_env"
        owner.diagnostics["local_base_last_value"] = str(value or "").strip()
        owner.diagnostics["local_base_last_discovered_at"] = now
        snapshot = dict(owner.diagnostics)
    try:
        observe_hub_root_route_runtime(**snapshot)
    except Exception:
        pass


def _discover_active_runtime_local_base(
    *,
    timeout_s: float = 0.6,
    allow_network_probe: bool = False,
    state: HubRouteDiscoveryState | None = None,
) -> str | None:
    try:
        import requests  # type: ignore
    except Exception:
        return None

    owner = state or _DEFAULT_ROUTE_DISCOVERY_STATE
    now = time.time()
    ttl_s = _route_local_base_cache_ttl_s()
    with owner.lock:
        cached_value = str(owner.cache.get("value") or "").strip() or None
        cached_expires_at = float(owner.cache.get("expires_at") or 0.0)
        if cached_value and cached_expires_at > now:
            owner.diagnostics["local_base_cache_hit_total"] = int(
                owner.diagnostics.get("local_base_cache_hit_total") or 0
            ) + 1
            owner.diagnostics["local_base_last_source"] = "cache"
            owner.diagnostics["local_base_last_value"] = cached_value
            snapshot = dict(owner.diagnostics)
            try:
                observe_hub_root_route_runtime(**snapshot)
            except Exception:
                pass
            return cached_value

        # Route handling runs on the event loop. When we have no cached local base,
        # skip synchronous network probing in the hot path and fall back to static
        # localhost candidates instead of blocking the loop on connect timeouts.
        if not allow_network_probe:
            owner.diagnostics["local_base_cache_miss_total"] = int(
                owner.diagnostics.get("local_base_cache_miss_total") or 0
            ) + 1
            owner.diagnostics["local_base_last_source"] = "cache_miss_no_probe"
            owner.diagnostics["local_base_last_value"] = ""
            owner.diagnostics["local_base_last_error"] = "network_probe_skipped"
            owner.diagnostics["local_base_last_error_at"] = now
            snapshot = dict(owner.diagnostics)
            try:
                observe_hub_root_route_runtime(**snapshot)
            except Exception:
                pass
            return None

    started = time.monotonic()
    result: str | None = None
    result_source = ""
    last_error = ""
    sess = requests.Session()
    try:
        try:
            sess.trust_env = False
        except Exception:
            pass

        for supervisor_base in _supervisor_local_bases():
            try:
                response = sess.get(
                    supervisor_base + "/api/supervisor/public/update-status",
                    headers={"Accept": "application/json"},
                    timeout=max(0.1, float(timeout_s)),
                )
                if int(response.status_code) != 200:
                    last_error = f"status:{response.status_code}"
                    continue
                payload = response.json()
                runtime = payload.get("runtime") if isinstance(payload, dict) else {}
                runtime_url = str((runtime or {}).get("runtime_url") or "").strip().rstrip("/")
                if runtime_url and _is_local_http_base(runtime_url):
                    result = runtime_url
                    result_source = "supervisor_public_status"
                    break
            except Exception as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                continue
        if not result:
            probe_timeout_s = max(0.1, min(float(timeout_s), 0.35))
            for runtime_base in _runtime_port_probe_candidates():
                if _probe_runtime_http_base(sess, base=runtime_base, timeout_s=probe_timeout_s):
                    result = runtime_base.rstrip("/")
                    result_source = "runtime_port_probe"
                    last_error = ""
                    break
    finally:
        try:
            sess.close()
        except Exception:
            pass

    latency_ms = round((time.monotonic() - started) * 1000.0, 3)
    with owner.lock:
        owner.diagnostics["local_base_discovery_total"] = int(
            owner.diagnostics.get("local_base_discovery_total") or 0
        ) + 1
        owner.diagnostics["local_base_last_latency_ms"] = latency_ms
        owner.diagnostics["local_base_last_source"] = (
            str(result_source or "").strip()
            if result
            else "supervisor_public_status_failed"
        )
        owner.diagnostics["local_base_last_value"] = str(result or "").strip()
        owner.diagnostics["local_base_last_discovered_at"] = time.time()
        if result:
            owner.cache["value"] = result
            owner.cache["expires_at"] = time.time() + max(0.0, ttl_s)
            owner.diagnostics["local_base_last_error"] = ""
        else:
            owner.diagnostics["local_base_error_total"] = int(
                owner.diagnostics.get("local_base_error_total") or 0
            ) + 1
            owner.diagnostics["local_base_last_error"] = last_error
            owner.diagnostics["local_base_last_error_at"] = time.time()
            owner.cache["value"] = None
            owner.cache["expires_at"] = 0.0
        snapshot = dict(owner.diagnostics)
    try:
        observe_hub_root_route_runtime(**snapshot)
    except Exception:
        pass
    return result


def _build_hub_route_http_bases(
    *,
    path_norm: str,
    method: str,
    cfg: Any | None,
    ctx: Any | None = None,
    state: HubRouteDiscoveryState | None = None,
) -> list[str]:
    bases: list[str] = []
    env_base = (
        os.getenv("ADAOS_SELF_BASE_URL")
        or os.getenv("ADAOS_BASE")
        or os.getenv("ADAOS_API_BASE")
        or ""
    ).strip()
    cfg_base = str(getattr(cfg, "hub_url", None) or "").strip()
    runtime_port = str(os.getenv("ADAOS_RUNTIME_PORT") or "").strip()
    runtime_port_base = _runtime_port_local_http_base()
    state_bases = _active_runtime_state_local_http_bases(ctx)

    if _hub_route_prefers_supervisor_public_status(path_norm, method):
        bases.extend(_supervisor_local_bases())

    if runtime_port_base:
        _note_route_local_base_shortcut(
            source="runtime_port_env",
            value=runtime_port_base,
            state=state,
        )
        bases.append(runtime_port_base)
    if runtime_port.isdigit():
        bases.append(f"http://127.0.0.1:{runtime_port}")
    bases.extend(state_bases)
    _append_local_http_base(bases, env_base)
    _append_local_http_base(bases, cfg_base)

    if not runtime_port_base and not state_bases:
        active_runtime_base = _discover_active_runtime_local_base(state=state)
        if active_runtime_base:
            _append_local_http_base(bases, active_runtime_base)

    # Keep runtime ports as fallback even for the browser-safe supervisor status path.
    bases.extend(
        local_http_bases(
            (DEFAULT_CANDIDATE_RUNTIME_PORT, DEFAULT_RUNTIME_PORT),
            hosts=("127.0.0.1",),
        )
    )

    seen_bases: set[str] = set()
    return [b for b in bases if (b not in seen_bases and not seen_bases.add(b))]


def _http_base_to_ws_base(base: str) -> str:
    value = str(base or "").strip().rstrip("/")
    if value.startswith("https://"):
        return "wss://" + value[len("https://"):]
    if value.startswith("http://"):
        return "ws://" + value[len("http://"):]
    return value


def _build_hub_route_ws_bases(
    *,
    cfg: Any | None,
    path: str | None = None,
    ctx: Any | None = None,
    state: HubRouteDiscoveryState | None = None,
) -> list[str]:
    bases: list[str] = []
    role = str(getattr(cfg, "role", None) or "").strip().lower() or None
    bases.extend(realtime_sidecar_route_tunnel_ws_bases(path=path, role=role))
    env_base = str(os.getenv("ADAOS_SELF_BASE_URL") or "").strip()
    cfg_base = str(getattr(cfg, "hub_url", None) or "").strip()
    runtime_port_base = _runtime_port_local_http_base()
    state_bases = _active_runtime_state_local_http_bases(ctx)

    if runtime_port_base:
        _note_route_local_base_shortcut(
            source="runtime_port_env",
            value=runtime_port_base,
            state=state,
        )
        bases.append(_http_base_to_ws_base(runtime_port_base))
    for state_base in state_bases:
        bases.append(_http_base_to_ws_base(state_base))
    if env_base and _is_local_http_base(env_base):
        bases.append(_http_base_to_ws_base(env_base))
    if cfg_base and _is_local_http_base(cfg_base):
        bases.append(_http_base_to_ws_base(cfg_base))

    if not runtime_port_base and not state_bases:
        active_runtime_base = _discover_active_runtime_local_base(state=state)
        if active_runtime_base:
            bases.append(_http_base_to_ws_base(active_runtime_base))

    bases.extend(runtime_fallback_ws_bases(include_localhost=False, include_dev=False))

    seen_bases: set[str] = set()
    return [b for b in bases if (b not in seen_bases and not seen_bases.add(b))]


def _hub_route_force_close_no_upstream_s() -> float:
    raw = os.getenv("HUB_ROUTE_FORCE_CLOSE_NO_UPSTREAM_S")
    if raw is None:
        return 1.5
    try:
        value = float(str(raw).strip() or "0")
    except Exception:
        value = 0.0
    if value <= 0.0:
        return 0.0
    if value < 0.25:
        value = 0.25
    if value > 30.0:
        value = 30.0
    return value


class HubRouteProxyPolicy:
    """Typed route policy with an instance-owned discovery cache."""

    def __init__(self) -> None:
        self.discovery = HubRouteDiscoveryState()

    max_chunk_raw_bytes = staticmethod(_hub_route_max_chunk_raw_bytes)
    normalize_resend_chunk_indexes = staticmethod(_hub_route_normalize_resend_chunk_indexes)
    path_token = staticmethod(_hub_route_path_token)
    semantic_flow_for_path = staticmethod(_hub_route_semantic_flow_for_path)
    should_shed_sync_frame = staticmethod(_hub_route_should_shed_sync_frame)
    sync_frame_force_flush_enabled = staticmethod(_hub_route_sync_frame_force_flush_enabled)
    should_force_flush_reply = staticmethod(_hub_route_should_force_flush_reply)
    subnet_sync_payload_type = staticmethod(_hub_route_subnet_sync_payload_type)
    should_drop_subnet_sync_frame = staticmethod(_hub_route_should_drop_subnet_sync_frame)
    is_local_http_base = staticmethod(_is_local_http_base)
    prefers_supervisor_public_status = staticmethod(_hub_route_prefers_supervisor_public_status)
    local_http_timeout = staticmethod(_hub_route_local_http_timeout)
    tools_call_has_idempotency = staticmethod(_hub_route_tools_call_has_idempotency)
    should_retry_http_upstream_error = staticmethod(_hub_route_should_retry_http_upstream_error)
    parse_resend_delays = staticmethod(_hub_route_parse_resend_delays)
    should_resend_http_resp = staticmethod(_hub_route_should_resend_http_resp)
    http_base_to_ws_base = staticmethod(_http_base_to_ws_base)
    force_close_no_upstream_s = staticmethod(_hub_route_force_close_no_upstream_s)

    def observe_local_base(self, **details: Any) -> None:
        _observe_route_local_base_diag(state=self.discovery, **details)

    def note_local_base_shortcut(self, *, source: str, value: str | None) -> None:
        _note_route_local_base_shortcut(source=source, value=value, state=self.discovery)

    def discover_active_runtime_local_base(
        self,
        *,
        timeout_s: float = 0.6,
        allow_network_probe: bool = False,
    ) -> str | None:
        return _discover_active_runtime_local_base(
            timeout_s=timeout_s,
            allow_network_probe=allow_network_probe,
            state=self.discovery,
        )

    def build_http_bases(
        self,
        *,
        path_norm: str,
        method: str,
        cfg: Any | None,
        ctx: Any | None = None,
    ) -> list[str]:
        return _build_hub_route_http_bases(
            path_norm=path_norm,
            method=method,
            cfg=cfg,
            ctx=ctx,
            state=self.discovery,
        )

    def build_ws_bases(
        self,
        *,
        cfg: Any | None,
        path: str | None = None,
        ctx: Any | None = None,
    ) -> list[str]:
        return _build_hub_route_ws_bases(
            cfg=cfg,
            path=path,
            ctx=ctx,
            state=self.discovery,
        )
