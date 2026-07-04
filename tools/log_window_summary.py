from __future__ import annotations

import argparse
import json
import math
import re
import time
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]

DEFAULT_LOGS = [
    ".adaos/logs/adaos.log",
    ".adaos/logs/events.log",
    ".adaos/logs/service.__ui_runtime__.ui_runtime.log",
    ".adaos/logs/service.notebook_skill.runtime.log",
    ".adaos/logs/service.infrastate_skill.runtime.log",
    ".adaos/logs/service.weather_skill.runtime.log",
    ".adaos/logs/service.ai_event_analysis_skill.runtime.log",
    ".adaos/logs/service.browsers_skill.runtime.log",
    ".adaos/logs/service.conversation_companions.runtime.log",
    ".adaos/logs/service.cv_descriptor.runtime.log",
    ".adaos/diagnostics/realtime_sidecar.log",
]

SLOW_HANDLER_RE = re.compile(
    r"slow async event handler handler=(?P<handler>.*?) type=(?P<topic>\S+) duration=(?P<duration>[0-9.]+)s"
)
EVENT_LOOP_LAG_RE = re.compile(r"event loop lag.*(?:duration|lag)[_=](?P<duration>[0-9.]+)s")
BACKLOG_RE = re.compile(r"eventbus backlog")

MARKERS = {
    "event_loop_lag": ["event loop lag"],
    "slow_handler": ["slow async event handler"],
    "eventbus_backlog": ["eventbus backlog"],
    "stream_snapshot_requested": ["webio.stream.snapshot.requested"],
    "stream_subscription_changed": ["webio.stream.subscription.changed"],
    "stream_publish": ["io.out.stream.publish"],
    "yjs_projection_amplification": ["YJS projection write amplification"],
    "yjs_projection_suppressed": ["YJS projection write suppressed"],
    "blocked_yroom_update": ["blocked backend YRoom update"],
    "yroom_repair": ["YRoom effective branches repaired"],
    "yws_reconnect_storm": ["yws reconnect storm"],
    "peer_failed": ["connectionState=failed", "peer failed"],
    "stale_peer": ["timed out closing stale peer"],
    "weather_observer_slow": ["weather observer slow"],
    "weather_zone_proxy_missing": ["zone_proxy_proxy_not_configured"],
    "weather_read_timeout": ["read_timeout"],
    "rtc_ice_timeout": ["rtc.ice timeout"],
    "rtc_offer_timeout": ["rtc.offer timeout"],
    "rtc_dc_open_timeout": ["dc_open_timeout"],
    "rtc_wrong_state": ["setRemoteDescription wrong state"],
    "yjs_red": ["yjs_status=red", "status=red"],
    "yjs_first_sync_timeout": ["first_sync.timeout", "upgrade_first_sync_timeout"],
    "provider_disconnected": ["provider_disconnected"],
}


@dataclass
class SlowBucket:
    count: int = 0
    total_s: float = 0.0
    max_s: float = 0.0

    def add(self, duration_s: float) -> None:
        self.count += 1
        self.total_s += duration_s
        self.max_s = max(self.max_s, duration_s)

    def to_json(self) -> dict[str, Any]:
        return {
            "count": self.count,
            "total_s": round(self.total_s, 3),
            "max_s": round(self.max_s, 3),
        }


@dataclass
class Summary:
    label: str
    started_at_wall: str
    duration_s: float
    files: Counter[str] = field(default_factory=Counter)
    levels: Counter[str] = field(default_factory=Counter)
    topics: Counter[str] = field(default_factory=Counter)
    loggers: Counter[str] = field(default_factory=Counter)
    markers: Counter[str] = field(default_factory=Counter)
    ui_codes: Counter[str] = field(default_factory=Counter)
    devices: Counter[str] = field(default_factory=Counter)
    webspaces: Counter[str] = field(default_factory=Counter)
    slow_by_handler: dict[str, SlowBucket] = field(default_factory=lambda: defaultdict(SlowBucket))
    slow_by_topic: dict[str, SlowBucket] = field(default_factory=lambda: defaultdict(SlowBucket))
    recent_examples: list[dict[str, Any]] = field(default_factory=list)

    def add_example(self, file_name: str, line_no: int, text: str) -> None:
        if len(self.recent_examples) >= 30:
            self.recent_examples.pop(0)
        self.recent_examples.append(
            {
                "file": file_name,
                "line": line_no,
                "text": text[:500],
            }
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "started_at_wall": self.started_at_wall,
            "duration_s": round(self.duration_s, 3),
            "files": dict(self.files.most_common()),
            "levels": dict(self.levels.most_common()),
            "topics": dict(self.topics.most_common(40)),
            "loggers": dict(self.loggers.most_common(40)),
            "markers": dict(self.markers.most_common()),
            "ui_codes": dict(self.ui_codes.most_common(40)),
            "devices": dict(self.devices.most_common(20)),
            "webspaces": dict(self.webspaces.most_common(20)),
            "slow_by_handler": {
                key: bucket.to_json()
                for key, bucket in sorted(
                    self.slow_by_handler.items(),
                    key=lambda item: item[1].total_s,
                    reverse=True,
                )[:30]
            },
            "slow_by_topic": {
                key: bucket.to_json()
                for key, bucket in sorted(
                    self.slow_by_topic.items(),
                    key=lambda item: item[1].total_s,
                    reverse=True,
                )[:30]
            },
            "recent_examples": self.recent_examples[-20:],
        }


def parse_ts(value: Any) -> float | None:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str) and value.strip():
        text = value.strip()
        try:
            return float(text)
        except ValueError:
            pass
        try:
            return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return None


def iter_dict_values(value: Any) -> Iterable[tuple[str, Any]]:
    if isinstance(value, dict):
        for key, item in value.items():
            yield str(key), item
            yield from iter_dict_values(item)
    elif isinstance(value, list):
        for item in value:
            yield from iter_dict_values(item)


def field_counter(obj: dict[str, Any], names: set[str]) -> Counter[str]:
    out: Counter[str] = Counter()
    for key, value in iter_dict_values(obj):
        if key in names and value not in (None, ""):
            out[str(value)] += 1
    return out


def text_for(obj: dict[str, Any] | None, raw_line: str) -> str:
    if not obj:
        return raw_line
    parts: list[str] = []
    for key in ("msg", "message", "topic", "kind", "code", "logger", "level"):
        value = obj.get(key)
        if value not in (None, ""):
            parts.append(str(value))
    if not parts:
        parts.append(raw_line)
    return " | ".join(parts)


def analyze_line(summary: Summary, file_name: str, line_no: int, line: str) -> None:
    raw = line.strip()
    if not raw:
        return
    obj: dict[str, Any] | None = None
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            obj = parsed
    except json.JSONDecodeError:
        obj = None

    summary.files[file_name] += 1
    if obj:
        level = str(obj.get("level") or "").upper()
        if level:
            summary.levels[level] += 1
        topic = str(obj.get("topic") or "").strip()
        if topic:
            summary.topics[topic] += 1
        logger = str(obj.get("logger") or "").strip()
        if logger:
            summary.loggers[logger] += 1
        code = str(obj.get("code") or obj.get("kind") or "").strip()
        if code:
            summary.ui_codes[code] += 1
        summary.devices.update(field_counter(obj, {"device_id", "browser_device_id", "client_device_id"}))
        summary.webspaces.update(field_counter(obj, {"webspace_id", "workspace_id"}))

    text = text_for(obj, raw)
    for marker, needles in MARKERS.items():
        if any(needle in raw or needle in text for needle in needles):
            summary.markers[marker] += 1
            if marker not in {"stream_publish", "stream_snapshot_requested", "stream_subscription_changed"}:
                summary.add_example(file_name, line_no, raw)

    match = SLOW_HANDLER_RE.search(raw)
    if match:
        duration_s = float(match.group("duration"))
        handler = match.group("handler").strip()
        topic = match.group("topic").strip()
        summary.slow_by_handler[handler].add(duration_s)
        summary.slow_by_topic[topic].add(duration_s)
        summary.add_example(file_name, line_no, raw)
        return

    match = EVENT_LOOP_LAG_RE.search(raw)
    if match:
        summary.add_example(file_name, line_no, raw)
    elif BACKLOG_RE.search(raw):
        summary.add_example(file_name, line_no, raw)


def parse_offsets(paths: list[Path]) -> dict[Path, int]:
    offsets: dict[Path, int] = {}
    for path in paths:
        try:
            offsets[path] = path.stat().st_size
        except FileNotFoundError:
            offsets[path] = 0
    return offsets


def read_new_lines(path: Path, offset: int) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    size = path.stat().st_size
    start = offset if size >= offset else 0
    lines: list[tuple[int, str]] = []
    with path.open("rb") as handle:
        handle.seek(start)
        raw = handle.read()
    text = raw.decode("utf-8", errors="replace")
    base_line = 0
    if start > 0:
        with path.open("rb") as handle:
            chunk_size = 1024 * 1024
            remaining = start
            while remaining > 0:
                chunk = handle.read(min(chunk_size, remaining))
                if not chunk:
                    break
                base_line += chunk.count(b"\n")
                remaining -= len(chunk)
    for index, line in enumerate(text.splitlines(), start=base_line + 1):
        lines.append((index, line))
    return lines


def read_last_window(path: Path, since_ts: float) -> list[tuple[int, str]]:
    if not path.exists():
        return []
    rows: list[tuple[int, str]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line_no, line in enumerate(handle, start=1):
            raw = line.strip()
            if not raw:
                continue
            try:
                obj = json.loads(raw)
            except json.JSONDecodeError:
                obj = None
            ts = None
            if isinstance(obj, dict):
                ts = parse_ts(obj.get("ts")) or parse_ts(obj.get("time"))
            if ts is not None and ts >= since_ts:
                rows.append((line_no, line))
    return rows


def resolve_logs(extra_logs: list[str]) -> list[Path]:
    paths: list[Path] = []
    for token in [*DEFAULT_LOGS, *extra_logs]:
        path = Path(token)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            paths.append(path)
    return paths


def render_text(payload: dict[str, Any]) -> str:
    lines = [
        f"label: {payload['label']}",
        f"started_at_wall: {payload['started_at_wall']}",
        f"duration_s: {payload['duration_s']}",
        "",
        "files:",
    ]
    for key, value in payload["files"].items():
        lines.append(f"  {key}: {value}")
    lines.append("")
    lines.append("markers:")
    for key, value in payload["markers"].items():
        lines.append(f"  {key}: {value}")
    lines.append("")
    lines.append("slow_by_topic:")
    for key, value in payload["slow_by_topic"].items():
        lines.append(f"  {key}: count={value['count']} total_s={value['total_s']} max_s={value['max_s']}")
    lines.append("")
    lines.append("slow_by_handler:")
    for key, value in payload["slow_by_handler"].items():
        lines.append(f"  {key}: count={value['count']} total_s={value['total_s']} max_s={value['max_s']}")
    if payload.get("ui_codes"):
        lines.append("")
        lines.append("ui_codes:")
        for key, value in payload["ui_codes"].items():
            lines.append(f"  {key}: {value}")
    if payload.get("devices"):
        lines.append("")
        lines.append("devices:")
        for key, value in payload["devices"].items():
            lines.append(f"  {key}: {value}")
    if payload.get("webspaces"):
        lines.append("")
        lines.append("webspaces:")
        for key, value in payload["webspaces"].items():
            lines.append(f"  {key}: {value}")
    if payload.get("recent_examples"):
        lines.append("")
        lines.append("recent_examples:")
        for item in payload["recent_examples"][-10:]:
            lines.append(f"  {item['file']}:{item['line']} {item['text']}")
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Summarize AdaOS logs for a fixed window.")
    parser.add_argument("--duration", type=float, default=180.0, help="Window duration in seconds.")
    parser.add_argument("--follow", action="store_true", help="Wait for duration and analyze lines appended during the wait.")
    parser.add_argument("--last", action="store_true", help="Analyze the last duration seconds based on log timestamps.")
    parser.add_argument("--label", default="baseline", help="Label stored in the report.")
    parser.add_argument("--out", default="", help="Optional JSON output path.")
    parser.add_argument("--text-out", default="", help="Optional text output path.")
    parser.add_argument("--log", action="append", default=[], help="Additional log path.")
    args = parser.parse_args()

    paths = resolve_logs(args.log)
    started = datetime.now(timezone.utc).isoformat()
    summary = Summary(label=args.label, started_at_wall=started, duration_s=float(args.duration))

    if args.follow:
        offsets = parse_offsets(paths)
        time.sleep(max(0.0, float(args.duration)))
        for path in paths:
            rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            for line_no, line in read_new_lines(path, offsets.get(path, 0)):
                analyze_line(summary, rel, line_no, line)
    else:
        now = time.time()
        since = now - max(0.0, float(args.duration))
        for path in paths:
            rel = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
            for line_no, line in read_last_window(path, since):
                analyze_line(summary, rel, line_no, line)

    payload = summary.to_json()
    text = render_text(payload)
    if args.out:
        out_path = Path(args.out)
        if not out_path.is_absolute():
            out_path = ROOT / out_path
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if args.text_out:
        text_path = Path(args.text_out)
        if not text_path.is_absolute():
            text_path = ROOT / text_path
        text_path.parent.mkdir(parents=True, exist_ok=True)
        text_path.write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
