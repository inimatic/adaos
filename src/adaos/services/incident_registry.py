from __future__ import annotations

import hashlib
import re
import time
from collections import Counter, deque
from threading import RLock
from typing import Any, Iterable


_SCHEMA = "adaos.incident_registry.v1"
_MAX_INCIDENTS = 256
_MAX_EVIDENCE_SAMPLES = 3
_ACTIVE_WINDOW_S = 10 * 60
_LOCK = RLock()
_INCIDENTS: dict[str, dict[str, Any]] = {}
_ORDER: deque[str] = deque(maxlen=_MAX_INCIDENTS)

_SECRET_PATTERNS = (
    re.compile(r"(?i)(token|authorization|password|secret|key)=([^&\s]+)"),
    re.compile(r"(?i)(bearer\s+)[a-z0-9._~+/=-]+"),
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
    if depth >= 5:
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


def _domain_from_cmdline(cmdline: str) -> str:
    text = str(cmdline or "")
    marker = "/.adaos/workspace/skills/.runtime/"
    if marker in text:
        tail = text.split(marker, 1)[1]
        skill = tail.split("/", 1)[0].strip()
        if skill:
            return f"skill:{skill}"
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


def _process_samples(limit: int = 8) -> dict[str, Any]:
    try:
        import psutil  # type: ignore
    except Exception:
        return {}

    rows: list[dict[str, Any]] = []
    for proc in psutil.process_iter(["pid", "name", "cmdline", "status", "memory_info"]):
        try:
            info = proc.info
            cmdline_items = info.get("cmdline") or []
            cmdline = " ".join(str(item) for item in cmdline_items)
            if not cmdline:
                cmdline = str(info.get("name") or "")
            io = proc.io_counters() if hasattr(proc, "io_counters") else None
            mem = info.get("memory_info")
            rss = int(getattr(mem, "rss", 0) or 0)
            read_bytes = int(getattr(io, "read_bytes", 0) or 0) if io is not None else 0
            write_bytes = int(getattr(io, "write_bytes", 0) or 0) if io is not None else 0
            rows.append(
                {
                    "pid": int(info.get("pid") or proc.pid),
                    "name": _redact_text(info.get("name") or "", limit=80),
                    "status": _redact_text(info.get("status") or "", limit=40),
                    "rss_bytes": rss,
                    "read_bytes": read_bytes,
                    "write_bytes": write_bytes,
                    "domain": _domain_from_cmdline(cmdline),
                    "cmdline": _redact_text(cmdline, limit=220),
                }
            )
        except Exception:
            continue

    top_rss = sorted(rows, key=lambda item: int(item.get("rss_bytes") or 0), reverse=True)[:limit]
    top_write = sorted(rows, key=lambda item: int(item.get("write_bytes") or 0), reverse=True)[:limit]
    return {
        "process_total": len(rows),
        "top_rss": top_rss,
        "top_write_bytes": top_write,
    }


def local_blocking_evidence(*, include_processes: bool = True) -> dict[str, Any]:
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
    merged = {
        "path": path,
        "timeout_s": float(timeout_s),
        "exception_type": kind,
        "exception": _redact_text(exc, limit=500),
        **(evidence or {}),
        **local_blocking_evidence(include_processes=True),
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
        },
        fingerprint_parts=("slow_event_handler", kind, event_type, handler_label),
        tags=("eventbus", "latency"),
    )


def record_event_handler_crash(*, handler_label: str, event_type: str, exc: BaseException) -> dict[str, Any]:
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


def record_channel_incident(
    *,
    channel: str,
    status: str,
    summary: str,
    details: dict[str, Any] | None = None,
    previous_status: str | None = None,
) -> dict[str, Any]:
    channel_token = _clean_token(channel)
    domain = "hub_root_browser" if channel_token == "route" else "hub_root" if channel_token == "root_control" else f"channel:{channel_token}"
    severity = "degraded" if str(status or "").lower() in {"down", "forced_close_no_upstream"} else "warning"
    return record_incident(
        incident_class="channel_transition",
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
            "details": details or {},
        },
        fingerprint_parts=("channel_transition", channel_token, status, summary),
        tags=("transport", channel_token),
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


def reset_incident_registry() -> None:
    with _LOCK:
        _INCIDENTS.clear()
        _ORDER.clear()


__all__ = [
    "incident_registry_snapshot",
    "local_blocking_evidence",
    "record_channel_incident",
    "record_event_handler_crash",
    "record_incident",
    "record_runtime_api_timeout",
    "record_slow_event_handler",
    "reset_incident_registry",
]
