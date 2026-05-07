from __future__ import annotations

import asyncio
import json
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from adaos.services.agent_context import get_ctx
from adaos.services.root_mcp.logs import aggregate_subnet_logs, list_local_logs
from adaos.services.runtime_paths import current_state_dir


SNAPSHOT_VERSION = 1
LOG_CATEGORIES = ("adaos", "events", "yjs", "skills")
SECRET_KEY_RE = re.compile(r"(token|secret|password|passwd|authorization|auth|jwt|session)", re.IGNORECASE)
JWT_RE = re.compile(r"\beyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\b")
QUERY_SECRET_RE = re.compile(r"([?&](?:access_token|auth|session_jwt|token|jwt|password|secret)=)[^&\s]+", re.IGNORECASE)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _ts_to_iso(value: Any) -> str | None:
    try:
        ts = float(value)
        if ts <= 0:
            return None
        return datetime.fromtimestamp(ts, timezone.utc).isoformat().replace("+00:00", "Z")
    except Exception:
        return None


def _snapshot_id(now: float | None = None) -> str:
    stamp = datetime.fromtimestamp(float(now or time.time()), timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"360log-{stamp}-{uuid.uuid4().hex[:8]}"


def _snapshot_root() -> Path:
    root = current_state_dir() / "diag360" / "snapshots"
    root.mkdir(parents=True, exist_ok=True)
    return root


def snapshot_path(snapshot_id: str) -> Path:
    token = str(snapshot_id or "").strip()
    if not token or "/" in token or "\\" in token or token in {".", ".."}:
        raise ValueError("invalid_snapshot_id")
    return (_snapshot_root() / token / "snapshot.json").resolve()


def _redact_string(value: str) -> str:
    text = QUERY_SECRET_RE.sub(r"\1redacted", value)
    text = JWT_RE.sub("redacted.jwt", text)
    return text


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, child in value.items():
            key_text = str(key)
            out[key_text] = "redacted" if SECRET_KEY_RE.search(key_text) else redact(child)
        return out
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, str):
        return _redact_string(value)
    return value


def _read_json_file(path: Path) -> dict[str, Any] | None:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {"value": payload}
    except Exception:
        return None


def _read_state_files() -> dict[str, Any]:
    state_dir = current_state_dir()
    files = {
        "node_runtime": state_dir / "node_runtime.json",
        "supervisor_runtime": state_dir / "supervisor" / "runtime.json",
        "core_update_status": state_dir / "core_update" / "status.json",
        "core_update_last_result": state_dir / "core_update" / "last_result.json",
        "core_slots_status": state_dir / "core_slots" / "status.json",
    }
    out: dict[str, Any] = {}
    for name, path in files.items():
        payload = _read_json_file(path)
        out[name] = {
            "available": payload is not None,
            "path": str(path),
            "payload": redact(payload) if payload is not None else None,
        }
    return out


def _try_runtime_snapshots(webspace_id: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    try:
        from adaos.services.reliability import reliability_snapshot

        out["reliability"] = redact(reliability_snapshot(webspace_id=webspace_id))
    except Exception as exc:
        out["reliability"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from adaos.services.yjs.store import ystore_runtime_snapshot

        out["yjs_store"] = redact(ystore_runtime_snapshot(webspace_id=webspace_id))
    except Exception as exc:
        out["yjs_store"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    try:
        from adaos.services.scenario.webspace_runtime import member_snapshot_rebuild_runtime_snapshot

        out["member_snapshot_rebuild"] = redact(member_snapshot_rebuild_runtime_snapshot(limit=25))
    except Exception as exc:
        out["member_snapshot_rebuild"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    return out


def _parse_log_line(line: str) -> dict[str, Any] | None:
    text = str(line or "").strip()
    if not text:
        return None
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return None


def _event_ts(payload: dict[str, Any] | None, fallback: float | None) -> float:
    if payload:
        for key in ("ts", "time_ts", "timestamp"):
            try:
                value = float(payload.get(key))
                if value > 0:
                    return value
            except Exception:
                pass
        raw_time = str(payload.get("time") or payload.get("@timestamp") or "").strip()
        if raw_time:
            try:
                return datetime.fromisoformat(raw_time.replace("Z", "+00:00")).timestamp()
            except Exception:
                pass
    try:
        if fallback:
            return float(fallback)
    except Exception:
        pass
    return time.time()


def _line_event(
    *,
    category: str,
    source: str,
    node_id: str | None,
    item: dict[str, Any],
    line: str,
    index: int,
) -> dict[str, Any]:
    parsed = _parse_log_line(line)
    ts = _event_ts(parsed, item.get("modified_at"))
    level = "info"
    event = category
    message = str(line or "").strip()
    details: dict[str, Any] = {}
    if parsed:
        level = str(parsed.get("level") or parsed.get("severity") or "info").strip().lower() or "info"
        event = str(parsed.get("type") or parsed.get("event") or parsed.get("logger") or category).strip() or category
        message = str(parsed.get("msg") or parsed.get("message") or event).strip() or event
        details = redact(parsed)
    return {
        "ts": _ts_to_iso(ts),
        "ts_epoch": ts,
        "observed_at": _utc_now_iso(),
        "source": source,
        "node_id": node_id,
        "scope": category,
        "level": level,
        "event": event,
        "message": _redact_string(message),
        "details": details,
        "raw": {"line": _redact_string(line)} if not parsed else {"payload": details},
        "raw_ref": {
            "kind": "log_tail",
            "path": item.get("path"),
            "file": item.get("name"),
            "tail_index": index,
        },
        "lossiness": "none_except_redaction",
    }


def _source_for_log_item(category: str, item: dict[str, Any], *, node_id: str | None = None) -> str:
    name = str(item.get("name") or "").strip()
    if category == "skills" and name.startswith("service.") and name.endswith(".log"):
        return f"skill.{name[len('service.'):-len('.log')]}"
    if category == "events":
        return "runtime.events"
    if category == "yjs":
        return "runtime.yjs"
    return "runtime.log"


def _timeline_from_local_logs(logs_by_category: dict[str, Any], *, node_id: str | None) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for category, payload in logs_by_category.items():
        items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            tail = item.get("tail") if isinstance(item.get("tail"), list) else []
            source = _source_for_log_item(category, item, node_id=node_id)
            for index, line in enumerate(tail):
                events.append(
                    _line_event(
                        category=category,
                        source=source,
                        node_id=node_id,
                        item=item,
                        line=str(line),
                        index=index,
                    )
                )
    return events


def _timeline_from_subnet_logs(payloads: dict[str, Any]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for category, aggregate in payloads.items():
        nodes = aggregate.get("nodes") if isinstance(aggregate, dict) and isinstance(aggregate.get("nodes"), list) else []
        for node in nodes:
            if not isinstance(node, dict):
                continue
            node_id = str(node.get("node_id") or "").strip() or None
            logs = node.get("logs") if isinstance(node.get("logs"), dict) else {}
            items = logs.get("items") if isinstance(logs.get("items"), list) else []
            for item in items:
                if not isinstance(item, dict):
                    continue
                source = _source_for_log_item(category, item, node_id=node_id)
                tail = item.get("tail") if isinstance(item.get("tail"), list) else []
                for index, line in enumerate(tail):
                    event = _line_event(
                        category=category,
                        source=source,
                        node_id=node_id,
                        item=item,
                        line=str(line),
                        index=index,
                    )
                    event["raw_ref"]["node_id"] = node_id
                    event["raw_ref"]["node_source"] = node.get("source")
                    events.append(event)
    return events


def _read_browser_debug_export(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="ignore")
    except Exception as exc:
        return {"available": False, "path": str(path), "error": f"{type(exc).__name__}: {exc}"}, []
    records: list[Any] = []
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            records = parsed
        elif isinstance(parsed, dict):
            records = parsed.get("items") if isinstance(parsed.get("items"), list) else [parsed]
    except Exception:
        records = [_parse_log_line(line) or {"message": line} for line in raw.splitlines() if line.strip()]
    events: list[dict[str, Any]] = []
    for index, record in enumerate(records):
        payload = record if isinstance(record, dict) else {"message": str(record)}
        ts = _event_ts(payload, None)
        event = str(payload.get("kind") or payload.get("event") or payload.get("type") or "browser.debug").strip()
        message = str(payload.get("message") or event).strip()
        events.append(
            {
                "ts": _ts_to_iso(ts),
                "ts_epoch": ts,
                "observed_at": _utc_now_iso(),
                "source": "browser",
                "node_id": payload.get("node_id"),
                "webspace_id": payload.get("webspace_id"),
                "scope": "browser",
                "level": str(payload.get("level") or "info").strip().lower() or "info",
                "event": event,
                "message": _redact_string(message),
                "details": redact(payload),
                "raw": {"payload": redact(payload)},
                "raw_ref": {"kind": "browser_debug_export", "path": str(path), "index": index},
                "lossiness": "none_except_redaction",
            }
        )
    return {"available": True, "path": str(path), "items_total": len(records)}, events


async def _collect_logs(
    *,
    scope: str,
    subnet_id: str | None,
    lines: int,
    files: int,
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    logs: dict[str, Any] = {}
    timeline: list[dict[str, Any]] = []
    ctx = get_ctx()
    cfg = getattr(ctx, "config", None)
    node_id = str(getattr(cfg, "node_id", None) or "").strip() or None
    role = str(getattr(cfg, "role", None) or "").strip().lower()
    effective_scope = scope
    if scope == "auto":
        effective_scope = "subnet" if role == "hub" and (subnet_id or getattr(cfg, "subnet_id", None)) else "local"
    if effective_scope == "subnet":
        effective_subnet_id = str(subnet_id or getattr(cfg, "subnet_id", None) or "").strip()
        if not effective_subnet_id:
            logs["subnet"] = {"available": False, "error": "subnet_id_missing"}
            return logs, timeline
        subnet_payloads: dict[str, Any] = {}
        for category in LOG_CATEGORIES:
            try:
                subnet_payloads[category] = redact(
                    await aggregate_subnet_logs(
                        category=category,
                        subnet_id=effective_subnet_id,
                        limit=files,
                        lines=lines,
                        include_hub=True,
                        timeout=timeout,
                    )
                )
            except Exception as exc:
                subnet_payloads[category] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
        logs["subnet"] = subnet_payloads
        timeline.extend(_timeline_from_subnet_logs(subnet_payloads))
        return logs, timeline

    local_payloads: dict[str, Any] = {}
    for category in LOG_CATEGORIES:
        try:
            local_payloads[category] = redact(list_local_logs(category=category, limit=files, lines=lines))
        except Exception as exc:
            local_payloads[category] = {"available": False, "error": f"{type(exc).__name__}: {exc}"}
    logs["local"] = local_payloads
    timeline.extend(_timeline_from_local_logs(local_payloads, node_id=node_id))
    return logs, timeline


def _write_jsonl(path: Path, items: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as fh:
        for item in items:
            fh.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")


def _run_collect_logs_sync(
    *,
    scope: str,
    subnet_id: str | None,
    lines: int,
    files: int,
    timeout: float,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    coro = _collect_logs(scope=scope, subnet_id=subnet_id, lines=lines, files=files, timeout=timeout)
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    result: dict[str, Any] = {}
    error: list[BaseException] = []

    def _runner() -> None:
        try:
            logs, timeline = asyncio.run(coro)
            result["logs"] = logs
            result["timeline"] = timeline
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=_runner, name="adaos-diag360-collector", daemon=True)
    thread.start()
    thread.join()
    if error:
        raise error[0]
    return (
        result.get("logs") if isinstance(result.get("logs"), dict) else {},
        result.get("timeline") if isinstance(result.get("timeline"), list) else [],
    )


def create_360log_snapshot(
    *,
    reason: str | None = None,
    scope: str = "auto",
    subnet_id: str | None = None,
    webspace_id: str | None = "desktop",
    lines: int = 300,
    files: int = 8,
    timeout: float = 2.0,
    browser_log_path: Path | None = None,
) -> dict[str, Any]:
    now = time.time()
    sid = _snapshot_id(now)
    out_dir = (_snapshot_root() / sid).resolve()
    out_dir.mkdir(parents=True, exist_ok=False)
    safe_lines = max(1, min(int(lines), 2000))
    safe_files = max(1, min(int(files), 50))
    logs, timeline = _run_collect_logs_sync(
        scope=str(scope or "auto").strip().lower(),
        subnet_id=subnet_id,
        lines=safe_lines,
        files=safe_files,
        timeout=max(0.2, min(float(timeout), 15.0)),
    )
    browser_source: dict[str, Any] = {
        "available": False,
        "reason": "browser runtime debug lives in browser storage unless an export path is provided",
    }
    if browser_log_path is not None:
        browser_source, browser_events = _read_browser_debug_export(browser_log_path)
        timeline.extend(browser_events)
    timeline.sort(key=lambda item: float(item.get("ts_epoch") or 0.0))
    for item in timeline:
        item.pop("ts_epoch", None)

    ctx = get_ctx()
    cfg = getattr(ctx, "config", None)
    snapshot = {
        "ok": True,
        "snapshot_id": sid,
        "version": SNAPSHOT_VERSION,
        "created_at": _ts_to_iso(now),
        "reason": str(reason or "").strip() or None,
        "scope": str(scope or "auto").strip().lower(),
        "node": {
            "node_id": str(getattr(cfg, "node_id", "") or "").strip() or None,
            "subnet_id": str(getattr(cfg, "subnet_id", "") or "").strip() or None,
            "role": str(getattr(cfg, "role", "") or "").strip() or None,
        },
        "query": {
            "lines": safe_lines,
            "files": safe_files,
            "timeout": max(0.2, min(float(timeout), 15.0)),
            "webspace_id": webspace_id,
            "subnet_id": subnet_id,
            "browser_log_path": str(browser_log_path) if browser_log_path else None,
        },
        "artifacts": {
            "snapshot_json": str(out_dir / "snapshot.json"),
            "timeline_jsonl": str(out_dir / "timeline.jsonl"),
        },
        "sources": {
            "logs": logs,
            "browser": browser_source,
            "state": _read_state_files(),
            "runtime": _try_runtime_snapshots(webspace_id),
        },
        "timeline": {
            "items_total": len(timeline),
            "items": timeline,
        },
        "redaction": {
            "enabled": True,
            "policy": "keys matching token/secret/password/auth/jwt/session and JWT/query-secret string patterns are redacted",
        },
    }
    (out_dir / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_jsonl(out_dir / "timeline.jsonl", timeline)
    return {
        "ok": True,
        "snapshot_id": sid,
        "path": str(out_dir / "snapshot.json"),
        "timeline_path": str(out_dir / "timeline.jsonl"),
        "items_total": len(timeline),
    }


def list_360log_snapshots(limit: int = 20) -> list[dict[str, Any]]:
    root = _snapshot_root()
    items: list[dict[str, Any]] = []
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        path = entry / "snapshot.json"
        if not path.exists():
            continue
        stat = path.stat()
        items.append({"snapshot_id": entry.name, "path": str(path), "modified_at": stat.st_mtime, "size_bytes": stat.st_size})
    items.sort(key=lambda item: float(item.get("modified_at") or 0.0), reverse=True)
    return items[: max(1, min(int(limit), 100))]


def load_360log_snapshot(snapshot_id: str) -> dict[str, Any]:
    path = snapshot_path(snapshot_id)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("invalid_snapshot_payload")
    return payload


def view_360log_snapshot(
    snapshot_id: str,
    *,
    include_timeline: bool = True,
    timeline_limit: int = 300,
    include_sources: bool = True,
) -> dict[str, Any]:
    payload = load_360log_snapshot(snapshot_id)
    if not include_sources:
        payload["sources"] = {"omitted": True}
    timeline = payload.get("timeline") if isinstance(payload.get("timeline"), dict) else {}
    items = timeline.get("items") if isinstance(timeline.get("items"), list) else []
    if not include_timeline:
        payload["timeline"] = {
            "items_total": timeline.get("items_total", len(items)),
            "items": [],
            "omitted": True,
        }
        return payload
    limit = max(1, min(int(timeline_limit), 2000))
    if len(items) > limit:
        payload["timeline"] = {
            **timeline,
            "items_total": timeline.get("items_total", len(items)),
            "items": items[-limit:],
            "truncated": True,
            "returned": limit,
        }
    return payload
