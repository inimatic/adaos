"""Fixed, in-process skill runtime for the experimental Android profile."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlencode
from urllib.request import Request, urlopen

import y_py as Y

from .ystore import AndroidYStore


_POPULAR_CITIES = {
    "berlin": (52.52, 13.41, "Berlin"),
    "moscow": (55.75, 37.62, "Moscow"),
    "new york": (40.71, -74.01, "New York"),
    "paris": (48.86, 2.35, "Paris"),
    "tokyo": (35.68, 139.69, "Tokyo"),
}
_WEATHER_CODES = {
    0: "Clear",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Freezing fog",
    51: "Light drizzle",
    53: "Drizzle",
    55: "Heavy drizzle",
    61: "Light rain",
    63: "Rain",
    65: "Heavy rain",
    71: "Light snow",
    73: "Snow",
    75: "Heavy snow",
    80: "Rain showers",
    81: "Rain showers",
    82: "Heavy showers",
    95: "Thunderstorm",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _plain_at_path(snapshot: dict[str, Any], path: str) -> Any:
    current: Any = snapshot
    for part in (item for item in path.split("/") if item):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _append_array_value(target: Y.YArray, transaction: Any, value: Any) -> None:
    if isinstance(value, dict):
        child = Y.YMap({})
        target.append(transaction, child)
        for key, item in value.items():
            _set_map_value(child, transaction, str(key), item)
    elif isinstance(value, list):
        child = Y.YArray()
        target.append(transaction, child)
        for item in value:
            _append_array_value(child, transaction, item)
    else:
        target.append(transaction, value)


def _set_map_value(target: Y.YMap, transaction: Any, key: str, value: Any) -> None:
    if isinstance(value, dict):
        child = Y.YMap({})
        target.set(transaction, key, child)
        for child_key, item in value.items():
            _set_map_value(child, transaction, str(child_key), item)
    elif isinstance(value, list):
        child = Y.YArray()
        target.set(transaction, key, child)
        for item in value:
            _append_array_value(child, transaction, item)
    else:
        target.set(transaction, key, value)


class AndroidSkillError(RuntimeError):
    pass


class AndroidSkillRuntime:
    """Allowlisted skills without venvs, subprocesses, git, or runtime pip."""

    skill_ids = (
        "web_desktop_skill",
        "subnet_env",
        "weather_skill",
        "adaos_connect",
        "notebook_skill",
        "demo_metrics_skill",
    )

    def __init__(
        self,
        data_root: Path,
        store: AndroidYStore,
        *,
        node_id: str,
        subnet_id: str,
        desktop_application: dict[str, Any],
        taiga_application: dict[str, Any],
        publish_yjs: Callable[[bytes], None],
        publish_event: Callable[[str, dict[str, Any], str], None],
    ) -> None:
        self.store = store
        self.node_id = node_id
        self.subnet_id = subnet_id
        self.desktop_application = desktop_application
        self.taiga_application = taiga_application
        self.publish_yjs = publish_yjs
        self.publish_event = publish_event
        self._lock = threading.RLock()
        self._stream_revision = 0
        self._database = sqlite3.connect(
            str(Path(data_root) / "android-skills.sqlite3"),
            check_same_thread=False,
        )
        self._database.row_factory = sqlite3.Row
        self._database.execute("PRAGMA journal_mode=WAL")
        self._database.execute("PRAGMA synchronous=NORMAL")
        self._database.executescript(
            """
            CREATE TABLE IF NOT EXISTS notebook_notes (
                note_id TEXT PRIMARY KEY,
                content TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                version INTEGER NOT NULL
            );
            CREATE TABLE IF NOT EXISTS idempotency_results (
                request_key TEXT PRIMARY KEY,
                response_json TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            """
        )
        if not self._database.execute("SELECT 1 FROM notebook_notes LIMIT 1").fetchone():
            now = _utc_now()
            self._database.execute(
                "INSERT INTO notebook_notes VALUES (?, ?, ?, ?, ?)",
                ("note-1", "", now, now, 1),
            )
        self._database.commit()
        self._selected_note_id = "note-1"
        self._initialize_projections()

    def _initialize_projections(self) -> None:
        snapshot = self.store.snapshot_json()
        updates: dict[str, Any] = {
            "data/desktop/notebook": self._notebook_snapshot(),
            "data/demo_metrics": self._demo_snapshot(),
            "runtime/environment/install_profile": {
                "id": "android_poc_v1",
                "execution": "in_process",
                "dynamic_install": False,
                "skills": list(self.skill_ids),
            },
        }
        connect = _plain_at_path(snapshot, "data/adaos_connect")
        if not isinstance(connect, dict) or "current" not in connect:
            updates["data/adaos_connect"] = self._connect_snapshot("browser")
        self._set_paths(updates)

    def _set_paths(self, values: dict[str, Any]) -> bytes:
        snapshot = self.store.snapshot_json()
        changed = {
            path: value
            for path, value in values.items()
            if _plain_at_path(snapshot, path) != value
        }
        if not changed:
            return b""

        def mutate(document: Y.YDoc, transaction: Any) -> None:
            for path, value in changed.items():
                parts = [item for item in path.split("/") if item]
                target = document.get_map(parts[0])
                for part in parts[1:-1]:
                    child = target.get(part, None)
                    if not isinstance(child, Y.YMap):
                        child = Y.YMap({})
                        target.set(transaction, part, child)
                    target = child
                _set_map_value(target, transaction, parts[-1], value)

        update = self.store.mutate(mutate)
        if update:
            self.publish_yjs(update)
        return update

    def status(self) -> dict[str, Any]:
        with self._lock:
            note_count = int(
                self._database.execute("SELECT COUNT(*) FROM notebook_notes").fetchone()[0]
            )
        return {
            "ready": True,
            "profile": "android_poc_v1",
            "execution": "in_process",
            "dynamic_install": False,
            "skills": list(self.skill_ids),
            "note_count": note_count,
        }

    def call_tool(
        self,
        tool: str,
        arguments: dict[str, Any],
        *,
        idempotency_key: str = "",
    ) -> dict[str, Any]:
        normalized = str(tool or "").replace(":", ".", 1).strip()
        allowed = {
            "notebook_skill.select_note": self._notebook_select,
            "notebook_skill.create_note": self._notebook_create,
            "notebook_skill.save_note": self._notebook_save,
            "notebook_skill.delete_note": self._notebook_delete,
            "notebook_skill.get_notebook_snapshot": lambda _args: self._notebook_snapshot(),
            "notebook_skill.attach_note_upload": self._unsupported_attachment,
            "notebook_skill.send_note_to_telegram": self._unsupported_telegram,
            "demo_metrics_skill.emit_demo_event": self._demo_event,
            "weather_skill.get_weather": self._weather_event,
            "subnet_env.get_snapshot": lambda _args: self._subnet_snapshot(),
            "adaos_connect.get_snapshot": lambda _args: self._connect_current(),
        }
        handler = allowed.get(normalized)
        if handler is None:
            raise AndroidSkillError(f"skill_not_in_android_descriptor:{normalized}")

        request_key = str(idempotency_key or "").strip()
        if request_key:
            with self._lock:
                cached = self._database.execute(
                    "SELECT response_json FROM idempotency_results WHERE request_key = ?",
                    (request_key,),
                ).fetchone()
            if cached:
                return json.loads(str(cached[0]))
        result = handler(arguments if isinstance(arguments, dict) else {})
        if request_key:
            with self._lock:
                self._database.execute(
                    "INSERT OR REPLACE INTO idempotency_results VALUES (?, ?, ?)",
                    (request_key, json.dumps(result, ensure_ascii=False), time.time()),
                )
                self._database.execute(
                    "DELETE FROM idempotency_results WHERE request_key NOT IN "
                    "(SELECT request_key FROM idempotency_results ORDER BY created_at DESC LIMIT 256)"
                )
                self._database.commit()
        return result

    def handle_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = str(event_type or "").strip()
        if normalized == "weather.location.requested":
            return self._weather_event(payload)
        if normalized.startswith("adaos_connect.prepare"):
            mode = normalized.rsplit(".", 1)[-1]
            if mode == "prepare":
                mode = str(payload.get("mode") or "browser")
            return self._prepare_connect(mode, payload)
        if normalized == "demo_metrics.host_action":
            return self._demo_event(payload, source="android.host")
        raise AndroidSkillError(f"event_not_in_android_descriptor:{normalized}")

    def switch_scenario(self, scenario_id: str) -> dict[str, Any]:
        selected = str(scenario_id or "").strip()
        if selected == "web_desktop":
            application = self.desktop_application
        elif selected == "taiga_ui_demo_scenario":
            application = self.taiga_application
        else:
            raise AndroidSkillError(f"scenario_not_in_android_descriptor:{selected}")
        self._set_paths(
            {
                "ui/current_scenario": selected,
                "ui/application": application,
                "runtime/environment/materialization/scenario_id": selected,
            }
        )
        return {
            "webspace_id": "desktop",
            "scenario_id": selected,
            "switch_skipped": False,
            "rebuild_ready": True,
        }

    def stream_snapshot(self, receiver: str) -> dict[str, Any] | None:
        if str(receiver or "").strip() != "notebook_skill.notes":
            return None
        return self._publish_notebook_stream()

    def _note_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._database.execute(
                    "SELECT * FROM notebook_notes ORDER BY updated_at DESC LIMIT 64"
                ).fetchall()
            )

    @staticmethod
    def _note_item(row: sqlite3.Row) -> dict[str, Any]:
        content = str(row["content"] or "")
        title = next((line.strip() for line in content.splitlines() if line.strip()), "New note")[:80]
        preview = " ".join(content.split())[:160]
        return {
            "id": str(row["note_id"]),
            "title": title,
            "subtitle": str(row["updated_at"]),
            "content": content,
            "text": content,
            "preview": preview,
            "description": preview,
            "attachments": [],
            "updated_at": str(row["updated_at"]),
            "version": int(row["version"]),
        }

    def _notebook_snapshot(self) -> dict[str, Any]:
        items = [self._note_item(row) for row in self._note_rows()]
        selected = next(
            (item for item in items if item["id"] == self._selected_note_id),
            items[0] if items else None,
        )
        if selected is not None:
            self._selected_note_id = str(selected["id"])
        editor = dict(selected or {"id": "", "content": "", "attachments": [], "version": 0})
        editor["editing"] = True
        return {
            "ok": True,
            "status": "ready",
            "message": "Notebook is stored locally on this phone.",
            "selected_note_id": self._selected_note_id,
            "display_note_id": self._selected_note_id,
            "editing_note_id": self._selected_note_id,
            "items": items,
            "display": dict(selected or {}),
            "editor": editor,
            "widget": {"items": items[:1]},
            "updated_at": _utc_now(),
        }

    def _refresh_notebook_projection(self) -> dict[str, Any]:
        snapshot = self._notebook_snapshot()
        self._set_paths({"data/desktop/notebook": snapshot})
        self._publish_notebook_stream(snapshot)
        return snapshot

    def _publish_notebook_stream(self, snapshot: dict[str, Any] | None = None) -> dict[str, Any]:
        self._stream_revision += 1
        data = dict(snapshot or self._notebook_snapshot())
        data["_stream_rev"] = self._stream_revision
        data["_stream_require_revision"] = True
        self.publish_event(
            "webio.stream.desktop.notebook_skill.notes",
            {"data": data},
            "android.notebook_skill",
        )
        return data

    def _notebook_select(self, arguments: dict[str, Any]) -> dict[str, Any]:
        note_id = str(arguments.get("note_id") or "").strip()
        if not any(str(row["note_id"]) == note_id for row in self._note_rows()):
            raise AndroidSkillError("notebook_note_not_found")
        self._selected_note_id = note_id
        return self._refresh_notebook_projection()

    def _notebook_create(self, arguments: dict[str, Any]) -> dict[str, Any]:
        note_id = f"note-{uuid.uuid4().hex[:12]}"
        content = str(arguments.get("content") or "")[:65536]
        now = _utc_now()
        with self._lock:
            self._database.execute(
                "INSERT INTO notebook_notes VALUES (?, ?, ?, ?, ?)",
                (note_id, content, now, now, 1),
            )
            self._database.commit()
        self._selected_note_id = note_id
        return self._refresh_notebook_projection()

    def _notebook_save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        note_id = str(arguments.get("note_id") or self._selected_note_id).strip()
        content = str(arguments.get("content") or "")[:65536]
        now = _utc_now()
        with self._lock:
            cursor = self._database.execute(
                "UPDATE notebook_notes SET content = ?, updated_at = ?, version = version + 1 "
                "WHERE note_id = ?",
                (content, now, note_id),
            )
            if cursor.rowcount != 1:
                raise AndroidSkillError("notebook_note_not_found")
            self._database.commit()
        self._selected_note_id = note_id
        return self._refresh_notebook_projection()

    def _notebook_delete(self, arguments: dict[str, Any]) -> dict[str, Any]:
        note_id = str(arguments.get("note_id") or self._selected_note_id).strip()
        with self._lock:
            self._database.execute("DELETE FROM notebook_notes WHERE note_id = ?", (note_id,))
            self._database.commit()
        rows = self._note_rows()
        if not rows:
            return self._notebook_create({"content": ""})
        self._selected_note_id = str(rows[0]["note_id"])
        return self._refresh_notebook_projection()

    @staticmethod
    def _unsupported_attachment(_arguments: dict[str, Any]) -> dict[str, Any]:
        raise AndroidSkillError("notebook_attachments_deferred_android_poc")

    @staticmethod
    def _unsupported_telegram(_arguments: dict[str, Any]) -> dict[str, Any]:
        raise AndroidSkillError("notebook_telegram_export_deferred_android_poc")

    def _subnet_snapshot(self) -> dict[str, Any]:
        return {
            "ok": True,
            "node_id": self.node_id,
            "subnet_id": self.subnet_id,
            "role": "member",
            "runtime_profile": "android_poc",
        }

    @staticmethod
    def _connect_snapshot(mode: str, request_id: str = "") -> dict[str, Any]:
        return {
            "current": {
                "mode": mode if mode in {"browser", "telegram", "node"} else "browser",
                "status": "offline",
                "degraded": True,
                "pending": False,
                "error": "root_unavailable_android_poc",
                "summary": "Root connection is not configured. Local AdaOS remains available.",
                "summary_language": "text",
                "request_id": request_id,
                "updated_at": _utc_now(),
                "expires_at": "",
                "expires_at_display": "",
                "expires_at_language": "text",
                "expires_at_epoch": 0,
                "expires_in_seconds": 0,
                "qr_text": "",
                "link": "",
                "link_language": "text",
                "code": "",
                "code_language": "text",
                "node_connect_command": "",
                "node_connect_language": "bash",
                "linux_command": "",
                "linux_language": "bash",
                "windows_ps_command": "",
                "windows_ps_language": "powershell",
                "windows_cmd_command": "",
                "windows_cmd_language": "bat",
            }
        }

    def _connect_current(self) -> dict[str, Any]:
        current = _plain_at_path(self.store.snapshot_json(), "data/adaos_connect")
        return current if isinstance(current, dict) else self._connect_snapshot("browser")

    def _prepare_connect(self, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        snapshot = self._connect_snapshot(mode, str(payload.get("request_id") or ""))
        self._set_paths({"data/adaos_connect": snapshot})
        return snapshot

    @staticmethod
    def _demo_snapshot() -> dict[str, Any]:
        now = int(time.time())
        table = [
            {"id": "cpu", "title": "CPU", "status": "ready", "value": 18, "unit": "%", "group": "phone"},
            {"id": "memory", "title": "Memory", "status": "ready", "value": 142, "unit": "MiB", "group": "phone"},
            {"id": "yjs", "title": "Yjs revision", "status": "ready", "value": 1, "unit": "rev", "group": "runtime"},
        ]
        return {
            "summary": {"value": 3, "label": "Android metrics", "status": "ready"},
            "table": table,
            "tree": [{"id": "phone", "title": "Android phone", "children": table}],
            "chart": {
                "title": "Runtime sample",
                "unit": "%",
                "points": [
                    {"ts": now - 120, "value": 12},
                    {"ts": now - 60, "value": 16},
                    {"ts": now, "value": 18},
                ],
            },
            "chat": [
                {"id": "android-ready", "author": "system", "text": "Android in-process skills are ready."}
            ],
        }

    def _demo_event(self, arguments: dict[str, Any], *, source: str = "android.demo_metrics_skill") -> dict[str, Any]:
        event = {
            "id": f"demo-{uuid.uuid4().hex[:10]}",
            "title": str(arguments.get("action_id") or "Demo event"),
            "description": f"Metric {str(arguments.get('metric_id') or 'cpu')}",
            "updated_at": _utc_now(),
        }
        self.publish_event(
            "webio.stream.desktop.demo_metrics.events",
            {"data": [event]},
            source,
        )
        return {"ok": True, "event": event}

    def _weather_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        request_id = str(payload.get("request_id") or "")
        location = payload.get("location") if isinstance(payload.get("location"), dict) else {}
        city = str(payload.get("city") or "").strip()
        try:
            if location and location.get("latitude") is not None and location.get("longitude") is not None:
                latitude = float(location["latitude"])
                longitude = float(location["longitude"])
                label = city or "Current location"
            else:
                latitude, longitude, label = self._resolve_city(city or "Moscow")
            result = self._fetch_weather(latitude, longitude, label, request_id)
        except Exception as exc:
            result = {
                "current": {
                    "city": city or "Moscow",
                    "label": city or "Moscow",
                    "temp_c": None,
                    "condition": "Offline",
                    "description": "Weather service is unavailable.",
                    "summary": "Weather service is unavailable; local skills remain ready.",
                    "wind_ms": None,
                    "pending": False,
                    "source": "offline",
                    "error": str(exc)[:160],
                    "request_id": request_id,
                    "updated_at": _utc_now(),
                },
                "hourly_chart": {"title": "Next hours", "unit": "C", "points": []},
                "daily": [],
            }
        self._set_paths(
            {
                "data/weather/current": result["current"],
                "data/weather/hourly_chart": result["hourly_chart"],
                "data/weather/daily": result["daily"],
            }
        )
        return result

    @staticmethod
    def _json_request(url: str) -> dict[str, Any]:
        request = Request(url, headers={"User-Agent": "AdaOS-Android-PoC/0.3"})
        with urlopen(request, timeout=5) as response:
            payload = json.loads(response.read(512 * 1024).decode("utf-8"))
        if not isinstance(payload, dict):
            raise AndroidSkillError("weather_invalid_response")
        return payload

    def _resolve_city(self, city: str) -> tuple[float, float, str]:
        known = _POPULAR_CITIES.get(city.casefold())
        if known:
            return known
        query = urlencode({"name": city, "count": 1, "language": "en", "format": "json"})
        result = self._json_request(f"https://geocoding-api.open-meteo.com/v1/search?{query}")
        rows = result.get("results") if isinstance(result.get("results"), list) else []
        if not rows or not isinstance(rows[0], dict):
            raise AndroidSkillError("weather_city_not_found")
        row = rows[0]
        return float(row["latitude"]), float(row["longitude"]), str(row.get("name") or city)

    def _fetch_weather(
        self,
        latitude: float,
        longitude: float,
        label: str,
        request_id: str,
    ) -> dict[str, Any]:
        query = urlencode(
            {
                "latitude": f"{latitude:.5f}",
                "longitude": f"{longitude:.5f}",
                "current": "temperature_2m,weather_code,wind_speed_10m",
                "hourly": "temperature_2m",
                "daily": "weather_code,temperature_2m_max,temperature_2m_min,precipitation_probability_max,wind_speed_10m_max",
                "timezone": "auto",
                "forecast_days": 5,
            }
        )
        payload = self._json_request(f"https://api.open-meteo.com/v1/forecast?{query}")
        current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
        hourly = payload.get("hourly") if isinstance(payload.get("hourly"), dict) else {}
        daily = payload.get("daily") if isinstance(payload.get("daily"), dict) else {}
        code = int(current.get("weather_code") or 0)
        condition = _WEATHER_CODES.get(code, f"Weather code {code}")
        temperatures = list(hourly.get("temperature_2m") or [])[:12]
        timestamps = list(hourly.get("time") or [])[: len(temperatures)]
        days = list(daily.get("time") or [])
        result_daily: list[dict[str, Any]] = []
        for index, day in enumerate(days[:5]):
            daily_codes = list(daily.get("weather_code") or [])
            daily_code = int(daily_codes[index]) if index < len(daily_codes) else 0

            def item(name: str) -> Any:
                values = list(daily.get(name) or [])
                return values[index] if index < len(values) else None

            result_daily.append(
                {
                    "day": day,
                    "condition": _WEATHER_CODES.get(daily_code, str(daily_code)),
                    "temp_min_c": item("temperature_2m_min"),
                    "temp_max_c": item("temperature_2m_max"),
                    "precip_pct": item("precipitation_probability_max"),
                    "wind_ms": item("wind_speed_10m_max"),
                }
            )
        temperature = current.get("temperature_2m")
        return {
            "current": {
                "city": label,
                "label": label,
                "temp_c": temperature,
                "condition": condition,
                "description": condition,
                "summary": f"{label}: {temperature} C, {condition}",
                "wind_ms": current.get("wind_speed_10m"),
                "pending": False,
                "source": "open-meteo",
                "error": "",
                "request_id": request_id,
                "updated_at": _utc_now(),
            },
            "hourly_chart": {
                "title": "Next hours",
                "unit": "C",
                "points": [
                    {"ts": timestamp, "value": value}
                    for timestamp, value in zip(timestamps, temperatures)
                ],
            },
            "daily": result_daily,
        }

    def close(self) -> None:
        with self._lock:
            self._database.commit()
            self._database.close()
