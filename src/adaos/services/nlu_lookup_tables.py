from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Any, Iterable, Mapping

from adaos.services.agent_context import AgentContext, get_ctx

_log = logging.getLogger(__name__)

LOOKUP_NAMES = ("modal_id", "node_ref", "app_id", "scenario_id", "webspace_id")
DEFAULT_WEBSPACE_ID = "desktop"


def _hash_payload(payload: Any) -> str:
    data = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode("utf-8")
    return hashlib.sha256(data).hexdigest()


def _path_from_ctx(ctx: AgentContext, name: str) -> Path | None:
    paths = getattr(ctx, "paths", None)
    if paths is None:
        return None
    value = getattr(paths, name, None)
    if callable(value):
        try:
            value = value()
        except Exception:
            return None
    if not value:
        return None
    return Path(value)


def _package_workspace_dir(ctx: AgentContext) -> Path | None:
    paths = getattr(ctx, "paths", None)
    package_dir = getattr(paths, "package_dir", None)
    if callable(package_dir):
        try:
            package_dir = package_dir()
        except Exception:
            package_dir = None
    if not package_dir:
        return None
    workspace = Path(package_dir) / ".adaos" / "workspace"
    return workspace if workspace.exists() else None


def _unique_paths(paths: Iterable[Path | None]) -> list[Path]:
    seen: set[str] = set()
    out: list[Path] = []
    for path in paths:
        if not path:
            continue
        resolved = str(Path(path).resolve())
        if resolved in seen:
            continue
        seen.add(resolved)
        out.append(Path(path))
    return out


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        _log.debug("failed to read NLU lookup source %s", path, exc_info=True)
        return None


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _iter_mapping_values(value: Any) -> Iterable[tuple[str | None, Any]]:
    if isinstance(value, Mapping):
        for key, item in value.items():
            yield str(key), item
        return
    if isinstance(value, list):
        for item in value:
            if isinstance(item, Mapping):
                key = item.get("id")
                yield str(key) if key else None, item
            else:
                yield str(item), item


def _token(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _add(buckets: dict[str, dict[str, dict[str, Any]]], lookup: str, value: Any, *, source: str, label: Any = None) -> None:
    token = _token(value)
    if not token:
        return
    bucket = buckets.setdefault(lookup, {})
    current = bucket.setdefault(token, {"value": token, "sources": []})
    if source and source not in current["sources"]:
        current["sources"].append(source)
    label_token = _token(label)
    if label_token and label_token != token:
        current.setdefault("labels", [])
        if label_token not in current["labels"]:
            current["labels"].append(label_token)


def _add_app_entry(buckets: dict[str, dict[str, dict[str, Any]]], key: str | None, item: Any, *, source: str) -> None:
    item_map = _as_mapping(item)
    app_id = key or item_map.get("id") or item_map.get("app_id")
    _add(buckets, "app_id", app_id, source=source, label=item_map.get("title") or item_map.get("name"))
    for field in ("launchModal", "launch_modal", "modal_id", "modalId"):
        if item_map.get(field):
            _add(buckets, "modal_id", item_map.get(field), source=f"{source}.{field}")


def _add_modal_entry(buckets: dict[str, dict[str, dict[str, Any]]], key: str | None, item: Any, *, source: str) -> None:
    item_map = _as_mapping(item)
    modal_id = key or item_map.get("id") or item_map.get("modal_id")
    _add(buckets, "modal_id", modal_id, source=source, label=item_map.get("title") or item_map.get("name"))


def _collect_from_manifest(
    buckets: dict[str, dict[str, dict[str, Any]]],
    doc: Mapping[str, Any],
    *,
    source: str,
    scenario_id: str | None = None,
) -> None:
    if scenario_id:
        _add(buckets, "scenario_id", scenario_id, source=source)
    if doc.get("id"):
        _add(buckets, "scenario_id", doc.get("id"), source=f"{source}.id")

    catalog = _as_mapping(doc.get("catalog"))
    for key, app in _iter_mapping_values(catalog.get("apps")):
        _add_app_entry(buckets, key, app, source=f"{source}.catalog.apps")

    apps = doc.get("apps")
    for key, app in _iter_mapping_values(apps):
        _add_app_entry(buckets, key, app, source=f"{source}.apps")

    registry = _as_mapping(doc.get("registry"))
    for key, modal in _iter_mapping_values(registry.get("modals")):
        _add_modal_entry(buckets, key, modal, source=f"{source}.registry.modals")

    ui = _as_mapping(doc.get("ui"))
    application = _as_mapping(ui.get("application"))
    for key, modal in _iter_mapping_values(application.get("modals")):
        _add_modal_entry(buckets, key, modal, source=f"{source}.ui.application.modals")

    data = _as_mapping(doc.get("data"))
    data_catalog = _as_mapping(data.get("catalog"))
    for key, app in _iter_mapping_values(data_catalog.get("apps")):
        _add_app_entry(buckets, key, app, source=f"{source}.data.catalog.apps")


def _collect_workspace_manifests(buckets: dict[str, dict[str, dict[str, Any]]], ctx: AgentContext) -> None:
    package_workspace = _package_workspace_dir(ctx)
    skill_roots = _unique_paths(
        [
            _path_from_ctx(ctx, "skills_dir"),
            package_workspace / "skills" if package_workspace else None,
        ]
    )
    for skills_dir in skill_roots:
        if not skills_dir.exists():
            continue
        for path in sorted(skills_dir.glob("*/webui.json")):
            doc = _read_json(path)
            if isinstance(doc, Mapping):
                _collect_from_manifest(buckets, doc, source=path.parent.name)

    scenario_roots = _unique_paths(
        [
            _path_from_ctx(ctx, "scenarios_dir"),
            package_workspace / "scenarios" if package_workspace else None,
        ]
    )
    for scenarios_dir in scenario_roots:
        if not scenarios_dir.exists():
            continue
        for scenario_root in sorted(child for child in scenarios_dir.iterdir() if child.is_dir()):
            for file_name in ("scenario.json", "scenario.yaml", "scenario.yml"):
                path = scenario_root / file_name
                if path.exists() and path.suffix == ".json":
                    doc = _read_json(path)
                    if isinstance(doc, Mapping):
                        _collect_from_manifest(
                            buckets,
                            doc,
                            source=f"scenario.{scenario_root.name}",
                            scenario_id=scenario_root.name,
                        )
                    break
                if path.exists():
                    _add(buckets, "scenario_id", scenario_root.name, source=f"scenario.{scenario_root.name}")
                    break


def _collect_node_refs(buckets: dict[str, dict[str, dict[str, Any]]], ctx: AgentContext) -> None:
    config = getattr(ctx, "config", None)
    for attr in ("node_id", "node_name", "node_ref"):
        _add(buckets, "node_ref", getattr(config, attr, None), source=f"config.{attr}")

    try:
        from adaos.services.registry.subnet_directory import get_directory

        directory = get_directory()
        for node in directory.list_known_nodes():
            node_map = node if isinstance(node, Mapping) else getattr(node, "__dict__", {})
            for field in ("node_id", "id", "ref", "name", "label", "display_name"):
                _add(buckets, "node_ref", node_map.get(field), source=f"subnet_directory.{field}")
            aliases = node_map.get("aliases")
            if isinstance(aliases, list):
                for alias in aliases:
                    _add(buckets, "node_ref", alias, source="subnet_directory.aliases")
    except Exception:
        _log.debug("failed to collect node refs from subnet directory", exc_info=True)


def _finalize(buckets: dict[str, dict[str, dict[str, Any]]]) -> dict[str, list[dict[str, Any]]]:
    out: dict[str, list[dict[str, Any]]] = {name: [] for name in LOOKUP_NAMES}
    for name in LOOKUP_NAMES:
        items = []
        for value, meta in sorted(buckets.get(name, {}).items()):
            item = {"value": value, "sources": sorted(meta.get("sources") or [])}
            labels = sorted(meta.get("labels") or [])
            if labels:
                item["labels"] = labels
            items.append(item)
        out[name] = items
    return out


def lookup_values(payload: Mapping[str, Any], lookup: str) -> list[str]:
    values = []
    lookups = payload.get("lookups") if isinstance(payload, Mapping) else None
    for item in (lookups or {}).get(lookup, []):
        if isinstance(item, Mapping) and item.get("value"):
            values.append(str(item["value"]))
    return values


def rasa_lookup_entries(payload: Mapping[str, Any]) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for name in LOOKUP_NAMES:
        values = lookup_values(payload, name)
        if values:
            entries.append({"lookup": name, "examples": "\n".join(f"- {value}" for value in values)})
    return entries


def summarize_lookup_tables(payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for name in LOOKUP_NAMES:
        values = lookup_values(payload, name)
        summary.append({"lookup": name, "count": len(values), "hash": _hash_payload(values)})
    return summary


def collect_desktop_lookup_tables(
    ctx: AgentContext | None = None,
    *,
    webspace_id: str | None = None,
) -> dict[str, Any]:
    ctx = ctx or get_ctx()
    ws_token = webspace_id if isinstance(webspace_id, str) else None
    ws = (ws_token or DEFAULT_WEBSPACE_ID).strip() or DEFAULT_WEBSPACE_ID
    buckets: dict[str, dict[str, dict[str, Any]]] = {name: {} for name in LOOKUP_NAMES}

    _add(buckets, "webspace_id", ws, source="request.webspace_id")
    _collect_workspace_manifests(buckets, ctx)
    _collect_node_refs(buckets, ctx)

    lookups = _finalize(buckets)
    summary = summarize_lookup_tables({"lookups": lookups})
    return {
        "ok": True,
        "webspace_id": ws,
        "lookups": lookups,
        "summary": summary,
        "fingerprint": _hash_payload(summary),
    }
