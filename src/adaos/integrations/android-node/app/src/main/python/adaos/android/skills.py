"""Fixed, in-process skill runtime for the experimental Android profile."""

from __future__ import annotations

import copy
import json
import re
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

from adaos.services.nlu.portable_rasa import PortableRasaRuntime, load_portable_rasa

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
_MAX_NOTE_COUNT = 256
_MAX_PROJECTED_NOTE_COUNT = 32
_MAX_NOTE_CONTENT_CHARS = 16 * 1024
_MAX_IDEMPOTENCY_RESULTS = 256
_MAX_VOICE_MESSAGES = 32
_MAX_VOICE_TEXT_CHARS = 2 * 1024
_RASA_LOW_CONFIDENCE = 0.45
_RASA_BUNDLE_PATH = Path(__file__).with_name("bundle") / "rasa_mobile_bundle.json.gz"
_GENERAL_DIALOG_AGENT_ID = "agent:android:local"
_ARSENI_AGENT_ID = "agent:conversation_companions:arseni"
_NIKA_AGENT_ID = "agent:conversation_companions:nika"
_MIRA_AGENT_ID = "agent:conversation_companions:mira"
_BUILDER_AGENT_ID = "agent:builder_skill:builder"
_DIALOG_AGENTS: tuple[dict[str, Any], ...] = (
    {
        "id": _GENERAL_DIALOG_AGENT_ID,
        "label": "AdaOS Mobile",
        "owner": "core:general_assistant",
        "kind": "core_agent",
        "channel_id": "general",
        "gender": "neutral",
        "voice": "ru-RU",
        "icon": "sparkles-outline",
        "aliases": ("adaos", "ада", "ассистент", "adaos mobile"),
        "capabilities": ("node_status", "weather", "notebook_create"),
    },
    {
        "id": _ARSENI_AGENT_ID,
        "label": "Арсений",
        "owner": "skill:conversation_companions",
        "kind": "skill_agent",
        "channel_id": "conversational",
        "gender": "male",
        "voice": "ru-male",
        "icon": "male-outline",
        "aliases": ("арсений", "арсени", "arseni", "arseniy"),
        "capabilities": ("local_companion", "node_status", "weather", "notebook_create"),
    },
    {
        "id": _NIKA_AGENT_ID,
        "label": "Ника",
        "owner": "skill:conversation_companions",
        "kind": "skill_agent",
        "channel_id": "conversational",
        "gender": "female",
        "voice": "ru-female",
        "icon": "female-outline",
        "aliases": ("ника", "nika"),
        "capabilities": ("local_companion", "node_status", "weather", "notebook_create"),
    },
    {
        "id": _MIRA_AGENT_ID,
        "label": "Мира",
        "owner": "skill:conversation_companions",
        "kind": "skill_agent",
        "channel_id": "conversational",
        "gender": "female",
        "voice": "ru-female",
        "icon": "heart-circle-outline",
        "aliases": ("мира", "mira"),
        "capabilities": ("local_companion", "node_status", "weather", "notebook_create"),
    },
    {
        "id": _BUILDER_AGENT_ID,
        "label": "Строитель",
        "owner": "skill:builder_skill",
        "kind": "skill_agent",
        "channel_id": "builder",
        "gender": "male",
        "voice": "ru-male",
        "icon": "construct-outline",
        "aliases": ("строитель", "builder", "билдер"),
        "capabilities": ("mobile_architecture_advice", "node_status", "weather", "notebook_create"),
    },
)
_DIALOG_AGENT_BY_ID = {str(item["id"]): item for item in _DIALOG_AGENTS}
_DIALOG_CHANNEL_DEFAULTS = {
    "general": _GENERAL_DIALOG_AGENT_ID,
    "conversational": _ARSENI_AGENT_ID,
    "builder": _BUILDER_AGENT_ID,
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


def _merge_scenario_application(
    desktop_application: dict[str, Any],
    scenario_application: dict[str, Any],
) -> dict[str, Any]:
    """Overlay a scenario surface without dropping desktop-wide contracts."""

    merged = copy.deepcopy(desktop_application)
    for key, value in scenario_application.items():
        if key in {"modals", "interfaces"} and isinstance(value, dict):
            target = merged.get(key)
            if not isinstance(target, dict):
                target = {}
                merged[key] = target
            target.update(copy.deepcopy(value))
            continue
        if key == "resources" and isinstance(value, list):
            resources = merged.get("resources")
            if not isinstance(resources, list):
                resources = []
            merged["resources"] = resources + [
                copy.deepcopy(item) for item in value if item not in resources
            ]
            continue
        merged[key] = copy.deepcopy(value)
    return merged


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
        "browsers_skill",
        "voice_assistant",
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
        desktop_catalog: dict[str, Any],
        desktop_installed: dict[str, Any],
        desktop_registry: dict[str, Any],
        taiga_application: dict[str, Any],
        publish_yjs: Callable[[bytes], None],
        publish_event: Callable[[str, dict[str, Any], str], None],
    ) -> None:
        self.store = store
        self.node_id = node_id
        self.subnet_id = subnet_id
        self.desktop_application = copy.deepcopy(desktop_application)
        self.desktop_catalog = copy.deepcopy(desktop_catalog)
        self.desktop_installed = copy.deepcopy(desktop_installed)
        self.desktop_registry = copy.deepcopy(desktop_registry)
        self.taiga_application = _merge_scenario_application(
            self.desktop_application,
            taiga_application,
        )
        self.publish_yjs = publish_yjs
        self.publish_event = publish_event
        self.member_link: Any | None = None
        self._nlu_runtime: PortableRasaRuntime | None = None
        self._nlu_error = ""
        try:
            self._nlu_runtime = load_portable_rasa(_RASA_BUNDLE_PATH)
        except Exception as exc:
            self._nlu_error = f"{type(exc).__name__}:{str(exc)[:200]}"
        self._lock = threading.RLock()
        self._closed = False
        self._connect_prepare_generation = 0
        self._connect_prepare_thread: threading.Thread | None = None
        self._member_join_generation = 0
        self._member_join_thread: threading.Thread | None = None
        self._last_dialog_route = "not_used"
        self._last_dialog_error = ""
        self._last_dialog_route_at = ""
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
            CREATE TABLE IF NOT EXISTS android_settings (
                setting_key TEXT PRIMARY KEY,
                setting_value TEXT NOT NULL,
                updated_at TEXT NOT NULL
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
        current_scenario = str(
            _plain_at_path(snapshot, "ui/current_scenario") or "web_desktop"
        ).strip()
        if current_scenario == "taiga_ui_demo_scenario":
            current_application = self.taiga_application
        else:
            current_scenario = "web_desktop"
            current_application = self.desktop_application
        updates: dict[str, Any] = {
            "ui/current_scenario": current_scenario,
            "ui/application": current_application,
            "data/catalog": self.desktop_catalog,
            "data/installed": self.desktop_installed,
            "registry/merged": self.desktop_registry.get("merged", {}),
            "data/desktop/notebook": self._notebook_snapshot(),
            "data/demo_metrics": self._demo_snapshot(),
            "data/subnet_env/current": self._subnet_snapshot(),
            "data/browsers": self._empty_browser_snapshot(),
            "runtime/environment/materialization/scenario_id": current_scenario,
            "runtime/environment/install_profile": {
                "id": "android_poc_v1",
                "execution": "in_process",
                "dynamic_install": False,
                "skills": list(self.skill_ids),
            },
            "runtime/environment/nlu": self._nlu_status(),
        }
        connect = _plain_at_path(snapshot, "data/adaos_connect")
        connect_current = connect.get("current") if isinstance(connect, dict) else {}
        connect_mode = (
            str(connect_current.get("mode") or "member")
            if isinstance(connect_current, dict)
            else "member"
        )
        updates["data/adaos_connect"] = self._connect_snapshot(connect_mode)
        voice_chat = _plain_at_path(snapshot, "data/voice_chat")
        if not isinstance(voice_chat, dict) or not isinstance(
            voice_chat.get("messages"), list
        ):
            updates["data/voice_chat"] = self._empty_voice_chat()
        else:
            voice_chat = dict(voice_chat)
            voice_chat["assistant"] = self._dialog_agent_projection(
                self._active_dialog_agent()
            )
            updates["data/voice_chat"] = voice_chat
        updates["data/dialog"] = self._dialog_snapshot(event="startup")
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
            note_count, note_content_chars = self._database.execute(
                "SELECT COUNT(*), COALESCE(SUM(LENGTH(content)), 0) FROM notebook_notes"
            ).fetchone()
            idempotency_count = int(
                self._database.execute(
                    "SELECT COUNT(*) FROM idempotency_results"
                ).fetchone()[0]
            )
        return {
            "ready": True,
            "profile": "android_poc_v1",
            "execution": "in_process",
            "dynamic_install": False,
            "skills": list(self.skill_ids),
            "nlu": self._nlu_status(),
            "note_count": int(note_count),
            "resource_bounds": {
                "note_count_limit": _MAX_NOTE_COUNT,
                "projected_note_count_limit": _MAX_PROJECTED_NOTE_COUNT,
                "note_content_chars_limit": _MAX_NOTE_CONTENT_CHARS,
                "idempotency_result_limit": _MAX_IDEMPOTENCY_RESULTS,
                "note_content_chars": int(note_content_chars),
                "idempotency_result_count": idempotency_count,
            },
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
            "subnet_env.set_node_label": self._subnet_set_node_label,
            "adaos_connect.get_snapshot": lambda _args: self._connect_current(),
            "adaos_connect.configure_member": self._configure_member,
            "adaos_connect.join_member": self._join_member,
            "adaos_connect.disconnect_member": self._disconnect_member,
            "browsers_skill.refresh_snapshot": lambda _args: self._browser_current(),
            "voice_assistant.get_snapshot": lambda _args: self._voice_current(),
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
                    "(SELECT request_key FROM idempotency_results "
                    "ORDER BY created_at DESC LIMIT ?)",
                    (_MAX_IDEMPOTENCY_RESULTS,),
                )
                self._database.commit()
        return result

    def handle_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        normalized = str(event_type or "").strip()
        if normalized == "weather.location.requested":
            return self._weather_event(payload)
        if normalized == "subnet_env.snapshot.requested":
            snapshot = self._subnet_snapshot()
            self._set_paths({"data/subnet_env/current": snapshot})
            return snapshot
        if normalized == "subnet_env.node_label.changed":
            return self._subnet_set_node_label(payload)
        if normalized.startswith("adaos_connect.prepare"):
            mode = normalized.rsplit(".", 1)[-1]
            if mode == "prepare":
                mode = str(payload.get("mode") or "browser")
            return self._prepare_connect(mode, payload)
        if normalized == "adaos_connect.member.root_url.set":
            return self._set_member_root_url(payload)
        if normalized == "adaos_connect.member.join":
            return self._join_member(payload)
        if normalized == "adaos_connect.member.disconnect":
            return self._disconnect_member(payload)
        if normalized == "browsers.refresh":
            return self._browser_current()
        if normalized == "dialog.channel.select":
            return self.select_dialog_channel(payload)
        if normalized == "dialog.agent.select":
            return self.select_dialog_agent(payload)
        if normalized == "demo_metrics.host_action":
            return self._demo_event(payload, source="android.host")
        if normalized == "demo_metrics.selection.changed":
            metric_id = str(payload.get("metric_id") or "").strip()
            if metric_id not in {"cpu", "memory", "yjs"}:
                raise AndroidSkillError("demo_metrics_selection_invalid")
            selection = {"metric_id": metric_id, "updated_at": _utc_now()}
            self._set_paths({"data/demo_metrics/selection": selection})
            return {"ok": True, "selection": selection}
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
        selected = str(receiver or "").strip()
        if selected == "notebook_skill.notes":
            return self._publish_notebook_stream()
        if selected == "voice_chat.messages":
            return self._voice_current()
        return None

    def _note_rows(self) -> list[sqlite3.Row]:
        with self._lock:
            return list(
                self._database.execute(
                    "SELECT * FROM notebook_notes ORDER BY updated_at DESC LIMIT ?",
                    (_MAX_PROJECTED_NOTE_COUNT,),
                ).fetchall()
            )

    @staticmethod
    def _note_item(row: sqlite3.Row) -> dict[str, Any]:
        content = str(row["content"] or "")[:_MAX_NOTE_CONTENT_CHARS]
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
        content = str(arguments.get("content") or "")[:_MAX_NOTE_CONTENT_CHARS]
        now = _utc_now()
        with self._lock:
            note_count = int(
                self._database.execute(
                    "SELECT COUNT(*) FROM notebook_notes"
                ).fetchone()[0]
            )
            if note_count >= _MAX_NOTE_COUNT:
                raise AndroidSkillError("notebook_note_limit_reached")
            self._database.execute(
                "INSERT INTO notebook_notes VALUES (?, ?, ?, ?, ?)",
                (note_id, content, now, now, 1),
            )
            self._database.commit()
        self._selected_note_id = note_id
        return self._refresh_notebook_projection()

    def _notebook_save(self, arguments: dict[str, Any]) -> dict[str, Any]:
        note_id = str(arguments.get("note_id") or self._selected_note_id).strip()
        content = str(arguments.get("content") or "")[:_MAX_NOTE_CONTENT_CHARS]
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
        member = self.member_link.snapshot() if self.member_link is not None else {}
        subnet_id = str(member.get("subnet_id") or self.subnet_id)
        link_state = str(member.get("state") or "offline")
        return {
            "ok": True,
            "node_id": self.node_id,
            "subnet_id": subnet_id,
            "role": "member",
            "node_label": self._setting("node_label", "Android phone"),
            "summary": (
                f"{self._setting('node_label', 'Android phone')} is a local Android "
                f"member of {subnet_id}; upstream link is {link_state}."
            ),
            "runtime_profile": "android_poc",
            "member_link_state": link_state,
            "connected_to_hub": bool(member.get("connected")),
            "updated_at": _utc_now(),
        }

    def _setting(self, key: str, default: str = "") -> str:
        with self._lock:
            row = self._database.execute(
                "SELECT setting_value FROM android_settings WHERE setting_key = ?",
                (str(key),),
            ).fetchone()
        return str(row[0]) if row else str(default)

    def _set_setting(self, key: str, value: str) -> None:
        with self._lock:
            self._database.execute(
                """
                INSERT INTO android_settings(setting_key, setting_value, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (str(key), str(value), _utc_now()),
            )
            self._database.commit()

    def _subnet_set_node_label(self, arguments: dict[str, Any]) -> dict[str, Any]:
        label = str(
            arguments.get("node_label")
            or arguments.get("value")
            or ""
        ).strip()[:64]
        if not label:
            raise AndroidSkillError("subnet_env_node_label_required")
        now = _utc_now()
        with self._lock:
            self._database.execute(
                """
                INSERT INTO android_settings(setting_key, setting_value, updated_at)
                VALUES ('node_label', ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (label, now),
            )
            self._database.commit()
        snapshot = self._subnet_snapshot()
        self._set_paths({"data/subnet_env/current": snapshot})
        return snapshot

    def attach_member_link(self, member_link: Any) -> None:
        self.member_link = member_link
        self.project_member_link(member_link.snapshot())

    def project_member_link(self, member_status: dict[str, Any]) -> dict[str, Any]:
        current = self._connect_current().get("current") or {}
        selected_mode = str(current.get("mode") or "member")
        if selected_mode not in {"member", "browser", "telegram", "node"}:
            selected_mode = "member"
        if (
            bool(member_status.get("configured"))
            and selected_mode == "node"
            and str(current.get("source") or "") != "hub_delegated"
        ):
            # PoC9 used "node" for this phone's own member link. Migrate the
            # persisted projection now that "node" means an invitation for a
            # different node, matching the canonical AdaOS Connect skill.
            selected_mode = "member"
        snapshot = self._connect_snapshot(selected_mode, member_status=member_status)
        self._set_paths(
            {
                "data/adaos_connect": snapshot,
                "data/subnet_env/current": self._subnet_snapshot(),
                "data/dialog": self._dialog_snapshot(event="member_link_state"),
            }
        )
        return snapshot

    @staticmethod
    def _empty_browser_snapshot() -> dict[str, Any]:
        return {
            "summary": {
                "title": "Browsers",
                "value": 0,
                "subtitle": "0 active endpoints",
                "details": "Waiting for a local browser",
                "updated_at": _utc_now(),
            },
            "clients": [],
            "updated_at": _utc_now(),
        }

    def project_browser_sessions(
        self,
        sessions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        clients: list[dict[str, Any]] = []
        for index, session in enumerate(sessions[:32]):
            device_id = str(session.get("device_id") or "").strip()
            client_id = str(session.get("client_id") or "").strip()
            title = str(
                session.get("name")
                or session.get("browser_family")
                or (f"Browser {index + 1}")
            ).strip()
            clients.append(
                {
                    "id": client_id or device_id or f"browser-{index + 1}",
                    "device_id": device_id,
                    "client_id": client_id,
                    "title": title,
                    "subtitle": "Online on this phone",
                    "description": str(session.get("user_agent") or "")[:240],
                    "content": {
                        "webspace_id": str(session.get("webspace_id") or "desktop"),
                        "connected_at": float(session.get("connected_at") or 0.0),
                        "origin": str(session.get("origin") or ""),
                    },
                    "online": True,
                }
            )
        total = len(clients)
        snapshot = {
            "summary": {
                "title": "Browsers",
                "value": total,
                "subtitle": f"{total} active endpoint{'s' if total != 1 else ''}",
                "details": "Local loopback sessions; management is read-only in the Android MVP.",
                "updated_at": _utc_now(),
            },
            "clients": clients,
            "updated_at": _utc_now(),
        }
        self._set_paths({"data/browsers": snapshot})
        return snapshot

    def _browser_current(self) -> dict[str, Any]:
        current = _plain_at_path(self.store.snapshot_json(), "data/browsers")
        return current if isinstance(current, dict) else self._empty_browser_snapshot()

    def _connect_snapshot(
        self,
        mode: str,
        request_id: str = "",
        *,
        member_status: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        member = (
            dict(member_status)
            if isinstance(member_status, dict)
            else self.member_link.snapshot()
            if self.member_link is not None
            else {}
        )
        selected_mode = (
            mode if mode in {"member", "browser", "telegram", "node"} else "member"
        )
        configured = bool(member.get("configured"))
        connected = bool(member.get("connected"))
        state = str(member.get("state") or "offline")
        pending = state == "connecting"
        error = str(member.get("last_error") or "")
        if not configured:
            error = "member_link_not_configured"
        if selected_mode == "member" and connected:
            summary = f"Connected to {member.get('hub_url') or 'AdaOS Hub'}."
        elif selected_mode == "member" and configured:
            summary = (
                f"Member link is {state}; local AdaOS remains available."
                + (f" {error}" if error else "")
            )
        elif selected_mode == "member":
            summary = "Enter Root URL and a one-time join code to connect this phone."
        elif connected:
            state = "ready"
            pending = False
            error = ""
            target = {"browser": "browser", "telegram": "Telegram", "node": "node"}[
                selected_mode
            ]
            summary = f"Request a remote {target} invitation from the connected Hub."
        else:
            pending = False
            summary = "Connect this phone to a Hub before creating remote invitations."
        return {
            "current": {
                "mode": selected_mode,
                "status": "connected" if selected_mode == "member" and connected else state,
                "degraded": not connected,
                "pending": pending,
                "error": "" if connected else error,
                "summary": summary,
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
                "root_url": str(
                    member.get("root_url") or self._setting("member_root_url", "")
                ),
                "hub_url": str(member.get("hub_url") or ""),
                "join_code": "",
                "join_status": "idle",
                "subnet_id": str(member.get("subnet_id") or self.subnet_id),
                "member_link_state": state,
                "connected": connected,
                "configured": configured,
                "transport_security": str(member.get("transport_security") or "unconfigured"),
                "connect_attempts": int(member.get("connect_attempts") or 0),
                "reconnect_total": int(member.get("reconnect_total") or 0),
                "zone_id": "",
                "navigation_destination": {},
                "source": "android_member_link",
            }
        }

    def _connect_current(self) -> dict[str, Any]:
        current = _plain_at_path(self.store.snapshot_json(), "data/adaos_connect")
        return current if isinstance(current, dict) else self._connect_snapshot("member")

    def _prepare_connect(self, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        selected_mode = str(mode or "member").strip().lower() or "member"
        request_id = str(payload.get("request_id") or f"android-connect-{uuid.uuid4().hex}")
        if selected_mode == "member":
            snapshot = self._connect_snapshot("member", request_id)
            self._set_paths({"data/adaos_connect": snapshot})
            return snapshot
        if selected_mode not in {"browser", "telegram", "node"}:
            raise AndroidSkillError(f"adaos_connect_mode_invalid:{selected_mode}")
        member = self.member_link.snapshot() if self.member_link is not None else {}
        if not bool(member.get("connected")):
            snapshot = self._connect_snapshot(selected_mode, request_id, member_status=member)
            self._set_paths({"data/adaos_connect": snapshot})
            return snapshot
        with self._lock:
            active = self._connect_prepare_thread
            if active is not None and active.is_alive():
                current = self._connect_current()
                return current
            self._connect_prepare_generation += 1
            generation = self._connect_prepare_generation
            arguments = {
                "mode": selected_mode,
                "webspace_id": str(payload.get("webspace_id") or "desktop"),
                "refresh": bool(payload.get("refresh", True)),
                "force_new": bool(payload.get("force_new", False)),
                "renew": bool(payload.get("renew", False)),
            }
            worker = threading.Thread(
                target=self._finish_connect_prepare,
                args=(selected_mode, arguments, request_id, generation),
                name="adaos-android-connect-prepare",
                daemon=True,
            )
            self._connect_prepare_thread = worker
        snapshot = self._connect_snapshot(selected_mode, request_id, member_status=member)
        snapshot["current"].update(
            {
                "status": "pending",
                "degraded": False,
                "pending": True,
                "error": "",
                "summary": "The connected Hub is preparing this invitation.",
                "source": "hub_delegated",
            }
        )
        self._set_paths({"data/adaos_connect": snapshot})
        worker.start()
        return snapshot

    def _finish_connect_prepare(
        self,
        selected_mode: str,
        arguments: dict[str, Any],
        request_id: str,
        generation: int,
    ) -> None:
        error: Exception | None = None
        result: Any = None
        try:
            if self.member_link is None:
                raise AndroidSkillError("android_member_link_not_ready")
            result = self.member_link.call_hub_tool(
                "adaos_connect:prepare", arguments, timeout=45.0
            )
        except Exception as exc:
            error = exc
        with self._lock:
            if self._closed or generation != self._connect_prepare_generation:
                return
            member = self.member_link.snapshot() if self.member_link is not None else {}
            if error is None:
                remote_current = (
                    dict(result.get("current") or {}) if isinstance(result, dict) else {}
                )
                if not remote_current:
                    error = AndroidSkillError("adaos_connect_hub_result_invalid")
            if error is None:
                remote_current.update(
                    {
                        "mode": selected_mode,
                        "member_link_state": str(member.get("state") or "connected"),
                        "connected": True,
                        "configured": bool(member.get("configured")),
                        "transport_security": str(
                            member.get("transport_security") or "unconfigured"
                        ),
                        "root_url": str(
                            member.get("root_url")
                            or self._setting("member_root_url", "")
                        ),
                        "hub_url": str(member.get("hub_url") or ""),
                        "subnet_id": str(member.get("subnet_id") or self.subnet_id),
                        "source": "hub_delegated",
                    }
                )
                snapshot = {"current": remote_current}
            else:
                snapshot = self._connect_snapshot(
                    selected_mode, request_id, member_status=member
                )
                snapshot["current"].update(
                    {
                        "status": "error",
                        "degraded": True,
                        "pending": False,
                        "error": f"{type(error).__name__}:{str(error)[:180]}",
                        "summary": "The connected Hub could not create this invitation.",
                        "source": "hub_delegated",
                    }
                )
            self._connect_prepare_thread = None
        self._set_paths({"data/adaos_connect": snapshot})

    def _set_member_root_url(self, arguments: dict[str, Any]) -> dict[str, Any]:
        root_url = str(arguments.get("root_url") or arguments.get("value") or "").strip()
        if not root_url.startswith(("http://", "https://")):
            raise AndroidSkillError("adaos_connect_root_url_invalid")
        with self._lock:
            self._database.execute(
                """
                INSERT INTO android_settings(setting_key, setting_value, updated_at)
                VALUES ('member_root_url', ?, ?)
                ON CONFLICT(setting_key) DO UPDATE SET
                    setting_value = excluded.setting_value,
                    updated_at = excluded.updated_at
                """,
                (root_url.rstrip("/"), _utc_now()),
            )
            self._database.commit()
        snapshot = self._connect_snapshot("member")
        self._set_paths({"data/adaos_connect": snapshot})
        return snapshot

    def _configure_member(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.member_link is None:
            raise AndroidSkillError("android_member_link_not_ready")
        try:
            result = self.member_link.configure(
                hub_url=str(arguments.get("hub_url") or ""),
                subnet_id=str(arguments.get("subnet_id") or ""),
                token=str(arguments.get("token") or ""),
            )
        except (ValueError, RuntimeError) as exc:
            raise AndroidSkillError(str(exc)) from exc
        return self.project_member_link(result)

    def _join_member(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.member_link is None:
            raise AndroidSkillError("android_member_link_not_ready")
        root_url = str(
            arguments.get("root_url") or self._setting("member_root_url", "")
        ).strip()
        code = str(arguments.get("code") or arguments.get("join_code") or "").strip()
        if not root_url.startswith(("http://", "https://")):
            raise AndroidSkillError("adaos_connect_root_url_invalid")
        if not code:
            raise AndroidSkillError("member_join_code_required")
        request_id = str(
            arguments.get("request_id") or f"android-join-{uuid.uuid4().hex}"
        )
        with self._lock:
            active = self._member_join_thread
            if active is not None and active.is_alive():
                return self._connect_current()
            self._member_join_generation += 1
            generation = self._member_join_generation
            worker = threading.Thread(
                target=self._finish_member_join,
                args=(root_url, code, request_id, generation),
                name="adaos-android-member-join",
                daemon=True,
            )
            self._member_join_thread = worker
        member = self.member_link.snapshot()
        snapshot = self._connect_snapshot(
            "member", request_id, member_status=member
        )
        snapshot["current"].update(
            {
                "status": "pending",
                "degraded": False,
                "pending": True,
                "error": "",
                "summary": "The phone is validating the join code with AdaOS Root.",
                "join_code": "",
                "join_status": "validating",
                "source": "android_member_join",
            }
        )
        self._set_paths({"data/adaos_connect": snapshot})
        worker.start()
        return snapshot

    def _finish_member_join(
        self,
        root_url: str,
        code: str,
        request_id: str,
        generation: int,
    ) -> None:
        error: Exception | None = None
        result: dict[str, Any] | None = None
        try:
            result = self.member_link.join(root_url=root_url, code=code)
        except Exception as exc:
            error = exc
        with self._lock:
            if self._closed or generation != self._member_join_generation:
                return
            member = (
                dict(result)
                if isinstance(result, dict)
                else self.member_link.snapshot()
                if self.member_link is not None
                else {}
            )
            if error is None:
                joined_root_url = str(member.get("root_url") or root_url).strip()
                if joined_root_url:
                    self._set_setting("member_root_url", joined_root_url.rstrip("/"))
                snapshot = self._connect_snapshot(
                    "member", request_id, member_status=member
                )
                connected = bool(member.get("connected"))
                snapshot["current"].update(
                    {
                        "status": "connected" if connected else "connecting",
                        "degraded": False,
                        "pending": not connected,
                        "error": "",
                        "summary": (
                            "The phone joined the subnet and the Hub link is ready."
                            if connected
                            else "Join accepted; the phone is establishing the Hub link."
                        ),
                        "join_code": "",
                        "join_status": "joined",
                        "source": "android_member_join",
                    }
                )
            else:
                snapshot = self._connect_snapshot(
                    "member", request_id, member_status=member
                )
                snapshot["current"].update(
                    {
                        "status": "error",
                        "degraded": True,
                        "pending": False,
                        "error": f"{type(error).__name__}:{str(error)[:180]}",
                        "summary": "The phone could not join the subnet. Check that the one-time code is still valid.",
                        "join_code": "",
                        "join_status": "error",
                        "source": "android_member_join",
                    }
                )
            self._member_join_thread = None
        self._set_paths({"data/adaos_connect": snapshot})

    def _disconnect_member(self, arguments: dict[str, Any]) -> dict[str, Any]:
        if self.member_link is None:
            raise AndroidSkillError("android_member_link_not_ready")
        with self._lock:
            self._member_join_generation += 1
        result = self.member_link.disconnect(forget=bool(arguments.get("forget")))
        return self.project_member_link(result)

    @staticmethod
    def _dialog_agent(agent_id: str) -> dict[str, Any]:
        return dict(
            _DIALOG_AGENT_BY_ID.get(str(agent_id), _DIALOG_AGENT_BY_ID[_GENERAL_DIALOG_AGENT_ID])
        )

    def _member_link_connected(self) -> bool:
        if self.member_link is None:
            return False
        snapshot = getattr(self.member_link, "snapshot", None)
        if not callable(snapshot):
            # Protocol fakes and older adapters which expose callable Hub RPC
            # but no status projection are treated as an available link.
            return callable(getattr(self.member_link, "call_hub_tool", None))
        try:
            return bool(snapshot().get("connected"))
        except Exception:
            return False

    def _dialog_agent_projection(self, agent: dict[str, Any]) -> dict[str, Any]:
        projected = {
            key: copy.deepcopy(value)
            for key, value in agent.items()
            if key != "aliases"
        }
        projected["capabilities"] = list(projected.get("capabilities") or [])
        hub_connected = self._member_link_connected()
        hub_companion = (
            str(projected.get("owner") or "") == "skill:conversation_companions"
        )
        if hub_companion and hub_connected:
            implementation = "hub_delegated"
            availability = "ready"
            scope = "hub"
            model_backed = True
            capabilities = [
                item
                for item in projected["capabilities"]
                if item != "local_companion"
            ]
            if "remote_llm" not in capabilities:
                capabilities.append("remote_llm")
            projected["capabilities"] = capabilities
        elif hub_companion:
            implementation = "android_offline_fallback"
            availability = "degraded"
            scope = "local"
            model_backed = False
        else:
            implementation = "android_local_bounded"
            availability = "ready"
            scope = "local"
            model_backed = False
        projected.update(
            {
                "availability": availability,
                "implementation": implementation,
                "model_backed": model_backed,
                "full_runtime": hub_companion and hub_connected,
                "scope": scope,
                "llm_route_status": (
                    "root_connected_unverified"
                    if hub_companion and hub_connected
                    else "offline_fallback"
                    if hub_companion
                    else "not_applicable"
                ),
                "llm_last_route": self._last_dialog_route if hub_companion else "not_used",
                "llm_last_error": self._last_dialog_error if hub_companion else "",
                "llm_last_route_at": self._last_dialog_route_at if hub_companion else "",
                "voice_profile": {
                    "lang": "ru-RU",
                    "gender": projected.get("gender"),
                    "voice": projected.get("voice"),
                },
            }
        )
        return projected

    def _active_dialog_agent(self) -> dict[str, Any]:
        agent_id = self._setting("dialog_active_agent_id", _GENERAL_DIALOG_AGENT_ID)
        return self._dialog_agent(agent_id)

    def _preferred_conversational_agent(self) -> dict[str, Any]:
        agent_id = self._setting("dialog_conversational_agent_id", _ARSENI_AGENT_ID)
        agent = self._dialog_agent(agent_id)
        if agent.get("channel_id") != "conversational":
            return self._dialog_agent(_ARSENI_AGENT_ID)
        return agent

    def _dialog_channel_agent(self, channel_id: str) -> dict[str, Any]:
        normalized = str(channel_id or "general").strip().lower() or "general"
        if normalized == "conversational":
            return self._preferred_conversational_agent()
        return self._dialog_agent(
            _DIALOG_CHANNEL_DEFAULTS.get(normalized, _GENERAL_DIALOG_AGENT_ID)
        )

    def _dialog_snapshot(self, *, event: str = "snapshot") -> dict[str, Any]:
        active_agent = self._active_dialog_agent()
        active_channel_id = str(active_agent.get("channel_id") or "general")
        agents = [self._dialog_agent_projection(dict(item)) for item in _DIALOG_AGENTS]
        channel_specs = (
            ("general", "General", "core:general_assistant", "voice_chat_skill", "handle_text"),
            (
                "conversational",
                "Conversational",
                "skill:conversation_companions",
                "conversation_companions",
                "talk",
            ),
            ("builder", "Builder", "skill:builder_skill", "builder_skill", "chat"),
        )
        channels: list[dict[str, Any]] = []
        for channel_id, label, owner, default_skill, default_tool in channel_specs:
            channel_agent = (
                active_agent
                if channel_id == active_channel_id
                else self._dialog_channel_agent(channel_id)
            )
            agent_projection = self._dialog_agent_projection(channel_agent)
            channels.append(
                {
                    "id": channel_id,
                    "channel_id": channel_id,
                    "label": label,
                    "owner": owner,
                    "route_id": "voice_chat",
                    "conversation_id": f"conv.android.{channel_id}.desktop",
                    "default_skill": default_skill,
                    "default_tool": default_tool,
                    "active_agent_id": agent_projection["id"],
                    "active_agent_label": agent_projection["label"],
                    "active_agent_gender": agent_projection.get("gender"),
                    "active_agent_voice": agent_projection.get("voice"),
                    "active_agent_icon": agent_projection.get("icon"),
                    "active_agent": agent_projection,
                    "active": channel_id == active_channel_id,
                    "implementation": agent_projection.get(
                        "implementation", "android_local_bounded"
                    ),
                }
            )
        active_projection = self._dialog_agent_projection(active_agent)
        active_channel = next(
            item for item in channels if item["id"] == active_channel_id
        )
        hub_connected = self._member_link_connected()
        return {
            "schema": "adaos.dialog.android.v1",
            "active_channel_id": active_channel_id,
            "active_channel": copy.deepcopy(active_channel),
            "active_agent": active_projection,
            "channels": channels,
            "agents": agents,
            "implementation": {
                "id": "android_hub_delegated" if hub_connected else "android_local_bounded",
                "status": "ready" if hub_connected else "degraded",
                "model_backed": hub_connected,
                "full_builder_runtime": False,
                "limitations": [
                    (
                        "conversation companions run on the connected Hub"
                        if hub_connected
                        else "conversation companions use the bounded offline fallback"
                    ),
                    "no skill generation or subprocess execution",
                ],
            },
            "event": event,
            "webspace_id": "desktop",
            "updated_at": _utc_now(),
        }

    def _activate_dialog_agent(
        self,
        agent_id: str,
        *,
        event: str,
        project: bool = True,
    ) -> dict[str, Any]:
        normalized = str(agent_id or "").strip()
        if normalized not in _DIALOG_AGENT_BY_ID:
            raise AndroidSkillError(f"dialog_agent_not_available_android_poc:{normalized}")
        agent = self._dialog_agent(normalized)
        self._set_setting("dialog_active_agent_id", normalized)
        if agent.get("channel_id") == "conversational":
            self._set_setting("dialog_conversational_agent_id", normalized)
        snapshot = self._dialog_snapshot(event=event)
        if project:
            voice = self._voice_current()
            voice = dict(voice)
            voice["assistant"] = self._dialog_agent_projection(agent)
            voice["updated_at"] = _utc_now()
            self._set_paths({"data/dialog": snapshot, "data/voice_chat": voice})
        return snapshot

    def select_dialog_channel(self, payload: dict[str, Any]) -> dict[str, Any]:
        channel_id = str(
            payload.get("channel_id") or payload.get("id") or payload.get("value") or ""
        ).strip().lower()
        if channel_id in {"", "default"}:
            channel_id = "general"
        if channel_id not in _DIALOG_CHANNEL_DEFAULTS:
            raise AndroidSkillError(
                f"dialog_channel_not_available_android_poc:{channel_id}"
            )
        agent = self._dialog_channel_agent(channel_id)
        snapshot = self._activate_dialog_agent(
            str(agent["id"]), event="channel_selected"
        )
        return {
            "ok": True,
            "accepted": True,
            "channel_id": snapshot["active_channel_id"],
            "active_agent": snapshot["active_agent"],
        }

    def select_dialog_agent(self, payload: dict[str, Any]) -> dict[str, Any]:
        agent_id = str(
            payload.get("agent_id")
            or payload.get("active_agent_id")
            or payload.get("id")
            or ""
        ).strip()
        snapshot = self._activate_dialog_agent(agent_id, event="agent_selected")
        return {
            "ok": True,
            "accepted": True,
            "channel_id": snapshot["active_channel_id"],
            "active_agent": snapshot["active_agent"],
        }

    def _addressed_dialog_agent(self, text: str) -> tuple[dict[str, Any], str] | None:
        value = str(text or "").strip()
        lowered = value.casefold()
        for agent in _DIALOG_AGENTS:
            aliases = sorted(
                (str(item) for item in agent.get("aliases") or ()),
                key=len,
                reverse=True,
            )
            for alias in aliases:
                match = re.match(
                    rf"^\s*{re.escape(alias)}(?:\s*[,;:.!?-]\s*|\s+)(?P<rest>.*)$",
                    value,
                    re.IGNORECASE | re.UNICODE,
                )
                if lowered == alias.casefold():
                    return dict(agent), ""
                if match:
                    return dict(agent), str(match.group("rest") or "").strip()
        return None

    def _empty_voice_chat(self) -> dict[str, Any]:
        return {
            "messages": [],
            "status": "ready",
            "assistant": self._dialog_agent_projection(self._active_dialog_agent()),
            "updated_at": _utc_now(),
        }

    def _voice_current(self) -> dict[str, Any]:
        current = _plain_at_path(self.store.snapshot_json(), "data/voice_chat")
        return current if isinstance(current, dict) else self._empty_voice_chat()

    def _nlu_status(self) -> dict[str, Any]:
        runtime = self._nlu_runtime
        if runtime is None:
            return {
                "status": "failed",
                "provider": "rasa",
                "mode": "always",
                "training": "off_device",
                "error": self._nlu_error or "rasa_mobile_bundle_unavailable",
            }
        return {
            "status": "ready",
            "provider": "rasa",
            "mode": "always",
            "training": "off_device",
            **runtime.describe(),
        }

    def _parse_nlu(self, text: str) -> dict[str, Any]:
        runtime = self._nlu_runtime
        if runtime is None:
            return {
                "text": text,
                "intent": {"name": None, "confidence": 0.0},
                "intent_ranking": [],
                "entities": [],
                "error": self._nlu_error or "rasa_mobile_bundle_unavailable",
            }
        try:
            return runtime.parse(text)
        except Exception as exc:
            self._nlu_error = f"{type(exc).__name__}:{str(exc)[:200]}"
            return {
                "text": text,
                "intent": {"name": None, "confidence": 0.0},
                "intent_ranking": [],
                "entities": [],
                "error": self._nlu_error,
            }

    def _dispatch_nlu_teacher(
        self,
        *,
        text: str,
        nlu_result: dict[str, Any],
        request_id: str,
        webspace_id: str,
    ) -> bool:
        intent = nlu_result.get("intent")
        confidence = (
            float(intent.get("confidence") or 0.0) if isinstance(intent, dict) else 0.0
        )
        if confidence >= _RASA_LOW_CONFIDENCE:
            return False
        member_link = self.member_link
        if member_link is None:
            return False
        return bool(
            member_link.send_bus_event(
                "nlp.intent.not_obtained",
                {
                    "text": text,
                    "utterance": text,
                    "reason": "low_confidence",
                    "via": "rasa_android",
                    "confidence": confidence,
                    "request_id": request_id,
                    "webspace_id": webspace_id,
                    "_meta": {
                        "route_id": "voice_chat",
                        "runtime_profile": "android",
                        "rasa_model_id": (
                            self._nlu_runtime.metadata.get("model_id")
                            if self._nlu_runtime is not None
                            else None
                        ),
                    },
                },
                source="android.nlu.rasa",
            )
        )

    def _hub_dialog_response(
        self,
        text: str,
        agent: dict[str, Any],
        *,
        webspace_id: str,
    ) -> tuple[str | None, bool]:
        if str(agent.get("owner") or "") != "skill:conversation_companions":
            return None, False
        member_link = self.member_link
        if member_link is None:
            return None, False
        character_id = str(agent.get("id") or "").rsplit(":", 1)[-1]
        try:
            result = member_link.call_hub_tool(
                "conversation_companions:talk",
                {
                    "text": text,
                    "character_id": character_id,
                    "mode": "single",
                    "webspace_id": webspace_id,
                    "_meta": {
                        "dialog_channel_id": agent.get("channel_id"),
                        "active_agent_id": agent.get("id"),
                        "runtime_profile": "android",
                    },
                },
                timeout=40.0,
            )
        except Exception as exc:
            self._last_dialog_route = "root_rpc_failed"
            self._last_dialog_error = f"{type(exc).__name__}:{str(exc)[:180]}"
            self._last_dialog_route_at = _utc_now()
            return None, False
        if not isinstance(result, dict):
            self._last_dialog_route = "root_result_invalid"
            self._last_dialog_error = "conversation_companions_result_invalid"
            self._last_dialog_route_at = _utc_now()
            return None, False
        message = str(result.get("message") or "").strip()
        used_llm = bool(result.get("used_llm"))
        self._last_dialog_route = "root_llm" if used_llm else "root_skill_without_llm"
        self._last_dialog_error = ""
        self._last_dialog_route_at = _utc_now()
        return (message or None), used_llm

    def handle_dialog_message(self, payload: dict[str, Any]) -> dict[str, Any]:
        text = str(payload.get("text") or "").strip()[:_MAX_VOICE_TEXT_CHARS]
        if not text:
            raise AndroidSkillError("voice_assistant_text_required")
        meta = payload.get("_meta") if isinstance(payload.get("_meta"), dict) else {}
        webspace_id = str(payload.get("webspace_id") or meta.get("webspace_id") or "desktop")
        nlu_result = self._parse_nlu(text)
        requested_agent_id = str(
            payload.get("active_agent_id") or meta.get("active_agent_id") or ""
        ).strip()
        requested_channel_id = str(
            payload.get("dialog_channel_id") or meta.get("dialog_channel_id") or ""
        ).strip().lower()
        active_agent = self._active_dialog_agent()
        if requested_agent_id and requested_agent_id in _DIALOG_AGENT_BY_ID:
            active_agent = self._dialog_agent(requested_agent_id)
            self._activate_dialog_agent(
                requested_agent_id, event="message_target", project=False
            )
        elif (
            requested_channel_id in _DIALOG_CHANNEL_DEFAULTS
            and requested_channel_id != active_agent.get("channel_id")
        ):
            active_agent = self._dialog_channel_agent(requested_channel_id)
            self._activate_dialog_agent(
                str(active_agent["id"]), event="message_channel", project=False
            )
        addressed = self._addressed_dialog_agent(text)
        response_input = text
        if addressed is not None:
            active_agent, addressed_text = addressed
            self._activate_dialog_agent(
                str(active_agent["id"]), event="addressed_agent", project=False
            )
            response_input = addressed_text or "привет"
        now = time.time()
        turn_id = uuid.uuid4().hex[:12]
        teacher_dispatched = self._dispatch_nlu_teacher(
            text=text,
            nlu_result=nlu_result,
            request_id=f"mobile-{turn_id}",
            webspace_id=webspace_id,
        )
        response_text, used_llm = self._hub_dialog_response(
            response_input,
            active_agent,
            webspace_id=webspace_id,
        )
        response_source = "hub_skill_llm" if response_text and used_llm else "hub_skill"
        if not response_text:
            response_text = self._voice_response(response_input, active_agent)
            response_source = "android_offline_fallback"
            used_llm = False
            if str(active_agent.get("owner") or "") == "skill:conversation_companions":
                if self._last_dialog_route not in {"root_rpc_failed", "root_result_invalid"}:
                    self._last_dialog_route = "android_offline_fallback"
                    self._last_dialog_error = "hub_link_unavailable"
                    self._last_dialog_route_at = _utc_now()
        current = self._voice_current()
        messages = [
            dict(item)
            for item in current.get("messages") or []
            if isinstance(item, dict)
        ]
        messages.extend(
            [
                {
                    "id": f"mobile-user-{turn_id}",
                    "from": "user",
                    "text": text,
                    "ts": now,
                    "dialog_channel_id": active_agent["channel_id"],
                    "active_agent_id": active_agent["id"],
                },
                {
                    "id": f"mobile-assistant-{turn_id}",
                    "from": "hub",
                    "text": response_text,
                    "ts": now + 0.001,
                    "dialog_channel_id": active_agent["channel_id"],
                    "active_agent_id": active_agent["id"],
                    "active_agent_label": active_agent["label"],
                    "active_agent_gender": active_agent.get("gender"),
                    "active_agent_voice": active_agent.get("voice"),
                    "active_agent_icon": active_agent.get("icon"),
                    "voice": active_agent.get("voice"),
                    "voice_profile": {
                        "lang": "ru-RU",
                        "gender": active_agent.get("gender"),
                        "voice": active_agent.get("voice"),
                    },
                    "response_source": response_source,
                    "used_llm": used_llm,
                    "llm_route": self._last_dialog_route,
                    "llm_route_error": self._last_dialog_error,
                },
            ]
        )
        snapshot = {
            "messages": messages[-_MAX_VOICE_MESSAGES:],
            "status": "ready",
            "assistant": self._dialog_agent_projection(active_agent),
            "last_turn_id": turn_id,
            "updated_at": _utc_now(),
        }
        dialog = self._dialog_snapshot(event="turn")
        intent = nlu_result.get("intent") if isinstance(nlu_result.get("intent"), dict) else {}
        nlu_projection = {
            "provider": "rasa",
            "mode": "always",
            "text": text,
            "intent": dict(intent),
            "intent_ranking": [
                dict(item)
                for item in (nlu_result.get("intent_ranking") or [])[:5]
                if isinstance(item, dict)
            ],
            "entities": [
                dict(item)
                for item in nlu_result.get("entities") or []
                if isinstance(item, dict)
            ],
            "teacher_dispatched": teacher_dispatched,
            "model_id": (
                self._nlu_runtime.metadata.get("model_id")
                if self._nlu_runtime is not None
                else None
            ),
            "updated_at": _utc_now(),
        }
        if nlu_result.get("error"):
            nlu_projection["error"] = str(nlu_result.get("error"))
        self._set_paths(
            {
                "data/voice_chat": snapshot,
                "data/dialog": dialog,
                "data/nlu/current": nlu_projection,
            }
        )
        return {
            "ok": True,
            "accepted": True,
            "turn_id": turn_id,
            "response": response_text,
            "message_count": len(snapshot["messages"]),
            "dialog_channel_id": active_agent["channel_id"],
            "active_agent_id": active_agent["id"],
            "active_agent_label": active_agent["label"],
            "response_source": response_source,
            "used_llm": used_llm,
            "llm_route": self._last_dialog_route,
            "llm_route_error": self._last_dialog_error,
            "nlu": nlu_projection,
        }

    def _voice_response(self, text: str, agent: dict[str, Any]) -> str:
        normalized = " ".join(text.casefold().split())
        label = str(agent.get("label") or "AdaOS Mobile")
        if any(
            token in normalized
            for token in (
                "кто доступен",
                "какие ассистенты",
                "список ассистентов",
                "покажи ассистентов",
                "agent roster",
            )
        ):
            return (
                "Локально доступны AdaOS Mobile, Арсений, Ника, Мира и Строитель. "
                "Это ограниченные Android-персоны без LLM: статус ноды, погода, "
                "заметки и базовые подсказки."
            )
        if any(token in normalized for token in ("погод", "weather")):
            city = "Moscow"
            city_aliases = {
                "москв": "Moscow",
                "берлин": "Berlin",
                "париж": "Paris",
                "токио": "Tokyo",
                "нью-йорк": "New York",
                "new york": "New York",
            }
            for token, candidate in city_aliases.items():
                if token in normalized:
                    city = candidate
                    break
            weather = self._weather_event(
                {"city": city, "request_id": f"voice-{uuid.uuid4().hex[:10]}"}
            )
            return str((weather.get("current") or {}).get("summary") or "Weather is unavailable.")
        if any(token in normalized for token in ("статус", "состояние ноды", "node status")):
            node = self._subnet_snapshot()
            return (
                f"Нода {node.get('node_label') or 'Android phone'} готова. "
                f"Локальная зона {node.get('subnet_id') or self.subnet_id}."
            )
        if normalized.startswith(("создай заметку", "запиши заметку", "create note")):
            content = text
            for prefix in ("создай заметку", "запиши заметку", "create note"):
                if normalized.startswith(prefix):
                    content = text[len(prefix):].lstrip(" :,-")
                    break
            if not content:
                return "Скажите текст заметки после команды «создай заметку»."
            self._notebook_create({"content": content})
            return "Заметка сохранена локально на телефоне."
        if any(token in normalized for token in ("привет", "здравств", "hello", "hi")):
            return (
                f"Привет! Я {label}, локальный ассистент в экспериментальном "
                "Android-профиле AdaOS."
            )
        if agent.get("id") == _BUILDER_AGENT_ID:
            return (
                "Я Строитель в ограниченном мобильном режиме. Могу обсудить "
                "архитектуру телефона-ноды, проверить её статус и сохранить план "
                "в Notebook. Сборка навыков, subprocess и модельный runtime здесь "
                "не запускаются."
            )
        if agent.get("id") == _ARSENI_AGENT_ID:
            return (
                "Я Арсений в локальном мобильном режиме. Пока могу спокойно помочь "
                "со статусом ноды, погодой и заметками; свободный LLM-диалог не включён."
            )
        if agent.get("id") == _NIKA_AGENT_ID:
            return (
                "Я Ника в локальном мобильном режиме. Могу проверить факты о состоянии "
                "ноды, погоде и заметках; модельная дискуссия пока недоступна."
            )
        if agent.get("id") == _MIRA_AGENT_ID:
            return (
                "Я Мира в локальном мобильном режиме. Могу поддержать короткий "
                "сценарий со статусом, погодой и заметками; LLM пока не подключён."
            )
        return (
            f"Я {label} и работаю локально в экспериментальном Android-профиле. "
            "Сейчас я умею сообщать статус ноды, узнавать погоду и создавать заметки."
        )

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
            "selection": {"metric_id": "cpu", "updated_at": _utc_now()},
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
            self._closed = True
            self._connect_prepare_generation += 1
            self._member_join_generation += 1
            self._database.commit()
            self._database.close()
