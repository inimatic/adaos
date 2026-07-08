from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import psutil  # type: ignore[import-not-found]
except Exception:  # pragma: no cover - optional runtime dependency for diagnostics
    psutil = None  # type: ignore[assignment]


ROOT = Path(__file__).resolve().parents[1]

if str(ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(ROOT / "tools"))

try:
    from log_window_summary import Summary, analyze_line, parse_offsets, read_new_lines, resolve_logs
except Exception:  # pragma: no cover - benchmark should still run without log helper
    Summary = None  # type: ignore[assignment]
    analyze_line = None  # type: ignore[assignment]
    parse_offsets = None  # type: ignore[assignment]
    read_new_lines = None  # type: ignore[assignment]
    resolve_logs = None  # type: ignore[assignment]


DEFAULT_ACTION_TIMEOUT = 60.0


def _now_epoch() -> float:
    return time.time()


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _jsonable(value: Any, *, max_text: int = 4000) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v, max_text=max_text) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(item, max_text=max_text) for item in value[:200]]
    if isinstance(value, str):
        return value if len(value) <= max_text else value[:max_text] + "...<truncated>"
    return value


def _pick_path(payload: dict[str, Any], path: str) -> Any:
    current: Any = payload
    for token in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(token)
    return current


def _float_or_none(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if parsed != parsed:
        return None
    return parsed


def _summarize_numbers(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "p50": None, "p95": None, "max": None, "avg": None}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, int(round((len(ordered) - 1) * 0.95)))
    return {
        "count": len(ordered),
        "min": round(ordered[0], 3),
        "p50": round(statistics.median(ordered), 3),
        "p95": round(ordered[p95_index], 3),
        "max": round(ordered[-1], 3),
        "avg": round(sum(ordered) / len(ordered), 3),
    }


def _base_url_port(base_url: str) -> int | None:
    try:
        parsed = urllib.parse.urlparse(str(base_url or ""))
    except Exception:
        return None
    if parsed.port:
        return int(parsed.port)
    scheme = str(parsed.scheme or "").lower()
    if scheme == "https":
        return 443
    if scheme == "http":
        return 80
    return None


def _find_listening_pid(port: int | None) -> int | None:
    if not port or psutil is None:
        return None
    try:
        connections = psutil.net_connections(kind="tcp")
    except Exception:
        return None
    for connection in connections:
        try:
            laddr = connection.laddr
            if int(getattr(laddr, "port", 0) or 0) != int(port):
                continue
            status = str(getattr(connection, "status", "") or "").upper()
            if status and status != "LISTEN":
                continue
            pid = getattr(connection, "pid", None)
            if pid:
                return int(pid)
        except Exception:
            continue
    return None


def _resolve_memory_pid(args: argparse.Namespace) -> tuple[int | None, dict[str, Any]]:
    if bool(getattr(args, "no_memory", False)):
        return None, {"enabled": False, "reason": "disabled_by_cli"}
    explicit_pid = int(getattr(args, "memory_pid", 0) or 0)
    if explicit_pid > 0:
        return explicit_pid, {"enabled": True, "source": "cli_pid", "pid": explicit_pid}
    if psutil is None:
        return None, {"enabled": False, "reason": "psutil_unavailable"}
    port = int(getattr(args, "memory_port", 0) or 0) or _base_url_port(str(getattr(args, "base_url", "") or ""))
    pid = _find_listening_pid(port)
    return pid, {
        "enabled": bool(pid),
        "source": "listening_port" if pid else "listening_port_not_found",
        "port": port,
        "pid": pid,
    }


def _process_memory_snapshot(pid: int | None) -> dict[str, Any]:
    if not pid:
        return {"available": False, "reason": "missing_pid"}
    if psutil is None:
        return {"available": False, "pid": pid, "reason": "psutil_unavailable"}
    try:
        process = psutil.Process(int(pid))
        info = process.memory_info()._asdict()
        try:
            full_info = process.memory_full_info()._asdict()
        except Exception:
            full_info = {}
        rss = int(info.get("rss") or info.get("wset") or 0)
        vms = int(info.get("vms") or info.get("pagefile") or 0)
        private_bytes = int(full_info.get("private") or full_info.get("uss") or info.get("private") or 0)
        uss = int(full_info.get("uss") or 0)
        snapshot: dict[str, Any] = {
            "available": True,
            "pid": int(pid),
            "name": process.name(),
            "rss_bytes": rss,
            "rss_mb": round(rss / 1048576.0, 3),
            "vms_bytes": vms,
            "vms_mb": round(vms / 1048576.0, 3),
            "private_bytes": private_bytes,
            "private_mb": round(private_bytes / 1048576.0, 3),
            "uss_bytes": uss,
            "uss_mb": round(uss / 1048576.0, 3) if uss else 0.0,
            "num_threads": process.num_threads(),
        }
        num_handles = getattr(process, "num_handles", None)
        if callable(num_handles):
            try:
                snapshot["num_handles"] = int(num_handles())
            except Exception:
                pass
        return snapshot
    except Exception as exc:
        return {"available": False, "pid": pid, "reason": f"{type(exc).__name__}: {exc}"}


def _process_memory_delta(before: dict[str, Any] | None, after: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(before, dict) or not isinstance(after, dict):
        return {"available": False, "reason": "missing_snapshot"}
    if not before.get("available") or not after.get("available"):
        return {"available": False, "reason": "snapshot_unavailable"}
    delta: dict[str, Any] = {"available": True}
    for field in ("rss_bytes", "vms_bytes", "private_bytes", "uss_bytes", "num_threads", "num_handles"):
        if field not in before or field not in after:
            continue
        try:
            delta[field] = int(after.get(field) or 0) - int(before.get(field) or 0)
        except Exception:
            continue
    for field in ("rss_bytes", "vms_bytes", "private_bytes", "uss_bytes"):
        if field in delta:
            delta[field.removesuffix("_bytes") + "_mb"] = round(float(delta[field]) / 1048576.0, 3)
    return delta


class ApiClient:
    def __init__(self, base_url: str, token: str, timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, body: dict[str, Any] | None = None, timeout: float | None = None) -> dict[str, Any]:
        url = self.base_url + path
        headers = {
            "X-AdaOS-Token": self.token,
            "Accept": "application/json",
        }
        data: bytes | None = None
        if body is not None:
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.timeout) as response:
                raw = response.read()
                parsed = json.loads(raw.decode("utf-8")) if raw else {}
                if isinstance(parsed, dict):
                    return parsed
                return {"ok": True, "value": parsed}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                detail = json.loads(raw)
            except json.JSONDecodeError:
                detail = raw
            return {"ok": False, "http_status": exc.code, "error": "http_error", "detail": detail}
        except Exception as exc:
            return {"ok": False, "error": type(exc).__name__, "detail": str(exc)}

    def get(self, path: str, timeout: float | None = None) -> dict[str, Any]:
        return self._request("GET", path, timeout=timeout)

    def post(self, path: str, body: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        return self._request("POST", path, body=body, timeout=timeout)

    def call_tool(self, tool: str, arguments: dict[str, Any], timeout: float | None = None) -> dict[str, Any]:
        return self.post(
            "/api/tools/call",
            {
                "tool": tool,
                "arguments": arguments,
                "timeout": timeout or DEFAULT_ACTION_TIMEOUT,
            },
            timeout=max(timeout or DEFAULT_ACTION_TIMEOUT, 5.0) + 5.0,
        )


def _extract_tool_result(payload: dict[str, Any]) -> dict[str, Any]:
    result = payload.get("result")
    if isinstance(result, dict):
        return result
    return {}


def _tool_summary(tool: str, payload: dict[str, Any]) -> dict[str, Any]:
    result = _extract_tool_result(payload)
    summary: dict[str, Any] = {
        "ok": bool(payload.get("ok")) and result.get("ok") is not False,
        "trace_id": payload.get("trace_id"),
    }
    if result:
        for key in (
            "ok",
            "error",
            "status",
            "session_id",
            "scenario_id",
            "revision",
            "count",
            "path",
            "size_bytes",
            "exists",
        ):
            if key in result:
                summary[key] = result.get(key)
        if tool.endswith("prompt_list_project_objects") and isinstance(result, list):
            summary["count"] = len(result)
        timings = result.get("timings_ms")
        if isinstance(timings, dict):
            summary["timings_ms"] = dict(timings)
        chat_emit = result.get("chat_emit")
        if isinstance(chat_emit, dict):
            summary["chat_emit"] = {
                key: chat_emit.get(key)
                for key in ("scheduled", "mode", "delay_s")
                if key in chat_emit
            }
        dev_refresh = result.get("dev_runtime_refresh")
        if isinstance(dev_refresh, dict):
            summary["dev_runtime_refresh"] = {
                key: dev_refresh.get(key)
                for key in (
                    "ok",
                    "scheduled",
                    "mode",
                    "webspace_id",
                    "scenario_id",
                    "revision",
                    "source_fingerprint",
                    "user_id",
                    "roles",
                    "delay_s",
                    "skipped",
                    "error",
                    "detail",
                )
                if key in dev_refresh
            }
    if not summary["ok"]:
        summary["raw_error"] = _jsonable(payload, max_text=1000)
    return summary


def _rebuild_marker(payload: dict[str, Any]) -> float | None:
    rebuild = payload.get("rebuild") if isinstance(payload.get("rebuild"), dict) else payload
    if not isinstance(rebuild, dict):
        return None
    values = [
        _float_or_none(rebuild.get("updated_at")),
        _float_or_none(rebuild.get("finished_at")),
        _float_or_none(rebuild.get("started_at")),
        _float_or_none(rebuild.get("requested_at")),
    ]
    values = [item for item in values if item is not None]
    return max(values) if values else None


def _extract_rebuild(payload: dict[str, Any]) -> dict[str, Any]:
    rebuild = payload.get("rebuild")
    return dict(rebuild) if isinstance(rebuild, dict) else {}


def _extract_materialization(payload: dict[str, Any]) -> dict[str, Any]:
    materialization = payload.get("materialization")
    return dict(materialization) if isinstance(materialization, dict) else {}


def _compact_rebuild(rebuild: dict[str, Any]) -> dict[str, Any]:
    materialization = rebuild.get("materialization") if isinstance(rebuild.get("materialization"), dict) else {}
    apply_summary = rebuild.get("apply_summary") if isinstance(rebuild.get("apply_summary"), dict) else {}
    resolver = rebuild.get("resolver") if isinstance(rebuild.get("resolver"), dict) else {}
    return {
        "status": rebuild.get("status"),
        "pending": rebuild.get("pending"),
        "background": rebuild.get("background"),
        "action": rebuild.get("action"),
        "source_of_truth": rebuild.get("source_of_truth"),
        "scenario_id": rebuild.get("scenario_id"),
        "scenario_resolution": rebuild.get("scenario_resolution"),
        "switch_mode": rebuild.get("switch_mode"),
        "updated_at": rebuild.get("updated_at"),
        "started_at": rebuild.get("started_at"),
        "finished_at": rebuild.get("finished_at"),
        "recovery_fingerprint": rebuild.get("recovery_fingerprint"),
        "recovery_duplicate_total": rebuild.get("recovery_duplicate_total"),
        "recovery_last_command_id": rebuild.get("recovery_last_command_id"),
        "live_room_update_requested": rebuild.get("live_room_update_requested"),
        "live_room_publish": rebuild.get("live_room_publish"),
        "live_room_refresh": dict(rebuild.get("live_room_refresh") or {})
        if isinstance(rebuild.get("live_room_refresh"), dict)
        else rebuild.get("live_room_refresh"),
        "timings_ms": dict(rebuild.get("timings_ms") or {}) if isinstance(rebuild.get("timings_ms"), dict) else {},
        "switch_timings_ms": dict(rebuild.get("switch_timings_ms") or {})
        if isinstance(rebuild.get("switch_timings_ms"), dict)
        else {},
        "semantic_rebuild_timings_ms": dict(rebuild.get("semantic_rebuild_timings_ms") or {})
        if isinstance(rebuild.get("semantic_rebuild_timings_ms"), dict)
        else {},
        "ydoc_timings_ms": dict(rebuild.get("ydoc_timings_ms") or {}) if isinstance(rebuild.get("ydoc_timings_ms"), dict) else {},
        "phase_timings_ms": dict(rebuild.get("phase_timings_ms") or {}) if isinstance(rebuild.get("phase_timings_ms"), dict) else {},
        "resolver": {
            "source": resolver.get("source"),
            "cache_hit": resolver.get("cache_hit"),
            "legacy_fallback": resolver.get("legacy_fallback"),
            "input_fingerprint": resolver.get("input_fingerprint"),
        },
        "apply_summary": {
            "changed_branches": apply_summary.get("changed_branches"),
            "unchanged_branches": apply_summary.get("unchanged_branches"),
            "replaced_branches": apply_summary.get("replaced_branches"),
            "diff_applied_branches": apply_summary.get("diff_applied_branches"),
            "patch_applied_branches": apply_summary.get("patch_applied_branches"),
            "patch_actual_verified_branches": apply_summary.get("patch_actual_verified_branches"),
            "patch_fingerprint_mismatch_branches": apply_summary.get("patch_fingerprint_mismatch_branches"),
            "patch_fallback_branches": apply_summary.get("patch_fallback_branches"),
            "patch_fallback_reasons": dict(apply_summary.get("patch_fallback_reasons") or {})
            if isinstance(apply_summary.get("patch_fallback_reasons"), dict)
            else {},
            "fingerprint_unchanged_branches": apply_summary.get("fingerprint_unchanged_branches"),
            "failed_branches": apply_summary.get("failed_branches"),
        },
        "materialization": {
            "ready": materialization.get("ready"),
            "readiness_state": materialization.get("readiness_state"),
            "snapshot_source": materialization.get("snapshot_source"),
            "stale": materialization.get("stale"),
            "current_scenario": materialization.get("current_scenario"),
            "observed_at": materialization.get("observed_at"),
        },
    }


def _compact_materialization(materialization: dict[str, Any]) -> dict[str, Any]:
    compatibility = materialization.get("compatibility_caches") if isinstance(materialization.get("compatibility_caches"), dict) else {}
    return {
        "ready": materialization.get("ready"),
        "readiness_state": materialization.get("readiness_state"),
        "stale": materialization.get("stale"),
        "stale_reason": materialization.get("stale_reason"),
        "snapshot_source": materialization.get("snapshot_source"),
        "observed_at": materialization.get("observed_at"),
        "current_scenario": materialization.get("current_scenario"),
        "missing_branches": list(materialization.get("missing_branches") or [])[:20],
        "required_branches": list(materialization.get("required_branches") or [])[:20],
        "catalog_counts": dict(materialization.get("catalog_counts") or {})
        if isinstance(materialization.get("catalog_counts"), dict)
        else {},
        "installed_counts": dict(materialization.get("installed_counts") or {})
        if isinstance(materialization.get("installed_counts"), dict)
        else {},
        "compatibility_caches": {
            "present_count": compatibility.get("present_count"),
            "required_count": compatibility.get("required_count"),
            "client_fallback_readable": compatibility.get("client_fallback_readable"),
            "runtime_removal_ready": compatibility.get("runtime_removal_ready"),
            "legacy_fallback_active": compatibility.get("legacy_fallback_active"),
            "missing_branches": list(compatibility.get("missing_branches") or [])[:20],
        },
    }


def _materialized_view_signature(payload: dict[str, Any]) -> dict[str, Any]:
    snapshot = payload.get("snapshot") if isinstance(payload.get("snapshot"), dict) else {}
    ui = snapshot.get("ui") if isinstance(snapshot.get("ui"), dict) else {}
    application = ui.get("application") if isinstance(ui.get("application"), dict) else {}
    desktop = application.get("desktop") if isinstance(application.get("desktop"), dict) else {}
    page_schema = desktop.get("pageSchema") if isinstance(desktop.get("pageSchema"), dict) else {}
    if not page_schema:
        data = snapshot.get("data") if isinstance(snapshot.get("data"), dict) else {}
        data_desktop = data.get("desktop") if isinstance(data.get("desktop"), dict) else {}
        page_schema = data_desktop.get("pageSchema") if isinstance(data_desktop.get("pageSchema"), dict) else {}
    widgets = page_schema.get("widgets") if isinstance(page_schema.get("widgets"), list) else []
    cards = next(
        (
            item
            for item in widgets
            if isinstance(item, dict) and str(item.get("id") or "") in {"prototype-cards", "items_cards"}
        ),
        {},
    )
    cards_inputs = cards.get("inputs") if isinstance(cards.get("inputs"), dict) else {}
    data_source = cards.get("dataSource") if isinstance(cards.get("dataSource"), dict) else {}
    rows = data_source.get("value") if isinstance(data_source.get("value"), list) else []
    first_row = rows[0] if rows and isinstance(rows[0], dict) else {}
    preview_key = str(cards_inputs.get("previewKey") or "").strip()
    return {
        "state": payload.get("state"),
        "stale": payload.get("stale"),
        "last_good_snapshot_at": payload.get("last_good_snapshot_at"),
        "page_id": page_schema.get("id"),
        "page_title": page_schema.get("title"),
        "widgets": [
            str(item.get("id") or "")
            for item in widgets
            if isinstance(item, dict) and str(item.get("id") or "").strip()
        ],
        "cards": {
            "previewKey": preview_key or None,
            "subtitleKey": cards_inputs.get("subtitleKey"),
            "titleKey": cards_inputs.get("titleKey"),
            "firstPreviewValue": first_row.get(preview_key) if preview_key else None,
            "firstRowKeys": sorted(str(key) for key in first_row.keys())[:20],
        },
    }


def _wait_for_rebuild(
    client: ApiClient,
    *,
    webspace: str,
    since_epoch: float,
    timeout_s: float,
    poll_interval_s: float,
) -> dict[str, Any]:
    deadline = time.perf_counter() + max(0.1, timeout_s)
    started = time.perf_counter()
    started_epoch = _now_epoch()
    polls = 0
    poll_get_durations_ms: list[float] = []
    last_payload: dict[str, Any] = {}
    last_rebuild: dict[str, Any] = {}
    first_new_seen_ms: float | None = None

    def _delta_after_action_ms(value: float | None) -> float | None:
        if value is None:
            return None
        return round((value - since_epoch) * 1000.0, 3)

    def _observe_after_finish_ms(wait_ms: float, finished_at: float | None) -> float | None:
        if finished_at is None:
            return None
        observed_epoch = started_epoch + (wait_ms / 1000.0)
        return round((observed_epoch - finished_at) * 1000.0, 3)

    while True:
        polls += 1
        poll_started = time.perf_counter()
        payload = client.get(f"/api/node/yjs/webspaces/{urllib.parse.quote(webspace)}/rebuild?include_runtime=0", timeout=10.0)
        poll_get_durations_ms.append((time.perf_counter() - poll_started) * 1000.0)
        last_payload = payload
        rebuild = _extract_rebuild(payload)
        if rebuild:
            last_rebuild = rebuild
        marker = _rebuild_marker(rebuild)
        is_new = marker is not None and marker >= since_epoch - 0.05
        started_at = _float_or_none(rebuild.get("started_at"))
        finished_at = _float_or_none(rebuild.get("finished_at"))
        new_started = started_at is not None and started_at >= since_epoch - 0.05
        new_finished = finished_at is not None and finished_at >= since_epoch - 0.05
        if is_new and first_new_seen_ms is None:
            first_new_seen_ms = (time.perf_counter() - started) * 1000.0
        terminal = (
            str(rebuild.get("status") or "").strip().lower() in {"ready", "failed", "cancelled"}
            and not bool(rebuild.get("pending"))
        )
        if is_new and terminal:
            wait_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return {
                "timeout": False,
                "polls": polls,
                "wait_ms": wait_ms,
                "first_new_seen_ms": round(first_new_seen_ms, 3) if first_new_seen_ms is not None else None,
                "first_new_seen_after_action_ms": round(
                    (started_epoch + ((first_new_seen_ms or 0.0) / 1000.0) - since_epoch) * 1000.0,
                    3,
                )
                if first_new_seen_ms is not None
                else None,
                "new_started": bool(new_started),
                "new_finished": bool(new_finished),
                "rebuild_started_after_action_ms": _delta_after_action_ms(started_at),
                "rebuild_finished_after_action_ms": _delta_after_action_ms(finished_at),
                "observe_after_finished_ms": _observe_after_finish_ms(wait_ms, finished_at),
                "poll_get_ms": _summarize_numbers(poll_get_durations_ms),
                "rebuild": _compact_rebuild(rebuild),
            }
        if time.perf_counter() >= deadline:
            started_at = _float_or_none(last_rebuild.get("started_at"))
            finished_at = _float_or_none(last_rebuild.get("finished_at"))
            wait_ms = round((time.perf_counter() - started) * 1000.0, 3)
            return {
                "timeout": True,
                "polls": polls,
                "wait_ms": wait_ms,
                "first_new_seen_ms": round(first_new_seen_ms, 3) if first_new_seen_ms is not None else None,
                "first_new_seen_after_action_ms": round(
                    (started_epoch + ((first_new_seen_ms or 0.0) / 1000.0) - since_epoch) * 1000.0,
                    3,
                )
                if first_new_seen_ms is not None
                else None,
                "new_started": bool(started_at is not None and started_at >= since_epoch - 0.05),
                "new_finished": bool(finished_at is not None and finished_at >= since_epoch - 0.05),
                "rebuild_started_after_action_ms": _delta_after_action_ms(started_at),
                "rebuild_finished_after_action_ms": _delta_after_action_ms(finished_at),
                "observe_after_finished_ms": _observe_after_finish_ms(wait_ms, finished_at),
                "poll_get_ms": _summarize_numbers(poll_get_durations_ms),
                "rebuild": _compact_rebuild(last_rebuild),
                "last_payload_ok": last_payload.get("ok"),
            }
        time.sleep(max(0.05, poll_interval_s))


def _timed_call(name: str, func: Any) -> dict[str, Any]:
    started_epoch = _now_epoch()
    started = time.perf_counter()
    payload = func()
    duration_ms = (time.perf_counter() - started) * 1000.0
    return {
        "name": name,
        "started_at": started_epoch,
        "duration_ms": round(duration_ms, 3),
        "payload": payload,
    }


def _timed_call_with_memory(name: str, func: Any, memory_pid: int | None) -> dict[str, Any]:
    before_memory = _process_memory_snapshot(memory_pid)
    action = _timed_call(name, func)
    after_memory = _process_memory_snapshot(memory_pid)
    action["process_memory"] = {
        "before": before_memory,
        "after_tool_call": after_memory,
        "tool_delta": _process_memory_delta(before_memory, after_memory),
        "operation_delta": _process_memory_delta(before_memory, after_memory),
    }
    return action


def _set_action_memory_stage(action: dict[str, Any], stage: str, memory_pid: int | None) -> None:
    process_memory = action.setdefault("process_memory", {})
    if not isinstance(process_memory, dict):
        process_memory = {}
        action["process_memory"] = process_memory
    before = process_memory.get("before") if isinstance(process_memory.get("before"), dict) else None
    snapshot = _process_memory_snapshot(memory_pid)
    process_memory[stage] = snapshot
    if before is not None:
        process_memory[f"delta_to_{stage}"] = _process_memory_delta(before, snapshot)
        process_memory["operation_delta"] = _process_memory_delta(before, snapshot)


def _collect_logs(label: str, offsets: dict[Path, int] | None) -> dict[str, Any]:
    if not offsets or Summary is None or analyze_line is None or read_new_lines is None:
        return {"available": False}
    summary = Summary(label=label, started_at_wall=_now_iso(), duration_s=0.0)
    for path, offset in offsets.items():
        try:
            rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        except Exception:
            rel = str(path)
        for line_no, line in read_new_lines(path, offset):
            analyze_line(summary, rel, line_no, line)
    payload = summary.to_json()
    payload["available"] = True
    return payload


def _build_summary(actions: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[float]] = defaultdict(list)
    recovery_update_waits: list[float] = []
    rebuild_ready_waits: list[float] = []
    rebuild_started_after_action: list[float] = []
    rebuild_finished_after_action: list[float] = []
    rebuild_observe_after_finished: list[float] = []
    rebuild_total: list[float] = []
    semantic_total: list[float] = []
    sync_listing: list[float] = []
    set_current_timings: dict[str, list[float]] = defaultdict(list)
    memory_operation_private_mb: list[float] = []
    memory_operation_rss_mb: list[float] = []
    memory_by_action_private_mb: dict[str, list[float]] = defaultdict(list)
    memory_by_action_rss_mb: dict[str, list[float]] = defaultdict(list)
    memory_tool_private_mb: list[float] = []
    memory_rebuild_private_mb: list[float] = []
    for action in actions:
        action_name = str(action.get("name") or "")
        grouped[action_name].append(float(action.get("duration_ms") or 0.0))
        process_memory = action.get("process_memory") if isinstance(action.get("process_memory"), dict) else {}
        operation_delta = process_memory.get("operation_delta") if isinstance(process_memory.get("operation_delta"), dict) else {}
        private_delta = _float_or_none(operation_delta.get("private_mb"))
        rss_delta = _float_or_none(operation_delta.get("rss_mb"))
        if private_delta is not None:
            memory_operation_private_mb.append(private_delta)
            memory_by_action_private_mb[action_name].append(private_delta)
        if rss_delta is not None:
            memory_operation_rss_mb.append(rss_delta)
            memory_by_action_rss_mb[action_name].append(rss_delta)
        tool_delta = process_memory.get("tool_delta") if isinstance(process_memory.get("tool_delta"), dict) else {}
        tool_private_delta = _float_or_none(tool_delta.get("private_mb"))
        if tool_private_delta is not None:
            memory_tool_private_mb.append(tool_private_delta)
        rebuild_delta = process_memory.get("delta_to_after_rebuild_wait") if isinstance(process_memory.get("delta_to_after_rebuild_wait"), dict) else {}
        rebuild_private_delta = _float_or_none(rebuild_delta.get("private_mb"))
        if rebuild_private_delta is not None:
            memory_rebuild_private_mb.append(rebuild_private_delta)
        summary = action.get("summary") if isinstance(action.get("summary"), dict) else {}
        timings = summary.get("timings_ms") if isinstance(summary.get("timings_ms"), dict) else {}
        if action_name.startswith("set_current_"):
            for key, value in timings.items():
                parsed_timing = _float_or_none(value)
                if parsed_timing is not None:
                    set_current_timings[str(key)].append(parsed_timing)
        rebuild_wait = action.get("rebuild_wait") if isinstance(action.get("rebuild_wait"), dict) else {}
        wait = rebuild_wait.get("wait_ms")
        if wait is not None:
            recovery_update_waits.append(float(wait))
            if bool(rebuild_wait.get("new_finished")):
                rebuild_ready_waits.append(float(wait))
        for source_key, target in (
            ("rebuild_started_after_action_ms", rebuild_started_after_action),
            ("rebuild_finished_after_action_ms", rebuild_finished_after_action),
            ("observe_after_finished_ms", rebuild_observe_after_finished),
        ):
            parsed_lag = _float_or_none(rebuild_wait.get(source_key))
            if parsed_lag is not None:
                target.append(parsed_lag)
        has_new_rebuild = bool(rebuild_wait.get("new_started") or rebuild_wait.get("new_finished"))
        if not has_new_rebuild:
            continue
        for source, target in (
            ("rebuild_wait.rebuild.timings_ms.total", rebuild_total),
            ("rebuild_wait.rebuild.semantic_rebuild_timings_ms.total", semantic_total),
            ("rebuild_wait.rebuild.switch_timings_ms.sync_listing", sync_listing),
        ):
            value = _pick_path(action, source)
            parsed = _float_or_none(value)
            if parsed is not None:
                target.append(parsed)
    return {
        "actions_ms": {name: _summarize_numbers(values) for name, values in sorted(grouped.items())},
        "set_current_recovery_update_wait_ms": _summarize_numbers(recovery_update_waits),
        "set_current_rebuild_ready_wait_ms": _summarize_numbers(rebuild_ready_waits),
        "rebuild_started_after_action_ms": _summarize_numbers(rebuild_started_after_action),
        "rebuild_finished_after_action_ms": _summarize_numbers(rebuild_finished_after_action),
        "rebuild_observe_after_finished_ms": _summarize_numbers(rebuild_observe_after_finished),
        "rebuild_total_ms": _summarize_numbers(rebuild_total),
        "semantic_rebuild_total_ms": _summarize_numbers(semantic_total),
        "switch_sync_listing_ms": _summarize_numbers(sync_listing),
        "set_current_internal_timings_ms": {
            name: _summarize_numbers(values)
            for name, values in sorted(set_current_timings.items())
        },
        "process_memory_delta_mb": {
            "operation_private": _summarize_numbers(memory_operation_private_mb),
            "operation_rss": _summarize_numbers(memory_operation_rss_mb),
            "tool_call_private": _summarize_numbers(memory_tool_private_mb),
            "set_current_to_rebuild_wait_private": _summarize_numbers(memory_rebuild_private_mb),
            "by_action_private": {
                name: _summarize_numbers(values)
                for name, values in sorted(memory_by_action_private_mb.items())
            },
            "by_action_rss": {
                name: _summarize_numbers(values)
                for name, values in sorted(memory_by_action_rss_mb.items())
            },
        },
    }


def run(args: argparse.Namespace) -> dict[str, Any]:
    client = ApiClient(args.base_url, args.token, args.timeout)
    memory_pid, memory_target = _resolve_memory_pid(args)
    log_offsets = None
    if resolve_logs is not None and parse_offsets is not None:
        paths = resolve_logs(args.log or [])
        log_offsets = parse_offsets(paths)

    started_epoch = _now_epoch()
    payload: dict[str, Any] = {
        "schema": "adaos.builder_materialization_poc_benchmark.v1",
        "label": args.label,
        "started_at": _now_iso(),
        "base_url": args.base_url.rstrip("/"),
        "webspace_id": args.webspace,
        "dev_webspace_id": args.dev_webspace,
        "project_id": args.project_id,
        "revisions": list(args.revisions),
        "iterations": args.iterations,
        "actions": [],
        "process_memory": {
            "target": memory_target,
            "initial": _process_memory_snapshot(memory_pid),
        },
    }

    initial_status = _timed_call_with_memory("node_status", lambda: client.get("/api/node/status", timeout=10.0), memory_pid)
    payload["initial_status"] = {
        "duration_ms": initial_status["duration_ms"],
        "ok": initial_status["payload"].get("ready") is True or initial_status["payload"].get("ok") is True,
        "node_id": initial_status["payload"].get("node_id"),
        "role": initial_status["payload"].get("role"),
        "ready": initial_status["payload"].get("ready"),
        "process_memory": initial_status.get("process_memory"),
    }

    initial_materialization = _timed_call_with_memory(
        "initial_materialization",
        lambda: client.get(f"/api/node/yjs/webspaces/{urllib.parse.quote(args.dev_webspace)}/materialization?include_runtime=0", timeout=15.0),
        memory_pid,
    )
    payload["initial_materialization"] = {
        "duration_ms": initial_materialization["duration_ms"],
        "materialization": _compact_materialization(_extract_materialization(initial_materialization["payload"])),
        "rebuild": _compact_rebuild(_extract_rebuild(initial_materialization["payload"])),
        "process_memory": initial_materialization.get("process_memory"),
    }

    for iteration in range(1, args.iterations + 1):
        common_project = {
            "object_type": "scenario",
            "object_id": args.project_id,
            "project_type": "scenario",
            "project_id": args.project_id,
            "webspace_id": args.webspace,
        }
        read_actions: list[tuple[str, str, dict[str, Any]]] = [
            (
                "prompt_list_project_objects",
                "prompt_engineer_skill:prompt_list_project_objects",
                {
                    "project_type": "scenario",
                    "project_id": args.project_id,
                    "webspace_id": args.webspace,
                },
            ),
            (
                "prompt_list_project_file_tree",
                "prompt_engineer_skill:prompt_list_project_file_tree",
                common_project,
            ),
            (
                "prompt_read_project_file",
                "prompt_engineer_skill:prompt_read_project_file",
                {
                    "object_type": "scenario",
                    "object_id": args.project_id,
                    "path": args.read_path,
                    "webspace_id": args.webspace,
                    "max_bytes": args.max_read_bytes,
                },
            ),
        ]
        for name, tool, arguments in read_actions:
            action = _timed_call_with_memory(
                name,
                lambda tool=tool, arguments=arguments: client.call_tool(tool, arguments, timeout=args.timeout),
                memory_pid,
            )
            action["iteration"] = iteration
            action["tool"] = tool
            action["summary"] = _tool_summary(tool, action["payload"])
            action.pop("payload", None)
            payload["actions"].append(action)
            time.sleep(max(0.0, args.cooldown))

        for revision in args.revisions:
            tool = "builder_skill:set_ui_revision_current"
            arguments = {
                "revision": revision,
                "webspace_id": args.webspace,
                "_meta": {
                    "user_id": args.user_id,
                    "roles": args.roles,
                    "benchmark": args.label,
                },
            }
            action = _timed_call_with_memory(
                f"set_current_{revision}",
                lambda tool=tool, arguments=arguments: client.call_tool(tool, arguments, timeout=args.timeout),
                memory_pid,
            )
            action["iteration"] = iteration
            action["tool"] = tool
            action["summary"] = _tool_summary(tool, action["payload"])
            action.pop("payload", None)
            action["rebuild_wait"] = _wait_for_rebuild(
                client,
                webspace=args.dev_webspace,
                since_epoch=float(action["started_at"]),
                timeout_s=args.poll_timeout,
                poll_interval_s=args.poll_interval,
            )
            _set_action_memory_stage(action, "after_rebuild_wait", memory_pid)
            materialization_after = client.get(
                f"/api/node/yjs/webspaces/{urllib.parse.quote(args.dev_webspace)}/materialization?include_runtime=0",
                timeout=15.0,
            )
            _set_action_memory_stage(action, "after_materialization_read", memory_pid)
            action["materialization_after"] = _compact_materialization(_extract_materialization(materialization_after))
            try:
                snapshot_after = client.get(
                    f"/api/node/yjs/webspaces/{urllib.parse.quote(args.dev_webspace)}/materialization/snapshot?scope=essential",
                    timeout=30.0,
                )
                _set_action_memory_stage(action, "after_snapshot_read", memory_pid)
                action["view_signature_after"] = _materialized_view_signature(snapshot_after)
            except Exception as exc:
                _set_action_memory_stage(action, "after_snapshot_read", memory_pid)
                action["view_signature_after"] = {
                    "error": f"{type(exc).__name__}: {exc}",
                }
            payload["actions"].append(action)
            time.sleep(max(0.0, args.cooldown))

    payload["finished_at"] = _now_iso()
    payload["duration_s"] = round(_now_epoch() - started_epoch, 3)
    final_memory = _process_memory_snapshot(memory_pid)
    process_memory = payload.get("process_memory") if isinstance(payload.get("process_memory"), dict) else {}
    initial_memory = process_memory.get("initial") if isinstance(process_memory.get("initial"), dict) else None
    process_memory["final"] = final_memory
    process_memory["delta"] = _process_memory_delta(initial_memory, final_memory)
    payload["process_memory"] = process_memory
    payload["summary"] = _build_summary(payload["actions"])
    payload["logs"] = _collect_logs(args.label, log_offsets)
    return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="Benchmark Builder Set current materialization path through local API.")
    parser.add_argument("--base-url", default=os.getenv("ADAOS_CONTROL_URL") or "http://127.0.0.1:8777")
    parser.add_argument("--token", default=os.getenv("ADAOS_TOKEN") or "dev-local-token")
    parser.add_argument("--webspace", default="desktop")
    parser.add_argument("--dev-webspace", default="desktop-dev")
    parser.add_argument("--project-id", default="todo_list_5b9319fa")
    parser.add_argument("--read-path", default="scenario.json")
    parser.add_argument("--max-read-bytes", type=int, default=200000)
    parser.add_argument("--revisions", nargs="+", default=["020", "021"])
    parser.add_argument("--iterations", type=int, default=2)
    parser.add_argument("--timeout", type=float, default=DEFAULT_ACTION_TIMEOUT)
    parser.add_argument("--poll-timeout", type=float, default=45.0)
    parser.add_argument("--poll-interval", type=float, default=0.25)
    parser.add_argument("--cooldown", type=float, default=0.25)
    parser.add_argument("--label", default="builder-materialization-poc")
    parser.add_argument("--user-id", default="guest")
    parser.add_argument("--roles", nargs="*", default=[])
    parser.add_argument("--memory-pid", type=int, default=0)
    parser.add_argument("--memory-port", type=int, default=0)
    parser.add_argument("--no-memory", action="store_true")
    parser.add_argument("--log", action="append", default=[])
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    payload = run(args)
    out = args.out
    if not out:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out = f".adaos/diagnostics/builder_materialization_poc_{args.label}_{timestamp}.json"
    out_path = Path(out)
    if not out_path.is_absolute():
        out_path = ROOT / out_path
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(_jsonable(payload), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    print(f"report: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
