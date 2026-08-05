from __future__ import annotations

import math
import os
from typing import Any

from adaos.services.env_policy import truthy


def _bounded_interval_seconds(raw: Any, *, default: float, minimum: float) -> float:
    try:
        interval_s = float(raw)
    except Exception:
        interval_s = float(default)
    if not math.isfinite(interval_s):
        interval_s = float(default)
    if interval_s < float(minimum):
        interval_s = float(minimum)
    return float(interval_s)


def _hub_root_bridge_watchdog_interval_s() -> float:
    return _bounded_interval_seconds(
        os.getenv("HUB_ROOT_BRIDGE_WATCHDOG_INTERVAL_S", "2"),
        default=2.0,
        minimum=0.5,
    )


def _should_forward_node_status_to_members(payload: object) -> bool:
    if not isinstance(payload, dict):
        return True
    meta = payload.get("_meta")
    if not isinstance(meta, dict):
        return True
    return not bool(meta.get("subnet_origin_node_id"))


def _webio_control_target_node_id(payload: object) -> str:
    if not isinstance(payload, dict):
        return ""
    meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
    return str(
        payload.get("target_node_id")
        or payload.get("node_target_id")
        or payload.get("node_id")
        or meta.get("target_node_id")
        or meta.get("node_target_id")
        or meta.get("node_id")
        or ""
    ).strip()


def _should_forward_webio_control_to_members(payload: object) -> bool:
    return bool(_webio_control_target_node_id(payload))


def _node_status_dedupe_window_s() -> float:
    raw = os.getenv("ADAOS_NODE_STATUS_DEDUPE_WINDOW_S", "30") or "30"
    return _bounded_interval_seconds(raw, default=30.0, minimum=1.0)


def _node_status_emit_fingerprint(payload: object) -> tuple[Any, ...]:
    if not isinstance(payload, dict):
        return ("invalid",)
    node_names = payload.get("node_names")
    if isinstance(node_names, list):
        normalized_node_names = tuple(str(item or "").strip() for item in node_names if str(item or "").strip())
    else:
        normalized_node_names = ()
    connected_to_subnet = payload.get("connected_to_subnet")
    if connected_to_subnet is None:
        connected_to_subnet = payload.get("connected_to_hub")
    connected_to_hub = payload.get("connected_to_hub")
    if connected_to_hub is None:
        connected_to_hub = connected_to_subnet
    return (
        str(payload.get("node_id") or "").strip(),
        str(payload.get("subnet_id") or "").strip(),
        str(payload.get("role") or "").strip(),
        normalized_node_names,
        str(payload.get("primary_node_name") or "").strip(),
        bool(payload.get("ready")),
        str(payload.get("node_state") or "").strip(),
        bool(payload.get("draining")),
        str(payload.get("route_mode") or "").strip(),
        connected_to_subnet,
        connected_to_hub,
        str(payload.get("trigger") or "").strip(),
    )


def _should_emit_node_status(
    *,
    payload: object,
    now: float,
    last_emitted_at: float,
    last_fingerprint: tuple[Any, ...] | None,
    dedupe_window_s: float | None = None,
) -> tuple[bool, tuple[Any, ...]]:
    fingerprint = _node_status_emit_fingerprint(payload)
    window_s = _node_status_dedupe_window_s() if dedupe_window_s is None else float(dedupe_window_s)
    if (
        last_fingerprint is not None
        and fingerprint == last_fingerprint
        and (now - float(last_emitted_at or 0.0)) < window_s
    ):
        return False, fingerprint
    return True, fingerprint


def _env_truthy(value: Any, *, default: bool = False) -> bool:
    if value is None:
        return bool(default)
    return truthy(value, default=True)


def _loop_hang_watchdog_enabled_from_env() -> bool:
    if not _env_truthy(os.getenv("ADAOS_LOOP_HANG_WATCHDOG"), default=False):
        return False
    # The watchdog samples another thread's frame chain via sys._current_frames().
    # Frame references can hold y_py YDoc/YMap locals and drop them on the
    # watchdog thread, which trips PyO3's thread-affinity guard on Windows.
    return _env_truthy(os.getenv("ADAOS_LOOP_HANG_WATCHDOG_UNSAFE"), default=False)


def _hub_channel_console_trace_enabled() -> bool:
    return _env_truthy(os.getenv("HUB_CHANNEL_CONSOLE_TRACE"), default=False)


def _hub_channel_console_allow_rl(key: str, msg: str) -> bool:
    if _hub_channel_console_trace_enabled():
        return True
    text = str(msg or "")
    detail_prefixes = (
        "nats.ws_diag",
        "nats.ws_eof",
        "nats.env",
        "nats.transport",
        "nats.ws_hb",
        "nats.ws_tag",
        "nats.keepalive",
        "nats.connect_try",
        "nats.try",
        "root.snap",
        "root.snap_fail",
        "nats.sidecar_route",
        "nats.sidecar_unready",
        "hub-route.probe_resend",
        "hub-route.probe_resend_cfg",
    )
    if any(str(key or "").startswith(prefix) for prefix in detail_prefixes):
        return False
    if "[hub-io] nats ws diag:" in text:
        return False
    return True
