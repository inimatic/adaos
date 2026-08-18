from __future__ import annotations

import json
import hashlib
import os
import re
import time
from collections import Counter, deque
from pathlib import Path
from threading import RLock
from typing import Any, Iterable

from adaos.services.env_policy import env_bool


_SCHEMA = "adaos.incident_registry.v1"
_PERSIST_SCHEMA = "adaos.incident_registry.persisted.v1"
_MAX_INCIDENTS = 256
_MAX_EVIDENCE_SAMPLES = 3
_ACTIVE_WINDOW_S = 10 * 60
_LOCK = RLock()
_PROCESS_ACTIVITY_CAPTURE_LOCK = RLock()
try:
    _PROCESS_ACTIVITY_HISTORY_MAX = max(
        24,
        min(int(str(os.getenv("ADAOS_INCIDENT_PROCESS_HISTORY_MAX") or "96").strip()), 720),
    )
except Exception:
    _PROCESS_ACTIVITY_HISTORY_MAX = 96
try:
    _PROCESS_ACTIVITY_HISTORY_DEFAULT_LIMIT = max(
        24,
        min(
            int(str(os.getenv("ADAOS_INCIDENT_PROCESS_HISTORY_LIMIT") or "75").strip()),
            _PROCESS_ACTIVITY_HISTORY_MAX,
        ),
    )
except Exception:
    _PROCESS_ACTIVITY_HISTORY_DEFAULT_LIMIT = min(75, _PROCESS_ACTIVITY_HISTORY_MAX)
_INCIDENTS: dict[str, dict[str, Any]] = {}
_ORDER: deque[str] = deque(maxlen=_MAX_INCIDENTS)
_PROCESS_ACTIVITY_HISTORY: deque[dict[str, Any]] = deque(maxlen=_PROCESS_ACTIVITY_HISTORY_MAX)
_PROCESS_ACTIVITY_PREVIOUS: dict[int, dict[str, Any]] = {}
_PROCESS_ACTIVITY_PREVIOUS_SYSTEM: dict[str, int] = {}
_PROCESS_ACTIVITY_PREVIOUS_AT: float | None = None
_PROCESS_ACTIVITY_IDENTITY_CACHE: dict[int, dict[str, Any]] = {}

_SECRET_PATTERNS = (
    re.compile(r"(?i)(token|authorization|password|secret|key)=([^&\s]+)"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
)
_PROCESS_ACTIVITY_NAME_HINTS = (
    "python",
    "node",
    "chrome",
    "msedge",
    "firefox",
    "code",
    "powershell",
    "pwsh",
    "cmd",
    "curl",
    "wget",
    "aria",
    "torrent",
    "transmission",
    "onedrive",
    "rclone",
    "rsync",
    "ffmpeg",
    "java",
    "dotnet",
    "docker",
    "wsl",
    "git",
    "npm",
    "yarn",
    "pnpm",
    "rasa",
    "ollama",
    "sing-box",
    "openvpn",
    "wireguard",
    "system",
)


def _now() -> float:
    return time.time()


def _clean_token(value: Any, *, fallback: str = "unknown") -> str:
    text = str(value or "").strip()
    return text or fallback


def _redact_text(value: Any, *, limit: int = 320) -> str:
    text = str(value or "").replace("\x00", " ").strip()
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1)}<redacted>", text)
    if len(text) > limit:
        return text[: max(0, limit - 3)] + "..."
    return text


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth >= 8:
        return _redact_text(value, limit=160)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _redact_text(value)
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:80]:
            key_text = _redact_text(key, limit=120)
            if key_text.lower() in {"authorization", "x-adaos-token", "token", "password", "secret"}:
                out[key_text] = "<redacted>"
            else:
                out[key_text] = _json_safe(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple, set, frozenset)):
        return [_json_safe(item, depth=depth + 1) for item in list(value)[:80]]
    return _redact_text(value)


def _fingerprint(parts: Iterable[Any]) -> str:
    raw = "|".join(_redact_text(part, limit=200) for part in parts)
    return hashlib.sha1(raw.encode("utf-8", errors="replace")).hexdigest()


def _severity_rank(value: Any) -> int:
    token = str(value or "").strip().lower()
    order = {
        "critical": 50,
        "high": 40,
        "degraded": 35,
        "warning": 30,
        "medium": 25,
        "info": 10,
    }
    return int(order.get(token) or 20)


def _env_truthy(name: str) -> bool:
    return env_bool(name)


def incident_domain_from_owner(owner: Any, *, fallback: str = "core.runtime") -> str:
    text = str(owner or "").strip()
    if not text or text in {"-", "unknown", "none"}:
        return fallback
    lowered = text.lower()
    if lowered.startswith(("skill:", "member:", "browser:", "core.", "hub_root", "channel:")):
        return text
    if lowered.startswith("_by_owner/"):
        token = text.split("/", 1)[1].strip()
        token_l = token.lower()
        if token_l in {"core", "runtime", "gateway", "gateway_ws"}:
            return "core.yjs" if token_l == "gateway_ws" else "core.runtime"
        if token_l in {"yjs", "sync", "yws"}:
            return "core.yjs"
        if token_l.startswith("skill:"):
            return token
        if token_l.startswith("skill_"):
            return f"skill:{token[6:]}"
        if token_l.endswith("_skill"):
            return f"skill:{token}"
        return f"owner:{token}"
    if lowered.endswith("_skill"):
        return f"skill:{text}"
    return fallback


def _domain_from_handler_label(label: str) -> str:
    match = re.search(r"\bskill=([A-Za-z0-9_.-]+)", str(label or ""))
    if match:
        return f"skill:{match.group(1)}"
    if "adaos.services.webrtc" in label:
        return "core.webrtc"
    if "adaos.services.yjs" in label:
        return "core.yjs"
    if "adaos.services" in label or "adaos.apps" in label:
        return "core.runtime"
    return "core.eventbus"


def is_yjs_thread_affinity_fault(value: Any) -> bool:
    text = str(value or "").strip().lower()
    if not text:
        return False
    return (
        "y_py::" in text
        and any(
            object_name in text
            for object_name in (
                "ymap",
                "y_map",
                "yarray",
                "y_array",
                "ydoc",
                "y_doc",
                "ytransaction",
                "y_transaction",
                "ytext",
                "y_text",
                "yxml",
                "y_xml",
            )
        )
        and ("dropped on another thread" in text or "unsendbale" in text or "unsendable" in text)
    )


def _domain_from_cmdline(cmdline: str) -> str:
    text = str(cmdline or "").replace("\\", "/")
    marker = "/.adaos/workspace/skills/.runtime/"
    if marker in text:
        tail = text.split(marker, 1)[1]
        skill = tail.split("/", 1)[0].strip()
        if skill:
            return f"skill:{skill}"
    automation = re.search(
        r"--session-id(?:=|\s+)[\"']?automation\.(skill|scenario)\.([^\s\"']+)",
        text,
        flags=re.IGNORECASE,
    )
    if automation:
        object_type = automation.group(1).lower()
        object_id = automation.group(2).strip(" .")
        if object_id:
            return f"skill:{object_id}" if object_type == "skill" else f"scenario:{object_id}"
    if "adaos.apps.supervisor" in text:
        return "core.supervisor"
    if "adaos.services.realtime_sidecar" in text:
        return "core.sidecar"
    if "adaos.apps.autostart_runner" in text or "adaos.apps.api" in text:
        return "core.runtime"
    if "handlers." in text or "handlers/main.py" in text:
        return "skill:unknown"
    return "system.process"


def _read_pressure_file(name: str) -> dict[str, Any]:
    path = f"/proc/pressure/{name}"
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = [line.strip() for line in handle.read().splitlines() if line.strip()]
    except Exception:
        return {}
    parsed: dict[str, Any] = {}
    for line in lines:
        parts = line.split()
        if not parts:
            continue
        bucket = parts[0]
        values: dict[str, Any] = {}
        for part in parts[1:]:
            key, sep, raw = part.partition("=")
            if not sep:
                continue
            try:
                values[key] = float(raw) if "." in raw else int(raw)
            except Exception:
                values[key] = raw
        parsed[bucket] = values
    return parsed


def _process_activity_name_relevant(name: Any, *, pid: int) -> bool:
    if int(pid or 0) == os.getpid():
        return True
    token = str(name or "").strip().lower()
    if any(hint in token for hint in _PROCESS_ACTIVITY_NAME_HINTS):
        return True
    extra = str(os.getenv("ADAOS_INCIDENT_PROCESS_NAME_HINTS") or "").strip().lower()
    return any(hint.strip() and hint.strip() in token for hint in extra.split(","))


def _process_rows() -> list[dict[str, Any]]:
    try:
        import psutil  # type: ignore
    except Exception:
        return []

    rows: list[dict[str, Any]] = []
    observed_pids: set[int] = set()
    for proc in psutil.process_iter(["pid", "name", "create_time"]):
        try:
            initial = proc.info
            pid = int(initial.get("pid") or proc.pid)
            name = str(initial.get("name") or "")
            create_time = float(initial.get("create_time") or 0.0)
            if not _process_activity_name_relevant(name, pid=pid):
                continue
            observed_pids.add(pid)
            with _LOCK:
                identity = dict(_PROCESS_ACTIVITY_IDENTITY_CACHE.get(pid) or {})
            if (
                str(identity.get("name") or "") != name
                or float(identity.get("create_time") or 0.0) != create_time
            ):
                identity = {}
            with proc.oneshot():
                if not identity:
                    cmdline_items = proc.cmdline() or []
                    cmdline = " ".join(str(item) for item in cmdline_items) or name
                    identity = {
                        "name": name,
                        "create_time": create_time,
                        "cmdline": _redact_text(cmdline, limit=220),
                        "domain": _domain_from_cmdline(cmdline),
                    }
                    with _LOCK:
                        _PROCESS_ACTIVITY_IDENTITY_CACHE[pid] = dict(identity)
                mem = proc.memory_info()
                cpu_times = proc.cpu_times()
                io = proc.io_counters() if hasattr(proc, "io_counters") else None
                try:
                    priority = proc.nice() if hasattr(proc, "nice") else None
                except Exception:
                    priority = None
                try:
                    io_priority = proc.ionice() if hasattr(proc, "ionice") else None
                except Exception:
                    io_priority = None
            rss = int(getattr(mem, "rss", 0) or 0)
            cpu_time_s = float(getattr(cpu_times, "user", 0.0) or 0.0) + float(
                getattr(cpu_times, "system", 0.0) or 0.0
            )
            read_bytes = int(getattr(io, "read_bytes", 0) or 0) if io is not None else 0
            write_bytes = int(getattr(io, "write_bytes", 0) or 0) if io is not None else 0
            rows.append(
                {
                    "pid": pid,
                    "name": _redact_text(identity.get("name") or name, limit=80),
                    "rss_bytes": rss,
                    "cpu_time_s": round(cpu_time_s, 6),
                    "read_bytes": read_bytes,
                    "write_bytes": write_bytes,
                    "priority": str(priority) if priority is not None else None,
                    "io_priority": str(io_priority) if io_priority is not None else None,
                    "domain": identity.get("domain") or "system.process",
                    "cmdline": identity.get("cmdline") or name,
                }
            )
        except Exception:
            continue
    with _LOCK:
        for stale_pid in set(_PROCESS_ACTIVITY_IDENTITY_CACHE) - observed_pids:
            _PROCESS_ACTIVITY_IDENTITY_CACHE.pop(stale_pid, None)
    return rows


def _system_activity_counters() -> dict[str, int]:
    try:
        import psutil  # type: ignore
    except Exception:
        return {}
    counters: dict[str, int] = {}
    try:
        network = psutil.net_io_counters()
        counters["network_recv_bytes"] = int(getattr(network, "bytes_recv", 0) or 0)
        counters["network_sent_bytes"] = int(getattr(network, "bytes_sent", 0) or 0)
    except Exception:
        pass
    try:
        disk = psutil.disk_io_counters()
        counters["disk_read_bytes"] = int(getattr(disk, "read_bytes", 0) or 0)
        counters["disk_write_bytes"] = int(getattr(disk, "write_bytes", 0) or 0)
    except Exception:
        pass
    return counters


def capture_process_activity_sample(*, limit: int = 10, ts: float | None = None) -> dict[str, Any]:
    """Capture one serialized process/system activity sample for incident lookback."""

    with _PROCESS_ACTIVITY_CAPTURE_LOCK:
        return _capture_process_activity_sample(limit=limit, ts=ts)


def _capture_process_activity_sample(*, limit: int = 10, ts: float | None = None) -> dict[str, Any]:
    """Build a sample while the public capture lock is held."""

    global _PROCESS_ACTIVITY_PREVIOUS_AT
    capture_started = time.monotonic()
    now_ts = float(ts if ts is not None else _now())
    rows = _process_rows()
    system = _system_activity_counters()
    with _LOCK:
        previous = dict(_PROCESS_ACTIVITY_PREVIOUS)
        previous_system = dict(_PROCESS_ACTIVITY_PREVIOUS_SYSTEM)
        previous_at = _PROCESS_ACTIVITY_PREVIOUS_AT

    interval_s = max(0.0, now_ts - float(previous_at)) if isinstance(previous_at, (int, float)) else 0.0
    activity: list[dict[str, Any]] = []
    current_by_pid: dict[int, dict[str, Any]] = {}
    for row in rows:
        pid = int(row.get("pid") or 0)
        if pid <= 0:
            continue
        current_by_pid[pid] = row
        prior = previous.get(pid)
        read_delta = (
            max(0, int(row.get("read_bytes") or 0) - int(prior.get("read_bytes") or 0))
            if prior is not None
            else 0
        )
        write_delta = (
            max(0, int(row.get("write_bytes") or 0) - int(prior.get("write_bytes") or 0))
            if prior is not None
            else 0
        )
        cpu_delta_s = (
            max(0.0, float(row.get("cpu_time_s") or 0.0) - float(prior.get("cpu_time_s") or 0.0))
            if prior is not None
            else 0.0
        )
        activity.append(
            {
                "pid": pid,
                "name": row.get("name"),
                "domain": row.get("domain"),
                "cmdline": row.get("cmdline"),
                "rss_bytes": int(row.get("rss_bytes") or 0),
                "priority": row.get("priority"),
                "io_priority": row.get("io_priority"),
                "cpu_delta_s": round(cpu_delta_s, 6),
                "cpu_percent": round((cpu_delta_s / interval_s) * 100.0, 3) if interval_s > 0.0 else 0.0,
                "read_delta_bytes": read_delta,
                "write_delta_bytes": write_delta,
                "io_delta_bytes": read_delta + write_delta,
            }
        )
    system_delta = {
        f"{key}_delta": max(0, int(value) - int(previous_system.get(key) or 0))
        for key, value in system.items()
        if key in previous_system
    }
    bounded_limit = max(1, min(int(limit or 10), 25))
    ranked: list[dict[str, Any]] = []
    seen_pids: set[int] = set()
    rank_sources = (
        sorted(activity, key=lambda item: float(item.get("cpu_delta_s") or 0.0), reverse=True),
        sorted(activity, key=lambda item: int(item.get("io_delta_bytes") or 0), reverse=True),
        sorted(activity, key=lambda item: int(item.get("rss_bytes") or 0), reverse=True),
    )
    per_source = max(1, bounded_limit // len(rank_sources))
    for source in rank_sources:
        for item in source[:per_source]:
            pid = int(item.get("pid") or 0)
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            ranked.append(item)
    if len(ranked) < bounded_limit:
        combined = sorted(
            activity,
            key=lambda item: (
                float(item.get("cpu_delta_s") or 0.0),
                int(item.get("io_delta_bytes") or 0),
                int(item.get("rss_bytes") or 0),
            ),
            reverse=True,
        )
        for item in combined:
            pid = int(item.get("pid") or 0)
            if pid in seen_pids:
                continue
            seen_pids.add(pid)
            ranked.append(item)
            if len(ranked) >= bounded_limit:
                break
    sample = _json_safe(
        {
            "ts": now_ts,
            "interval_s": round(interval_s, 3),
            "process_total": len(current_by_pid),
            "process_scope": "relevant",
            "capture_duration_ms": round((time.monotonic() - capture_started) * 1000.0, 3),
            "system_delta": system_delta,
            "top_activity": ranked[:bounded_limit],
        }
    )
    with _LOCK:
        _PROCESS_ACTIVITY_PREVIOUS.clear()
        _PROCESS_ACTIVITY_PREVIOUS.update(current_by_pid)
        _PROCESS_ACTIVITY_PREVIOUS_SYSTEM.clear()
        _PROCESS_ACTIVITY_PREVIOUS_SYSTEM.update(system)
        _PROCESS_ACTIVITY_PREVIOUS_AT = now_ts
        _PROCESS_ACTIVITY_HISTORY.append(dict(sample))
    return dict(sample)


def latest_process_activity_sample() -> dict[str, Any]:
    with _LOCK:
        return dict(_PROCESS_ACTIVITY_HISTORY[-1]) if _PROCESS_ACTIVITY_HISTORY else {}


def process_activity_history_snapshot(*, limit: int = _PROCESS_ACTIVITY_HISTORY_DEFAULT_LIMIT) -> dict[str, Any]:
    with _LOCK:
        samples = list(_PROCESS_ACTIVITY_HISTORY)
    bounded_limit = max(1, min(int(limit or _PROCESS_ACTIVITY_HISTORY_DEFAULT_LIMIT), _PROCESS_ACTIVITY_HISTORY_MAX))
    selected = samples[-bounded_limit:]
    window_started_at = selected[0].get("ts") if selected else None
    window_ended_at = selected[-1].get("ts") if selected else None
    coverage_s = (
        max(0.0, float(window_ended_at) - float(window_started_at))
        if isinstance(window_started_at, (int, float)) and isinstance(window_ended_at, (int, float))
        else 0.0
    )
    return {
        "sample_total": len(samples),
        "returned": len(selected),
        "history_capacity": _PROCESS_ACTIVITY_HISTORY_MAX,
        "window_started_at": window_started_at,
        "window_ended_at": window_ended_at,
        "coverage_s": round(coverage_s, 3),
        "samples": selected,
    }


def _process_samples(limit: int = 8) -> dict[str, Any]:
    rows = _process_rows()
    if not rows:
        return {}
    top_rss = sorted(rows, key=lambda item: int(item.get("rss_bytes") or 0), reverse=True)[:limit]
    top_write = sorted(rows, key=lambda item: int(item.get("write_bytes") or 0), reverse=True)[:limit]
    return {
        "process_total": len(rows),
        "top_rss": top_rss,
        "top_write_bytes": top_write,
    }


def process_io_delta_sample(*, interval_s: float = 0.25, limit: int = 8) -> dict[str, Any]:
    start_rows = _process_rows()
    if not start_rows:
        return {}
    start = {int(row.get("pid") or 0): row for row in start_rows if int(row.get("pid") or 0) > 0}
    sleep_s = max(0.0, min(float(interval_s or 0.0), 2.0))
    if sleep_s:
        time.sleep(sleep_s)
    end_rows = _process_rows()
    rows: list[dict[str, Any]] = []
    for row in end_rows:
        pid = int(row.get("pid") or 0)
        prev = start.get(pid)
        if not prev:
            continue
        read_delta = max(0, int(row.get("read_bytes") or 0) - int(prev.get("read_bytes") or 0))
        write_delta = max(0, int(row.get("write_bytes") or 0) - int(prev.get("write_bytes") or 0))
        if read_delta <= 0 and write_delta <= 0:
            continue
        rows.append(
            {
                "pid": pid,
                "name": row.get("name"),
                "status": row.get("status"),
                "domain": row.get("domain"),
                "cmdline": row.get("cmdline"),
                "read_delta_bytes": read_delta,
                "write_delta_bytes": write_delta,
                "total_delta_bytes": read_delta + write_delta,
            }
        )
    rows.sort(key=lambda item: int(item.get("total_delta_bytes") or 0), reverse=True)
    return {
        "interval_s": round(sleep_s, 3),
        "process_total": len(end_rows),
        "top_io_delta": rows[: max(1, min(int(limit or 8), 50))],
    }


def local_blocking_evidence(
    *,
    include_processes: bool = True,
    include_io_delta: bool = False,
    io_delta_interval_s: float = 0.25,
) -> dict[str, Any]:
    evidence: dict[str, Any] = {
        "pressure": {
            "io": _read_pressure_file("io"),
            "cpu": _read_pressure_file("cpu"),
            "memory": _read_pressure_file("memory"),
        }
    }
    if include_processes:
        samples = _process_samples()
        if samples:
            evidence["processes"] = samples
    if include_io_delta:
        delta = process_io_delta_sample(interval_s=io_delta_interval_s)
        if delta:
            evidence["process_io_delta"] = delta
    return _json_safe(evidence)


def record_incident(
    *,
    incident_class: str,
    signal: str,
    severity: str = "warning",
    domain: str = "core.runtime",
    summary: str = "",
    evidence: dict[str, Any] | None = None,
    source: str = "",
    component: str | None = None,
    fingerprint_parts: Iterable[Any] | None = None,
    tags: Iterable[str] | None = None,
    ts: float | None = None,
    increment_occurrence: bool = True,
) -> dict[str, Any]:
    now_ts = float(ts if ts is not None else _now())
    inc_class = _clean_token(incident_class, fallback="unknown")
    signal_token = _clean_token(signal, fallback=inc_class)
    domain_token = _clean_token(domain, fallback="core.runtime")
    severity_token = _clean_token(severity, fallback="warning").lower()
    fingerprint = _fingerprint(
        fingerprint_parts
        if fingerprint_parts is not None
        else (inc_class, signal_token, domain_token, component or "")
    )
    incident_id = f"inc-{fingerprint[:12]}"
    safe_evidence = _json_safe(evidence or {})
    safe_tags = sorted({str(item or "").strip() for item in (tags or []) if str(item or "").strip()})
    with _LOCK:
        item = _INCIDENTS.get(fingerprint)
        if item is None:
            item = {
                "id": incident_id,
                "fingerprint": fingerprint,
                "class": inc_class,
                "signal": signal_token,
                "severity": severity_token,
                "domain": domain_token,
                "component": str(component or "").strip() or None,
                "source": str(source or "").strip() or None,
                "summary": str(summary or signal_token).strip(),
                "first_seen_at": now_ts,
                "last_seen_at": now_ts,
                "occurrence_count": 0,
                "tags": safe_tags,
                "latest_evidence": {},
                "evidence_samples": deque(maxlen=_MAX_EVIDENCE_SAMPLES),
            }
            _INCIDENTS[fingerprint] = item
            _ORDER.append(fingerprint)
        item["last_seen_at"] = now_ts
        if increment_occurrence or int(item.get("occurrence_count") or 0) <= 0:
            item["occurrence_count"] = int(item.get("occurrence_count") or 0) + 1
        if _severity_rank(severity_token) >= _severity_rank(item.get("severity")):
            item["severity"] = severity_token
        if summary:
            item["summary"] = str(summary).strip()
        if safe_tags:
            item["tags"] = sorted(set(list(item.get("tags") or []) + safe_tags))
        if safe_evidence:
            item["latest_evidence"] = safe_evidence
            samples = item.get("evidence_samples")
            if isinstance(samples, deque):
                samples.append({"ts": now_ts, "evidence": safe_evidence})

        while len(_INCIDENTS) > _MAX_INCIDENTS and _ORDER:
            old = _ORDER.popleft()
            _INCIDENTS.pop(old, None)
        return _snapshot_item(item, now_ts=now_ts, include_evidence=True)


def record_hub_root_transport_incident(
    *,
    event: str,
    server: str | None,
    error: BaseException | str | None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    capture_process_activity_sample()
    error_text = _redact_text(error, limit=500) if error is not None else None
    incident = record_incident(
        incident_class="hub_root_transport",
        signal=str(event or "transport_failure"),
        severity="degraded",
        domain="hub_root.transport",
        component="nats_bridge",
        source="hub_io",
        summary=f"Hub-root transport incident: {str(event or 'transport_failure')}",
        evidence={
            "server": _redact_text(server, limit=240) if server else None,
            "error": error_text,
            "details": details or {},
            "process_activity_history": process_activity_history_snapshot(),
            "failure_snapshot": local_blocking_evidence(include_processes=True),
        },
        fingerprint_parts=("hub_root_transport", str(event or "transport_failure"), server or ""),
        tags=("hub-root", "transport", "process-lookback", "durable"),
    )
    try:
        persisted = persist_incident_registry()
    except Exception as exc:
        persisted = {"ok": False, "error": type(exc).__name__}
    return {**incident, "persistence": persisted}


def record_runtime_api_timeout(
    *,
    source: str,
    path: str,
    timeout_s: float,
    exc: BaseException,
    component: str = "runtime_api",
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = type(exc).__name__
    if _env_truthy("ADAOS_INCIDENT_IO_DELTA_ON_TIMEOUT"):
        blocking = local_blocking_evidence(include_processes=True, include_io_delta=True)
    else:
        blocking = local_blocking_evidence(include_processes=True)
    merged = {
        "path": path,
        "timeout_s": float(timeout_s),
        "exception_type": kind,
        "exception": _redact_text(exc, limit=500),
        **(evidence or {}),
        **blocking,
    }
    signal = "supervisor_preflight_read_timeout" if "ReadTimeout" in kind else "runtime_api_unavailable"
    return record_incident(
        incident_class="runtime_api_timeout",
        signal=signal,
        severity="warning",
        domain="core.runtime",
        component=component,
        source=source,
        summary=f"Runtime API request timed out: {path}",
        evidence=merged,
        fingerprint_parts=("runtime_api_timeout", source, path, kind),
        tags=("latency", "local-runtime", "blocking-evidence"),
    )


def record_slow_event_handler(
    *,
    handler_label: str,
    event_type: str,
    duration_s: float,
    kind: str,
    threshold_s: float,
) -> dict[str, Any]:
    domain = _domain_from_handler_label(handler_label)
    return record_incident(
        incident_class="slow_event_handler",
        signal=f"slow_{kind}_event_handler",
        severity="warning",
        domain=domain,
        component="eventbus",
        source="eventbus",
        summary=f"Slow {kind} event handler for {event_type}",
        evidence={
            "handler": handler_label,
            "event_type": event_type,
            "duration_s": round(float(duration_s), 6),
            "threshold_s": round(float(threshold_s), 6),
            "process_activity_history": process_activity_history_snapshot(limit=8),
        },
        fingerprint_parts=("slow_event_handler", kind, event_type, handler_label),
        tags=("eventbus", "latency"),
    )


def record_runtime_event_loop_lag(
    *,
    lag_ms: float,
    threshold_ms: float,
    interval_sec: float,
) -> dict[str, Any]:
    try:
        from adaos.services.skill.subscription_execution import subscription_execution_snapshot

        skill_execution = subscription_execution_snapshot(limit=10)
    except Exception as exc:
        skill_execution = {"available": False, "error": type(exc).__name__}
    return record_incident(
        incident_class="runtime_event_loop_lag",
        signal="event_loop_lag_threshold_exceeded",
        severity="degraded",
        domain="core.runtime",
        component="runtime_event_loop",
        source="runtime_event_loop_monitor",
        summary=f"Runtime event loop lag reached {max(0.0, float(lag_ms)):.1f} ms",
        evidence={
            "lag_ms": round(max(0.0, float(lag_ms)), 3),
            "threshold_ms": round(max(0.0, float(threshold_ms)), 3),
            "interval_sec": round(max(0.0, float(interval_sec)), 3),
            "skill_subscription_execution": skill_execution,
            "process_activity_history": process_activity_history_snapshot(limit=8),
        },
        fingerprint_parts=("runtime_event_loop_lag",),
        tags=("event-loop", "latency", "channel-protection", "blocking-evidence"),
    )


def _domain_from_runtime_stack(stack_frames: Iterable[dict[str, Any]]) -> str:
    for frame in reversed(list(stack_frames)):
        filename = str(frame.get("filename") or "").replace("\\", "/")
        match = re.search(r"/(?:workspace/)?skills/(?:\.runtime/)?([^/]+)/", filename, re.IGNORECASE)
        if match:
            skill = match.group(1).strip()
            if skill and skill not in {"handlers", ".runtime"}:
                return f"skill:{skill}"
    for frame in reversed(list(stack_frames)):
        filename = str(frame.get("filename") or "").replace("\\", "/")
        if "/adaos/services/yjs/" in filename:
            return "core.yjs"
        if "/adaos/" in filename:
            return "core.runtime"
    return "core.runtime"


def record_runtime_event_loop_stall(
    *,
    stall_ms: float,
    threshold_ms: float,
    interval_sec: float,
    stack_frames: Iterable[dict[str, Any]],
    loop_thread_id: int,
    watchdog_thread_id: int,
    process_sample: dict[str, Any] | None = None,
    increment_occurrence: bool = True,
) -> dict[str, Any]:
    frames = [dict(frame) for frame in list(stack_frames)[-40:]]
    domain = _domain_from_runtime_stack(frames)
    try:
        from adaos.services.skill.subscription_execution import subscription_execution_snapshot

        skill_execution = subscription_execution_snapshot(limit=10)
    except Exception as exc:
        skill_execution = {"available": False, "error": type(exc).__name__}
    return record_incident(
        incident_class="runtime_event_loop_stall",
        signal="event_loop_unresponsive",
        severity="degraded",
        domain=domain,
        component="runtime_event_loop",
        source="runtime_event_loop_thread_watchdog",
        summary=f"Runtime event loop did not acknowledge a watchdog probe for {max(0.0, float(stall_ms)):.1f} ms",
        evidence={
            "stall_ms": round(max(0.0, float(stall_ms)), 3),
            "threshold_ms": round(max(0.0, float(threshold_ms)), 3),
            "interval_sec": round(max(0.0, float(interval_sec)), 3),
            "loop_thread_id": int(loop_thread_id),
            "watchdog_thread_id": int(watchdog_thread_id),
            "stack_frames": frames,
            "process_sample": process_sample or {},
            "process_activity_history": process_activity_history_snapshot(limit=8),
            "skill_subscription_execution": skill_execution,
        },
        fingerprint_parts=("runtime_event_loop_stall", domain),
        tags=("event-loop", "latency", "channel-protection", "blocking-stack"),
        increment_occurrence=increment_occurrence,
    )


def record_skill_handler_pressure(
    *,
    skill: str,
    topic: str,
    handler: str,
    signal: str,
    duration_s: float | None = None,
    pending: int | None = None,
    threshold_s: float | None = None,
) -> dict[str, Any]:
    skill_token = _clean_token(skill, fallback="unknown")
    signal_token = _clean_token(signal, fallback="execution_pressure")
    degraded_signals = {
        "execution_budget_exceeded",
        "event_loop_stall_circuit_opened",
    }
    evidence = {
        "skill": skill_token,
        "topic": _clean_token(topic),
        "handler": _clean_token(handler),
        "duration_s": round(max(0.0, float(duration_s)), 6) if duration_s is not None else None,
        "threshold_s": round(max(0.0, float(threshold_s)), 6) if threshold_s is not None else None,
        "pending": max(0, int(pending)) if pending is not None else None,
        "process_activity_history": process_activity_history_snapshot(limit=8),
    }
    return record_incident(
        incident_class="skill_handler_pressure",
        signal=signal_token,
        severity="degraded" if signal_token in degraded_signals else "warning",
        domain=f"skill:{skill_token}",
        component="skill_subscription_execution",
        source="skill_subscription_execution",
        summary=f"Skill subscription pressure for {skill_token} on {topic}",
        evidence=evidence,
        fingerprint_parts=("skill_handler_pressure", skill_token, topic, handler, signal_token),
        tags=("skill", "eventbus", "latency", "channel-protection"),
    )


def record_event_handler_crash(*, handler_label: str, event_type: str, exc: BaseException) -> dict[str, Any]:
    if is_yjs_thread_affinity_fault(exc):
        return record_yjs_thread_affinity_fault(
            source="eventbus",
            component="eventbus",
            operation=event_type,
            exc=exc,
            evidence={"handler": handler_label, "event_type": event_type},
        )
    domain = _domain_from_handler_label(handler_label)
    return record_incident(
        incident_class="event_handler_crash",
        signal="event_handler_exception",
        severity="degraded",
        domain=domain,
        component="eventbus",
        source="eventbus",
        summary=f"Event handler crashed for {event_type}",
        evidence={
            "handler": handler_label,
            "event_type": event_type,
            "exception_type": type(exc).__name__,
            "exception": _redact_text(exc, limit=500),
        },
        fingerprint_parts=("event_handler_crash", event_type, handler_label, type(exc).__name__),
        tags=("eventbus", "exception"),
    )


def record_yjs_thread_affinity_fault(
    *,
    source: str,
    component: str = "yjs",
    operation: str | None = None,
    exc: BaseException | str | None = None,
    object_type: str | None = None,
    evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    exception_text = _redact_text(exc, limit=500) if exc is not None else None
    operation_token = _clean_token(operation, fallback="unknown")
    component_token = _clean_token(component, fallback="yjs")
    source_token = _clean_token(source, fallback="runtime")
    return record_incident(
        incident_class="yjs_thread_affinity_fault",
        signal="yjs_thread_affinity_fault",
        severity="degraded",
        domain="core.yjs",
        component=component_token,
        source=source_token,
        summary="Yjs object crossed a thread boundary and state sync must be treated as degraded",
        evidence={
            **(evidence or {}),
            "operation": operation_token,
            "object_type": object_type,
            "exception_type": type(exc).__name__ if isinstance(exc, BaseException) else None,
            "exception": exception_text,
        },
        fingerprint_parts=("yjs_thread_affinity_fault", component_token, source_token, operation_token),
        tags=("yjs", "thread-affinity", "state-sync", "degradation"),
    )


def record_channel_incident(
    *,
    channel: str,
    status: str,
    summary: str,
    details: dict[str, Any] | None = None,
    previous_status: str | None = None,
) -> dict[str, Any]:
    channel_token = _clean_token(channel)
    if channel_token == "route":
        domain = "hub_root_browser"
    elif channel_token == "root_control":
        domain = "hub_root"
    else:
        domain = f"channel:{channel_token}"
    incident_details = dict(details or {})
    impact_scope = _clean_token(incident_details.get("impact_scope"), fallback="channel")
    severity = "degraded" if str(status or "").lower() in {"down", "forced_close_no_upstream"} else "warning"
    return record_incident(
        incident_class="channel_incident",
        signal=str(status or "non_ready").strip() or "non_ready",
        severity=severity,
        domain=domain,
        component=channel_token,
        source="reliability.channel",
        summary=summary,
        evidence={
            "channel": channel_token,
            "status": status,
            "previous_status": previous_status,
            "impact_scope": impact_scope,
            "details": incident_details,
        },
        fingerprint_parts=("channel_incident", channel_token, status, summary, impact_scope),
        tags=("transport", channel_token, impact_scope),
    )


def record_yjs_pressure_incident(
    *,
    pressure: dict[str, Any],
    owner: str | None = None,
    webspace_id: str | None = None,
    source: str = "yjs_pressure",
) -> dict[str, Any] | None:
    data = dict(pressure or {})
    policy = str(data.get("policy_state") or "ok").strip().lower()
    observed = str(data.get("observed_state") or "idle").strip().lower()
    quarantined = bool(data.get("quarantined") or data.get("active"))
    blocked_total = int(data.get("blocked_total") or data.get("suppressed_total") or 0)
    throttled_total = int(data.get("throttled_total") or 0)
    is_idle_pressure = (
        not quarantined
        and not blocked_total
        and not throttled_total
        and policy in {"", "ok"}
        and observed in {"", "idle", "ok"}
    )
    if is_idle_pressure:
        return None
    if quarantined or policy in {"block", "blocked"} or observed in {"critical", "blocked"}:
        severity = "critical"
    elif blocked_total or throttled_total or policy in {"throttle", "throttled"} or observed in {"pressure", "high"}:
        severity = "degraded"
    else:
        severity = "warning"
    route = data.get("last_route") if isinstance(data.get("last_route"), dict) else {}
    projection = data.get("last_projection") if isinstance(data.get("last_projection"), dict) else {}
    path = data.get("last_path") or route.get("path") or projection.get("path") or ""
    domain = incident_domain_from_owner(owner or data.get("owner"), fallback="core.yjs")
    webspace = str(webspace_id or data.get("webspace_id") or "").strip()
    reason = str(data.get("reason") or f"{policy}/{observed}").strip()
    return record_incident(
        incident_class="yjs_pressure",
        signal=f"yjs_pressure_{policy or 'unknown'}_{observed or 'unknown'}",
        severity=severity,
        domain=domain,
        component="yjs",
        source=source,
        summary=f"Yjs pressure {policy or '-'} / {observed or '-'}: {reason}",
        evidence={**data, "webspace_id": webspace or None, "path": path or None},
        fingerprint_parts=("yjs_pressure", domain, webspace, path, policy, observed),
        tags=("yjs", "pressure", "state-sync"),
    )


def record_action_timeout(
    *,
    action_id: str | None = None,
    skill: str | None = None,
    method: str | None = None,
    scenario_id: str | None = None,
    webspace_id: str | None = None,
    route: str | None = None,
    transport: str | None = None,
    timeout_s: float | None = None,
    evidence: dict[str, Any] | None = None,
    source: str = "action.host",
) -> dict[str, Any]:
    method_token = str(method or action_id or "unknown").strip()
    skill_token = str(skill or "").strip()
    if not skill_token and "." in method_token:
        skill_token = method_token.split(".", 1)[0].strip()
    domain = f"skill:{skill_token}" if skill_token else "core.runtime"
    label = str(action_id or method_token or "unknown").strip()
    return record_incident(
        incident_class="action_timeout",
        signal="action_command_timeout",
        severity="degraded",
        domain=domain,
        component="action_host",
        source=source,
        summary=f"Action timed out: {label}",
        evidence={
            **(evidence or {}),
            "action_id": action_id,
            "skill": skill_token or None,
            "method": method_token,
            "scenario_id": scenario_id,
            "webspace_id": webspace_id,
            "route": route,
            "transport": transport,
            "timeout_s": timeout_s,
        },
        fingerprint_parts=("action_timeout", domain, method_token, scenario_id or "", webspace_id or ""),
        tags=("action", "timeout", "transport"),
    )


def record_browser_transport_fallback(
    *,
    channel: str,
    from_transport: str | None = None,
    to_transport: str | None = None,
    reason: str | None = None,
    device_id: str | None = None,
    webspace_id: str | None = None,
    evidence: dict[str, Any] | None = None,
    source: str = "browser.diagnostics",
) -> dict[str, Any]:
    target = str(to_transport or "").strip().lower()
    severity = "degraded" if "http" in target or "relay" in target or "root" in target else "warning"
    domain = f"browser:{device_id}" if str(device_id or "").strip() else "hub_root_browser"
    channel_token = _clean_token(channel, fallback="browser")
    return record_incident(
        incident_class="browser_transport_fallback",
        signal="browser_transport_fallback",
        severity=severity,
        domain=domain,
        component=channel_token,
        source=source,
        summary=(
            f"Browser {channel_token} fallback "
            f"{from_transport or '-'} -> {to_transport or '-'}"
        ),
        evidence={
            **(evidence or {}),
            "channel": channel_token,
            "from_transport": from_transport,
            "to_transport": to_transport,
            "reason": reason,
            "device_id": device_id,
            "webspace_id": webspace_id,
        },
        fingerprint_parts=(
            "browser_transport_fallback",
            channel_token,
            domain,
            from_transport or "",
            to_transport or "",
            reason or "",
        ),
        tags=("browser", "transport", "fallback"),
    )


def record_member_link_stale(
    *,
    node_id: str,
    hostname: str | None = None,
    last_seen_ago_s: float | None = None,
    evidence: dict[str, Any] | None = None,
    source: str = "hub_member_connection_state",
) -> dict[str, Any]:
    node = _clean_token(node_id)
    age = float(last_seen_ago_s or 0.0)
    severity = "degraded" if age >= 300 else "warning"
    return record_incident(
        incident_class="member_link_stale",
        signal="member_link_stale",
        severity=severity,
        domain=f"member:{node}",
        component="member_link",
        source=source,
        summary=f"Member link is stale: {hostname or node}",
        evidence={
            **(evidence or {}),
            "node_id": node,
            "hostname": hostname,
            "last_seen_ago_s": age if last_seen_ago_s is not None else None,
        },
        fingerprint_parts=("member_link_stale", node),
        tags=("member", "transport", "stale"),
    )


def _snapshot_item(item: dict[str, Any], *, now_ts: float, include_evidence: bool) -> dict[str, Any]:
    last_seen = float(item.get("last_seen_at") or 0.0)
    payload = {
        "id": item.get("id"),
        "class": item.get("class"),
        "signal": item.get("signal"),
        "severity": item.get("severity"),
        "domain": item.get("domain"),
        "component": item.get("component"),
        "source": item.get("source"),
        "summary": item.get("summary"),
        "first_seen_at": item.get("first_seen_at"),
        "last_seen_at": last_seen or None,
        "last_seen_ago_s": round(max(0.0, now_ts - last_seen), 3) if last_seen else None,
        "occurrence_count": int(item.get("occurrence_count") or 0),
        "active": bool(last_seen and now_ts - last_seen <= _ACTIVE_WINDOW_S),
        "tags": list(item.get("tags") or []),
        "fingerprint": item.get("fingerprint"),
    }
    if include_evidence:
        payload["latest_evidence"] = _json_safe(item.get("latest_evidence") or {})
        samples = item.get("evidence_samples")
        if isinstance(samples, deque):
            payload["evidence_samples"] = list(samples)
    return payload


def incident_registry_snapshot(*, limit: int = 50, include_evidence: bool = True) -> dict[str, Any]:
    now_ts = _now()
    max_items = max(1, min(int(limit or 50), 200))
    with _LOCK:
        items = [
            _snapshot_item(item, now_ts=now_ts, include_evidence=include_evidence)
            for item in _INCIDENTS.values()
        ]
    items.sort(key=lambda item: float(item.get("last_seen_at") or 0.0), reverse=True)
    selected = items[:max_items]
    active_items = [item for item in items if item.get("active")]
    return {
        "schema": _SCHEMA,
        "available": True,
        "updated_at": now_ts,
        "total": len(items),
        "active_total": len(active_items),
        "returned": len(selected),
        "active_window_s": _ACTIVE_WINDOW_S,
        "counts": {
            "by_class": dict(Counter(str(item.get("class") or "unknown") for item in items)),
            "by_domain": dict(Counter(str(item.get("domain") or "unknown") for item in items)),
            "by_severity": dict(Counter(str(item.get("severity") or "unknown") for item in items)),
        },
        "items": selected,
    }


def default_incident_registry_path() -> Path:
    raw = str(os.environ.get("ADAOS_INCIDENT_REGISTRY_PATH") or "").strip()
    if raw:
        return Path(raw).expanduser()
    state_dir = str(os.environ.get("ADAOS_STATE_DIR") or os.environ.get("ADAOS_HOME") or "").strip()
    if state_dir:
        return Path(state_dir).expanduser() / "incident_registry.json"
    return Path(".adaos") / "state" / "incident_registry.json"


def persist_incident_registry(*, path: str | Path | None = None, limit: int = 200) -> dict[str, Any]:
    target = Path(path).expanduser() if path is not None else default_incident_registry_path()
    snapshot = incident_registry_snapshot(limit=limit, include_evidence=True)
    written_at = _now()
    payload = {
        "schema": _PERSIST_SCHEMA,
        "written_at": written_at,
        "snapshot": snapshot,
    }
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp")
    tmp.write_text(json.dumps(_json_safe(payload), ensure_ascii=True, sort_keys=True), encoding="utf-8")
    tmp.replace(target)
    return {
        "ok": True,
        "path": str(target),
        "written_at": written_at,
        "total": snapshot.get("total"),
        "returned": snapshot.get("returned"),
    }


def load_incident_registry(
    *,
    path: str | Path | None = None,
    ttl_s: float = 24 * 60 * 60,
    replace: bool = False,
    limit: int = 200,
) -> dict[str, Any]:
    target = Path(path).expanduser() if path is not None else default_incident_registry_path()
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {"ok": False, "path": str(target), "loaded": 0, "error": "not_found"}
    except Exception as exc:
        return {"ok": False, "path": str(target), "loaded": 0, "error": type(exc).__name__}

    snapshot = payload.get("snapshot") if isinstance(payload, dict) else {}
    items = snapshot.get("items") if isinstance(snapshot, dict) else []
    if not isinstance(items, list):
        return {"ok": False, "path": str(target), "loaded": 0, "error": "invalid_snapshot"}

    now_ts = _now()
    max_age = max(0.0, float(ttl_s or 0.0))
    selected: list[dict[str, Any]] = []
    for raw in items[: max(1, min(int(limit or 200), 200))]:
        if not isinstance(raw, dict):
            continue
        last_seen = float(raw.get("last_seen_at") or 0.0)
        if max_age and last_seen and now_ts - last_seen > max_age:
            continue
        selected.append(raw)

    with _LOCK:
        if replace:
            _INCIDENTS.clear()
            _ORDER.clear()
        for raw in selected:
            fingerprint = str(raw.get("fingerprint") or "").strip()
            if not fingerprint:
                fingerprint = _fingerprint(
                    (
                        raw.get("class") or "unknown",
                        raw.get("signal") or "unknown",
                        raw.get("domain") or "core.runtime",
                        raw.get("component") or "",
                    )
                )
            samples = raw.get("evidence_samples") if isinstance(raw.get("evidence_samples"), list) else []
            last_seen = float(raw.get("last_seen_at") or now_ts)
            _INCIDENTS[fingerprint] = {
                "id": str(raw.get("id") or f"inc-{fingerprint[:12]}"),
                "fingerprint": fingerprint,
                "class": _clean_token(raw.get("class"), fallback="unknown"),
                "signal": _clean_token(raw.get("signal"), fallback="unknown"),
                "severity": _clean_token(raw.get("severity"), fallback="warning").lower(),
                "domain": _clean_token(raw.get("domain"), fallback="core.runtime"),
                "component": str(raw.get("component") or "").strip() or None,
                "source": str(raw.get("source") or "").strip() or None,
                "summary": str(raw.get("summary") or raw.get("signal") or "incident").strip(),
                "first_seen_at": float(raw.get("first_seen_at") or last_seen),
                "last_seen_at": last_seen,
                "occurrence_count": max(1, int(raw.get("occurrence_count") or 1)),
                "tags": [str(item) for item in (raw.get("tags") or []) if str(item or "").strip()],
                "latest_evidence": _json_safe(raw.get("latest_evidence") or {}),
                "evidence_samples": deque(samples[-_MAX_EVIDENCE_SAMPLES:], maxlen=_MAX_EVIDENCE_SAMPLES),
            }
            if fingerprint not in _ORDER:
                _ORDER.append(fingerprint)
        while len(_INCIDENTS) > _MAX_INCIDENTS and _ORDER:
            old = _ORDER.popleft()
            _INCIDENTS.pop(old, None)

    return {"ok": True, "path": str(target), "loaded": len(selected), "replace": bool(replace)}


def reset_incident_registry() -> None:
    global _PROCESS_ACTIVITY_PREVIOUS_AT
    with _LOCK:
        _INCIDENTS.clear()
        _ORDER.clear()
        _PROCESS_ACTIVITY_HISTORY.clear()
        _PROCESS_ACTIVITY_PREVIOUS.clear()
        _PROCESS_ACTIVITY_PREVIOUS_SYSTEM.clear()
        _PROCESS_ACTIVITY_IDENTITY_CACHE.clear()
        _PROCESS_ACTIVITY_PREVIOUS_AT = None


__all__ = [
    "capture_process_activity_sample",
    "default_incident_registry_path",
    "incident_domain_from_owner",
    "incident_registry_snapshot",
    "is_yjs_thread_affinity_fault",
    "local_blocking_evidence",
    "latest_process_activity_sample",
    "load_incident_registry",
    "persist_incident_registry",
    "process_io_delta_sample",
    "process_activity_history_snapshot",
    "record_action_timeout",
    "record_browser_transport_fallback",
    "record_channel_incident",
    "record_event_handler_crash",
    "record_incident",
    "record_hub_root_transport_incident",
    "record_member_link_stale",
    "record_runtime_api_timeout",
    "record_runtime_event_loop_lag",
    "record_runtime_event_loop_stall",
    "record_slow_event_handler",
    "record_skill_handler_pressure",
    "record_yjs_thread_affinity_fault",
    "record_yjs_pressure_incident",
    "reset_incident_registry",
]
