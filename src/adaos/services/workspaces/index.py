from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional, Iterable, List, Any
import sqlite3
import json

from adaos.services.agent_context import get_ctx
from adaos.services.eventbus import emit
from adaos.services.yjs.store import ystore_path_for_webspace
from adaos.services.yjs.webspace import coerce_webspace_id, default_webspace_id, dev_webspace_id

DEFAULT_HOME_SCENARIO = "web_desktop"
KIND_WORKSPACE = "workspace"
KIND_DEV = "dev"
SOURCE_MODE_WORKSPACE = "workspace"
SOURCE_MODE_DEV = "dev"

_ROW_SELECT = (
    "workspace_id, path, created_at, display_name, "
    "kind, home_scenario, source_mode, owner_scope, profile_scope, device_binding, ui_overlay_json"
)

_UNSET = object()


def _normalize_workspace_id(value: Any) -> str:
    return coerce_webspace_id(value, fallback=default_webspace_id())


def _canonical_manifest(manifest: "WebspaceManifest") -> "WebspaceManifest":
    workspace_id = _normalize_workspace_id(manifest.workspace_id)
    if workspace_id == manifest.workspace_id:
        return manifest
    return replace(
        manifest,
        workspace_id=workspace_id,
        path=str(ystore_path_for_webspace(workspace_id)),
    )


def _dedupe_manifest_rows(rows: Iterable["WebspaceManifest"]) -> List["WebspaceManifest"]:
    manifests = list(rows)
    raw_ids = {str(row.workspace_id or "").strip() for row in manifests}
    seen: set[str] = set()
    out: List[WebspaceManifest] = []
    for row in manifests:
        raw_id = str(row.workspace_id or "").strip()
        normalized_id = _normalize_workspace_id(raw_id)
        if raw_id != normalized_id and normalized_id in raw_ids:
            continue
        manifest = _canonical_manifest(row)
        if manifest.workspace_id in seen:
            continue
        seen.add(manifest.workspace_id)
        out.append(manifest)
    return out


def _workspace_event_payload(
    row: "WebspaceManifest",
    *,
    catalog_version: int | None = None,
) -> dict[str, Any]:
    return {
        "workspace_id": row.workspace_id,
        "display_name": row.display_name,
        "kind": row.kind,
        "home_scenario": row.home_scenario,
        "source_mode": row.source_mode,
        "owner_scope": row.owner_scope,
        "profile_scope": row.profile_scope,
        "device_binding": row.device_binding,
        "catalog_version": (
            int(catalog_version)
            if catalog_version is not None
            else workspace_catalog_version()
        ),
    }


def _emit_workspace_event(
    event_type: str,
    row: "WebspaceManifest" | None = None,
    *,
    workspace_id: str | None = None,
    catalog_version: int | None = None,
) -> None:
    try:
        ctx = get_ctx()
        payload = (
            _workspace_event_payload(row, catalog_version=catalog_version)
            if row is not None
            else {
                "workspace_id": str(workspace_id or "").strip(),
                "catalog_version": workspace_catalog_version(),
            }
        )
        emit(ctx.bus, event_type, payload, "workspaces.index")
    except Exception:
        pass


def _is_dev_display_name(value: Optional[str]) -> bool:
    if not value:
        return False
    return str(value).lstrip().upper().startswith("DEV:")


def _normalize_optional_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _dedupe_text_list(values: Any) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        token = str(value or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _normalize_overlay_widget_list(values: Any) -> list[dict[str, Any]]:
    if not isinstance(values, list):
        return []
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        item_id = _normalize_optional_text(value.get("id"))
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        try:
            payload = json.loads(json.dumps(value, ensure_ascii=True))
        except Exception:
            payload = {str(k): v for k, v in value.items()}
        payload["id"] = item_id
        item_type = _normalize_optional_text(payload.get("type"))
        if item_type is not None:
            payload["type"] = item_type
        out.append(payload)
    return out


def _clone_overlay_json_dict(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    try:
        payload = json.loads(json.dumps(value, ensure_ascii=True))
    except Exception:
        payload = {str(k): v for k, v in value.items()}
    return payload if isinstance(payload, dict) else {}


def _clone_overlay_json_list(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    try:
        payload = json.loads(json.dumps(value, ensure_ascii=True))
    except Exception:
        payload = list(value)
    return payload if isinstance(payload, list) else []


def _clone_overlay_text_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        token = str(item or "").strip()
        if not token or token in seen:
            continue
        seen.add(token)
        out.append(token)
    return out


def _normalize_home_scenario_ref(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    scenario_id = _normalize_optional_text(
        value.get("scenario_id") or value.get("id") or value.get("name")
    )
    if not scenario_id:
        return {}
    out: dict[str, Any] = {"scenario_id": scenario_id}
    node_id = _normalize_optional_text(value.get("node_id") or value.get("nodeId"))
    if node_id:
        out["node_id"] = node_id
    node_label = _normalize_optional_text(
        value.get("node_label")
        or value.get("nodeLabel")
        or value.get("node_name")
        or value.get("nodeName")
    )
    if node_label:
        out["node_label"] = node_label
    title = _normalize_optional_text(value.get("title") or value.get("scenario_title"))
    if title and title != scenario_id:
        out["title"] = title
    return out


def _normalize_current_scenario(value: Any) -> Optional[str]:
    return _normalize_optional_text(value)


def _normalize_ui_overlay_payload(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    desktop_raw = value.get("desktop") if isinstance(value.get("desktop"), dict) else {}
    legacy_installed_raw = value.get("installed") if isinstance(value.get("installed"), dict) else {}
    installed_source = (
        desktop_raw.get("installed")
        if isinstance(desktop_raw.get("installed"), dict)
        else legacy_installed_raw
    )
    legacy_pinned_raw = value.get("pinnedWidgets")
    has_installed = "installed" in desktop_raw or "installed" in value
    has_pinned_widgets = "pinnedWidgets" in desktop_raw or "pinnedWidgets" in value
    installed = {
        "apps": _dedupe_text_list(installed_source.get("apps") if isinstance(installed_source, dict) else []),
        "widgets": _dedupe_text_list(installed_source.get("widgets") if isinstance(installed_source, dict) else []),
        "removedApps": _dedupe_text_list(
            installed_source.get("removedApps") if isinstance(installed_source, dict) else []
        ),
        "removedWidgets": _dedupe_text_list(
            installed_source.get("removedWidgets") if isinstance(installed_source, dict) else []
        ),
    }
    pinned_widgets_source = desktop_raw.get("pinnedWidgets") if "pinnedWidgets" in desktop_raw else legacy_pinned_raw
    pinned_widgets = _normalize_overlay_widget_list(pinned_widgets_source)
    legacy_icon_order_raw = value.get("iconOrder")
    legacy_widget_order_raw = value.get("widgetOrder")
    icon_order_source = desktop_raw.get("iconOrder") if "iconOrder" in desktop_raw else legacy_icon_order_raw
    widget_order_source = desktop_raw.get("widgetOrder") if "widgetOrder" in desktop_raw else legacy_widget_order_raw
    has_icon_order = "iconOrder" in desktop_raw or "iconOrder" in value
    has_widget_order = "widgetOrder" in desktop_raw or "widgetOrder" in value
    icon_order = _clone_overlay_text_list(icon_order_source)
    widget_order = _clone_overlay_text_list(widget_order_source)
    legacy_hidden_sections_raw = value.get("hiddenSections")
    hidden_sections_source = (
        desktop_raw.get("hiddenSections") if "hiddenSections" in desktop_raw else legacy_hidden_sections_raw
    )
    has_hidden_sections = "hiddenSections" in desktop_raw or "hiddenSections" in value
    hidden_sections = _clone_overlay_text_list(hidden_sections_source)
    overlay: dict[str, Any] = {}
    desktop: dict[str, Any] = {}
    workspace_raw = value.get("workspace") if isinstance(value.get("workspace"), dict) else {}
    home_scenario_ref_source = (
        workspace_raw.get("homeScenarioRef")
        if "homeScenarioRef" in workspace_raw
        else value.get("homeScenarioRef")
    )
    home_scenario_ref = _normalize_home_scenario_ref(home_scenario_ref_source)
    has_current_scenario = (
        "currentScenario" in workspace_raw
        or "current_scenario" in workspace_raw
        or "currentScenario" in value
        or "current_scenario" in value
    )
    current_scenario_source = (
        workspace_raw.get("currentScenario")
        if "currentScenario" in workspace_raw
        else workspace_raw.get("current_scenario")
        if "current_scenario" in workspace_raw
        else value.get("currentScenario")
        if "currentScenario" in value
        else value.get("current_scenario")
    )
    current_scenario = _normalize_current_scenario(current_scenario_source)
    if has_installed or installed["apps"] or installed["widgets"] or installed["removedApps"] or installed["removedWidgets"]:
        desktop["installed"] = installed
    if has_pinned_widgets or pinned_widgets:
        desktop["pinnedWidgets"] = pinned_widgets
    if has_icon_order or icon_order:
        desktop["iconOrder"] = icon_order
    if has_widget_order or widget_order:
        desktop["widgetOrder"] = widget_order
    if has_hidden_sections or hidden_sections:
        desktop["hiddenSections"] = hidden_sections
    if desktop:
        overlay["desktop"] = desktop
    workspace_overlay: dict[str, Any] = {}
    if home_scenario_ref:
        workspace_overlay["homeScenarioRef"] = home_scenario_ref
    if has_current_scenario:
        workspace_overlay["currentScenario"] = current_scenario or ""
    if workspace_overlay:
        overlay["workspace"] = workspace_overlay
    return overlay


def _encode_ui_overlay_json(value: Any) -> Optional[str]:
    overlay = _normalize_ui_overlay_payload(value)
    if not overlay:
        return None
    return json.dumps(overlay, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def _decode_ui_overlay_json(value: Any) -> dict[str, Any]:
    token = _normalize_optional_text(value)
    if not token:
        return {}
    try:
        payload = json.loads(token)
    except Exception:
        return {}
    return _normalize_ui_overlay_payload(payload)


def _normalize_kind(value: Any) -> Optional[str]:
    token = str(value or "").strip().lower()
    if token in (KIND_WORKSPACE, KIND_DEV):
        return token
    return None


def _is_dev_workspace_id(value: Any) -> bool:
    token = str(value or "").strip().lower()
    default_dev_id = str(dev_webspace_id() or "").strip().lower()
    return bool(token and (token == default_dev_id or token.endswith("-dev")))


def _infer_kind(workspace_id: str, display_name: Optional[str], kind: Optional[str]) -> str:
    explicit = _normalize_kind(kind)
    if explicit:
        return explicit
    # Compatibility only for manifests created before kind was persisted.
    # The suffix classifies a legacy row; it never establishes a relation.
    if _is_dev_workspace_id(workspace_id):
        return KIND_DEV
    if _is_dev_display_name(display_name):
        return KIND_DEV
    return KIND_WORKSPACE


def _normalize_source_mode(value: Any) -> Optional[str]:
    token = str(value or "").strip().lower()
    if token in (SOURCE_MODE_WORKSPACE, SOURCE_MODE_DEV):
        return token
    return None


def _infer_source_mode(source_mode: Optional[str], *, kind: str) -> str:
    if kind == KIND_DEV:
        return SOURCE_MODE_DEV
    explicit = _normalize_source_mode(source_mode)
    if explicit:
        return explicit
    return SOURCE_MODE_WORKSPACE


def _default_display_name(workspace_id: str, *, kind: str) -> str:
    token = str(workspace_id or "").strip() or default_webspace_id()
    if kind == KIND_DEV:
        return f"DEV: {token}"
    return token


@dataclass(slots=True)
class WebspaceManifest:
    workspace_id: str
    path: str
    created_at: int
    display_name: Optional[str] = None
    kind: Optional[str] = None
    home_scenario: Optional[str] = None
    source_mode: Optional[str] = None
    owner_scope: Optional[str] = None
    profile_scope: Optional[str] = None
    device_binding: Optional[str] = None
    ui_overlay_json: Optional[str] = None

    @property
    def effective_kind(self) -> str:
        return _infer_kind(self.workspace_id, self.display_name, self.kind)

    @property
    def is_dev(self) -> bool:
        return self.effective_kind == KIND_DEV

    @property
    def effective_source_mode(self) -> str:
        return _infer_source_mode(self.source_mode, kind=self.effective_kind)

    @property
    def effective_home_scenario(self) -> str:
        token = _normalize_optional_text(self.home_scenario)
        return token or DEFAULT_HOME_SCENARIO

    @property
    def title(self) -> str:
        token = _normalize_optional_text(self.display_name)
        if token:
            return token
        return _default_display_name(self.workspace_id, kind=self.effective_kind)

    def with_defaults(self) -> "WebspaceManifest":
        return WebspaceManifest(
            workspace_id=self.workspace_id,
            path=str(ystore_path_for_webspace(self.workspace_id)),
            created_at=self.created_at,
            display_name=self.title,
            kind=self.effective_kind,
            home_scenario=self.home_scenario,
            source_mode=self.effective_source_mode,
            owner_scope=_normalize_optional_text(self.owner_scope),
            profile_scope=_normalize_optional_text(self.profile_scope),
            device_binding=_normalize_optional_text(self.device_binding),
            ui_overlay_json=_encode_ui_overlay_json(_decode_ui_overlay_json(self.ui_overlay_json)),
        )

    @property
    def ui_overlay(self) -> dict[str, Any]:
        return _decode_ui_overlay_json(self.ui_overlay_json)

    @property
    def workspace_overlay(self) -> dict[str, Any]:
        workspace = self.ui_overlay.get("workspace") if isinstance(self.ui_overlay.get("workspace"), dict) else {}
        out: dict[str, Any] = {}
        if "homeScenarioRef" in workspace:
            normalized = _normalize_home_scenario_ref(workspace.get("homeScenarioRef"))
            if normalized:
                out["homeScenarioRef"] = normalized
        if "currentScenario" in workspace or "current_scenario" in workspace:
            out["currentScenario"] = _normalize_current_scenario(
                workspace.get("currentScenario") if "currentScenario" in workspace else workspace.get("current_scenario")
            ) or ""
        return out

    @property
    def desktop_overlay(self) -> dict[str, Any]:
        desktop = self.ui_overlay.get("desktop") if isinstance(self.ui_overlay.get("desktop"), dict) else {}
        out: dict[str, Any] = {}
        if "installed" in desktop:
            installed = desktop.get("installed") if isinstance(desktop.get("installed"), dict) else {}
            out["installed"] = {
                "apps": _dedupe_text_list(installed.get("apps")),
                "widgets": _dedupe_text_list(installed.get("widgets")),
                "removedApps": _dedupe_text_list(installed.get("removedApps")),
                "removedWidgets": _dedupe_text_list(installed.get("removedWidgets")),
            }
        if "pinnedWidgets" in desktop:
            out["pinnedWidgets"] = _normalize_overlay_widget_list(desktop.get("pinnedWidgets"))
        if "topbar" in desktop:
            out["topbar"] = _clone_overlay_json_list(desktop.get("topbar"))
        if "pageSchema" in desktop:
            out["pageSchema"] = _clone_overlay_json_dict(desktop.get("pageSchema"))
        if "iconOrder" in desktop:
            out["iconOrder"] = _clone_overlay_text_list(desktop.get("iconOrder"))
        if "widgetOrder" in desktop:
            out["widgetOrder"] = _clone_overlay_text_list(desktop.get("widgetOrder"))
        if "hiddenSections" in desktop:
            out["hiddenSections"] = _clone_overlay_text_list(desktop.get("hiddenSections"))
        return out

    @property
    def installed_overlay(self) -> dict[str, list[str]]:
        installed = self.desktop_overlay.get("installed") if isinstance(self.desktop_overlay.get("installed"), dict) else {}
        return {
            "apps": _dedupe_text_list(installed.get("apps")),
            "widgets": _dedupe_text_list(installed.get("widgets")),
            "removedApps": _dedupe_text_list(installed.get("removedApps")),
            "removedWidgets": _dedupe_text_list(installed.get("removedWidgets")),
        }

    @property
    def pinned_widgets_overlay(self) -> list[dict[str, Any]]:
        return _normalize_overlay_widget_list(self.desktop_overlay.get("pinnedWidgets"))

    @property
    def topbar_overlay(self) -> list[Any]:
        return _clone_overlay_json_list(self.desktop_overlay.get("topbar"))

    @property
    def page_schema_overlay(self) -> dict[str, Any]:
        return _clone_overlay_json_dict(self.desktop_overlay.get("pageSchema"))

    @property
    def icon_order_overlay(self) -> list[str]:
        return _clone_overlay_text_list(self.desktop_overlay.get("iconOrder"))

    @property
    def widget_order_overlay(self) -> list[str]:
        return _clone_overlay_text_list(self.desktop_overlay.get("widgetOrder"))

    @property
    def hidden_sections_overlay(self) -> list[str]:
        return _clone_overlay_text_list(self.desktop_overlay.get("hiddenSections"))

    @property
    def home_scenario_ref_overlay(self) -> dict[str, Any]:
        return _normalize_home_scenario_ref(self.workspace_overlay.get("homeScenarioRef"))

    @property
    def current_scenario_overlay(self) -> Optional[str]:
        if "currentScenario" not in self.workspace_overlay:
            return None
        return _normalize_current_scenario(self.workspace_overlay.get("currentScenario"))

    @property
    def has_installed_overlay(self) -> bool:
        return "installed" in self.desktop_overlay

    @property
    def has_pinned_widgets_overlay(self) -> bool:
        return "pinnedWidgets" in self.desktop_overlay

    @property
    def has_topbar_overlay(self) -> bool:
        return "topbar" in self.desktop_overlay

    @property
    def has_page_schema_overlay(self) -> bool:
        return "pageSchema" in self.desktop_overlay

    @property
    def has_icon_order_overlay(self) -> bool:
        return "iconOrder" in self.desktop_overlay

    @property
    def has_widget_order_overlay(self) -> bool:
        return "widgetOrder" in self.desktop_overlay

    @property
    def has_hidden_sections_overlay(self) -> bool:
        return "hiddenSections" in self.desktop_overlay

    @property
    def has_home_scenario_ref_overlay(self) -> bool:
        return "homeScenarioRef" in self.workspace_overlay

    @property
    def has_current_scenario_overlay(self) -> bool:
        return "currentScenario" in self.workspace_overlay

    @property
    def has_ui_overlay(self) -> bool:
        return bool(self.ui_overlay)


# Backward-compatible name used by current callers.
WorkspaceRow = WebspaceManifest


def _row_from_db(row: tuple[Any, ...], *, apply_defaults: bool = True) -> WebspaceManifest:
    manifest = WebspaceManifest(
        workspace_id=str(row[0]),
        path=str(row[1]),
        created_at=int(row[2]),
        display_name=_normalize_optional_text(row[3]),
        kind=_normalize_kind(row[4]),
        home_scenario=_normalize_optional_text(row[5]),
        source_mode=_normalize_source_mode(row[6]),
        owner_scope=_normalize_optional_text(row[7]),
        profile_scope=_normalize_optional_text(row[8]),
        device_binding=_normalize_optional_text(row[9]),
        ui_overlay_json=_encode_ui_overlay_json(_decode_ui_overlay_json(row[10])),
    )
    return manifest.with_defaults() if apply_defaults else manifest


def _manifest_needs_persisted_defaults(manifest: WebspaceManifest) -> bool:
    normalized = manifest.with_defaults()
    return any(
        (
            manifest.path != normalized.path,
            manifest.display_name != normalized.display_name,
            manifest.kind != normalized.kind,
            manifest.source_mode != normalized.source_mode,
            manifest.owner_scope != normalized.owner_scope,
            manifest.profile_scope != normalized.profile_scope,
            manifest.device_binding != normalized.device_binding,
            manifest.ui_overlay_json != normalized.ui_overlay_json,
        )
    )


def _persist_manifest_defaults(con, manifest: WebspaceManifest) -> WebspaceManifest:
    normalized = manifest.with_defaults()
    if not _manifest_needs_persisted_defaults(manifest):
        return normalized
    con.execute(
        """
        UPDATE y_workspaces
        SET path=?, display_name=?, kind=?, home_scenario=?, source_mode=?,
            owner_scope=?, profile_scope=?, device_binding=?, ui_overlay_json=?
        WHERE workspace_id=?
        """,
        (
            normalized.path,
            normalized.display_name,
            normalized.kind,
            manifest.home_scenario,
            normalized.source_mode,
            normalized.owner_scope,
            normalized.profile_scope,
            normalized.device_binding,
            normalized.ui_overlay_json,
            manifest.workspace_id,
        ),
    )
    return normalized


def _ensure_schema(con) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS y_workspaces(
            workspace_id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            created_at INTEGER NOT NULL,
            display_name TEXT,
            kind TEXT,
            home_scenario TEXT,
            source_mode TEXT,
            owner_scope TEXT,
            profile_scope TEXT,
            device_binding TEXT,
            ui_overlay_json TEXT
        )
        """
    )
    try:
        cols = {row[1] for row in con.execute("PRAGMA table_info(y_workspaces)")}
    except sqlite3.Error:
        cols = set()
    for name, ddl in (
        ("display_name", "TEXT"),
        ("kind", "TEXT"),
        ("home_scenario", "TEXT"),
        ("source_mode", "TEXT"),
        ("owner_scope", "TEXT"),
        ("profile_scope", "TEXT"),
        ("device_binding", "TEXT"),
        ("ui_overlay_json", "TEXT"),
    ):
        if name in cols:
            continue
        try:
            con.execute(f"ALTER TABLE y_workspaces ADD COLUMN {name} {ddl}")
        except sqlite3.OperationalError:
            pass
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS workspace_catalog_state(
            singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
            version INTEGER NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    con.execute(
        """
        INSERT OR IGNORE INTO workspace_catalog_state(singleton, version, updated_at)
        VALUES(1, 0, 0)
        """
    )


def _bump_workspace_catalog_version(con) -> int:
    import time as _time

    updated_at = int(_time.time() * 1000)
    con.execute(
        """
        UPDATE workspace_catalog_state
        SET version=version + 1, updated_at=?
        WHERE singleton=1
        """,
        (updated_at,),
    )
    row = con.execute(
        "SELECT version FROM workspace_catalog_state WHERE singleton=1"
    ).fetchone()
    return int(row[0] if row else 0)


def _delete_workspace_relations(con, workspace_id: str | None = None) -> None:
    relation_table = con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='webspace_relations'"
    ).fetchone()
    if relation_table is None:
        return
    if workspace_id is None:
        con.execute("DELETE FROM webspace_relations")
        return
    con.execute(
        "DELETE FROM webspace_relations WHERE source_webspace_id=? OR target_webspace_id=?",
        (workspace_id, workspace_id),
    )


def workspace_catalog_version() -> int:
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        row = con.execute(
            "SELECT version FROM workspace_catalog_state WHERE singleton=1"
        ).fetchone()
    return int(row[0] if row else 0)


def get_workspace(workspace_id: str) -> Optional[WebspaceManifest]:
    workspace_id = _normalize_workspace_id(workspace_id)
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        cur = con.execute(
            f"SELECT {_ROW_SELECT} FROM y_workspaces WHERE workspace_id=?",
            (workspace_id,),
        )
        row = cur.fetchone()
        manifest = None
        if row:
            raw_manifest = _row_from_db(row, apply_defaults=False)
            dirty = _manifest_needs_persisted_defaults(raw_manifest)
            manifest = _persist_manifest_defaults(con, raw_manifest)
            if dirty:
                _bump_workspace_catalog_version(con)
                con.commit()
    if not row:
        return None
    return manifest


def list_workspaces() -> List[WebspaceManifest]:
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        cur = con.execute(
            f"SELECT {_ROW_SELECT} FROM y_workspaces ORDER BY created_at"
        )
        rows = []
        dirty = False
        for db_row in cur.fetchall():
            manifest = _row_from_db(db_row, apply_defaults=False)
            if _manifest_needs_persisted_defaults(manifest):
                dirty = True
            rows.append(_persist_manifest_defaults(con, manifest))
        if dirty:
            _bump_workspace_catalog_version(con)
            con.commit()
    if not rows:
        rows = [ensure_workspace(default_webspace_id())]
    return _dedupe_manifest_rows(rows)


def normalize_workspaces() -> int:
    """
    Persist inferred manifest defaults for existing rows without changing
    legacy ``home_scenario`` semantics.

    Returns the number of rows that required normalization.
    """
    sql = get_ctx().sql
    updated = 0
    with sql.connect() as con:
        _ensure_schema(con)
        cur = con.execute(f"SELECT {_ROW_SELECT} FROM y_workspaces ORDER BY created_at")
        for db_row in cur.fetchall():
            manifest = _row_from_db(db_row, apply_defaults=False)
            if not _manifest_needs_persisted_defaults(manifest):
                continue
            _persist_manifest_defaults(con, manifest)
            updated += 1
        if updated:
            _bump_workspace_catalog_version(con)
            con.commit()
    return updated


def ensure_workspace(workspace_id: str) -> WebspaceManifest:
    """
    Ensure a workspace row exists and return it. The associated Yjs store
    path is derived from the current ctx paths.
    """
    workspace_id = _normalize_workspace_id(workspace_id)
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        cur = con.execute(
            f"SELECT {_ROW_SELECT} FROM y_workspaces WHERE workspace_id=?",
            (workspace_id,),
        )
        row = cur.fetchone()
        if row:
            raw_manifest = _row_from_db(row, apply_defaults=False)
            dirty = _manifest_needs_persisted_defaults(raw_manifest)
            manifest = _persist_manifest_defaults(con, raw_manifest)
            if dirty:
                _bump_workspace_catalog_version(con)
                con.commit()
            return manifest

        p: Path = ystore_path_for_webspace(workspace_id)
        import time as _time

        created_at = int(_time.time() * 1000)
        inferred_kind = _infer_kind(workspace_id, None, None)
        display_name = _default_display_name(workspace_id, kind=inferred_kind)
        con.execute(
            """
            INSERT INTO y_workspaces(
                workspace_id, path, created_at, display_name,
                kind, home_scenario, source_mode, owner_scope, profile_scope, device_binding, ui_overlay_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                workspace_id,
                str(p),
                created_at,
                display_name,
                inferred_kind,
                DEFAULT_HOME_SCENARIO,
                _infer_source_mode(None, kind=inferred_kind),
                None,
                None,
                None,
                None,
            ),
        )
        _bump_workspace_catalog_version(con)
        con.commit()
        manifest = WebspaceManifest(
            workspace_id=workspace_id,
            path=str(p),
            created_at=created_at,
            display_name=display_name,
            kind=inferred_kind,
            home_scenario=DEFAULT_HOME_SCENARIO,
            source_mode=_infer_source_mode(None, kind=inferred_kind),
            owner_scope=None,
            profile_scope=None,
            device_binding=None,
            ui_overlay_json=None,
        )
        _emit_workspace_event("workspace.created", manifest)
        return manifest


def set_workspace_manifest(
    workspace_id: str,
    *,
    display_name: Any = _UNSET,
    kind: Any = _UNSET,
    home_scenario: Any = _UNSET,
    source_mode: Any = _UNSET,
    owner_scope: Any = _UNSET,
    profile_scope: Any = _UNSET,
    device_binding: Any = _UNSET,
    ui_overlay_json: Any = _UNSET,
) -> WebspaceManifest:
    workspace_id = _normalize_workspace_id(workspace_id)
    current = ensure_workspace(workspace_id)
    next_display_name = current.display_name if display_name is _UNSET else _normalize_optional_text(display_name)
    next_kind_raw = current.kind if kind is _UNSET else _normalize_kind(kind)
    resolved_kind = _infer_kind(workspace_id, next_display_name, next_kind_raw)
    next_source_mode_raw = current.source_mode if source_mode is _UNSET else _normalize_source_mode(source_mode)
    resolved_source_mode = _infer_source_mode(next_source_mode_raw, kind=resolved_kind)
    next_home_scenario = current.home_scenario if home_scenario is _UNSET else _normalize_optional_text(home_scenario)
    next_owner_scope = current.owner_scope if owner_scope is _UNSET else _normalize_optional_text(owner_scope)
    next_profile_scope = current.profile_scope if profile_scope is _UNSET else _normalize_optional_text(profile_scope)
    next_device_binding = current.device_binding if device_binding is _UNSET else _normalize_optional_text(device_binding)
    next_ui_overlay_json = current.ui_overlay_json if ui_overlay_json is _UNSET else _encode_ui_overlay_json(ui_overlay_json)

    if (
        next_display_name == current.display_name
        and resolved_kind == current.kind
        and next_home_scenario == current.home_scenario
        and resolved_source_mode == current.source_mode
        and next_owner_scope == current.owner_scope
        and next_profile_scope == current.profile_scope
        and next_device_binding == current.device_binding
        and next_ui_overlay_json == current.ui_overlay_json
    ):
        return current

    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        con.execute(
            """
            UPDATE y_workspaces
            SET display_name=?, kind=?, home_scenario=?, source_mode=?,
                owner_scope=?, profile_scope=?, device_binding=?, ui_overlay_json=?
            WHERE workspace_id=?
            """,
            (
                next_display_name,
                resolved_kind,
                next_home_scenario,
                resolved_source_mode,
                next_owner_scope,
                next_profile_scope,
                next_device_binding,
                next_ui_overlay_json,
                workspace_id,
            ),
        )
        _bump_workspace_catalog_version(con)
        con.commit()
    row = get_workspace(workspace_id)
    if not row:
        raise KeyError(f"workspace {workspace_id} not found")
    _emit_workspace_event("workspace.manifest.changed", row)
    return row


def set_display_name(workspace_id: str, display_name: Optional[str]) -> WebspaceManifest:
    return set_workspace_manifest(workspace_id, display_name=display_name)


def delete_workspace(workspace_id: str) -> None:
    workspace_id = _normalize_workspace_id(workspace_id)
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        cur = con.execute("DELETE FROM y_workspaces WHERE workspace_id=?", (workspace_id,))
        deleted = int(cur.rowcount or 0) > 0
        if deleted:
            _delete_workspace_relations(con, workspace_id)
            _bump_workspace_catalog_version(con)
        con.commit()
    if not deleted:
        return
    _emit_workspace_event("workspace.deleted", workspace_id=workspace_id)
    try:
        path = ystore_path_for_webspace(workspace_id)
        if path.exists():
            path.unlink()
    except Exception:
        pass


def reset_webspaces(rows: Iterable[WorkspaceRow]) -> None:
    normalized_rows = list(rows)
    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        con.execute("DELETE FROM y_workspaces")
        _delete_workspace_relations(con)
        con.executemany(
            """
            INSERT INTO y_workspaces(
                workspace_id, path, created_at, display_name,
                kind, home_scenario, source_mode, owner_scope, profile_scope, device_binding, ui_overlay_json
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            [
                (
                    row.workspace_id,
                    row.path,
                    row.created_at,
                    row.display_name,
                    row.kind,
                    row.home_scenario,
                    row.source_mode,
                    row.owner_scope,
                    row.profile_scope,
                    row.device_binding,
                    row.ui_overlay_json,
                )
                for row in normalized_rows
            ],
        )
        _bump_workspace_catalog_version(con)
        con.commit()
    try:
        ctx = get_ctx()
        emit(
            ctx.bus,
            "workspace.reset",
            {
                "workspace_ids": [row.workspace_id for row in normalized_rows],
                "catalog_version": workspace_catalog_version(),
            },
            "workspaces.index",
        )
    except Exception:
        pass


def get_workspace_overlay(workspace_id: str) -> dict[str, Any]:
    row = get_workspace(workspace_id)
    if row is None:
        return {}
    return row.ui_overlay


def get_workspace_desktop_overlay(workspace_id: str) -> dict[str, Any]:
    row = get_workspace(workspace_id)
    if row is None:
        return {}
    return row.desktop_overlay


def has_workspace_overlay(workspace_id: str) -> bool:
    row = get_workspace(workspace_id)
    return bool(row and row.has_ui_overlay)


def get_workspace_installed_overlay(workspace_id: str) -> dict[str, list[str]]:
    row = get_workspace(workspace_id)
    if row is None:
        return {"apps": [], "widgets": []}
    return row.installed_overlay


def get_workspace_pinned_widgets_overlay(workspace_id: str) -> list[dict[str, Any]]:
    row = get_workspace(workspace_id)
    if row is None:
        return []
    return row.pinned_widgets_overlay


def get_workspace_topbar_overlay(workspace_id: str) -> list[Any]:
    row = get_workspace(workspace_id)
    if row is None:
        return []
    return row.topbar_overlay


def get_workspace_page_schema_overlay(workspace_id: str) -> dict[str, Any]:
    row = get_workspace(workspace_id)
    if row is None:
        return {}
    return row.page_schema_overlay


def get_workspace_icon_order_overlay(workspace_id: str) -> list[str]:
    row = get_workspace(workspace_id)
    if row is None:
        return []
    return row.icon_order_overlay


def get_workspace_widget_order_overlay(workspace_id: str) -> list[str]:
    row = get_workspace(workspace_id)
    if row is None:
        return []
    return row.widget_order_overlay


def get_workspace_hidden_sections_overlay(workspace_id: str) -> list[str]:
    row = get_workspace(workspace_id)
    if row is None:
        return []
    return row.hidden_sections_overlay


def get_workspace_home_scenario_ref_overlay(workspace_id: str) -> dict[str, Any]:
    row = get_workspace(workspace_id)
    if row is None:
        return {}
    return row.home_scenario_ref_overlay


def get_workspace_current_scenario_overlay(workspace_id: str) -> Optional[str]:
    row = get_workspace(workspace_id)
    if row is None or not row.has_current_scenario_overlay:
        return None
    return row.current_scenario_overlay


def set_workspace_overlay(workspace_id: str, overlay: Any) -> WebspaceManifest:
    return set_workspace_manifest(workspace_id, ui_overlay_json=overlay)


def set_workspace_desktop_overlay(workspace_id: str, desktop: Any) -> WebspaceManifest:
    current = get_workspace_overlay(workspace_id)
    overlay = dict(current) if isinstance(current, dict) else {}
    overlay["desktop"] = desktop
    return set_workspace_overlay(workspace_id, overlay)


def set_workspace_home_scenario_ref_overlay(workspace_id: str, scenario_ref: Any) -> WebspaceManifest:
    current = get_workspace_overlay(workspace_id)
    overlay = dict(current) if isinstance(current, dict) else {}
    workspace = dict(overlay.get("workspace")) if isinstance(overlay.get("workspace"), dict) else {}
    normalized = _normalize_home_scenario_ref(scenario_ref)
    if normalized:
        workspace["homeScenarioRef"] = normalized
        overlay["workspace"] = workspace
    else:
        workspace.pop("homeScenarioRef", None)
        if workspace:
            overlay["workspace"] = workspace
        else:
            overlay.pop("workspace", None)
    return set_workspace_overlay(workspace_id, overlay)


def set_workspace_current_scenario_overlay(workspace_id: str, scenario_id: Any) -> WebspaceManifest:
    workspace_id = _normalize_workspace_id(workspace_id)
    current = ensure_workspace(workspace_id)
    overlay = current.ui_overlay
    workspace = dict(overlay.get("workspace")) if isinstance(overlay.get("workspace"), dict) else {}
    normalized = _normalize_current_scenario(scenario_id)
    if normalized:
        workspace["currentScenario"] = normalized
        overlay["workspace"] = workspace
    else:
        workspace.pop("currentScenario", None)
        workspace.pop("current_scenario", None)
        if workspace:
            overlay["workspace"] = workspace
        else:
            overlay.pop("workspace", None)
    encoded_overlay = _encode_ui_overlay_json(overlay)
    if encoded_overlay == current.ui_overlay_json:
        return current

    sql = get_ctx().sql
    with sql.connect() as con:
        _ensure_schema(con)
        con.execute(
            "UPDATE y_workspaces SET ui_overlay_json=? WHERE workspace_id=?",
            (encoded_overlay, workspace_id),
        )
        catalog_version = _bump_workspace_catalog_version(con)
        con.commit()

    updated = replace(current, ui_overlay_json=encoded_overlay)
    _emit_workspace_event(
        "workspace.manifest.changed",
        updated,
        catalog_version=catalog_version,
    )
    return updated


def set_workspace_installed_overlay(workspace_id: str, installed: Any) -> WebspaceManifest:
    current = get_workspace_desktop_overlay(workspace_id)
    desktop = dict(current) if isinstance(current, dict) else {}
    desktop["installed"] = {
        "apps": _dedupe_text_list((installed or {}).get("apps") if isinstance(installed, dict) else []),
        "widgets": _dedupe_text_list((installed or {}).get("widgets") if isinstance(installed, dict) else []),
        "removedApps": _dedupe_text_list((installed or {}).get("removedApps") if isinstance(installed, dict) else []),
        "removedWidgets": _dedupe_text_list((installed or {}).get("removedWidgets") if isinstance(installed, dict) else []),
    }
    return set_workspace_desktop_overlay(workspace_id, desktop)


def set_workspace_pinned_widgets_overlay(workspace_id: str, pinned_widgets: Any) -> WebspaceManifest:
    current = get_workspace_desktop_overlay(workspace_id)
    desktop = dict(current) if isinstance(current, dict) else {}
    desktop["pinnedWidgets"] = _normalize_overlay_widget_list(pinned_widgets)
    return set_workspace_desktop_overlay(workspace_id, desktop)


def set_workspace_topbar_overlay(workspace_id: str, topbar: Any) -> WebspaceManifest:
    current = get_workspace_desktop_overlay(workspace_id)
    desktop = dict(current) if isinstance(current, dict) else {}
    desktop["topbar"] = _clone_overlay_json_list(topbar)
    return set_workspace_desktop_overlay(workspace_id, desktop)


def set_workspace_page_schema_overlay(workspace_id: str, page_schema: Any) -> WebspaceManifest:
    current = get_workspace_desktop_overlay(workspace_id)
    desktop = dict(current) if isinstance(current, dict) else {}
    desktop["pageSchema"] = _clone_overlay_json_dict(page_schema)
    return set_workspace_desktop_overlay(workspace_id, desktop)


def set_workspace_icon_order_overlay(workspace_id: str, icon_order: Any) -> WebspaceManifest:
    current = get_workspace_desktop_overlay(workspace_id)
    desktop = dict(current) if isinstance(current, dict) else {}
    desktop["iconOrder"] = _clone_overlay_text_list(icon_order)
    return set_workspace_desktop_overlay(workspace_id, desktop)


def set_workspace_widget_order_overlay(workspace_id: str, widget_order: Any) -> WebspaceManifest:
    current = get_workspace_desktop_overlay(workspace_id)
    desktop = dict(current) if isinstance(current, dict) else {}
    desktop["widgetOrder"] = _clone_overlay_text_list(widget_order)
    return set_workspace_desktop_overlay(workspace_id, desktop)


def set_workspace_hidden_sections_overlay(workspace_id: str, hidden_sections: Any) -> WebspaceManifest:
    current = get_workspace_desktop_overlay(workspace_id)
    desktop = dict(current) if isinstance(current, dict) else {}
    desktop["hiddenSections"] = _clone_overlay_text_list(hidden_sections)
    return set_workspace_desktop_overlay(workspace_id, desktop)
